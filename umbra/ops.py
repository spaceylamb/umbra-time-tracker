"""The ops layer: one idempotent command (sync) plus a thin OS adapter (schedule).

The design (the module docstring of tests/test_ops.py is the same text): a
scheduler only ever needs to know one sentence — "call umbra sync every N
hours". sync is idempotent (import is incremental, infer has a digest cache, viz
has a per-day cache), so with no new data it costs almost nothing. That reduces
the plist and the systemd unit to templates, with the traps that were hit baked
into them rather than asked of every user via the README:

    -u                Python buffers stdout under launchd; without it the logs
                      are permanently empty and a failure shows no cause at all
    absolute paths    launchd does not inherit the login shell's PATH
    log directory     not /tmp — a reboot clears it, and "the log from two runs
                      ago" is exactly what chasing a silent failure needs
    WorkingDirectory  cd into the repo, or python -m umbra cannot find the package

The env file is loaded by umbra itself (load_env), which leaves a shell wrapper
with no reason to exist — run-import.sh died of two accidents: a hardcoded path
that did not follow the repo when it moved, and an alarm threshold hand-copied
into two places that fell out of step with the plist's StartInterval.
"""

import contextlib
import os
import re
import subprocess
import sys

from . import config, paths

LOG_DIR = paths.log_dir()
LABEL_SYNC = "com.umbra.sync"
LABEL_COLLECTOR = "com.umbra.collector"
LABEL_VOICE = "com.umbra.voice"

# Sources the voice lane owns exclusively. With the split enabled, sync's import
# skips them — two jobs hitting the same API and writing the same rows at once is
# wasted requests plus real write-lock contention.
# **Removing the sharing is cheaper than adding a lock**, and the split makes it
# available for free.
VOICE_SOURCES = ("plaud",)

# Exit code for "another round is still running, so this one stands down".
# **It must not share a code with a real failure (1)** — this happens several
# times a day, and merging them drowns genuine faults in the noise.
EX_BUSY = 75                        # EX_TEMPFAIL from sysexits.h

# How long to wait for the lock at most. **Never wait forever**: sync has
# measurably run for six hours (the machine sleeps overnight, launchd wakes it
# inside darkwake but does not guarantee it finishes), and waiting forever
# starves the voice lane for a whole night — which is the problem we set out to
# fix, arriving by a different route. Standing down costs one hour, and the next
# round catches up by itself.
LOCK_WAIT_S = 300


def _conf() -> dict:
    return config.load()


# ── Intervals and thresholds: one source for both ─────────────

def interval_min() -> int:
    """The schedule interval in minutes. Config [schedule].interval_min,
    defaulting to six hours."""
    return int((_conf().get("schedule") or {}).get("interval_min", 360))


def stale_min_for(source: str) -> float:
    """How long this source may go without running before it should alarm.

    Since the split, the two lanes' intervals differ by a factor of six. viz used
    to take a single stale_min() and apply it to every source — plaud runs hourly
    but would not alarm until nine hours without an update, so eight hours of
    breakage stayed invisible. The threshold has to follow **the interval of the
    lane the source is actually on**.
    """
    if split_voice() and source in VOICE_SOURCES:
        return voice_interval_min() * 1.5
    return stale_min()


def page_stale_min() -> float:
    """How long the page itself may go unrefreshed before alarming — following
    **whichever round publishes it**.

    After the split the page belongs to the hourly voice round (sync no longer
    runs viz or deploy), so 90 minutes; without the split the six-hourly round
    publishes it, so 540. Get it wrong and the page can stop updating for half a
    day without a word.
    """
    return voice_interval_min() * 1.5 if split_voice() else stale_min()


def voice_interval_min() -> int:
    """The voice round's interval in minutes. Config
    [schedule].voice_interval_min, defaulting to one hour.

    Separate from the main round because their costs differ by three orders of
    magnitude: the voice round measures 56 seconds (import 41s + viz 4s + deploy
    11s), while the main round runs infer across twelve sources and took six
    hours today.
    """
    return int((_conf().get("schedule") or {}).get("voice_interval_min", 60))


def split_voice() -> bool:
    """Whether the voice lane runs on its own. Config [schedule].split_voice.

    **Off by default**: `umbra sync` promises "one command does everything", and
    someone who has not installed the voice round must still get a page out of
    it. Once enabled, sync handles only import and infer, while tasks, claims,
    viz and deploy belong to `umbra voice`.
    """
    return bool((_conf().get("schedule") or {}).get("split_voice", False))


def stale_min() -> int:
    """The "importer has not run for too long" line = one and a half intervals.

    The interval and the threshold must come from one source: previously one
    lived in the plist and the other was hardcoded at 120 minutes, so between
    rounds of a healthy six-hourly job all 13 sources showed ⚠ together — and a
    diagnostic tool that cries wolf is worse than no diagnostic at all.
    Half a round of slack: missing one round may just be the machine asleep
    (launchd does not make up missed runs); missing two in a row is real.
    """
    return int(interval_min() * 1.5)


# ── The env file ──────────────────────────────────────────────

_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def env_file() -> str:
    """Config [env].file; defaults to the config directory's env file. Keys
    you already keep elsewhere: point [env] file at them."""
    p = (_conf().get("env") or {}).get("file")
    if p:
        return os.path.expanduser(p)
    return os.path.join(config.CONFIG_DIR, "env")


def load_env(path: str | None = None) -> int:
    """Load the env file into os.environ. **Existing values are not
    overwritten** — the real environment beats the file: under launchd there is
    no real environment so the file takes effect, while a value exported by hand
    in an interactive shell has to win, or swapping in a different key for an
    experiment becomes impossible. Returns how many were actually set."""
    path = path or env_file()
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _ENV_LINE.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key not in os.environ:
                os.environ[key] = val
                n += 1
    return n


# ── launchd / systemd templates ───────────────────────────────

def plist(label: str, args: list[str], interval_s: int | None,
          python: str, workdir: str, logdir: str,
          path: str | None = None) -> str:
    """interval_s=None means resident (KeepAlive); otherwise scheduled
    (StartInterval).

    path exists for **[deploy] command**. An absolute path for the interpreter is
    enough, but deploy is a line of shell the user supplied — wrangler, netlify
    and the aws CLI all begin with `#!/usr/bin/env node` (or python), and the
    shebang goes back to PATH to find its own interpreter, so writing the CLI as
    an absolute path does not help. launchd's PATH is only
    /usr/bin:/bin:/usr/sbin:/sbin while those tools live under nvm, homebrew or
    pyenv, so deploy reports `env: node: No such file or directory` while import,
    infer and viz are all green — "it looks like it ran, and nothing was
    published".

    What is frozen is the PATH **at install time**, not a login shell sourced at
    run time. The latter ties the scheduled job's behaviour to a file you edit
    casually: add a line to .zshrc one day and six months later deploy fails
    silently while the plist has not changed by a character. The frozen copy can
    be read with cat and is identical to the day it worked.
    """
    argv = "\n".join(f"\t\t<string>{a}</string>"
                     for a in [python, "-u", *args])
    timing = ("\t<key>KeepAlive</key>\n\t<true/>" if interval_s is None else
              f"\t<key>StartInterval</key>\n\t<integer>{interval_s}</integer>")
    env = (f"\t<key>EnvironmentVariables</key>\n\t<dict>\n"
           f"\t\t<key>PATH</key>\n\t\t<string>{path}</string>\n\t</dict>\n"
           if path else "")
    short = label.rsplit(".", 1)[-1]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>{label}</string>
\t<key>ProgramArguments</key>
\t<array>
{argv}
\t</array>
\t<key>WorkingDirectory</key>
\t<string>{workdir}</string>
{env}\t<key>RunAtLoad</key>
\t<true/>
{timing}
\t<key>LowPriorityIO</key>
\t<true/>
\t<key>ProcessType</key>
\t<string>Background</string>
\t<key>StandardOutPath</key>
\t<string>{logdir}/{short}.log</string>
\t<key>StandardErrorPath</key>
\t<string>{logdir}/{short}.err.log</string>
</dict>
</plist>
"""


def systemd_unit(args: list[str], python: str, workdir: str) -> tuple[str, str]:
    """(service, timer). Linux users install them into
    ~/.config/systemd/user/."""
    service = (f"[Unit]\nDescription=Umbra sync\n\n[Service]\nType=oneshot\n"
               f"WorkingDirectory={workdir}\n"
               f"ExecStart={python} -u {' '.join(args)}\n")
    timer = ("[Unit]\nDescription=Umbra sync timer\n\n[Timer]\n"
             f"OnUnitActiveSec={interval_min()}m\nOnBootSec=2m\n\n"
             "[Install]\nWantedBy=timers.target\n")
    return service, timer


def repo_dir() -> str:
    """The directory python -m umbra runs from = the package's parent."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def spawn(argv: list[str], log: str | None = None) -> subprocess.Popen:
    """Start `umbra <argv>` in the background — the page's Save, Import now
    and Run now.

    Two things a bare `python -m umbra` got wrong. It ran from whatever
    directory the server was started in, and Python puts that directory
    first on the import path: start `umbra serve` inside another checkout
    and the child judged the week with that checkout's code. Running from the
    package's own parent makes the child load the code the server is
    running. And its output went nowhere, so every failure read the same on
    the page. With `log`, it goes to <log dir>/<log>.log, overwritten each
    run, for last_line() to read back.
    """
    out = subprocess.DEVNULL
    if log:
        os.makedirs(LOG_DIR, exist_ok=True)
        out = open(os.path.join(LOG_DIR, f"{log}.log"), "w", encoding="utf-8")
    try:
        return subprocess.Popen([sys.executable, "-m", "umbra", *argv],
                                cwd=repo_dir(), stdout=out,
                                stderr=subprocess.STDOUT,
                                start_new_session=True)
    finally:
        if log:
            out.close()             # the child holds its own copy


def last_line(log: str) -> str | None:
    """Why a spawned run failed, in one line: its last warning (⚠ / ✗), else
    its last line — a traceback ends with the exception itself."""
    try:
        with open(os.path.join(LOG_DIR, f"{log}.log"), encoding="utf-8",
                  errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    marked = [ln for ln in lines if ln[:1] in "⚠✗"]
    return (marked or lines)[-1].lstrip("⚠✗ ")[:200]


# ── sync: the only command a scheduler needs to call ──────────

def _run_tz() -> None:
    """Record the timezone now, and replay the trips entered in config.

    record() writes nothing when nothing changed, so calling it every round
    costs one SELECT.
    """
    from . import db as _db, tz as _tz_mod
    con = _db.connect()
    _tz_mod.record(con)
    _tz_mod.apply_config(con, (_conf().get("travel") or []))


def _corrected_days(since_ms: int) -> list[str]:
    from . import correct, db
    con = db.connect()
    try:
        return correct.days_said_since(con, since_ms)
    finally:
        con.close()


def sync(_import=None, _infer=None, _viz=None, _plugins=None, _corrected=None,
         _deploy=None, _tz=None, log=print) -> int:
    """import everything -> infer the last few days (incremental) -> every
    enabled plugin -> viz (incremental) -> deploy.

    Every step is independently fault-isolated and **the later ones still run**:
    collection breaking should not also take "the page is still readable" down
    with it. But the exit code reports non-zero honestly — that is how the
    launchd log shows this round was not clean.
    """
    days_back = int((_conf().get("sync") or {}).get("infer_days", 2))
    failed = []

    def step(name, fn):
        try:
            fn()
        except SystemExit as e:      # a failed import must not block viz
                                     # either
            failed.append(name)
            log(f"  ✗ {name}: {e}")
        except Exception as e:            # noqa: BLE001
            failed.append(name)
            log(f"  ✗ {name}: {e}")

    import datetime as dt
    recent = [str(dt.date.today() - dt.timedelta(days=i))
              for i in range(days_back)]

    # import reports the days it really wrote to, and those days are judged
    # again too. Looking only at "the last two days", late data never reaches
    # the judgement: a source offline for days catches up later, and those days
    # keep the judgement made before the data arrived. Re-judging is incremental
    # (the digest is the cache key), so unchanged windows make no call.
    # **The timezone first.** Everything after rests on it: without this step
    # tz_marks keeps its seed rows forever, no trip ever shows, and every layer
    # quietly goes on with the old offset. It must come before infer, which
    # writes rows keyed by the day's current cut.
    step("tz", _tz or _run_tz)

    touched: set[str] = set()

    def imp():
        try:
            touched.update((_import or _run_import)() or [])
        except SystemExit as e:
            # A failed source still leaves the days the others wrote, and those
            # days still need judging.
            touched.update(getattr(e, "days", None) or [])
            raise
    step("import", imp)
    # The backstop: a source that reports half a year of history as new (a
    # changed uid, --full) must not send half a year to the model in one round —
    # a never-judged day is a full day of calls.
    lookback = int((_conf().get("sync") or {}).get("infer_lookback_days", 14))
    floor = str(dt.date.today() - dt.timedelta(days=lookback - 1))
    # Days you corrected are re-judged too: a correction is not an import, and
    # a day corrected last week would otherwise never be re-read. Asking about
    # one whose digest is unchanged costs nothing, so "said within the
    # lookback" is a safe net rather than a tally to keep.
    try:
        since = int((dt.datetime.now() - dt.timedelta(days=lookback)).timestamp() * 1000)
        touched.update((_corrected or _corrected_days)(since) or [])
    except Exception as e:                                # noqa: BLE001
        log(f"  ⚠ could not read your corrections: {e}")
    late = sorted((d for d in touched if d >= floor and d not in recent),
                  reverse=True)
    step("infer", lambda: (_infer or _run_infer)(recent + late))
    # Task extraction and infer are two independent lines: they share the same
    # recordings and nothing else — not the calls, not the cache table, not the
    # prompt. It sits here because it reads the speech just imported, not because
    # it depends on infer's results — it runs even when infer has failed.
    if split_voice():
        # Split enabled: the remaining four steps belong to `umbra voice` (once
        # an hour). **The page belongs to it too** — so exactly one process
        # writes that 14 MB HTML, which retires the risk of viz.write() not being
        # atomic, and two jobs never push to Cloudflare at once.
        return 1 if failed else 0
    # Every enabled plugin, one step each, so one failing costs only itself.
    # **After infer, before viz**: goal attribution judges the stretches infer
    # produced, and every plugin's numbers belong in this round's page. Between
    # the plugins there is no order to keep — the bundled three share the
    # recordings and nothing else: not the calls, the cache, or the prompt.
    _run_plugins(step, recent, _plugins, log)
    step("viz", _viz or _run_viz)
    # Last, and necessarily after viz — there is nothing to publish until a new
    # page exists. Without [deploy] configured, deploy() returns False and this
    # is skipped rather than counted as a failure.
    step("deploy", lambda: (_deploy or deploy)(log=log))
    return 1 if failed else 0


def _run_import():
    from . import __main__ as cli
    import argparse
    return cli.cmd_import(argparse.Namespace(
        only=None, full=False,
        # Stand aside from the voice sources when the split is on — that lane
        # pulls them hourly, and pulling from both sides is wasted requests plus
        # real write-lock contention.
        exclude=list(VOICE_SOURCES) if split_voice() else []))


def _import_voice() -> list[str]:
    """Run only the voice sources, returning **the days this round actually
    touched** (local calendar days, on the same basis as analyze.day_bounds).

    That return value is what tasks and claims scan. **The "today plus
    yesterday" calendar window cannot be used**: measured, the delay before a
    recording reaches the cloud has a median of 40 hours and a maximum of 362,
    so a recording three days late is already outside the window by the time it
    lands, and that day's tasks and claims are never extracted at all — with no
    error and no trace, leaving the database quietly missing a piece.

    The importer already knows which rows it wrote; using that beats guessing at
    a calendar.
    """
    from . import __main__ as cli
    import argparse
    return cli.cmd_import(argparse.Namespace(
        only=list(VOICE_SOURCES), full=False, exclude=[])) or []


# ── voice: the voice lane on its own clock ────────────────────

def voice(_import=None, _infer=None, _plugins=None, _viz=None,
          _deploy=None, log=print) -> int:
    """import the voice sources -> **re-infer** -> every enabled plugin -> viz
    -> deploy.

    Why a separate round: the Plaud app uploads in a batch once a day (at a time
    set by the user's routine, which we cannot change) while Umbra polls every
    six hours — two independent clocks with random relative phase, so the
    expected delay is half a period. Measured on 2026-08-24 it came out at seven
    hours: a full day of 08-23 recordings reached the cloud at 07:01, the plaud
    import had already finished at 03:24, and the next round was not until 14:18.

    Why this particular set of steps: tasks and claims read only plaud (both have
    a hard source filter) and are entirely decoupled from the other twelve
    sources. viz and deploy follow along because the page is the only outlet a
    person actually looks at — importing without producing a page means the local
    data is fresh and you still cannot see it.

    infer follows along too, which is the lesson 2026-08-24 taught in production:
    **whoever publishes the page has to run the re-inference**. The first version
    left infer in sync alone, so speech landed at 13:32, the voice round built
    and published the page at 13:33, and sync only finished recomputing those 36
    windows against the new speech at 13:36 — the published version had 2,413
    speech records sitting in its raw data while the calendar event read
    "17:00-19:19 unsure what was happening". Every step succeeded, every exit
    code was 0, every log line was green; only the ordering was wrong, and the
    ordering belongs to no individual step.

    Note this infer does **not** duplicate the one in sync: sync scans a calendar
    window (today plus yesterday), this scans the days this round actually
    touched. A recording three days late is already outside sync's window by the
    time it lands — and it is precisely the day that most needs re-inferring.

    Same discipline as sync: every step fault-isolated, the later ones still run.
    That matters **more here than in sync** — after the split this is the page's
    only path, so if plaud authorisation expires (cmd_import then exits
    non-zero) and viz and deploy went down with it, the page would stop
    updating entirely.
    """
    failed = []

    def step(name, fn):
        try:
            fn()
        except SystemExit as e:          # a failed import must not block viz
            failed.append(name)
            log(f"  ✗ {name}: {e}")
        except Exception as e:            # noqa: BLE001
            failed.append(name)
            log(f"  ✗ {name}: {e}")

    days: list[str] = []

    def imp():
        try:
            days.extend((_import or _import_voice)())
        except SystemExit as e:
            # A failed source still leaves the days the others touched, and
            # those days still need scanning.
            days.extend(getattr(e, "days", None) or [])
            raise
    step("import", imp)
    # This gate governs **infer only**. With no new recordings there is no new
    # input to judge, and scanning zero days is a pointless read of the database —
    # and this round runs every hour.
    #
    # **Must precede viz**, for the same reason as the goals step below but more
    # urgently: new speech changes the **input** to the judgement. It goes ahead
    # of tasks and claims because it alone feeds the page (their output goes only
    # to Butler cards; the page reads proposals and claims nowhere)
    # — so if launchd cuts this round short inside darkwake, better that the page
    # is correct and the tasks catch up next round.
    if days:
        step("infer", lambda: (_infer or _run_infer)(days))
    # Plugins are **not gated on days**. Extraction genuinely only has work
    # when there are new recordings (scanning zero days is an empty loop and
    # costs nothing), but the bundled lanes each also carry a step waiting on
    # **a different clock**, with nothing to do with whether this round imported
    # anything:
    #
    #   actions  dispatch sends the backlog that max_per_run held back, and
    #            pull_verdicts collects the "done / not a task" you tapped on
    #            your phone — both waiting on you and the destination
    #   claims   pull / render_cards / digest — waiting on whether the online
    #            verification has finished
    #   goals    a priority you set between rounds — the page belongs to this
    #            round, and an hourly page deserves hourly goals
    #
    # The same trap as the original split_voice work: two clocks that know
    # nothing of each other, with one of them's gate closing the other's work.
    #
    # What happened on 2026-08-25: claims dispatched at 23:49 and the task hung
    # on OAuth at 09:08 the next morning; the following 10 voice rounds all
    # skipped this step because the cloud had no new recordings, leaving those 15
    # claims in limbo. At the same time 11 proposed tasks sat in the queue unable
    # to go out. Every log green, every exit code 0, and not one line mentioning
    # any of it.
    _run_plugins(step, days, _plugins, log)
    step("viz", _viz or _run_viz)
    # skip_unchanged: Plaud uploads once a day, so on every other hour the page
    # is **byte-for-byte identical** and re-publishing the same 14 MB is
    # pointless.
    step("deploy", lambda: (_deploy or deploy)(log=log, skip_unchanged=True))
    return 1 if failed else 0


# ── The concurrency gate ──────────────────────────────────────

class LockBusy(RuntimeError):
    """Another round still holds the lock. Not a fault — this round stands
    aside."""


@contextlib.contextmanager
def job_lock(name: str, wait_s: float | None = None, dir: str | None = None):
    """A coarse lock between the two jobs, **with a timeout**.

    Since the split the two no longer share a writable resource (sync stands
    aside from the voice sources, and the page belongs to the voice round
    exclusively); all that remains is the write lock on the events table, which
    WAL plus busy_timeout handles. This lock is the last line of defence, not the
    main mechanism.

    The timeout is the entire point: a coarse lock with no timeout starves the
    voice round for a whole night on the nights sync runs long. Standing down
    costs one hour.
    """
    import fcntl
    import time as _t
    wait = LOCK_WAIT_S if wait_s is None else wait_s
    d = dir or LOG_DIR
    os.makedirs(d, exist_ok=True)
    f = open(os.path.join(d, f".{name}.lock"), "w")
    deadline = _t.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if _t.monotonic() >= deadline:
                    raise LockBusy(f"{name}: another round is still running; standing down")
                _t.sleep(min(2.0, max(0.05, wait / 20)))
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def _run_infer(days):
    from . import db, infer, inference as INF
    con = db.connect()
    for day in days:
        r = infer.run_day(con, day)
        if r["spans"]:
            infer.merge(con, day, r["spans"],
                        dirty=r["fresh_calls"] > 0 or r["failed"] > 0)
        print(f"  infer {day}: reused {r.get('reused', 0)} / "
              f"called {r.get('fresh_calls', 0)} windows")


def _run_plugins(step, days, plugins=None, log=print) -> None:
    """One fault-isolated step per enabled plugin, named after it.

    A plugin that failed to import is reported as a failed step rather than
    skipped: it is something you installed trying to run and not managing to,
    and a round that says nothing about it exits 0 while doing less than you
    asked. (plugins: {name: fn(days)}, for tests to stand in for the registry.)
    """
    if plugins is None:
        from . import registry
        found, errors = registry.plugins()
        for name, err in sorted(errors.items()):
            step(name, lambda err=err: _broken(err))
        plugins = {name: (lambda d, name=name, mod=mod: _sync_one(name, mod, d, log))
                   for name, mod in sorted(registry.rounds(only_enabled=True).items())}
    for name, fn in plugins.items():
        step(name, lambda fn=fn: fn(days))


def _broken(err):
    raise RuntimeError(f"plugin failed to load: {err}")


def _sync_one(name, mod, days, log):
    from . import analyzer, db
    con = db.connect()
    try:
        return analyzer.sync_plugin(con, name, mod, days, log=log)
    finally:
        con.close()


def _run_viz():
    from . import viz
    out = viz.write()
    print(f"  page → {out}")
    # With deploy configured, also export the page as a site: what deploy
    # uploads, and what the phone reads (ADR 0003, 2026-09-28). The single
    # file stays the product promise — it opens on a double-click, works
    # copied elsewhere, and is the only offline backup form. Both read the
    # same per-day cache, so the second pass is almost all cache hits.
    if (_conf().get("deploy") or {}).get("command"):
        from . import export
        r = export.write(viz.SITE_DIR, ours=True)
        print(f"  export -> {viz.SITE_DIR} ({r['files']} files)")


def deploy(run=None, log=print, skip_unchanged: bool = False):
    """Publish the generated page to your own hosting. Returns True/None/False.

        True   published
        None   ran and failed (reported honestly, or you would believe what is
               online is current)
        False  not configured — the normal case, not a failure, and it must not
               dirty sync's exit code

    The command comes from [deploy] command; no vendor is hardcoded. {dir} is
    replaced with the directory holding the page: Pages, Netlify, rsync and S3
    all take a whole directory.

    ⚠️ The page embeds the complete raw history (a month of it holds thousands of
       email addresses and everything said to an AI, verbatim). So it is off by
       default, **and authentication has to be in place first** — a static file
       scraped once cannot be recalled.
    """
    import shutil
    import subprocess
    import tempfile
    cmd = (_conf().get("deploy") or {}).get("command")
    if not cmd:
        return False
    from . import viz
    if not os.path.exists(viz.OUT):
        log("  ✗ deploy: no page has been generated yet")
        return None
    # The voice round runs hourly, but Plaud uploads in one batch a day — so on
    # the other 23 hours the page is **byte-for-byte identical**. The fingerprint
    # is stored beside the page rather than in the database: it records "which
    # version was last published", which belongs to the artefact, not the data.
    stamp = viz.OUT + ".deployed"
    # The fingerprint is taken over **the index actually being uploaded**, not
    # the local self-contained copy.
    src = (os.path.join(viz.SITE_DIR, "index.html")
           if os.path.exists(os.path.join(viz.SITE_DIR, "index.html")) else viz.OUT)
    fp = _sha256(src) if skip_unchanged else None
    if fp is not None and _read_text(stamp) == fp:
        log("  = deploy: page unchanged, skipping")
        return True
    # **The data directory must never be handed over**: alongside the page it
    # holds events.db, google_token.json and whoop_token.json, so
    # `pages deploy <data dir>` would publish the database and the tokens too.
    # Hence a clean staging directory containing only the page, deleted
    # afterwards (the page is the complete raw history; do not leave a second
    # copy on disk).
    stage = tempfile.mkdtemp(prefix="umbra-deploy-")
    try:
        # Upload the exported site when it exists (index.html + assets/ +
        # api/), otherwise the single file — the same app with everything
        # inline — for the first round after [deploy] is configured.
        site = os.path.join(viz.SITE_DIR, "index.html")
        if os.path.exists(site):
            shutil.copytree(viz.SITE_DIR, stage, dirs_exist_ok=True)
        else:
            # Named index.html: Pages, Netlify and the like look for that name
            shutil.copyfile(viz.OUT, os.path.join(stage, "index.html"))
        line = cmd.replace("{dir}", stage).replace("{file}", os.path.join(stage, "index.html"))
        run = run or (lambda c, **k: subprocess.run(c, shell=True, **k))
        r = run(line, cwd=stage)
        if r is None or getattr(r, "returncode", 1) != 0:
            log(f"  ✗ deploy: command returned non-zero — {line}")
            return None
        # **A failure records no fingerprint** (the return None above has
        # already fired): treating a failure as published means this version
        # never goes up, leaving only an error line that has long since scrolled
        # out of the log.
        if fp is not None:
            _write_text(stamp, fp)
        log("  ✓ deploy")
        return True
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _write_text(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
