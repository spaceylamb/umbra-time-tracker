"""Umbra — your scattered digital footprints, on one timeline.

    python3 -m umbra import                    # import every source
    python3 -m umbra import --only knowledgec  # one source (run alone when it needs FDA)
    python3 -m umbra stats                     # what the database holds
    python3 -m umbra show 2026-08-13           # rule-based segments (offline)
    python3 -m umbra infer 2026-08-13          # let a model judge the day
"""

import argparse
import json
import sqlite3
import os
import sys
import time

from . import analyze, db, inference as INF, ops, sources
from .importers import ALL, SOURCE_OF
from .plaud_auth import PlaudAuthExpired
from .whoop_auth import WhoopAuthExpired

# Exceptions that mean "authorisation expired": the import still runs the other
# sources, then exits naming these. A new source needing authorisation adds one
# here.
#
# A tuple rather than one type checked by name: when PLAUD arrived only WHOOP
# was here, and PlaudAuthExpired quietly fell through as "log a failure and
# carry on" — the opposite of the hard-failure policy chosen on 2026-08-15,
# with no test going red.
# tests/test_import_auth_abort.py finds every *AuthExpired class and checks it
# against this tuple.
AUTH_EXPIRED = (WhoopAuthExpired, PlaudAuthExpired)


def roster(only, exclude=None):
    """The sources this round will run, and those standing aside (name → why).

    The list comes from discover() rather than a hard-coded ALL, so sources in
    your own directory run too. Wrong-platform sources stand aside on their own;
    but one named with --only that stands aside **must be visible**: that was a
    person asking, and skipping it silently reads as "ran, but no data". A broken
    user source goes into skipped as well — "why didn't my source run" has to
    have an answer.
    """
    found, errors = sources.discover()
    names, skipped = [], [(n, f"failed to load: {e}") for n, e in errors.items()]
    for name in (only or list(found)):
        if name not in found:
            skipped.append((name, "no such source"))
            continue
        why = sources.skip_reason(found[name])
        if why is None and exclude and name in exclude:
            # Standing aside must be visible — once voice is split, these
            # sources belong to `umbra voice`, and skipping them silently would
            # make one look broken when it runs hourly next door.
            # Only the voice sources really go next door; anything else named
            # with --exclude is just left out, and must not claim otherwise.
            why = ("→ umbra voice" if name in ops.VOICE_SOURCES
                   else "left out with --exclude")
        if why is None and not sources.enabled(name):
            why = "turned off in config.toml"
        if why is None:
            # Not set up is not a failure: someone without a WHOOP has nothing
            # to fix, and failing it every round would turn every sync red and
            # bury the source that really broke.
            gaps = sources.missing(found[name])
            why = f"not set up — {gaps[0]}" if gaps else None
        (skipped if why else names).append((name, why) if why else name)
    return names, skipped


class ImportIncomplete(SystemExit):
    """Some sources failed this round. Raised only once every source has had
    its turn — one failing never starves the rest — and it carries the days the
    round did touch, which the voice lane still has to scan."""

    def __init__(self, message: str, days: list[str]):
        super().__init__(message)
        self.days = days


def cmd_import(args):
    """Return **the days this round actually touched** (local calendar days).

    For ops.voice: which days tasks and claims scan comes from this, not from a
    "today plus yesterday" calendar window — recordings can reach the cloud
    over a week late, and a calendar window would miss those days for good,
    with no error.
    """
    con = db.connect()
    touched: set[str] = set()
    names, skipped = roster(args.only, getattr(args, "exclude", None))
    mods, _ = sources.discover()
    for name, why in skipped:
        print(f"  – {name:<11} {why}")
    run = db.progress_start(con, names)
    try:
        failed = _import_each(con, args, names, mods, run, touched)
    except BaseException:
        # Ctrl-C, a crash: never leave the page showing an import that is
        # still "reading".
        db.progress_abort(con, run)
        raise
    print()
    cmd_stats(args)
    if failed:
        # A round where a source failed must not exit 0. A scheduler reads the
        # exit code and nothing else, and "every source timed out, zero rows,
        # exit 0" is a silent failure the imports table holds for weeks.
        expired = [(n, e) for n, e, auth in failed if auth]
        msg = f"\n{len(failed)} source(s) failed: {', '.join(n for n, _, _ in failed)}"
        for n, e in expired:
            # Expired authorisation is the one failure a retry never heals,
            # so it is said loudest — but after the other sources ran, not
            # instead of them.
            msg += f"\nauth expired for {n} — fix it and rerun:\n  {e}"
        raise ImportIncomplete(msg, sorted(touched))
    return sorted(touched)


def _import_each(con, args, names, mods, run, touched):
    """Run each source in turn; return [(name, error, auth_expired)] for the
    ones that failed."""
    import datetime as _dt
    failed = []
    for name in names:
        db.progress_set(con, run, name, "reading")
        t0 = time.time()
        # By keyword, never by position: knowledgec's second parameter is
        # min_seconds, and a positional call would feed it a timestamp — no
        # error, just wrong data.
        since_ms = None if getattr(args, "full", False) else \
            db.since_for(con, getattr(mods[name], "SOURCE", name))
        try:
            rows = mods[name].collect(since_ms=since_ms)
        except Exception as e:                          # noqa: BLE001
            # An expired authorisation is a hard failure (the policy chosen on
            # 2026-08-15): it never heals on retry, and quietly logged it would
            # sit in the imports table until weeks of missing data were noticed.
            # Hard means the round exits non-zero and says so by name — not
            # that the sources after it go unrun.
            db.record_import(con, getattr(mods[name], "SOURCE", name), ok=False, error=str(e)[:400])
            db.progress_set(con, run, name, "failed", error=str(e))
            print(f"  ✗ {name:<11} {e}")
            failed.append((name, e, isinstance(e, AUTH_EXPIRED)))
            continue
        # Segmenting sources are recomputed: they fold raw records into
        # intervals, so a new algorithm produces different rows from the same
        # raw data. INSERT OR IGNORE would ignore the fix entirely (same uid,
        # new content), and rows the new algorithm no longer produces would be
        # left as orphans. Seen once: after the fix that merges same-place round
        # trips, the database still held the old 154-minute stay.
        rewrite = (getattr(args, "full", False)
                   or getattr(mods[name], "REWRITE", False))
        write_stats = None
        touched_rows = rows
        if getattr(mods[name], "SEGMENTED", False) and rows:
            # Delete **the window this recomputation covers**, not the new rows'
            # span. They differ: the new algorithm may no longer produce the last
            # few rows (merged, or judged a gap), and those orphans lie outside
            # the new span, so deleting by span would leave them.
            lo = since_ms if since_ms is not None else min(r[1] for r in rows)
            n = db.replace_range(con, getattr(mods[name], "SOURCE", name), lo,
                                 int(time.time() * 1000), rows)
        elif getattr(mods[name], "GROUPED", False) and rows:
            # Grouped sources: one thing split over several rows, and the count
            # changes. The index in the uid is a **position**, not an identity,
            # so when a new version is shorter an overwrite never reaches the
            # leftover rows — they would stay as duplicates for good (see
            # db.replace_groups).
            write_stats = db.replace_groups(con, rows)
            touched_rows = write_stats["rows"]
            # Rows really written. Unchanged rows are no longer rewritten, so
            # the log's "rewritten" has to say so — otherwise every round reads
            # as thousands rewritten, like busywork.
            n = write_stats["new"] + write_stats["changed"] + write_stats["deleted"]
        else:
            # 1:1 sources: --full overwrites the payload. The uid is the source's
            # natural key and never changes, so with INSERT OR IGNORE alone a
            # changed field in an importer would never reach the database.
            #
            # REWRITE sources overwrite every time: they return only what this
            # round actually fetched (plaud re-fetches recent files so a transcript
            # completed in the cloud replaces the partial one), so there is no
            # write amplification. A source returning everything must not set it.
            # An overwrite's rowcount includes unchanged rewrites, which must not
            # pass for new rows. Compare for real changes first: it keeps the log
            # honest and hands only the days that really changed downstream.
            if rewrite:
                write_stats = db.changed_rows(con, rows)
                touched_rows = write_stats["rows"]
            else:
                # IGNORE writes only new rows, so only their days were touched.
                touched_rows = db.new_rows(con, rows)
            n = db.insert(con, rows, replace=rewrite)
        actual = ((write_stats["new"] + write_stats["changed"])
                  if write_stats is not None else n)
        db.record_import(con, getattr(mods[name], "SOURCE", name), ok=True,
                         added=actual)
        db.progress_set(con, run, name, "done", rows=len(rows))
        # The days this round touched, by each row's own ts, not "now" — a
        # recording three days late belongs to the day it was made, and that is
        # the day to scan.
        touched.update(
            _dt.datetime.fromtimestamp(r[1] / 1000).date().isoformat()
            for r in touched_rows)
        if write_stats is not None:
            print(f"  ✓ {name:<11} pulled {len(rows):>7} / rewritten {n:>7} "
                  f"(new {write_stats['new']} / changed {write_stats['changed']} / "
                  f"unchanged {write_stats['unchanged']})"
                  f"  ({time.time() - t0:.1f}s)")
        else:
            print(f"  ✓ {name:<11} pulled {len(rows):>7} / added {n:>7}"
                  f"  ({time.time() - t0:.1f}s)")
    return failed


def cmd_sources(_args=None):
    """Each source: platform, requirements, and what is missing now."""
    found, errors = sources.discover()
    for name in sorted(found):
        mod = found[name]
        why = sources.skip_reason(mod)
        if why:
            print(f"  – {name:<11} {why}")
            continue
        if not sources.enabled(name):
            print(f"  – {name:<11} turned off in config.toml")
            continue
        st = sources.status(mod)
        unknown = [hint for _, ok, hint in st if ok is None]
        if missing := sources.missing(mod):
            print(f"  – {name:<11} not set up: " + "; ".join(missing))
        elif unknown:
            print(f"  ? {name:<11} " + "; ".join(unknown))
        else:
            print(f"  ✓ {name:<11} ready")
    for name, err in sorted(errors.items()):
        print(f"  ✗ {name:<11} failed to load: {err}")
    print("\nCustom sources go in ~/.config/umbra/sources/<name>.py with a "
          "collect(since_ms) function; same names override built-ins.")


def cmd_stats(_args=None):
    rows = db.stats(db.connect())
    if not rows:
        print("store is empty — run import first")
        return
    print(f"{'source':<10} {'events':>9}   range")
    print("-" * 46)
    for src, n, lo, hi in rows:
        print(f"{src:<10} {n:>9}   {lo} → {hi}")
    print("-" * 46)
    print(f"{'total':<10} {sum(r[1] for r in rows):>9}   → {db.DB_PATH}")


# The bundle launchd actually runs for the collector (see collector._tcc_target).
# Only a framework build (python.org, Homebrew) has one; it sits in the base
# installation, not in a venv.
PY_APP = os.path.join(sys.base_prefix, "Resources", "Python.app")

def job_python() -> str:
    """The interpreter `umbra schedule install` writes into the job: the one
    running umbra now, **unresolved**.

    Unresolved is the point. A uv / pipx / venv install's bin/python is a
    symlink to a base interpreter, and Python finds its environment by looking
    beside the path it was started from. Resolve it and the job starts the bare
    base interpreter: `No module named umbra` on a clean machine, or — worse —
    an older umbra installed there, running quietly. Full Disk Access is a
    separate question with its own answer (tcc_binary()).
    """
    return sys.executable


def launchd_python() -> str:
    """The interpreter the installed job actually runs — read from its plist.

    Not sys.executable: that is **the interpreter running doctor now**. Run
    from a `(base)` conda environment it names conda's python while launchd
    runs another. A diagnostic naming the wrong thing is worse than none: you
    would grant an interpreter that plays no part, still fail, and have no idea
    why (hit on 2026-08-15). Only with no job installed does it fall back to
    the one install would write.
    """
    import plistlib
    path = os.path.expanduser(f"~/Library/LaunchAgents/{ops.LABEL_SYNC}.plist")
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)["ProgramArguments"][0]
    except (OSError, KeyError, IndexError, plistlib.InvalidFileException):
        return job_python()


def tcc_binary() -> str:
    """What to grant Full Disk Access to: the real file behind the scheduled
    interpreter. TCC checks the image executed, not a symlink to it."""
    return os.path.realpath(launchd_python())


def _designated_req(path: str) -> str:
    """A bundle's own designated requirement — to compare with the csreq TCC
    stored."""
    import subprocess
    try:
        r = subprocess.run(["codesign", "-d", "-r-", path],
                           capture_output=True, timeout=10)
        out = (r.stdout + r.stderr).decode(errors="replace")
        for line in out.splitlines():
            if line.startswith("designated =>"):
                return line[len("designated =>"):].strip()
        return out.strip().splitlines()[-1] if out.strip() else "(none)"
    except Exception as e:                                  # noqa: BLE001
        return f"(unavailable: {e})"


def _decode_csreq(blob: bytes) -> str:
    """Decode a binary requirement from TCC into words. `csreq -r- -t` reads
    stdin and prints text."""
    import subprocess
    try:
        r = subprocess.run(["csreq", "-r-", "-t"], input=blob,
                           capture_output=True, timeout=5)
        return (r.stdout or r.stderr).decode(errors="replace").strip() or "(empty)"
    except Exception as e:                                  # noqa: BLE001
        return f"(could not decode: {e})"


def cmd_doctor(_args=None):
    """Diagnose TCC permissions. Run this first when knowledgeC cannot be read."""
    import subprocess
    import sys

    from .importers import knowledgec

    print(f"Python : {sys.executable}")
    print("Host process chain (TCC grants go to the GUI app on this chain):")
    pid = os.getpid()
    for _ in range(8):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            if not out:
                break
            ppid, comm = out.split(None, 1)
            print(f"    {comm}")
            pid = int(ppid)
            if pid <= 1:
                break
        except Exception:                               # noqa: BLE001
            break

    # The TCC grant list. System Settings shows every Python entry as "Python"
    # with the bundle id org.python.python — impossible to tell apart by eye.
    # Only the database says which was granted.
    #
    # Both services matter: the collector needs Accessibility, knowledgec needs
    # SystemPolicyAllFiles (Full Disk Access). They are **independent** records;
    # granting one is not granting the other — though the UI makes them look the
    # same.
    SERVICES = [("kTCCServiceAccessibility", "Accessibility (needed by collector)"),
                ("kTCCServiceSystemPolicyAllFiles", "Full Disk Access (needed by knowledgec)")]
    tcc = "/Library/Application Support/com.apple.TCC/TCC.db"
    for service, label in SERVICES:
        print(f"\n{label} grant list:")
        try:
            con = sqlite3.connect(f"file:{tcc}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT client, auth_value, csreq FROM access "
                "WHERE service=? ORDER BY auth_value DESC", (service,)).fetchall()
            con.close()
        except sqlite3.OperationalError as e:
            print(f"    cannot read TCC.db ({e}) — this step itself needs Full Disk Access.")
            continue
        hits = [r for r in rows if "python" in (r[0] or "").lower()]
        if not rows:
            print("    (empty)")
        for client, auth, csreq in rows:
            mark = "✓ allowed" if auth == 2 else ("✗ denied" if auth == 0 else f"? auth={auth}")
            print(f"    {mark}  {client}")
            # auth_value=2 only means "allowed"; TCC also checks the csreq — the
            # code requirement the record was bound to. A mismatch is still denied,
            # with no sign of it in the UI.
            if "python" in (client or "").lower() and csreq:
                print(f"        csreq: {_decode_csreq(csreq)}")
        if not hits:
            print("    ⚠️ no python on the list — this permission is not granted to Python")
        # "Not in the list" and "in the list but denied" are told apart: for the
        # latter, adding entries does nothing — an explicit denial wins, and that
        # entry has to be changed or removed first.
        denied = [c for c, a, _ in rows if a == 0 and c == tcc_binary()]
        if denied:
            print(f"    ⛔ the scheduled job's binary is EXPLICITLY denied: {denied[0]}")
            print("       Not a missing entry — auth_value=0. Adding others won't help;")
            print("       flip this one on in System Settings, or remove it with − and rerun.")

    # The control: the binary the scheduled job actually runs. TCC checks **the
    # image executed**, and when python3 runs an ordinary script that image is
    # bin/python3.13, not Python.app — granting only Python.app still leaves
    # ordinary scripts denied.
    print("\n    The binary the scheduled job actually runs (find it in the FDA list):")
    print(f"        {tcc_binary()}")
    cur = os.path.realpath(sys.executable)
    if cur != tcc_binary():
        # The interpreter you are in is not the one scheduled jobs use — so the
        # thing to grant is different too.
        print("    ⚠️ doctor is running under a different interpreter — don't grant the wrong one:")
        print(f"        {cur}")
    if os.path.isdir(PY_APP):
        print("    The bundle the collector (PyObjC) reports, and its designated requirement:")
        print(f"        {PY_APP}")
        print(f"        {_designated_req(PY_APP)}")

    print("\nknowledgeC.db :", end=" ")
    try:
        all_streams = knowledgec.streams()
        rows = knowledgec.collect()
    except knowledgec.PermissionDenied as e:
        print("✗ unreadable")
        print(f"    {e}")
        print("\n  How to fix:")
        print("    1. System Settings → Privacy & Security → Full Disk Access")
        print("    2. Add the terminal app you actually use (the one in the chain above),")
        print("       not Terminal.app — TCC grants per executable; the wrong app does nothing")
        print("    3. Fully quit and reopen that app afterwards (Cmd-Q, not closing the window);")
        print("       a running process never picks up newly granted permissions")
        return
    print(f"✓ readable, {len(all_streams)} streams")
    print(f"\n{'stream':<38} {'rows':>8}   range")
    print("-" * 72)
    for name, n, lo, hi in all_streams[:30]:
        print(f"{(name or '(null)'):<38} {n:>8}   {lo} → {hi}")

    if not rows:
        print("\n⚠️ the foreground-app streams (/app/inFocus etc.) have no data.")
        print("   Look through the list above for a stream that records app usage.")
        return
    import datetime as _dt
    fmt = "%Y-%m-%d"
    lo = _dt.datetime.fromtimestamp(min(r[1] for r in rows) / 1000).strftime(fmt)
    hi = _dt.datetime.fromtimestamp(max(r[1] for r in rows) / 1000).strftime(fmt)
    print(f"\n✓ {len(rows)} foreground-app records, {lo} → {hi}")
    print("  run: python3 -m umbra import --only knowledgec")


# Not updated for longer than this is stale. The import job runs every interval,
# so silence for two of them means the job has a problem, not that you were idle.
# Both the period and the threshold live in ops and come from config — once one
# was copied by hand into the plist and the other hard-coded as 120 minutes, and
# with a six-hour healthy job all 13 sources showed ⚠ between rounds.
STALE_MIN = ops.stale_min()

# The consecutive-failure threshold has one source, in db (the page's ⚠ uses it
# too; two copies once disagreed).
FAIL_STREAK = db.FAIL_STREAK


def _ago(minutes: float | None) -> str:
    if minutes is None:
        return "never"
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    if minutes < 48 * 60:
        return f"{minutes/60:.1f} h ago"
    return f"{minutes/1440:.1f} d ago"


def cmd_freshness(_args=None):
    import datetime as _dt
    rows = db.freshness(db.connect())
    if not rows:
        print("store is empty")
        return
    print(f"{'source':<10} {'events':>8}  {'last event':<14} "
          f"{'last try':<12} {'last ok':<12}  status")
    print("-" * 82)
    problems = []
    for r in rows:
        last_event = (_dt.datetime.fromtimestamp(r["last_event"] / 1000)
                      .strftime("%m-%d %H:%M") if r["last_event"] else "—")
        if r["live"]:
            # The collector writes continuously and has no import step; only the
            # data itself stopping is a problem.
            run_col = "(live)"
            bad = (r["event_age_min"] or 0) > STALE_MIN
            note = "⚠️ collector may be down" if bad else "✓"
        else:
            run_col = _ago(r["run_age_min"])
            err = (r["error"] or "")[:24]
            if r["fails"] >= FAIL_STREAK:
                bad, note = True, f"✗ {r['fails']} consecutive failures: {err}"
            elif r["last_ok"] is None:
                bad, note = True, f"✗ never succeeded: {err}"
            elif r["run_age_min"] is None or r["run_age_min"] > STALE_MIN:
                bad, note = True, "⚠️ importer has not run for too long"
            elif r["ok"] is False:
                # The last attempt failed but earlier ones succeeded — the data has
                # not stopped. That is what the four Full Disk Access sources look
                # like outside an interactive terminal; calling it a fault would
                # send someone to fix something that is not broken.
                bad, note = False, f"· last try failed (data not stale): {err}"
            else:
                bad, note = False, "✓"
        if bad:
            problems.append(r["source"])
        ok_col = "(live)" if r["live"] else _ago(r["ok_age_min"])
        print(f"{r['source']:<10} {r['count']:>8}  {last_event:<14} "
              f"{run_col:<12} {ok_col:<12}  {note}")

    print("\nAn old 'last event' is often fine — no commits for two days is normal.")
    print("Old 'last try' = the schedule is not running; old 'last ok' = no data coming in.")
    print(f"One failure never alarms (could be network/permissions); {FAIL_STREAK} in a row does.")
    if problems:
        print(f"\n⚠️ sources with problems: {', '.join(problems)}")
        print("   catch up manually: python3 -m umbra import")
        print("   inspect the job: launchctl print gui/$(id -u)/com.umbra.sync")
    else:
        print("\n✓ all sources healthy")


def cmd_collect(_args=None):
    try:
        from . import collector      # deferred: only this command needs PyObjC
    except ImportError as e:
        # A common trap: conda's (base) takes over PATH in an interactive shell,
        # so `python3` is not the interpreter the dependencies were installed into.
        print(f"PyObjC missing: {e}\n")
        print(f"current interpreter: {sys.executable}")
        print("install into THIS interpreter:\n")
        print(f'  "{sys.executable}" -m pip install \\')
        print("      pyobjc-framework-Cocoa pyobjc-framework-Quartz \\")
        print("      pyobjc-framework-ApplicationServices\n")
        print("Use `python3 -m pip`, not bare `pip3` — it guarantees pip matches the python.")
        raise SystemExit(1)
    collector.run()


def cmd_coverage(args):
    print(f"{'day':<12} {'A content':>9} {'B container':>9} {'off-screen':>9}"
          f" {'C no data':>9} {'away':>8} {'suspect':>8}   {'events':>7}")
    print("-" * 88)
    for day in args.days:
        c = analyze.coverage(day)
        print(f"{c['day']:<12} {c['A']:>7}min {c['B']:>7}min {c['off']:>7}min"
              f" {c['C']:>7}min {c['away']:>6}min {c['ambiguous']:>6}min"
              f"   {c['events']:>7}")
    print("\nA = content-level evidence; we can say what was being done")
    print("B = only which app/domain; not what was actually happening")
    print("off-screen = WHOOP sleep/workouts: away from the computer but accounted for")
    print("C = no events at all")
    print("away = collector idle detection confirmed nobody present (excluded from the denominator)")
    print(f"suspect = ≥{analyze.STALE_FOCUS_MIN}min parked on one app; "
          "deep focus and absence look the same — this column shrinks once the "
          "collector runs")


def cmd_infer(args):
    """Run inference. By default only days **not yet run** — re-running a day is
    dozens of model calls, and redoing everything by default would burn a lot
    with nobody watching. Re-running needs an explicit --force."""
    from . import infer
    con = db.connect()
    days = args.days or [r[0] for r in con.execute(
        "SELECT DISTINCT date(ts/1000,'unixepoch','localtime') d FROM events "
        "WHERE d >= ? ORDER BY d", (args.since,))]
    model = args.model or infer.MODEL
    from . import providers
    try:
        providers.resolve(model)
    except RuntimeError as e:
        raise SystemExit(f"  ✗ {e}") from None
    for day in days:
        have = INF.load_events(con, day).get(model)
        if have and not args.force:
            print(f"  · {day} already has {len(have)} events, skipping (--force to redo)")
            continue
        print(f"  {day} …")
        r = infer.run_day(con, day, model, log=lambda m: print(m),
                          fresh=getattr(args, "fresh", False))
        if r.get("reused"):
            print(f"    reused {r['reused']} spans (digest unchanged), "
                  f"called {r['fresh_calls']} windows")
        if not r["spans"]:
            print(f"    ✗ got no spans at all ({r['failed']} windows failed)")
            continue
        # One day's second pass failing must not take every later day with it —
        # the same reason each source is isolated in cmd_import. The first pass is
        # already stored; a re-run only redoes the second.
        try:
            evs = infer.merge(con, day, r["spans"], model,
                              dirty=r["fresh_calls"] > 0 or r["failed"] > 0)
        except Exception as e:                          # noqa: BLE001
            print(f"    ⚠ {len(r['spans'])} spans saved, but composing events failed: {e}")
            continue
        if not evs:
            print(f"    ⚠ {len(r['spans'])} spans saved, but no events came out")
            continue
        cov = sum(INF._min(e["end"]) - INF._min(e["start"]) for e in evs)
        warn = f"  ⚠ {r['failed']} windows failed" if r["failed"] else ""
        lo, hi = analyze.day_bounds(day, con)
        print(f"    ✓ {len(r['spans'])} spans → {len(evs)} events · "
              f"covered {cov}/{(hi - lo + 1) // 60000} min{warn}")


def cmd_viz(args):
    """The page as one self-contained file next to the database — the same
    file every scheduled run leaves — and open it."""
    from . import viz
    out = viz.write(args.out or viz.OUT, raw_days=getattr(args, "raw_days", None))
    size = os.path.getsize(out) / 1e6
    print(f"{out}  ({size:.1f} MB)")
    # Written next to the database by default, never into a repository — this
    # file embeds browsing history, clipboard text and AI conversations in full,
    # and must never have a chance of being picked up by git add.
    print("⚠️  contains your full raw history — never commit or share this file")
    if not args.no_open:
        import subprocess
        subprocess.run(["open", out], check=False)


def cmd_export(args):
    """The new page as a static site (or one file): what the phone reads."""
    from . import export, viz
    if args.single:
        r = export.write_single(args.single, raw_days=args.raw_days)
        print(f"{args.single}  ({r['bytes'] / 1e6:.1f} MB, {r['files']} payloads inline)")
    else:
        out = args.out or viz.SITE_DIR
        r = export.write(out, raw_days=args.raw_days, ours=args.out is None)
        print(f"{out}  ({r['files']} files, {r['bytes'] / 1e6:.1f} MB of data)")
    print("⚠️  contains your raw history — deploy it only behind authentication")


def cmd_serve(args):
    from . import serve
    serve.serve(host=args.host, port=args.port or serve.PORT,
                open_browser=not args.no_open, api_only=args.api_only)


def cmd_show(args):
    """Rule-based segments for one day. Offline, no model call.

    This used to make a second model call of its own, through a prompt and a
    hardcoded Gemini endpoint that lived in analyze.py — a duplicate of the
    inference path with a category list that had drifted out of step with
    taxonomy.py. That path is gone; `umbra infer <day>` is the model pass, and
    it is the only one.
    """
    segs = analyze.build(args.day)
    if not segs:
        print(f"no data for {args.day}")
        return
    print(f"=== {args.day} rule-based segments: {len(segs)} ===")
    print(analyze.render(segs))
    print(f"\nThis is the offline rule-based view. For the model's "
          f"reconstruction: umbra infer {args.day}")


from .blocks import CAT_NAME        # noqa: E402  the copy beside the tiers; do not copy it again


def cmd_blocks(args):
    """A day cut into 5-minute slots, each saying what was going on.

    Runs of identical slots are merged by default — 288 raw rows are unreadable.
    --raw shows them all.
    """
    from . import blocks as B
    con = db.connect()
    lo, hi = analyze.day_bounds(args.day, con)
    bs = B.blocks(db.load_range(con, lo, hi), lo, (hi - lo + 1) // 60000)

    if args.raw:
        for b in bs:
            cat = CAT_NAME.get(b["cat"], "—")
            print(f"{b['t']}  {cat:<7} {b['label'][:46]:<46} "
                  f"{'⚠' if b['conflict'] else ' '} {','.join(b['why'])}")
    else:
        runs, cur = [], None
        for b in bs:
            k = (b["cat"], b["label"], tuple(b["also"]))
            if cur and cur["k"] == k:
                cur["n"] += 1
                cur["cf"] |= bool(b["conflict"])
                continue
            if cur:
                runs.append(cur)
            cur = {"k": k, "n": 1, "t": b["t"], "i": b["i"],
                   "cf": bool(b["conflict"])}
        runs.append(cur)
        for r in runs:
            cat, lab, also = r["k"]
            end = (r["i"] + r["n"]) * B.SLICE_MIN
            extra = ("   + " + " / ".join(a[:30] for a in also)) if also else ""
            print(f"  {r['t']}–{end//60:02d}:{end%60:02d} {r['n']*B.SLICE_MIN:>4}min  "
                  f"[{CAT_NAME.get(cat, '(empty)')}] {lab[:40]}{extra}"
                  f"{'  ⚠' if r['cf'] else ''}")

    import collections
    c = collections.Counter(b["cat"] for b in bs)
    print(f"\n{args.day} · {B.SLICE_MIN}-minute slots, {len(bs)} total")
    for cat, n in c.most_common():
        print(f"  {CAT_NAME.get(cat, '(empty)'):<8} {n:>3} slots = {n*B.SLICE_MIN:>4}min"
              f"  {'█' * int(n/3)}")
    # Conflicts are for you to settle; blind spots only explain why a source was
    # silent. Counted apart — mixed, "⚠ how many still need a look" would be
    # permanently inflated.
    for fld, mark, name in (("conflict", "⚠", "slots in conflict"),
                            ("note", "ⓘ", "slots in a known blind spot")):
        n_blocks = sum(1 for b in bs if b[fld])
        if not n_blocks:
            continue
        print(f"\n  {mark} {name}: {n_blocks}")
        seen = collections.Counter()
        for b in bs:
            for x in b[fld]:
                seen[x] += 1
        for k, n in seen.most_common():
            print(f"      {n:>3} slots  {k}")


def _locked(fn):
    """The two jobs share one lock. Without it this round gives up, with an exit
    code distinct from a real failure."""
    try:
        with ops.job_lock("umbra"):
            rc = fn()
    except ops.LockBusy as e:
        print(f"  – {e}")
        raise SystemExit(ops.EX_BUSY)
    raise SystemExit(rc)


def cmd_sync(_args=None):
    _locked(ops.sync)


def cmd_voice(_args=None):
    """The voice lane's own round: import voice sources → plugins → viz → deploy."""
    _locked(ops.voice)


def cmd_goals(args):
    """This week's three most important things, and whether the time went there.

    The one place priorities are set from the terminal; the page sets them
    through `umbra serve`. Both write the same table, and saving new wording
    starts judging the week.
    """
    import datetime as dt

    from . import db, goals as G, infer

    week = args.week or G.week_of(dt.date.today().isoformat())
    con = db.connect()

    if args.action == "set":
        texts = [t.strip() for t in (args.text or []) if t.strip()]
        # Three is a ceiling, not a suggestion. A fourth "most important thing"
        # means none of them is — the constraint is where the feature's value
        # comes from, so it does not bend.
        if len(texts) > 3:
            print(f"  ✗ at most three, you gave {len(texts)}")
            return
        if not texts:
            print('  usage: umbra goals set "first thing" "second thing" "third thing"')
            return
        G.save_goals(con, week, texts,
                     now_ms=int(dt.datetime.now().timestamp() * 1000))
        print(f"  ✓ {week}: {len(texts)} set")
        for i, t in enumerate(texts, 1):
            print(f"    {i}. {t}")
        print("\n  The next `umbra sync` credits this week's time to them.")
        return

    texts = G.load_goals(con, week)
    if not texts:
        print(f"  {week} has no priorities yet.")
        print('  umbra goals set "first thing" "second thing" "third thing"')
        return

    # The seven days: ISO week → that week's Monday
    y, w = int(week[:4]), int(week[6:])
    mon = dt.date.fromisocalendar(y, w, 1)
    days = [(mon + dt.timedelta(i)).isoformat() for i in range(7)]
    gdig = G.gdigest(texts)
    elapsed = [d for d in days if d <= dt.date.today().isoformat()]
    mins = G.totals(con, days, infer.MODEL, gdig)
    done = G.done_days(con, elapsed, infer.MODEL, gdig)

    print(f"  {week}  ({mon} – {mon + dt.timedelta(6)})")
    for i, t in enumerate(texts, 1):
        h = mins.get(i, 0) / 60          # nothing credited is 0 — "no time spent"
        mark = "✓" if h >= 10 else " "   # and "not judged yet" must not look alike
        print(f"   {mark} {i}. {t}")
        print(f"        {h:.1f} h   {h / 168 * 100:.1f}% of the week   floor 10 h")
    if len(done) < len(elapsed):
        print(f"\n  ⚠ only {len(done)} days of this week are judged — the numbers are incomplete")


def cmd_schedule(args):
    """Generate and load the scheduled jobs (launchd on macOS; elsewhere, print
    configuration to copy)."""
    py = job_python()
    jobs = [(ops.LABEL_SYNC, ["-m", "umbra", "sync"],
             ops.interval_min() * 60)]
    if ops.split_voice():
        jobs.append((ops.LABEL_VOICE, ["-m", "umbra", "voice"],
                     ops.voice_interval_min() * 60))
    if args.collector:
        jobs.append((ops.LABEL_COLLECTOR, ["-m", "umbra", "collect"], None))

    if sys.platform != "darwin":
        svc, timer = ops.systemd_unit(["-m", "umbra", "sync"],
                                      sys.executable, ops.repo_dir())
        print("# ~/.config/systemd/user/umbra-sync.service")
        print(svc)
        print("# ~/.config/systemd/user/umbra-sync.timer")
        print(timer)
        print("# systemctl --user enable --now umbra-sync.timer")
        return

    import subprocess
    agents = os.path.expanduser("~/Library/LaunchAgents")
    uid = os.getuid()
    for label, argv, every in jobs:
        path = os.path.join(agents, label + ".plist")
        if args.action == "uninstall":
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                           capture_output=True)
            if os.path.exists(path):
                os.remove(path)
            print(f"  uninstalled {label}")
            continue
        if args.action == "status":
            r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                               capture_output=True, text=True)
            state = "loaded" if r.returncode == 0 else "not loaded"
            print(f"  {label}: {state}")
            continue
        if args.action == "logs":
            short = label.rsplit(".", 1)[-1]
            for suffix in (".log", ".err.log"):
                print(f"  tail -f {ops.LOG_DIR}/{short}{suffix}")
            continue
        # install
        os.makedirs(agents, exist_ok=True)
        os.makedirs(ops.LOG_DIR, exist_ok=True)
        with open(path, "w") as f:
            # Freeze the current PATH into it: wrangler / netlify / aws in a
            # [deploy] command find node/python again through their shebangs,
            # and launchd provides only /usr/bin:/bin:/usr/sbin:/sbin. See ops.plist.
            f.write(ops.plist(label, argv, every, py,
                              ops.repo_dir(), ops.LOG_DIR,
                              path=os.environ.get("PATH")))
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                       capture_output=True)          # unload the old one first; idempotent
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", path],
                           capture_output=True, text=True)
        ok = "✓" if r.returncode == 0 else f"✗ {r.stderr.strip()}"
        print(f"  {ok} {label} → {path}"
              + (f" (every {every // 60} min)" if every else " (resident)"))
    # Warn if the old wrapper chain is still loaded — running both would do
    # everything twice (harmless, being idempotent, but the logs get confusing).
    if args.action == "install":
        legacy = [l for l in os.listdir(agents)
                  if ("shadow" in l or "umbra" in l) and l.endswith(".plist")
                  and not l.startswith("com.umbra.")]
        for l in legacy:
            print(f"  ⚠ legacy job still loaded: {l} — once the new one works, "
                  f"launchctl bootout gui/{uid}/{l[:-6]} and delete the file")


def _plugin(name: str):
    """A bundled lane, resolved the way a round resolves it.

    Through the registry rather than an import, so a plugin of the same name in
    your own directory is the one these commands drive — the same one the
    scheduled round runs. Importing the bundled module here would let the two
    disagree about which code is "the tasks lane".
    """
    from . import registry
    found, errors = registry.plugins()
    if name in errors:
        raise SystemExit(f"  ✗ plugin {name!r} failed to load: {errors[name]}")
    if name not in found:
        raise SystemExit(f"  ✗ no plugin named {name!r} (umbra plugins lists them)")
    return found[name]


def cmd_plugins(args):
    """What is installed, what is on, and what is allowed to send.

    Three states, shown as three columns, because they are three decisions:
    installing a plugin, letting it analyse, and letting it put things in
    somebody else's system.
    """
    import os

    from . import analyzer, config, plugins, registry, sink
    if args.action == "run":
        return _plugins_run(args)
    found, errors = registry.plugins()
    conf = config.load()
    if not found and not errors:
        print("  no plugins installed.")
    for name in sorted(found):
        mod = found[name]
        kinds = [k for k, ok in (("analyzer", getattr(mod, "INPUT", None)),
                                 ("check", registry.has_round(mod)
                                  and not getattr(mod, "INPUT", None)),
                                 ("sink", callable(getattr(mod, "send", None))))
                 if ok]
        where = ("bundled" if os.path.dirname(os.path.abspath(mod.__file__))
                 .startswith(os.path.abspath(plugins.DIR)) else "yours")
        is_analyzer = bool(getattr(mod, "INPUT", None))
        # A sink-only plugin is not switched on or off: it is used when an
        # analyzer names it. Showing it as "off" would read as a problem.
        on = registry.enabled(name, conf) if registry.has_round(mod) else None
        state = "—" if on is None else ("on" if on else "off")
        line = (f"  {'●' if on else '○'} {name:12s} {'/'.join(kinds) or '—':16s} "
                f"{where:8s} {state:3s}")
        if is_analyzer:
            win = getattr(mod, "WINDOW", 30)
            try:
                analyzer.resolve_input(mod.INPUT)
                analyzer.resolve_window(win)
                line += f"   reads {mod.INPUT}, window {win}"
            except analyzer.BadDeclaration as e:
                line += f"   ✗ {e}"
        to = sink.enabled_for(name, conf)
        if to:
            line += f"   sends to {to}"
        print(line.rstrip())
    for name, err in sorted(errors.items()):
        print(f"  ✗ {name:12s} failed to load: {str(err)[:120]}")


def _plugins_run(args):
    """Run one plugin now: its own round, or a given day or week."""
    import datetime as _dt

    from . import analyzer
    if not args.name:
        raise SystemExit("  usage: umbra plugins run <name> [--day D | --week 2026-W38]")
    mod = _plugin(args.name)
    from . import registry
    if not registry.has_round(mod):
        raise SystemExit(f"  ✗ {args.name} is a sink — there is nothing to run")
    if not getattr(mod, "INPUT", None):
        # A check with a round of its own and no timeline to read: run it.
        con = db.connect()
        try:
            mod.sync(con, [], print)
        finally:
            con.close()
        return
    con = db.connect()
    try:
        if args.day or args.week:
            if args.week:
                y, w = int(args.week[:4]), int(args.week[6:])
                mon = _dt.date.fromisocalendar(y, w, 1)
                days = [(mon + _dt.timedelta(i)).isoformat() for i in range(7)]
            else:
                days = [args.day]
            r = analyzer.run_days(con, args.name, mod, days, log=print)
            print(f"  {args.name}: {len(days)} days · asked {r['called']} · "
                  f"reused {r['reused']} · failed {r['failed']} · rows {r['rows']}")
            for day, err in r["errors"].items():
                print(f"  ⚠ {day}: {err}")
            # A window whose call failed is a question still open, not an
            # answer of nothing. Exit non-zero so whatever started this —
            # the page, a script — can tell.
            if r["errors"] or r["failed"]:
                raise SystemExit(1)
        else:
            today = str(_dt.date.today())
            analyzer.sync_plugin(con, args.name, mod, [today], log=print)
    finally:
        con.close()


def cmd_auth(args):
    """One-time authorisation. PLAUD's comes from its own CLI (plaud login)."""
    from . import whoop_login
    whoop_login.main(args.rest)


def cmd_setup(_args=None):
    """What a new source needs and what is missing now, on one screen."""
    cmd_sources()
    print(chr(10) + "One-time authorizations:")
    print("  WHOOP   umbra auth whoop   (one browser click)")
    print("  PLAUD   npm i -g @plaud-ai/cli && plaud login")
    print("  FDA     System Settings → Privacy & Security → Full Disk Access → add the scheduled interpreter")
    print(chr(10) + f"env file: {ops.env_file()} (loaded by umbra itself — no shell wrapper needed)")
    print("schedule: python3 -m umbra schedule install")


def cmd_correct(args):
    """Your word for a stretch of the day: "that hour I was actually …".

        umbra correct 2026-09-12 18:00-19:00 read "reading over dinner"
        umbra correct --list 2026-09-12

    The category is required, the words are not (see correct.py): the category
    is what the page and the totals use at once; the words are what the model
    reads next round.
    """
    import datetime as _dt

    from . import correct, infer, inference as INF, taxonomy
    con = db.connect()
    day = args.day or str(_dt.date.today())
    lang = infer.lang()
    if args.action == "list":
        rows = correct.load(con, day)
        if not rows:
            print(f"  {day}: nothing corrected.")
            return
        # The first-pass spans, not the calendar — see correct.disagrees.
        spans = INF.load(con, day).get(infer.MODEL) or []
        digests = INF.load_digests(con, day).get(infer.MODEL) or []
        for c in rows:
            print(f"  {correct.hm(c['a'])}–{correct.hm(c['b'])}  "
                  f"[{taxonomy.label(c['cat'], lang)}]  {c['text'] or '(category only)'}")
            if correct.read_by_model(c, digests) is False:
                print("        · the model has not re-read this since you said it (next round)")
                continue
            d = correct.disagrees(c, spans)
            # Three answers kept apart: said nothing / agrees / disagrees.
            if d is False:
                print("        ✓ the model agrees")
            elif d is None:
                print("        · the model has not said anything about this stretch yet")
            elif d and d.get("soft"):
                print(f"        · the model filed it as {taxonomy.label(d['cat'], lang)}, "
                      f"but also named what you said: {d['title']}")
            elif d:
                print(f"        ⚠ re-asked, the model still says {taxonomy.label(d['cat'], lang)}: "
                      f"{d['title']}")
        return
    if not (args.span and args.cat):
        sys.exit("usage: umbra correct <day> <18:00-19:00> <category> [\"what you were doing\"]\n"
                 f"  categories: {taxonomy.prompt_cats(lang)}")
    text = " ".join(args.text or [])
    try:
        a, b = correct.parse_span(args.span)
        correct.save(con, day, a, b, args.cat, text)
    except ValueError as e:
        sys.exit(f"  ✗ {e}")
    print(f"  ✎ {day} {args.span} → {taxonomy.label(taxonomy.norm(args.cat), lang)}"
          f"  {text or '(category only)'}")
    # Two sentences, because they happen at different times.
    print("  The page and the totals use it now.")
    print("  The next round re-asks the model about the windows it covers; yours stays as it is.")


def build_parser() -> argparse.ArgumentParser:
    """The whole command line: the core's commands, then each installed
    plugin's (see _plugin_commands)."""
    p = argparse.ArgumentParser(prog="umbra")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("import")
    pi.add_argument("--only", nargs="+", choices=list(ALL))
    pi.add_argument("--exclude", nargs="+", choices=list(ALL),
                    help="skip these sources (they are handled elsewhere)")
    pi.add_argument("--full", action="store_true",
                    help="ignore existing data and pull from scratch (after field-mapping changes or suspected gaps)")
    pi.set_defaults(func=cmd_import)

    pf = sub.add_parser("infer", help="let a model judge each day (calls an API)")
    pf.add_argument("days", nargs="*", help="default: every day since --since")
    pf.add_argument("--since", default="2026-08-10")
    pf.add_argument("--model")
    pf.add_argument("--force", action="store_true", help="redo days that already have results")
    pf.add_argument("--fresh", action="store_true",
                    help="bypass the digest cache and re-call every window (default is incremental)")
    pf.set_defaults(func=cmd_infer)

    pv = sub.add_parser("viz", help="write the page as one self-contained file and open it (embeds raw history — keep out of repos)")
    pv.add_argument("--out", help="output path; default is next to the database")
    pv.add_argument("--no-open", action="store_true")
    pv.add_argument("--raw-days", type=int, default=None,
                    help="embed raw records only for the last N days (0 = all; default from config, 30)")
    pv.set_defaults(func=cmd_viz)

    pe = sub.add_parser("export", help="write the page as a static, read-only site (what the phone reads)")
    pe.add_argument("--out", help="directory to write; default is the site directory deploy uploads")
    pe.add_argument("--single", metavar="FILE", help="one self-contained HTML file instead of a directory")
    pe.add_argument("--raw-days", type=int, default=None,
                    help="days that carry raw records (0 = all; default from config, 30)")
    pe.set_defaults(func=cmd_export)

    ps = sub.add_parser("serve", help="serve the timeline page and its API on localhost")
    ps.add_argument("--port", type=int, default=None)
    ps.add_argument("--host", default="127.0.0.1",
                    help="bind address; anything but localhost exposes your full history")
    ps.add_argument("--no-open", action="store_true")
    ps.add_argument("--api-only", action="store_true",
                    help="only /api/* — for developing the page with the Vite dev server")
    ps.set_defaults(func=cmd_serve)

    pb = sub.add_parser("blocks", help="cut a day into 5-minute slots to see where time went")
    pb.add_argument("day", help="YYYY-MM-DD")
    pb.add_argument("--raw", action="store_true", help="list all 288 slots unmerged")
    pb.set_defaults(func=cmd_blocks)

    sub.add_parser("sources", help="each source: platform, requirements, what is missing"
                   ).set_defaults(func=cmd_sources)
    sub.add_parser("sync", help="import → infer → viz in one idempotent pass (for schedulers)"
                   ).set_defaults(func=cmd_sync)
    sub.add_parser("voice", help="the voice lane on its own clock: plaud → tasks → claims → viz → deploy"
                   ).set_defaults(func=cmd_voice)
    pg = sub.add_parser("goals", help="this week's three priorities, and whether the hours went there")
    pg.add_argument("action", nargs="?", default="show", choices=["show", "set"])
    pg.add_argument("text", nargs="*", help='up to three, e.g. goals set "ship Umbra" "read a book"')
    pg.add_argument("--week", help="ISO week, e.g. 2026-W34 (default: this week)")
    pg.set_defaults(func=cmd_goals)
    ps = sub.add_parser("schedule", help="scheduled jobs: generate and load the plist/timer")
    ps.add_argument("action", choices=["install", "uninstall", "status", "logs"])
    ps.add_argument("--collector", action="store_true",
                    help="also manage the resident collector")
    ps.set_defaults(func=cmd_schedule)
    sub.add_parser("setup", help="onboarding: what each source needs and where to get it"
                   ).set_defaults(func=cmd_setup)
    pa = sub.add_parser("auth", help="one-time authorisation for a source (whoop)")
    pa.add_argument("service", choices=["whoop"])
    pa.add_argument("rest", nargs=argparse.REMAINDER,
                    help="passed through, e.g. --redirect URL --port N")
    pa.set_defaults(func=cmd_auth)
    pp = sub.add_parser("plugins", help="installed plugins: what they read, whether they are on, where they send")
    pp.add_argument("action", nargs="?", default="list", choices=["list", "run"])
    pp.add_argument("name", nargs="?", help="the plugin to run")
    pp.add_argument("--day", help="judge one day, e.g. 2026-09-16")
    pp.add_argument("--week", help="judge one ISO week, e.g. 2026-W38")
    pp.set_defaults(func=cmd_plugins)
    pk = sub.add_parser("correct", help="say what you were actually doing "
                                        "(your word wins; the model re-reads only what it covers)")
    pk.add_argument("day", nargs="?", help="default: today")
    pk.add_argument("span", nargs="?", help="18:00-19:00")
    pk.add_argument("cat", nargs="?", help="a category: read / Reading / 读书")
    pk.add_argument("text", nargs="*", help="optional: what you were really doing")
    pk.add_argument("--list", dest="action", action="store_const", const="list",
                    default="set", help="this day's corrections, and whether the model came around")
    pk.set_defaults(func=cmd_correct)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("freshness", help="when each source last updated (stale ones flagged)"
                   ).set_defaults(func=cmd_freshness)
    sub.add_parser("collect", help="resident collector: window titles + clipboard + idle"
                   ).set_defaults(func=cmd_collect)

    pc = sub.add_parser("coverage", help="per-minute evidence tiers — an honest precision self-audit")
    pc.add_argument("days", nargs="+")
    pc.set_defaults(func=cmd_coverage)

    ps = sub.add_parser(
        "show", help="rule-based segments for one day (offline; see `infer` "
                     "for the model's reconstruction)")
    ps.add_argument("day")
    ps.set_defaults(func=cmd_show)

    _plugin_commands(sub)
    return p


def _plugin_commands(sub) -> None:
    """A command for every installed plugin that declares `cli(args)`: named
    CLI_NAME (the plugin's name otherwise), described by CLI_HELP, with its
    arguments added by `cli_args(parser)`.

    Read off the registry, so a plugin of the same name in your own directory
    replaces the command along with the lane — the command and the scheduled
    round drive the same code. A plugin cannot take a core command's name:
    `umbra sync` meaning something a plugin decided would be a surprise nobody
    could see coming. It is skipped, and said so.
    """
    from . import registry
    found, _ = registry.plugins()
    taken = set(sub.choices)
    for name, mod in sorted(found.items()):
        fn = getattr(mod, "cli", None)
        if not callable(fn):
            continue
        cmd = getattr(mod, "CLI_NAME", None) or name
        if cmd in taken:
            print(f"  ⚠ plugin {name!r} wants the command {cmd!r}, which is taken; "
                  "it has no command", file=sys.stderr)
            continue
        taken.add(cmd)
        pc = sub.add_parser(cmd, help=getattr(mod, "CLI_HELP", None))
        if callable(getattr(mod, "cli_args", None)):
            mod.cli_args(pc)
        pc.set_defaults(func=fn)


def main():
    # The env file goes in first — any command, not only sync, may need a key,
    # and launchd's environment has nothing. Existing variables are not overwritten.
    ops.load_env()
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
