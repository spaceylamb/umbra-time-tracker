"""The resident collector.

It fills the two hard gaps in backfilled data:
  1. window titles -> turns "45 minutes in Zoom" into "Zoom - team weekly"
     (tier B becomes tier A)
  2. input idleness -> separates "deep focus" from "nobody at the computer", a
     signal that simply does not exist in backfilled data

Everything happens in-process; no subprocesses are spawned. Measured cost on an
M2:
    subprocess approach  ioreg + lsappinfo + osascript    ->  8.5% of a core
    in-process approach  CGEventSource + NSWorkspace + AX ->  0.0001% of a core
One osascript call alone takes 157ms, which makes it entirely unusable for
resident polling.

Write strategy: buffer in memory, flush every 10 seconds or once 50 events have
accumulated. Two SQLite commits a second means two fsyncs a second, and that
costs far more battery than the CPU ever does.
"""

import signal
import sys
import time

import AppKit
import Foundation
import objc
import Quartz
from ApplicationServices import (AXIsProcessTrusted, AXUIElementCopyAttributeValue,
                                 AXUIElementCreateApplication,
                                 kAXFocusedWindowAttribute, kAXTitleAttribute)

from . import db, privacy

WINDOW_POLL_SEC = 2.0      # window titles
CLIP_POLL_SEC = 0.5        # clipboard changeCount (reading one int; very cheap)
IDLE_POLL_SEC = 5.0        # input idleness
FLUSH_SEC = 10.0
FLUSH_COUNT = 50
IDLE_THRESHOLD_SEC = 120   # no key or mouse for this long counts as "away"


# ─────────────────────────────────────────────────────────────
# 👇 This function is the one meant to be edited. What follows is a working
#    default; change it and it takes effect immediately.
# ─────────────────────────────────────────────────────────────
def clipboard_policy(text: str, source_app: str) -> dict | None:
    """Decide what to record for one clipboard write. None means discard entirely.

    The trade-off is yours to set, because it is your own data on the line:
      - store the full text  -> the richest context, but passwords, tokens and
                                private messages land in the store in plaintext
      - store length + hash  -> zero risk, but "what did I copy" is gone
                                (duplicate detection still works)
      - store the first N    -> the middle ground; what N should be, and which
                                apps to skip outright, depends on how you use it

    Available signals: text length, the source app's bundle id, and the shape of
    the content (URL / code / secret).
    """
    if privacy.classify_bundle(source_app) == privacy.DROP:
        return None
    stripped = text.strip()
    # Anything that looks like a secret is recorded by shape, never content
    if 20 <= len(stripped) <= 200 and " " not in stripped and any(
            c.isdigit() for c in stripped) and any(c.isalpha() for c in stripped):
        return {"kind": "opaque", "len": len(stripped), "app": source_app}
    return {"text": stripped[:500], "len": len(stripped), "app": source_app}


class Collector(Foundation.NSObject):
    """A PyObjC NSObject subclass — both notification callbacks and NSTimer need
    an ObjC object to deliver to."""

    def init(self):
        self = objc.super(Collector, self).init()
        self.buf = []
        self.seq = 0
        self.last_flush = time.time()
        self.ws = AppKit.NSWorkspace.sharedWorkspace()
        self.pb = AppKit.NSPasteboard.generalPasteboard()
        self.pb_count = self.pb.changeCount()
        self.last_title = None
        self.last_bundle = None
        self.idle_since = None
        self.ax_trusted = None   # None = not checked yet; the first poll prints
                                 # the actual state
        self.con = db.connect()
        return self

    # ── Event buffer ──────────────────────────────────────────
    # ⚠️ PyObjC registers **every** method on an NSObject subclass as an ObjC
    #    selector, inferring the argument count from the method name (`emit`
    #    becomes selector "emit" taking zero arguments). Pure-Python helpers must
    #    be marked @objc.python_method or importing raises BadPrototypeError.
    #    Only the names ending in _ are genuinely meant to be exposed to ObjC.
    @objc.python_method
    def emit(self, source, kind, payload, ts=None, dur_ms=None):
        ts_ms = int((ts or time.time()) * 1000)
        self.seq += 1
        self.buf.append((f"live:{ts_ms}:{self.seq}", ts_ms, dur_ms,
                         source, kind, payload))
        if len(self.buf) >= FLUSH_COUNT:
            self.flush()

    @objc.python_method
    def flush(self):
        if self.buf:
            db.insert(self.con, self.buf)
            self.buf.clear()
        self.last_flush = time.time()

    # ── Collection ────────────────────────────────────────────
    @objc.python_method
    def front_app(self):
        app = self.ws.frontmostApplication()
        return (app.bundleIdentifier() or "?", app.localizedName() or "?",
                app.processIdentifier()) if app else ("?", "?", 0)

    @objc.python_method
    def window_title(self, pid: int) -> str | None:
        """The focused window's title. Needs Accessibility permission; returns
        None without it."""
        el = AXUIElementCreateApplication(pid)
        err, win = AXUIElementCopyAttributeValue(el, kAXFocusedWindowAttribute, None)
        if err != 0 or win is None:
            return None
        err, title = AXUIElementCopyAttributeValue(win, kAXTitleAttribute, None)
        return str(title) if err == 0 and title else None

    # ── Timer callbacks ───────────────────────────────────────
    def pollWindow_(self, _timer):
        # Accessibility permission is **mutable at runtime**: flipping the
        # switch in System Settings takes effect for an already-running process
        # immediately, with no restart. So the single AXIsProcessTrusted() call
        # at startup is only a snapshot, and once printed it stays on screen
        # forever as a false alarm (measured: it reported ✗ at launch while
        # titles had been collecting fine for two hours). Re-check every poll and
        # log the transition.
        trusted = AXIsProcessTrusted()
        if trusted != self.ax_trusted:
            self.ax_trusted = trusted
            print(f"[{time.strftime('%H:%M:%S')}] Accessibility changed: "
                  f"{'✓ granted — collecting window titles' if trusted else '✗ revoked — titles will be empty'}")
            self.last_title = None          # state changed; force one re-record

        if self.idle_since:                 # nobody here; do not record windows
            return
        bundle, name, pid = self.front_app()
        if privacy.classify_bundle(bundle) == privacy.DROP:
            return
        title = self.window_title(pid)
        if (bundle, title) == (self.last_bundle, self.last_title):
            return                          # unchanged; nothing to record
        self.last_bundle, self.last_title = bundle, title
        self.emit("live", "window",
                  {"bundle": bundle, "app": name, "title": (title or "")[:300]})

    def pollClipboard_(self, _timer):
        count = self.pb.changeCount()
        if count == self.pb_count:
            return                    # only one int compared; unchanged, return
        self.pb_count = count
        text = self.pb.stringForType_(AppKit.NSPasteboardTypeString)
        if not text:
            return
        bundle, _, _ = self.front_app()
        payload = clipboard_policy(str(text), bundle)
        if payload is not None:
            self.emit("live", "clip", payload)

    def pollIdle_(self, _timer):
        idle = Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType)
        now = time.time()
        if idle >= IDLE_THRESHOLD_SEC and self.idle_since is None:
            self.idle_since = now - idle    # the moment idleness began
        elif idle < IDLE_THRESHOLD_SEC and self.idle_since is not None:
            dur = now - idle - self.idle_since
            if dur > 0:
                self.emit("idle", "away", {"reason": "no_input"},
                          ts=self.idle_since, dur_ms=int(dur * 1000))
            self.idle_since = None
            self.last_title = None      # on return, force one window re-record

    def pollFlush_(self, _timer):
        if self.buf and time.time() - self.last_flush >= FLUSH_SEC:
            self.flush()

    # ── System notifications (event-driven, no polling) ───────
    def appActivated_(self, note):
        app = note.userInfo().get("NSWorkspaceApplicationKey")
        if not app:
            return
        bundle = app.bundleIdentifier() or "?"
        if privacy.classify_bundle(bundle) == privacy.DROP:
            return
        self.emit("live", "activate",
                  {"bundle": bundle, "app": app.localizedName() or "?"})
        self.last_title = None    # app switched; force a title re-record next poll

    def screenLocked_(self, _n):
        self.emit("live", "lock", {}); self.flush()

    def screenUnlocked_(self, _n):
        self.emit("live", "unlock", {}); self.last_title = None

    def willSleep_(self, _n):
        self.emit("live", "sleep", {}); self.flush()

    def didWake_(self, _n):
        self.emit("live", "wake", {}); self.last_title = None


def _tcc_target() -> str:
    """Who TCC should be granting permission to.

    The permission attaches to **an executable**, and the host varies with how
    the process was started:
      run by hand in iTerm  -> the host is iTerm.app
      a launchd agent       -> the host is Python.app (the bundle the framework
                               build ships)
    So the instructions cannot be hardcoded; the current real identity has to be
    printed.

    ⚠️ Neither sys.executable nor realpath works:
         sys.executable           = /usr/local/bin/python3
         realpath(sys.executable) = .../Versions/3.13/bin/python3.13
         the image actually run   = .../Versions/3.13/Resources/Python.app
                                    /Contents/MacOS/Python
       TCC recognises the last of these, and it is not on the parent path of
       either of the first two.
       NSRunningApplication also returns nil under launchd (it is not an app as
       far as LaunchServices is concerned).
       NSBundle.mainBundle() is the only one that gives the right answer.
    """
    path = Foundation.NSBundle.mainBundle().bundlePath()
    return str(path) if path and str(path).endswith(".app") else sys.executable


def run():
    if not AXIsProcessTrusted():
        print("⚠️  No Accessibility permission — window titles will be empty.")
        print("   System Settings → Privacy & Security → Accessibility; add via Cmd-Shift-G:\n")
        print(f"       {_tcc_target()}\n")
        print("   No restart needed after flipping the switch — TCC applies to running")
        print("   processes immediately; the next poll starts collecting and logs the flip.\n")

    c = Collector.alloc().init()

    nc = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
    nc.addObserver_selector_name_object_(
        c, b"appActivated:", "NSWorkspaceDidActivateApplicationNotification", None)
    nc.addObserver_selector_name_object_(
        c, b"willSleep:", "NSWorkspaceWillSleepNotification", None)
    nc.addObserver_selector_name_object_(
        c, b"didWake:", "NSWorkspaceDidWakeNotification", None)

    dnc = Foundation.NSDistributedNotificationCenter.defaultCenter()
    dnc.addObserver_selector_name_object_(
        c, b"screenLocked:", "com.apple.screenIsLocked", None)
    dnc.addObserver_selector_name_object_(
        c, b"screenUnlocked:", "com.apple.screenIsUnlocked", None)

    for interval, sel in ((WINDOW_POLL_SEC, b"pollWindow:"),
                          (CLIP_POLL_SEC, b"pollClipboard:"),
                          (IDLE_POLL_SEC, b"pollIdle:"),
                          (FLUSH_SEC, b"pollFlush:")):
        Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval, c, sel, None, True)

    def bye(*_):
        c.flush()
        print("\nsaved, exiting.")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)

    print(f"Umbra collector running (Accessibility: "
          f"{'✓' if AXIsProcessTrusted() else '✗'})  Ctrl-C to stop")
    try:
        Foundation.NSRunLoop.currentRunLoop().run()
    finally:
        c.flush()
