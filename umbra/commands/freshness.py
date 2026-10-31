"""`umbra freshness`: how long ago each source last imported, and which have stopped."""

from .. import db
from .. import ops


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
