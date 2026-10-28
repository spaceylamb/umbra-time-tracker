"""macOS knowledgeC importer — the only backfill source that covers native apps.

Source: ~/Library/Application Support/Knowledge/knowledgeC.db
The store underneath Screen Time, which the system writes continuously.

The /app/inFocus stream is "which app was in front, from when to when". It
covers every native app — Zoom, Lark, Cursor, Figma — the half of the world
Chrome cannot see.

Times are Apple Cocoa epoch (seconds since 2001-01-01); offset = 978307200.

⚠️ Needs Full Disk Access, granted to **the executable running this code**.
   A process running under a daemon cannot get it; run it from your own terminal:
       python3 umbra.py import --only knowledgec
⚠️ App level only, no window titles. "47 minutes in Zoom" is available;
   "in which meeting, with whom" is not — that takes a calendar or the live
   collector.
"""

import os
import shutil
import sqlite3
import tempfile

from .. import privacy

SOURCE = "app"          # the source name written into events, unlike the importer name
# Something every normal day: this many days with nothing new means it has
# stopped, and STALE_HINT is where to look first (plugins/staleness.py).
STALE_DAYS = 4
STALE_HINT = ('Which app was in front on this Mac. A few days away from the computer is '
              'normal; otherwise check that the scheduled job still has Full Disk '
              'Access.')
PLATFORM = "darwin"
REQUIRES = ["fda"]
APPLE_OFFSET = 978307200
KDB = os.path.expanduser("~/Library/Application Support/Knowledge/knowledgeC.db")


class PermissionDenied(RuntimeError):
    pass


def _open(tmp: str) -> sqlite3.Connection:
    snap = os.path.join(tmp, "k.db")
    try:
        shutil.copy2(KDB, snap)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(KDB + suffix):
                shutil.copy2(KDB + suffix, snap + suffix)
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM ZOBJECT LIMIT 1")
        return con
    except (PermissionError, OSError, sqlite3.OperationalError) as e:
        raise PermissionDenied(
            f"cannot read knowledgeC.db ({e}). Grant Full Disk Access to the HOST "
            "GUI app: System Settings → Privacy & Security → Full Disk Access."
        ) from e


def streams() -> list[tuple]:
    """Every stream in the store, with what it covers.

    /app/inFocus has long been the foreground-app stream, but Apple has changed
    stream names and retention across macOS versions. When nothing comes back,
    look at what is actually there before guessing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        con = _open(tmp)
        rows = con.execute("""
            SELECT ZSTREAMNAME, count(*),
                   date(min(ZSTARTDATE) + 978307200, 'unixepoch', 'localtime'),
                   date(max(ZSTARTDATE) + 978307200, 'unixepoch', 'localtime')
            FROM ZOBJECT GROUP BY ZSTREAMNAME ORDER BY 2 DESC
        """).fetchall()
        con.close()
    return rows


def collect(since_ms: int | None = None,
            min_seconds: int = 3) -> list[tuple]:
    # since_ms is accepted and ignored: this source reads a local file, and a
    # re-read takes milliseconds — there is no API quota to save. It stays so
    # every importer has the same signature and one loop can pass it by keyword.
    if not os.path.exists(KDB):
        raise PermissionDenied(f"{KDB} not found")

    with tempfile.TemporaryDirectory() as tmp:
        con = _open(tmp)
        rows = []
        for zid, bundle, start, end in con.execute("""
            SELECT Z_PK, ZVALUESTRING, ZSTARTDATE, ZENDDATE
            FROM ZOBJECT
            WHERE ZSTREAMNAME IN ('/app/inFocus', '/app/usage', '/app/activity')
              AND ZVALUESTRING IS NOT NULL
        """):
            if start is None or end is None:
                continue
            dur = end - start
            if dur < min_seconds:          # a flick past, noise
                continue
            if privacy.classify_bundle(bundle) == privacy.DROP:
                continue
            rows.append((
                f"kc:{zid}", int((start + APPLE_OFFSET) * 1000), int(dur * 1000),
                "app", "focus",
                {"bundle": bundle, "app": bundle.split(".")[-1]},
            ))
        con.close()
    return rows


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "app", "kind": None,
          "origin": ("knowledgeC.db", "macOS's own foreground-window log")}]

# /app/usage records foreground focus, which is real attention time. Without it a
# 30-minute Zoom call collapses into an instant and vanishes from the timeline.
DURATION = "trusted"


def render(e) -> str | None:
    from .. import render as R
    n = R.app_name(e)
    return f"📱 computer foreground: {n}" if n else None


def classify(e) -> str | None:
    """Container level: which app, never what was being done.

    suspect means App.InFocus could not see the screen lock, so this stretch is
    most likely the machine sitting unattended — claiming a category for it would
    assert we know what those hours were.
    """
    return None if e.get("suspect") else "app"
