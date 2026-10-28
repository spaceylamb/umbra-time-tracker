"""An expired authorisation must fail the import loudly, not log a failure and
carry on — and without starving the sources after it.

A policy chosen on 2026-08-15, when WHOOP was the only source needing
authorisation. The reason: a retry never heals, and the failure would sit
quietly in the imports table until weeks of missing data were noticed.

It used to stop the whole import on the spot. That made one person's missing
WHOOP token cost them their recordings and their reading every round, so now
every source runs and the round exits non-zero naming the expired one. Any
other failure exits non-zero too: a scheduler reads the exit code and nothing
else.

cmd_import special-cases by exception type — a new source's exception class
left off the list would be reported as an ordinary failure, **with no test
going red**. This file closes that hole.
"""

# argparse.Namespace rather than mock.Mock: a Mock invents a truthy object for
# **any** attribute, so every new cmd_import option (--exclude, say) would blow
# up here with a baffling TypeError while the behaviour under test had not
# changed. Namespace is what argparse really passes in.
import argparse
import contextlib
import io
import sqlite3
import unittest
from unittest import mock

from umbra import __main__ as cli
from umbra import db
from umbra.plaud_auth import PlaudAuthExpired
from umbra.whoop_auth import WhoopAuthExpired


class AuthExpiryFailsImportTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def _run(self, source, exc):
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL[source], "collect", side_effect=exc), \
             mock.patch.object(cli, "cmd_stats"):
            cli.cmd_import(argparse.Namespace(only=[source], full=True, exclude=None))

    def test_whoop_auth_expiry_aborts(self):
        with self.assertRaises(SystemExit):
            self._run("whoop", WhoopAuthExpired("gone"))

    def test_plaud_auth_expiry_aborts(self):
        with self.assertRaises(SystemExit):
            self._run("plaud", PlaudAuthExpired("gone"))

    def test_the_failure_is_recorded_before_aborting(self):
        """Record the failure in the imports table before stopping — otherwise
        freshness shows only that the source "hasn't run in a while", not why."""
        with self.assertRaises(SystemExit):
            self._run("plaud", PlaudAuthExpired("token gone"))
        row = self.con.execute(
            "SELECT ok, error FROM imports WHERE source='plaud'").fetchone()
        self.assertEqual(row[0], 0)
        self.assertIn("token gone", row[1])

    def test_auth_expiry_does_not_starve_the_sources_after_it(self):
        """WHOOP's token gone must not cost the round its recordings."""
        ran = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["whoop"], "collect",
                               side_effect=WhoopAuthExpired("gone")), \
             mock.patch.object(cli.ALL["plaud"], "collect",
                               side_effect=lambda **_: ran.append("plaud") or []), \
             mock.patch.object(cli, "cmd_stats"):
            with self.assertRaises(cli.ImportIncomplete) as cm:
                cli.cmd_import(argparse.Namespace(only=["whoop", "plaud"], full=True,
                                                  exclude=None))
        self.assertEqual(ran, ["plaud"])
        self.assertIn("auth expired for whoop", str(cm.exception))

    def test_ordinary_errors_run_the_rest_and_exit_non_zero(self):
        """A network blip stays isolated per source, but the round is not clean
        and must not exit 0 — seen: every PLAUD request died of SSL EOF, nothing
        was written, and import exited 0."""
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["plaud"], "collect",
                               side_effect=TimeoutError("the network hiccuped")), \
             mock.patch.object(cli, "cmd_stats"):
            with self.assertRaises(SystemExit) as cm:
                cli.cmd_import(argparse.Namespace(only=["plaud"], full=True, exclude=None))
        self.assertNotIn("auth expired", str(cm.exception.code))
        row = self.con.execute(
            "SELECT ok FROM imports WHERE source='plaud'").fetchone()
        self.assertEqual(row[0], 0)

    def test_the_days_touched_survive_a_failure(self):
        """The voice lane scans the days a round touched; a failing source must
        not lose the days the others wrote."""
        row = ("plaud:f1:0", 86_400_000 * 3, 0, "plaud", "speech", {"text": "x"})
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["whoop"], "collect",
                               side_effect=TimeoutError("down")), \
             mock.patch.object(cli.ALL["plaud"], "collect", return_value=[row]), \
             mock.patch.object(cli, "cmd_stats"):
            with self.assertRaises(cli.ImportIncomplete) as cm:
                cli.cmd_import(argparse.Namespace(only=["whoop", "plaud"], full=True,
                                                  exclude=None))
        self.assertEqual(len(cm.exception.days), 1)

    def test_every_auth_exception_is_wired_into_cmd_import(self):
        """A new authorisation exception added without wiring it into cmd_import
        — this catches it.

        Found by name: every exception class called *AuthExpired must be
        special-cased by cmd_import.
        """
        import pkgutil
        import importlib
        import umbra

        # Only an absent PyObjC is exempt (the collector cannot import without
        # it, on Linux or a clean CI runner). Any other ImportError still fails —
        # a real broken import swallowed by the exemption would make this sweep
        # meaningless.
        PYOBJC = {"AppKit", "Quartz", "Foundation", "ApplicationServices",
                  "objc"}
        found = set()
        for m in pkgutil.iter_modules(umbra.__path__):
            try:
                mod = importlib.import_module(f"umbra.{m.name}")
            except ImportError as e:
                if e.name in PYOBJC:
                    continue
                raise
            for name, obj in vars(mod).items():
                if (name.endswith("AuthExpired") and isinstance(obj, type)
                        and issubclass(obj, Exception)):
                    found.add(obj)
        self.assertTrue(found, "found none — this test itself has stopped working")
        missing = found - set(cli.AUTH_EXPIRED)
        self.assertEqual(missing, set(),
                         f"these authorisation exceptions are not wired into cmd_import: "
                         f"{sorted(c.__name__ for c in missing)}")


if __name__ == "__main__":
    unittest.main()
