"""Umbra — your scattered digital footprints, on one timeline.

    python3 -m umbra import                    # import every source
    python3 -m umbra import --only knowledgec  # one source (run alone when it needs FDA)
    python3 -m umbra stats                     # what the database holds
    python3 -m umbra show 2026-08-13           # rule-based segments (offline)
    python3 -m umbra infer 2026-08-13          # let a model judge the day
"""

import argparse
import os
import sys
import time

from . import analyze, db, inference as INF, ops, sources
from .importers import ALL
from .plaud_auth import PlaudAuthExpired
from .whoop_auth import WhoopAuthExpired
from .commands.doctor import PY_APP, _decode_csreq, _designated_req, cmd_doctor, cmd_schedule, job_python, launchd_python, tcc_binary  # noqa: F401  (re-exported)
from .commands.freshness import FAIL_STREAK, STALE_MIN, _ago, cmd_freshness  # noqa: F401  (re-exported)
from .commands.plugin_cli import _plugin, _plugin_commands, _plugins_run, cmd_plugins  # noqa: F401  (re-exported)
from .commands.days import cmd_blocks, cmd_correct, cmd_coverage, cmd_goals, cmd_show  # noqa: F401  (re-exported)

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


def main():
    # The env file goes in first — any command, not only sync, may need a key,
    # and launchd's environment has nothing. Existing variables are not overwritten.
    ops.load_env()
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
