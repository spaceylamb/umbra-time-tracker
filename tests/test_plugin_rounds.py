"""What a scheduled round does with plugins.

`umbra sync` and `umbra voice` used to name three lanes. They now run every
enabled plugin, one fault-isolated step each, which is what lets a plugin in
your own directory take part in a round with no core edit. Three things are
pinned here because each fails quietly:

* an upgrade must not stop lanes someone is already running;
* a plugin that cannot load is a failed step, not a missing one;
* the documented way of switching sending on has to actually switch it on.
"""

import os
import sqlite3
import tempfile
import textwrap
import unittest
from unittest import mock

from umbra import analyzer, config, db, ops, plugins, registry

DAY = "2026-08-27"

COUNTING = textwrap.dedent('''
    INPUT  = "events:zsh"
    WINDOW = 720
    PROMPT = "{text}"

    def parse(items, window):
        return list(items)
''')


class LegacyEnablementTest(unittest.TestCase):
    """Before plugins, every round ran tasks and claims extraction, and their
    config sections only decided what was sent."""

    def test_an_old_actions_section_keeps_the_lane_running(self):
        self.assertTrue(registry.enabled("actions",
                                         {"actions": {"dispatch": "butler"}}))

    def test_an_old_claims_section_keeps_the_lane_running(self):
        self.assertTrue(registry.enabled("claims", {"claims": {"check": "butler"}}))

    def test_the_new_switch_wins_over_the_old_section(self):
        conf = {"actions": {"dispatch": "butler"},
                "plugins": {"actions": {"enabled": False}}}
        self.assertFalse(registry.enabled("actions", conf))

    def test_no_section_means_off_for_a_new_install(self):
        self.assertFalse(registry.enabled("actions", {}))

    def test_only_the_two_lanes_that_predate_plugins_get_this(self):
        """A section that happens to share a plugin's name is not a switch."""
        self.assertFalse(registry.enabled("mood", {"mood": {"x": 1}}))


class SendGateTest(unittest.TestCase):
    def test_the_documented_config_switches_actions_sending_on(self):
        """The gate used to check only `[actions] dispatch = "butler"`, so a sink
        configured the documented way was refused before dispatch() — which
        would have sent — was ever asked."""
        from umbra.plugins import actions
        conf = {"plugins": {"actions": {"sink": "todo", "send": True}}}
        with mock.patch.object(actions, "_conf", return_value=conf):
            self.assertTrue(actions.dispatch_enabled())

    def test_naming_a_sink_alone_still_does_not(self):
        from umbra.plugins import actions
        conf = {"plugins": {"actions": {"sink": "todo"}}}
        with mock.patch.object(actions, "_conf", return_value=conf):
            self.assertFalse(actions.dispatch_enabled())

    def test_the_old_key_still_works(self):
        from umbra.plugins import actions
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": {"dispatch": "butler"}}):
            self.assertTrue(actions.dispatch_enabled())


class _Dirs(unittest.TestCase):
    def setUp(self):
        self.user = tempfile.mkdtemp()
        self.bundled = tempfile.mkdtemp()
        self.patches = [mock.patch.object(config, "PLUGINS_DIR", self.user),
                        mock.patch.object(plugins, "DIR", self.bundled)]
        for p in self.patches:
            p.start()
        registry.reload()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        registry.reload()

    def write(self, name, body):
        with open(os.path.join(self.user, f"{name}.py"), "w") as f:
            f.write(body)
        registry.reload()

    def _round(self, conf):
        steps, failed, ran = [], [], []

        def step(name, fn):
            steps.append(name)
            try:
                fn()
            except Exception:                         # noqa: BLE001
                failed.append(name)

        with mock.patch.object(registry, "enabled",
                               side_effect=lambda n, c=None: n in conf), \
             mock.patch.object(ops, "_sync_one",
                               side_effect=lambda n, m, d, log: ran.append(n)):
            ops._run_plugins(step, [DAY], None, log=lambda *_: None)
        return steps, failed, ran


class RoundTest(_Dirs):
    def test_every_enabled_plugin_gets_a_step_named_after_it(self):
        self.write("alpha", COUNTING)
        self.write("beta", COUNTING)
        steps, _failed, ran = self._round({"alpha", "beta"})
        self.assertEqual(steps, ["alpha", "beta"])
        self.assertEqual(ran, ["alpha", "beta"])

    def test_a_disabled_plugin_is_not_run(self):
        self.write("alpha", COUNTING)
        self.write("beta", COUNTING)
        steps, _failed, _ran = self._round({"alpha"})
        self.assertEqual(steps, ["alpha"])

    def test_a_plugin_that_cannot_load_is_a_failed_step(self):
        """Something you installed is trying to run and not managing to. A
        round that says nothing exits 0 while doing less than you asked."""
        self.write("broken", "this is not valid python (\n")
        self.write("alpha", COUNTING)
        steps, failed, ran = self._round({"alpha"})
        self.assertIn("broken", failed)
        self.assertEqual(ran, ["alpha"])


class SyncHookTest(_Dirs):
    def setUp(self):
        super().setUp()
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def test_without_a_hook_the_round_judges_the_days_handed_over(self):
        self.write("alpha", COUNTING)
        mod = registry.analyzers()["alpha"]
        with mock.patch.object(analyzer, "run_days",
                               return_value={k: 0 for k in analyzer.COUNTS}
                               | {"errors": {}}) as rd:
            analyzer.sync_plugin(self.con, "alpha", mod, [DAY],
                                 log=lambda *_: None)
        self.assertEqual(rd.call_args.args[3], [DAY])

    def test_a_declared_hook_replaces_the_default(self):
        self.write("alpha", COUNTING + textwrap.dedent('''
            SEEN = []
            def sync(con, days, log):
                SEEN.append(list(days))
        '''))
        mod = registry.analyzers()["alpha"]
        analyzer.sync_plugin(self.con, "alpha", mod, [DAY])
        self.assertEqual(mod.SEEN, [[DAY]])

    def test_one_day_failing_does_not_cost_the_others(self):
        self.write("alpha", COUNTING)
        mod = registry.analyzers()["alpha"]
        real = analyzer.run

        def run(con, name, m, day, model, **kw):
            if day == "2026-08-26":
                raise RuntimeError("boom")
            return real(con, name, m, day, model, call=lambda p: "[]", **kw)

        with mock.patch.object(analyzer, "run", side_effect=run):
            r = analyzer.run_days(self.con, "alpha", mod,
                                  ["2026-08-26", DAY], model="m")
        self.assertEqual(list(r["errors"]), ["2026-08-26"])


class GoalsRoundTest(unittest.TestCase):
    def test_goals_judges_its_own_two_weeks_not_the_days_handed_over(self):
        """A late recording can land in last week, and a priority set this
        morning applies to Monday too."""
        from umbra import goals as G
        from umbra.plugins import goals as P
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        with mock.patch.object(G, "attrib_days", return_value=["2026-09-14"]), \
             mock.patch.object(analyzer, "run_days",
                               return_value={k: 0 for k in analyzer.COUNTS}
                               | {"errors": {}}) as rd:
            P.sync(con, ["2026-09-20"], log=lambda *_: None)
        self.assertEqual(rd.call_args.args[3], ["2026-09-14"])


if __name__ == "__main__":
    unittest.main()
