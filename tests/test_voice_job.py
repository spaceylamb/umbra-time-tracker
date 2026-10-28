"""The voice lane split into a round of its own (umbra voice).

Prompted by 2026-08-24: a whole day of recordings reached the Plaud cloud at
07:01, but sync's plaud import had finished at 03:24 — 3 hours 37 minutes too
early. The next round was at 14:18, so "in the cloud, not on the page" lasted
over seven hours.

The root cause is two clocks that know nothing of each other: the Plaud app
uploads in a batch once a day (when depends on the person's routine and cannot
be changed), and Umbra polled every six hours (the one that can). With random
phase the expected delay is half a period.

The split follows **real dependency**, not speed: tasks and claims read only
plaud (both filter on `source == "plaud"`), and their output went only to
destination cards — at the time the page referenced proposals and claims
nowhere. So they were always two separate lines:

    plaud ─┬─> tasks  ─> destination cards
           └─> claims ─> online verification ─> daily card     (neither on the page)
    13 sources ─> infer ─> viz ─> deploy ─> the page

After the split viz + deploy belong to the voice round alone (it runs more
often), so **only one process writes the page** — and the risk of viz.write()
not being atomic disappears.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import config, db, ops


def with_conf(text):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    return mock.patch.object(config, "CONFIG_DIR", d)


def _days_ignored(fn):
    """goals' round picks its own days; the old _attrib stand-in took none."""
    return lambda days: fn()


class EachStepIsolatedTest(unittest.TestCase):
    """The same discipline as sync: collection breaking must not take "the page
    is still readable" with it.

    It matters **more here than in sync** — after the split this is the page's
    only path. If plaud authorisation expires (cmd_import raises SystemExit and
    stops the whole import) and viz and deploy go down too, the page stops
    updating altogether.
    """

    def _run(self, **kw):
        seen = []
        base = dict(
            _import=lambda: seen.append("import") or ["2026-08-23"],
            _infer=lambda days: seen.append(("infer", days)),
            _plugins={"actions": lambda days: seen.append(("tasks", days)),
                      "claims": lambda days: seen.append(("claims", days)),
                      "goals": _days_ignored(lambda: seen.append("attrib"))},
            _viz=lambda: seen.append("viz"),
            _deploy=lambda **k: seen.append("deploy"),
        )
        # A lane passed by name replaces just that lane's stand-in.
        for lane in ("actions", "claims", "goals"):
            if lane in kw:
                base["_plugins"][lane] = kw.pop(lane)
        base.update(kw)
        with with_conf(""):
            rc = ops.voice(log=lambda *_: None, **base)
        return rc, seen

    def test_order_is_import_infer_plugins_viz_deploy(self):
        rc, seen = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual([s[0] if isinstance(s, tuple) else s for s in seen],
                         ["import", "infer", "tasks", "claims",
                          "attrib", "viz", "deploy"])

    def test_a_failed_infer_still_runs_viz_and_deploy(self):
        """Judging is the most expensive step and the likeliest to break (it calls
        a model). Broken, the page falls back to the old judgement and still
        goes out — an old judgement beats no page."""
        def boom(_days):
            raise RuntimeError("model 429")
        rc, seen = self._run(_infer=boom)
        names = [s[0] if isinstance(s, tuple) else s for s in seen]
        self.assertIn("viz", names)
        self.assertIn("deploy", names)
        self.assertEqual(rc, 1)

    def test_a_failed_import_still_runs_viz_and_deploy(self):
        def boom():
            raise SystemExit("auth expired")
        rc, seen = self._run(_import=boom)
        names = [s[0] if isinstance(s, tuple) else s for s in seen]
        self.assertIn("viz", names)
        self.assertIn("deploy", names)
        self.assertEqual(rc, 1)          # the exit code is honestly dirty

    def test_a_partly_failed_import_still_judges_the_days_it_touched(self):
        from umbra import __main__ as cli

        def partial():
            raise cli.ImportIncomplete("1 source(s) failed", ["2026-08-23"])
        rc, seen = self._run(_import=partial)
        self.assertIn(("infer", ["2026-08-23"]), seen)
        self.assertEqual(rc, 1)

    def test_a_failed_claims_still_runs_viz_and_deploy(self):
        def boom(_days):
            raise RuntimeError("butler 503")
        rc, seen = self._run(claims=boom)
        names = [s[0] if isinstance(s, tuple) else s for s in seen]
        self.assertIn("viz", names)
        self.assertIn("deploy", names)
        self.assertEqual(rc, 1)


class WhichDaysTest(unittest.TestCase):
    """**Run the days this round really got new events for, not a calendar
    window.**

    sync uses "today plus yesterday" ([sync].infer_days, default 2). For speech
    that window leaks: measured upload delay is median 40 hours, maximum 362; a
    recording three days late arrives outside the window, and that day's tasks
    and claims never run — with no error and no trace.

    The importer knows which rows it wrote; that beats guessing from a calendar.
    """

    def test_days_come_from_the_rows_imported_not_today_and_yesterday(self):
        got = {}
        with with_conf(""):
            ops.voice(
                _import=lambda: ["2026-07-01", "2026-08-23"],
                _infer=lambda days: got.setdefault("infer", days),
                _plugins={"actions": lambda days: got.setdefault("tasks", days),
                          "claims": lambda days: got.setdefault("claims", days),
                          "goals": _days_ignored(lambda: None)},
                _viz=lambda: None, _deploy=lambda **k: None,
                log=lambda *_: None)
        self.assertEqual(got["infer"], ["2026-07-01", "2026-08-23"])
        self.assertEqual(got["tasks"], ["2026-07-01", "2026-08-23"])
        self.assertEqual(got["claims"], ["2026-07-01", "2026-08-23"])

    def test_no_new_events_means_no_rejudging(self):
        """Nothing new in the cloud is the usual case (one batch a day). Scanning
        zero days would read the database for nothing — and this round runs
        hourly.

        infer is the **only** thing this gate should hold back: it computes how
        new recordings change judgements, and with none there is nothing to do.
        tasks and claims are different; see below."""
        called = []
        with with_conf(""):
            ops.voice(_import=lambda: [],
                      _infer=lambda days: called.append("infer"),
                      _plugins={"actions": lambda days: called.append("tasks"),
                                "claims": lambda days: called.append("claims"),
                                "goals": _days_ignored(lambda: None)},
                      _viz=lambda: None, _deploy=lambda **k: None,
                      log=lambda *_: None)
        self.assertEqual(called, ["tasks", "claims"])

    def test_no_new_events_still_collects_verdicts_and_sends_the_backlog(self):
        """The same hole as claims, the same gate.

        After scanning, the tasks lane also dispatches (the backlog max_per_run
        held back) and pulls verdicts (what you tapped on your phone) — both wait
        on **you** and **the destination**, not on whether this round imported
        a recording.

        What happened: on 08-25 max_per_run=5 held back 13 to send, 11 proposals
        sat in the queue, and the verdicts you had tapped could not come back. Until
        the cloud's next batch upload neither would happen — with every log line
        green."""
        got = {}
        with with_conf(""):
            ops.voice(_import=lambda: [],
                      _infer=lambda days: None,
                      _plugins={"actions": lambda days: got.setdefault("tasks", days),
                                "claims": lambda days: got.setdefault("claims", days),
                                "goals": _days_ignored(lambda: None)},
                      _viz=lambda: None, _deploy=lambda **k: None,
                      log=lambda *_: None)
        self.assertEqual(got, {"tasks": [], "claims": []})

    def test_no_new_events_still_collects_verification_results(self):
        """The 15 claims of 2026-08-25 died at exactly this gate.

        `if days` asks **whether this round imported a recording**, while the
        claims lane's pull / render_cards / digest wait on **whether the
        destination's task finished** — two clocks that know nothing of each
        other, the same trap the split itself fell into.

        What happened: dispatched at 23:49, the task hung on OAuth at 09:08 the
        next morning; the next 10 voice rounds all skipped claims, logs green, exit
        codes 0, the 15 left hanging with not a line about them. scan should follow
        days; pull should not."""
        got = {}
        with with_conf(""):
            ops.voice(_import=lambda: [],
                      _infer=lambda days: None,
                      _plugins={"actions": lambda days: None,
                                "claims": lambda days: got.setdefault("days", days),
                                "goals": _days_ignored(lambda: None)},
                      _viz=lambda: None, _deploy=lambda **k: None,
                      log=lambda *_: None)
        self.assertEqual(got, {"days": []})


class NewSpeechIsRejudgedBeforeThePageTest(unittest.TestCase):
    """A second hole from 2026-08-24, left by the same split.

    A whole day of recordings reached the database at 13:32 (12,702 rows). The
    voice round built and published the page at 13:33 — while infer belonged to
    sync and only finished re-judging those 36 windows at 13:36. So the live
    page looked like this:

        raw (records in the drill-down)  2,413 speech rows, someone talking at
                                         length around 16:00
        ev  (the calendar event above)   17:00–19:19  other  "not sure what was
                                         going on"

    Built in the same second. The evidence was in it; the conclusion said unknown.

    No step failed: import, infer, viz and deploy all succeeded, exit codes 0,
    logs green. Only the order of three things was wrong —

        load data → build the page → judge again      (what the split did)
        load data → judge again → build the page      (what it should be)

    — and order belongs to no single step, so nobody owned it.

    The root cause: the split cut an implicit ordering contract. Before it, sync
    guaranteed import → infer → viz; after it viz belonged to the voice round and
    infer stayed in sync — **the page and its input on two jobs and two clocks**.
    And speech is exactly the source most able to change a judgement — hours
    away from any computer or phone depend on it entirely. The hole always lands
    where it is most needed.
    """

    def _run(self, **kw):
        seen = []
        base = dict(
            _import=lambda: ["2026-08-23"],
            _infer=lambda days: seen.append(("infer", days)),
            _plugins={"actions": lambda days: seen.append(("tasks", days)),
                      "claims": lambda days: seen.append(("claims", days)),
                      "goals": _days_ignored(lambda: seen.append("attrib"))},
            _viz=lambda: seen.append("viz"),
            _deploy=lambda **k: seen.append("deploy"),
        )
        base.update(kw)
        with with_conf(""):
            ops.voice(log=lambda *_: None, **base)
        names = [x[0] if isinstance(x, tuple) else x for x in seen]
        return names, dict(x for x in seen if isinstance(x, tuple))

    def test_infer_comes_before_viz(self):
        names, _ = self._run()
        self.assertIn("infer", names, "a voice round that does not rejudge leaves the page a round behind")
        self.assertLess(names.index("infer"), names.index("viz"),
                        f"infer must come before viz; actual order: {names}")

    def test_infer_scans_the_days_this_round_really_touched(self):
        """The same measure as tasks and claims, **never the "today plus
        yesterday" calendar window**. With upload delays of median 40 hours and
        max 362, a recording three days late arrives outside sync's window and
        that day is never rejudged — the day most in need, judged entirely
        without speech."""
        _, got = self._run(_import=lambda: ["2026-07-01", "2026-08-23"])
        self.assertEqual(got["infer"], ["2026-07-01", "2026-08-23"])
        self.assertEqual(got["infer"], got["tasks"],
                         "three lines sharing one set of recordings should share one set of days")

    def test_no_new_speech_means_no_infer(self):
        """This round runs hourly and Plaud uploads once a day — the other 23
        hours nothing on the speech side changed. Without new speech these days'
        judgements will not change because of this round (the other 12 sources
        belong to sync's infer), and scanning zero days is a wasted pass.
        """
        names, _ = self._run(_import=lambda: [])
        self.assertNotIn("infer", names)
        self.assertIn("viz", names, "the page is built even without new speech")


class EveryRoundThatBuildsThePageRejudgesFirstTest(unittest.TestCase):
    """One level stronger than the group above: checked against **source order**,
    not just the behaviour of today's two rounds.

    The bug's shape is "some round built the page and nobody rejudged before it".
    The next split (by source, or a realtime round) would reopen the same hole in
    the same way — while behaviour tests only watch the two functions that exist
    today.

    The same approach as the injection guard below: for the kind of error where
    **no test goes red** when it is missed, only a rule held against the source
    stops it.
    """

    def _steps(self, fn):
        import inspect
        import re
        return re.findall(r'step\("(\w+)"', inspect.getsource(fn))

    def test_every_round_that_builds_the_page(self):
        for fn in (ops.sync, ops.voice):
            steps = self._steps(fn)
            if "viz" not in steps:
                continue
            with self.subTest(job=fn.__name__):
                self.assertIn("infer", steps,
                              f"{fn.__name__} builds the page without rejudging: {steps}")
                self.assertLess(
                    steps.index("infer"), steps.index("viz"),
                    f"{fn.__name__} runs infer after viz: {steps}")


class UnchangedContentIsNotPublishedTest(unittest.TestCase):
    """The voice round runs hourly but Plaud uploads once a day — the other 23
    hours the page is **byte-for-byte identical**. Re-publishing the same 14 MB
    is pointless.
    """

    def setUp(self):
        from umbra import viz
        d = tempfile.mkdtemp()
        self.page = os.path.join(d, "umbra-viz.html")
        self.site = os.path.join(d, "umbra-site")
        self._write("<html>一</html>")
        for p in (mock.patch.object(viz, "OUT", self.page),
                  mock.patch.object(viz, "SITE_DIR", self.site)):
            p.start()
            self.addCleanup(p.stop)

    def _write(self, s):
        with open(self.page, "w", encoding="utf-8") as f:
            f.write(s + "x" * 2000)

    CONF = '[deploy]\ncommand = "ship {dir}"\n'

    def _deploy(self, rc=0):
        got = []
        with with_conf(self.CONF):
            r = ops.deploy(run=lambda c, **k: got.append(c)
                           or type("R", (), {"returncode": rc})(),
                           skip_unchanged=True)
        return r, got

    def test_the_first_publish(self):
        r, got = self._deploy()
        self.assertIs(r, True)
        self.assertEqual(len(got), 1)

    def test_unchanged_content_does_not_call_the_command_again(self):
        self._deploy()
        r, got = self._deploy()
        self.assertEqual(got, [])
        self.assertIs(r, True)           # not a failure — already up to date

    def test_changed_content_is_published(self):
        self._deploy()
        self._write("<html>二</html>")
        _, got = self._deploy()
        self.assertEqual(len(got), 1)

    def test_a_failed_publish_records_no_fingerprint_so_the_next_round_retries(self):
        """Counting a failure as "published" means this version never goes up,
        with only one long-scrolled-away error line to say so."""
        self._deploy(rc=1)
        _, got = self._deploy(rc=0)
        self.assertEqual(len(got), 1)


class ConcurrencyGateTest(unittest.TestCase):
    """The two jobs overlap periodically (one every 6 h, one hourly).

    The coarse lock must not **wait forever**: sync once ran six hours (the
    machine sleeps at night; launchd wakes it in darkwake but does not promise
    to let it finish), and waiting forever would starve the voice round for six
    hours — the very problem being fixed, for another reason. So without the
    lock it gives up this round; the worst case misses an hour.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_it_runs_when_it_gets_the_lock(self):
        with ops.job_lock("t", wait_s=0, dir=self.dir):
            pass

    def test_held_by_someone_else_it_gives_up_after_a_timeout(self):
        with ops.job_lock("t", wait_s=0, dir=self.dir):
            with self.assertRaises(ops.LockBusy):
                with ops.job_lock("t", wait_s=0, dir=self.dir):
                    pass

    def test_it_can_be_taken_again_once_released(self):
        with ops.job_lock("t", wait_s=0, dir=self.dir):
            pass
        with ops.job_lock("t", wait_s=0, dir=self.dir):
            pass

    def test_different_names_do_not_interfere(self):
        with ops.job_lock("a", wait_s=0, dir=self.dir):
            with ops.job_lock("b", wait_s=0, dir=self.dir):
                pass

    def test_its_exit_code_is_distinct_from_a_real_failure(self):
        """In launchd's log "another round is running" and "this round failed"
        must be told apart, or there is daily noise and real faults drown in it."""
        self.assertNotIn(ops.EX_BUSY, (0, 1))


class SplitSwitchTest(unittest.TestCase):
    """The open-source `umbra sync` is "one command does everything", and the
    split must not quietly change that — someone without the voice round must
    still get a page from sync. So the split is an explicit switch.
    """

    def _sync(self, conf):
        seen = []
        with with_conf(conf):
            ops.sync(_tz=lambda: None, _import=lambda: seen.append("import"),
                     _infer=lambda days: seen.append("infer"),
                     _plugins={"actions": lambda days: seen.append("tasks"),
                               "claims": lambda days: seen.append("claims"),
                               "goals": _days_ignored(lambda: seen.append("attrib"))},
                     _viz=lambda: seen.append("viz"),
                     _deploy=lambda **k: seen.append("deploy"),
                     log=lambda *_: None)
        return seen

    def test_unsplit_by_default_sync_is_unchanged(self):
        seen = self._sync("")
        self.assertEqual(seen, ["import", "infer", "tasks", "claims",
                                "attrib", "viz", "deploy"])

    def test_split_sync_keeps_only_import_and_infer(self):
        seen = self._sync("[schedule]\nsplit_voice = true\n")
        self.assertEqual(seen, ["import", "infer"])

    def test_split_import_excludes_voice_sources(self):
        """Two jobs hitting the Plaud API and writing the same rows at once =
        wasted requests + real write-lock contention. Removing the sharing is
        cheaper than locking."""
        from umbra import __main__ as cli
        got = {}
        with with_conf("[schedule]\nsplit_voice = true\n"):
            with mock.patch.object(cli, "cmd_import",
                                   lambda a: got.setdefault("ns", a)):
                ops._run_import()
        self.assertEqual(got["ns"].exclude, list(ops.VOICE_SOURCES))

    def test_unsplit_import_excludes_nothing(self):
        from umbra import __main__ as cli
        got = {}
        with with_conf(""):
            with mock.patch.object(cli, "cmd_import",
                                   lambda a: got.setdefault("ns", a)):
                ops._run_import()
        self.assertFalse(got["ns"].exclude)


class IntervalTest(unittest.TestCase):
    def test_the_voice_round_defaults_to_an_hour(self):
        with with_conf(""):
            self.assertEqual(ops.voice_interval_min(), 60)

    def test_it_is_configurable(self):
        with with_conf("[schedule]\nvoice_interval_min = 30\n"):
            self.assertEqual(ops.voice_interval_min(), 30)

    def test_it_is_separate_from_the_main_interval(self):
        with with_conf("[schedule]\ninterval_min = 360\n"
                       "voice_interval_min = 60\n"):
            self.assertEqual((ops.interval_min(), ops.voice_interval_min()),
                             (360, 60))


class DatabaseWriteLockWaitTest(unittest.TestCase):
    """The collector writes continuously, and the voice round writes hourly. It
    really hit `sqlite3.OperationalError: database is locked` on 8-22
    (collector.err.log). One plaud round's executemany writes 12,702 rows; five
    seconds is not enough.
    """

    def test_busy_timeout_is_at_least_a_minute(self):
        d = tempfile.mkdtemp()
        con = db.connect(os.path.join(d, "t.db"))
        try:
            ms = con.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            con.close()
        self.assertGreaterEqual(ms, 60_000)


class EveryStepHasAnInjectionPointTest(unittest.TestCase):
    """A new step without an injection point = that step runs **for real** in
    tests.

    It happened on 2026-08-24: when claims joined sync, three ops.sync(...) calls
    in test_ops and test_deploy did not follow, so _run_claims used the
    production database, a real model and sent real tasks; deploy likewise —
    one `unittest discover` made 16 production deploys.

    "Remember to update the tests" cannot stop this, because a miss turns **no
    test red**. So this checks signatures: every step name must have a matching
    `_name` parameter.
    """

    def _steps_and_params(self, fn):
        import inspect
        import re
        src = inspect.getsource(fn)
        steps = set(re.findall(r'step\("(\w+)"', src))
        # Plugins are steps too — one per enabled plugin, named at run time,
        # so the regex above cannot see them. Their isolation is `_plugins`, and
        # missing it means every enabled plugin runs for real against the
        # production database: the fourth time this shape would have bitten.
        if "_run_plugins(" in src:
            steps.add("plugins")
        return steps, set(inspect.signature(fn).parameters)

    def test_sync(self):
        steps, params = self._steps_and_params(ops.sync)
        self.assertTrue(steps, "no step parsed at all — the regex needs updating")
        missing = sorted(s for s in steps if f"_{s}" not in params)
        self.assertEqual(missing, [], f"these steps have no injection point and would run for real in tests: {missing}")

    def test_voice(self):
        steps, params = self._steps_and_params(ops.voice)
        self.assertTrue(steps)
        missing = sorted(s for s in steps if f"_{s}" not in params)
        self.assertEqual(missing, [], f"these steps have no injection point and would run for real in tests: {missing}")

    # ── An injection point is not enough; it has to be passed ──────
    #
    # The third fall into the same hole on 2026-08-24: when infer joined voice,
    # the two tests above **went green at once** — the signature did gain
    # `_infer`. But two ops.voice(...) calls in WhichDaysTest did not follow, so
    # _run_infer used the production database and a real model; one `unittest
    # discover` took 72 seconds and computed a whole timeline for 2026-07-01.
    #
    # The first two were claims and deploy (16 real deploys). Three times the same
    # shape: **whoever added the step changed the signature and forgot the call
    # sites, and the guard watched only the signature.** A guard giving false
    # comfort is more dangerous than none.
    #
    # So this checks the call sites. Either kind of isolation counts:
    #   ops.voice(_infer=...)                     injected directly
    #   mock.patch.object(ops, "_run_infer")      the function underneath replaced
    # Neither = that step runs for real in the test.

    def _call_sites(self):
        import ast
        import os
        import re
        names = {"sync": self._steps_and_params(ops.sync)[0],
                 "voice": self._steps_and_params(ops.voice)[0]}
        root = os.path.dirname(os.path.abspath(__file__))
        for f in sorted(os.listdir(root)):
            if not f.endswith(".py"):
                continue
            src = open(os.path.join(root, f), encoding="utf-8").read()
            tree = ast.parse(src)
            # Each Call is assigned to its enclosing def — helpers like `_run`
            # build a base dict and then expand `**base`, which the AST cannot see
            # through, so the source has to be read. Only the def itself is
            # recorded — **don't slice the source here**: get_source_segment
            # re-slices the whole text every call, and per node that is 12 seconds
            # on the suite.
            scope = {}
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for n in ast.walk(fn):
                        scope.setdefault(id(n), fn)
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == "ops"
                        and n.func.attr in names):
                    # patch.object(...) is often split over lines; flatten whitespace first
                    fn = scope.get(id(n))
                    seg = ast.get_source_segment(src, fn) if fn else src
                    enclosing = re.sub(r"\s+", " ", seg or src)
                    yield (f, n.lineno, n.func.attr, names[n.func.attr],
                           {k.arg for k in n.keywords}, enclosing)

    def test_every_call_site_isolates_every_step(self):
        import re
        bad = []
        for f, line, job, steps, kw, enclosing in self._call_sites():
            splat = None in kw           # **kwargs, which the AST cannot see through
            for st in steps:
                if f"_{st}" in kw:
                    continue
                if splat and f"_{st}=" in enclosing:
                    continue             # passed in the base dict
                if re.search(rf'patch\.object\(\s*ops,\s*"(_run_)?{st}"',
                             enclosing):
                    continue             # the function underneath was replaced
                bad.append(f"{f}:{line} ops.{job}'s {st} would run for real")
        self.assertEqual(bad, [], "these call sites are not isolated:\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main()
