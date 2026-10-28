"""`umbra plugins`, and the lane commands resolving through the registry.

The lane commands used to import the bundled modules directly. A plugin of the
same name in your own directory replaces the bundled one for the scheduled
round, so the commands and the round could drive two different "tasks lanes"
with nothing to say which was which.
"""

import argparse
import io
import os
import sqlite3
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from unittest import mock

from umbra import __main__ as cli
from umbra import config, db, registry

MINE = textwrap.dedent('''
    INPUT  = "events:zsh"
    WINDOW = 720
    PROMPT = "{text}"

    def parse(items, window):
        return list(items)

    def pending(con):
        return [(1, "2026-09-16", "09:00", "from my own plugin", "q", "idea", "proposed")]
''')


class _Base(unittest.TestCase):
    def setUp(self):
        self.user = tempfile.mkdtemp()
        self.cfg = tempfile.mkdtemp()
        open(os.path.join(self.cfg, "config.toml"), "w").close()
        self.patches = [mock.patch.object(config, "PLUGINS_DIR", self.user),
                        mock.patch.object(config, "CONFIG_DIR", self.cfg)]
        for p in self.patches:
            p.start()
        registry.reload()
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        registry.reload()

    def write(self, name, body):
        with open(os.path.join(self.user, f"{name}.py"), "w") as f:
            f.write(body)
        registry.reload()

    def run_cli(self, fn, **kw):
        out = io.StringIO()
        with redirect_stdout(out), \
             mock.patch.object(db, "connect", return_value=self.con):
            fn(argparse.Namespace(**kw))
        return out.getvalue()


class ListTest(_Base):
    def test_lists_the_bundled_three_with_their_state(self):
        out = self.run_cli(cli.cmd_plugins, action="list", name=None,
                           day=None, week=None)
        self.assertRegex(out, r"● goals .* on ")
        self.assertRegex(out, r"○ actions .* off ")

    def test_a_sink_only_plugin_is_neither_on_nor_off(self):
        """It is used when an analyzer names it. "off" would read as a fault."""
        out = self.run_cli(cli.cmd_plugins, action="list", name=None,
                           day=None, week=None)
        line = next(l for l in out.splitlines() if " butler " in l)
        self.assertIn("—", line)
        self.assertNotIn(" off", line)

    def test_yours_are_marked_as_yours(self):
        self.write("mood", MINE)
        out = self.run_cli(cli.cmd_plugins, action="list", name=None,
                           day=None, week=None)
        self.assertRegex(out, r"mood .* yours ")

    def test_a_broken_plugin_is_listed_with_its_error(self):
        self.write("broken", "this is not valid python (\n")
        out = self.run_cli(cli.cmd_plugins, action="list", name=None,
                           day=None, week=None)
        self.assertIn("✗ broken", out)

    def test_a_bad_declaration_is_shown_not_hidden(self):
        self.write("odd", MINE.replace("WINDOW = 720", "WINDOW = 7"))
        out = self.run_cli(cli.cmd_plugins, action="list", name=None,
                           day=None, week=None)
        self.assertIn("does not divide a day", out)


class RunTest(_Base):
    def test_a_week_runs_seven_days(self):
        self.write("mood", MINE)
        with mock.patch("umbra.analyzer.run_days",
                        return_value={"windows": 0, "called": 0, "reused": 0,
                                      "empty": 0, "failed": 0, "rows": 0,
                                      "errors": {}}) as rd:
            self.run_cli(cli.cmd_plugins, action="run", name="mood",
                         day=None, week="2026-W38")
        self.assertEqual(rd.call_args.args[3][0], "2026-09-14")
        self.assertEqual(len(rd.call_args.args[3]), 7)

    def test_a_sink_cannot_be_run(self):
        with self.assertRaises(SystemExit):
            self.run_cli(cli.cmd_plugins, action="run", name="butler",
                         day=None, week=None)

    def test_an_unknown_name_says_so(self):
        with self.assertRaises(SystemExit) as e:
            self.run_cli(cli.cmd_plugins, action="run", name="nope",
                         day=None, week=None)
        self.assertIn("no plugin named", str(e.exception))


class LaneCommandsTest(_Base):
    """A lane's command is the plugin's own: `cli(args)`, with `cli_args(parser)`
    for its arguments and CLI_NAME for its name (the plugin's name otherwise).
    The core registers whatever is installed, so a plugin of the same name in
    your own directory replaces the command along with the lane, and the
    command and the scheduled round cannot drive two different lanes."""

    def run_cmd(self, *argv):
        args = cli.build_parser().parse_args(list(argv))
        return self.run_cli(args.func, **vars(args))

    def test_tasks_is_the_bundled_lanes_command(self):
        out = self.run_cmd("tasks", "list")
        self.assertIn("no pending proposals", out)

    def test_your_override_replaces_the_command_too(self):
        self.write("actions", MINE + textwrap.dedent('''
            CLI_NAME = "tasks"
            def cli(args):
                print("from my own plugin")
        '''))
        self.assertIn("from my own plugin", self.run_cmd("tasks"))

    def test_any_plugin_can_add_a_command(self):
        self.write("mood", MINE + textwrap.dedent('''
            def cli_args(p):
                p.add_argument("level")
            def cli(args):
                print("mood is", args.level)
        '''))
        self.assertIn("mood is calm", self.run_cmd("mood", "calm"))

    def test_a_plugin_cannot_take_a_core_commands_name(self):
        self.write("mood", MINE + textwrap.dedent('''
            CLI_NAME = "sync"
            def cli(args):
                print("hijacked")
        '''))
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            args = cli.build_parser().parse_args(["sync"])
        self.assertIs(args.func, cli.cmd_sync)
        self.assertIn("mood", err.getvalue())

    def test_dismiss_has_to_say_which_verdict(self):
        """"Dismissed" alone cannot say whether extraction was wrong."""
        with self.assertRaises(SystemExit) as e:
            self.run_cmd("tasks", "dismiss", "1")
        self.assertIn("--not-a-task", str(e.exception))

    def test_dismiss_not_a_task_goes_through_close(self):
        from umbra.plugins import actions
        with mock.patch.object(actions, "close", return_value=True) as close:
            out = self.run_cmd("tasks", "dismiss", "7", "--not-a-task")
        self.assertEqual(close.call_args.args[1:], (7, "not_a_task"))
        self.assertIn("not a task", out)

    def test_claims_lists_through_the_plugin(self):
        out = self.run_cmd("claims")
        self.assertIn("no claims yet", out)


if __name__ == "__main__":
    unittest.main()
