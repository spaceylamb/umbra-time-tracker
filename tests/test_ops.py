"""The ops layer: one idempotent command and a thin OS adapter.

From the 2026-08-19 open-source refactor. The scheduled chain used to be
launchd plist → run-import.sh (a zsh wrapper) → python -m umbra import,
and the wrapper's only reason to exist was "launchd does not load the env
file". It died of two kinds of incident:

  · the repository moved and the UMBRA_DIR hard-coded in the wrapper did not
    follow (measured: it pointed at one folder, the repository was in another)
    — hand-made glue everyone installing would trip over again
  · the alarm threshold and the plist's StartInterval were the same number
    copied by hand in two places

The fix was not writing the wrapper correctly but leaving it no reason to exist:

  umbra sync        import everything → infer recent days (incremental) → viz.
                     Idempotent: almost free with no new data; any number of runs
                     gives the same result.
  env_file           umbra loads it itself; no shell wrapper sourcing it
  schedule install   generates the plist / systemd timer, with the lessons baked in
                     (-u against buffering, absolute paths, logs not in /tmp)
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from umbra import config, ops


def _days_ignored(fn):
    """goals' round picks its own days; the old _attrib stand-in took none."""
    return lambda days: fn()


class TaskTriageLog(unittest.TestCase):
    def test_it_says_how_many_were_extracted_and_why_they_were_not_sent(self):
        """"7 extracted" alone cannot be acted on.

        Held-back proposals stay in the database with their status untouched, so
        the log is the only place the difference between "nothing was found" and
        "things were found and deliberately not sent" is visible. Each reason is
        printed separately for the same reason they are stored separately: an
        echo means the task was already being done as it was spoken, while a
        domain filter means it was work-related — and they are fixed in
        completely different places.
        """
        result = {"reused": 30, "fresh_calls": 1, "fresh": [{}] * 7,
                  "inserted": 18, "echo_filtered": 11,
                  "domain_filtered": 4}
        from umbra.plugins import actions
        out = []
        with mock.patch("umbra.plugins.actions.scan", return_value=result), \
             mock.patch("umbra.plugins.actions.dispatch_enabled", return_value=False):
            actions.sync(object(), ["2026-08-24"], log=out.append)
        text = "\n".join(out)
        self.assertIn("extracted 18", text)
        self.assertIn("echoes 11", text)
        self.assertIn("domain-filtered 4", text)
        self.assertIn("awaiting dispatch 3", text)


class EnvFileTest(unittest.TestCase):
    def _load(self, body, pre=None):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "env")
        with open(path, "w") as f:
            f.write(body)
        env = dict(pre or {})
        with mock.patch.dict(os.environ, env, clear=True):
            n = ops.load_env(path)
            return n, dict(os.environ)

    def test_export_and_bare_assignments_are_both_read(self):
        n, env = self._load("export A=1\nB=2\n")
        self.assertEqual((env["A"], env["B"]), ("1", "2"))
        self.assertEqual(n, 2)

    def test_variables_already_set_are_not_overwritten(self):
        """The real environment beats the file. Under launchd there is none and the
        file applies; in an interactive shell a manually exported value must win,
        or there is no way to try another key for a moment."""
        _, env = self._load("export A=文件里的\n", pre={"A": "手动的"})
        self.assertEqual(env["A"], "手动的")

    def test_quotes_are_stripped_and_comments_skipped(self):
        _, env = self._load('# 注释\nexport A="x y"\nB=\'z\'\n')
        self.assertEqual((env["A"], env["B"]), ("x y", "z"))

    def test_a_missing_file_returns_zero_quietly(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ops.load_env("/不存在"), 0)


class LaunchdTemplateTest(unittest.TestCase):
    def test_every_lesson_is_baked_into_the_template(self):
        p = ops.plist("com.umbra.sync", ["-m", "umbra", "sync"],
                      interval_s=21600, python="/usr/local/bin/python3",
                      workdir="/repo", logdir="/Users/x/Library/Logs/Umbra")
        self.assertIn("<string>-u</string>", p)          # without -u the log stays empty
        self.assertIn("/usr/local/bin/python3", p)       # launchd has no login PATH
        self.assertNotIn("/tmp", p)                      # /tmp is cleared on reboot
        self.assertIn("<integer>21600</integer>", p)
        self.assertIn("<string>/repo</string>", p)       # cd into the repository so -m finds the package
        self.assertIn("com.umbra.sync", p)

    def test_path_is_carried_or_the_user_s_deploy_command_cannot_find_its_interpreter(self):
        """launchd's PATH is only /usr/bin:/bin:/usr/sbin:/sbin.

        An absolute path is enough for the Python interpreter, but the [deploy]
        command is the user's string run through a shell — wrangler / netlify /
        aws all start with `#!/usr/bin/env node` (or python), and the shebang
        **looks its interpreter up on PATH again**, so an absolute path to wrangler
        does not help. Measured: `env: node: No such file or directory` in the
        scheduled job, while import / infer / viz were all green and the log had
        one extra line about deploy failing — exactly the "looks like it ran but
        never published" this project cares most about.
        """
        p = ops.plist("com.umbra.sync", ["-m", "umbra", "sync"],
                      interval_s=21600, python="/usr/local/bin/python3",
                      workdir="/repo", logdir="/l",
                      path="/opt/homebrew/bin:/usr/bin:/bin")
        self.assertIn("EnvironmentVariables", p)
        self.assertIn("/opt/homebrew/bin", p)

    def test_no_path_given_means_no_such_section(self):
        """No directory is hard-coded: whatever PATH was at install time, visible
        in the plist."""
        p = ops.plist("com.umbra.sync", ["-m", "umbra", "sync"],
                      interval_s=21600, python="/p", workdir="/repo", logdir="/l")
        self.assertNotIn("EnvironmentVariables", p)

    def test_a_resident_job_has_no_interval_but_keepalive(self):
        p = ops.plist("com.umbra.collector", ["-m", "umbra", "collect"],
                      interval_s=None, python="/usr/local/bin/python3",
                      workdir="/repo", logdir="/l")
        self.assertIn("KeepAlive", p)
        self.assertNotIn("StartInterval", p)


class ScheduleInstallTest(unittest.TestCase):
    """plist() supporting PATH is not enough — install must really pass it.

    The two live in two files, the easiest place for "added on one side,
    forgotten on the other", and the symptom is the hardest to notice: install
    looks fine, and six hours later deploy fails quietly.
    """

    @unittest.skipUnless(sys.platform == "darwin", "launchd is macOS only")
    def test_install_writes_the_current_path_into_the_plist(self):
        import argparse
        from unittest import mock
        from umbra import __main__ as m
        with tempfile.TemporaryDirectory() as d:
            args = argparse.Namespace(action="install", collector=False)
            with mock.patch.dict(os.environ, {"HOME": d,
                                              "PATH": "/nvm/bin:/usr/bin"}), \
                 mock.patch.object(ops, "LOG_DIR", os.path.join(d, "logs")), \
                 mock.patch.object(m, "job_python", lambda: "/py"), \
                 mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stderr="", stdout="")
                m.cmd_schedule(args)
            f = os.path.join(d, "Library", "LaunchAgents",
                             "com.umbra.sync.plist")
            self.assertTrue(os.path.exists(f), "the plist was not written")
            got = open(f, encoding="utf-8").read()
            self.assertIn("/nvm/bin", got)


class SyncOrchestrationTest(unittest.TestCase):
    """⚠️ **Every step must be injected**, and configuration pinned to an empty
    temp directory.

    The 2026-08-24 incident: when claims joined sync this file did not follow, so
    `_run_claims` ran **for real** in tests — the production database, a real
    model, real tasks sent. deploy likewise: one `unittest discover` made 16
    production deploys.

    Nor may it read the user's real configuration: with [schedule].split_voice on,
    every expectation here changes — a test drifting with the machine's config
    is another kind of irreproducible.
    """

    def _run(self, **kw):
        from unittest import mock

        steps = []
        self.inferred = []
        fns = {
            # cmd_import returns the days it touched; an empty list here.
            "run_import": lambda: steps.append("import") or [],
            "run_infer": lambda days: (steps.append(f"infer:{len(days)}"),
                                       self.inferred.extend(days)) and True,
            "run_viz": lambda: steps.append("viz") or True,
            "run_tasks": lambda days: steps.append(f"tasks:{len(days)}") or True,
            "run_claims": lambda days: steps.append(f"claims:{len(days)}") or True,
            "run_attrib": lambda: steps.append("attrib") or True,
            "run_deploy": lambda **k: steps.append("deploy") or True,
        }
        fns.update(kw)
        # Pin the configuration too: with [schedule].split_voice on, every
        # expectation here changes — a test drifting with the machine's config is
        # another kind of irreproducible.
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
            f.write("")
        with mock.patch.object(config, "CONFIG_DIR", d):
            code = ops.sync(_tz=lambda: None, _import=fns["run_import"], _infer=fns["run_infer"],
                            _viz=fns["run_viz"],
                            _plugins={"actions": fns["run_tasks"],
                                      "claims": fns["run_claims"],
                                      "goals": _days_ignored(fns["run_attrib"])},
                            _deploy=fns["run_deploy"], log=lambda *a: None)
        return steps, code

    def test_late_data_for_an_older_day_rejudges_that_day(self):
        """Offline for days, a source catches up later — a whole week of sleep
        arrives at once. Judging only "the last two days" leaves those days on
        the judgement made before the data came, forever, with no error. import
        knows which days it wrote; that beats guessing at a calendar."""
        import datetime as dt
        ago = lambda n: str(dt.date.today() - dt.timedelta(days=n))
        self._run(run_import=lambda: [ago(6), ago(4), ago(0)])
        self.assertEqual(sorted(self.inferred),
                         sorted([ago(0), ago(1), ago(4), ago(6)]))

    def test_days_too_far_back_are_not_rejudged(self):
        """The backstop: a source that one day reports half a year of history as
        new (a changed uid, --full) must not send half a year to the model in
        one round — a never-judged day is a full day of calls."""
        import datetime as dt
        ago = lambda n: str(dt.date.today() - dt.timedelta(days=n))
        self._run(run_import=lambda: [ago(13), ago(14), ago(200)])
        self.assertIn(ago(13), self.inferred)
        self.assertNotIn(ago(14), self.inferred)
        self.assertNotIn(ago(200), self.inferred)

    def test_a_failed_source_still_rejudges_the_days_the_others_wrote(self):
        """cmd_import raises ImportIncomplete when a source fails, carrying the
        days the rest of the round did write. Swallowing it with the step would
        drop those days, and they would wait for a rejudge that never comes."""
        import datetime as dt

        from umbra import __main__ as cli
        ago = lambda n: str(dt.date.today() - dt.timedelta(days=n))

        def partial():
            raise cli.ImportIncomplete("1 source(s) failed: whoop", [ago(5)])
        steps, code = self._run(run_import=partial)
        self.assertIn(ago(5), self.inferred)
        self.assertEqual(code, 1)

    def test_order_import_infer_plugins_viz_deploy(self):
        steps, code = self._run()
        self.assertEqual(steps, ["import", "infer:2", "tasks:2", "claims:2",
                                 "attrib", "viz", "deploy"])
        self.assertEqual(code, 0)

    def test_attribution_runs_after_infer_and_before_the_page(self):
        """After infer: attribution judges the stretches infer produced, so the
        other order judges last round's. Before viz: this round's numbers belong
        in this round's page, or you wait a round to see a goal change."""
        steps, _ = self._run()
        self.assertLess(steps.index("infer:2"), steps.index("attrib"))
        self.assertLess(steps.index("attrib"), steps.index("viz"))

    def test_a_failed_attribution_still_builds_the_page(self):
        def bad():
            raise RuntimeError("model unavailable")
        steps, code = self._run(run_attrib=bad)
        self.assertIn("viz", steps)
        self.assertIn("deploy", steps)
        self.assertNotEqual(code, 0)

    def test_a_failed_import_still_rebuilds_the_page(self):
        """Collection breaking must not take "the page is still readable" with
        it — but the exit code must be non-zero, so launchd's log shows the
        round was not clean."""
        def bad():
            raise RuntimeError("a source broke")
        steps, code = self._run(run_import=bad)
        self.assertIn("viz", steps)
        self.assertNotEqual(code, 0)

    def test_a_failed_infer_still_rebuilds_the_page(self):
        def bad(days):
            raise RuntimeError("no GEMINI_API_KEY")
        steps, code = self._run(run_infer=bad)
        self.assertIn("viz", steps)
        self.assertNotEqual(code, 0)

    def test_a_failed_task_lane_does_not_hold_up_the_others(self):
        """The two lanes share recordings and **nothing else**: a destination
        down or a model rate-limited must not take "today's timeline is still
        readable" down with it."""
        def bad(days):
            raise RuntimeError("Butler 503")
        steps, code = self._run(run_tasks=bad)
        self.assertEqual(steps, ["import", "infer:2", "claims:2",
                                 "attrib", "viz", "deploy"])
        self.assertNotEqual(code, 0)

    def test_a_failed_infer_does_not_stop_task_extraction(self):
        """And the other way round — they share recordings, not calls."""
        def bad(days):
            raise RuntimeError("no GEMINI_API_KEY")
        steps, _ = self._run(run_infer=bad)
        self.assertIn("tasks:2", steps)


class ThresholdsShareOneSourceTest(unittest.TestCase):
    def test_the_alarm_threshold_derives_from_the_configured_interval(self):
        """The interval and the threshold must have one source. Once one lived in
        the plist and the other was hard-coded as 120 minutes, and with a
        six-hour healthy job all 13 sources wore ⚠ between rounds."""
        with mock.patch.object(ops, "_conf",
                               return_value={"schedule": {"interval_min": 60}}):
            self.assertEqual(ops.interval_min(), 60)
            self.assertEqual(ops.stale_min(), 90)

    def test_the_default_without_config_matches_the_existing_plist(self):
        with mock.patch.object(ops, "_conf", return_value={}):
            self.assertEqual(ops.interval_min(), 360)


if __name__ == "__main__":
    unittest.main()
