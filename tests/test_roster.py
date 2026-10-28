"""Which sources a round runs, and why the others stand aside.

A source that is not set up is not a failure. Someone without a WHOOP has
nothing to fix; failing it every round turned every sync red and, before the
policy change, stopped the import before PLAUD and WeRead ran at all.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import __main__ as cli
from umbra import config, sources
from umbra.importers import git, whoop


def _config(d: str, text: str) -> None:
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write(text)


class RosterTest(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(config, "CONFIG_DIR", self.dir))
        self.enterContext(mock.patch.object(config, "SOURCES_DIR",
                                            os.path.join(self.dir, "sources")))

    def test_a_source_missing_a_requirement_stands_aside_saying_why(self):
        with mock.patch.dict(os.environ, {"WEREAD_API_KEY": ""}):
            names, skipped = cli.roster(["weread"])
        self.assertEqual(names, [])
        self.assertIn("not set up", dict(skipped)["weread"])

    def test_a_source_turned_off_stands_aside(self):
        _config(self.dir, "[sources.zsh]\nenabled = false\n")
        names, skipped = cli.roster(["zsh"])
        self.assertEqual(names, [])
        self.assertEqual(dict(skipped)["zsh"], "turned off in config.toml")

    def test_exclude_says_voice_only_for_the_voice_sources(self):
        _, skipped = cli.roster(["zsh", "plaud"], exclude=["zsh", "plaud"])
        self.assertEqual(dict(skipped)["plaud"], "→ umbra voice")
        self.assertEqual(dict(skipped)["zsh"], "left out with --exclude")

    def test_whoop_never_authorised_is_not_set_up_not_expired(self):
        env = {"WHOOP_CLIENT_ID": "id", "WHOOP_CLIENT_SECRET": "secret"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(whoop.whoop_auth, "TOKEN_PATH",
                               os.path.join(self.dir, "none.json")):
            gaps = sources.missing(whoop)
        self.assertEqual(len(gaps), 1)
        self.assertIn("umbra auth whoop", gaps[0])


    def test_plaud_not_authorised_says_what_to_run(self):
        from umbra.importers import plaud
        with mock.patch.object(plaud.plaud_auth, "TOKEN_PATH",
                               os.path.join(self.dir, "none.json")):
            self.assertIn("plaud login", sources.missing(plaud)[0])


class GitRootsTest(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(config, "CONFIG_DIR", self.dir))

    def test_no_roots_is_not_set_up(self):
        self.assertIn("[sources.git] roots", sources.missing(git)[0])

    def test_roots_with_no_repository_say_so_rather_than_ready(self):
        """Seen: a path nobody else has, status "ready", zero rows."""
        _config(self.dir, f'[sources.git]\nroots = ["{self.dir}"]\n')
        self.assertIn("no git repositories", sources.missing(git)[0])

    def test_roots_with_a_repository_are_ready_and_imported(self):
        logs = os.path.join(self.dir, "proj", ".git", "logs")
        os.makedirs(logs)
        with open(os.path.join(logs, "HEAD"), "w") as f:
            f.write(f"{'0' * 40} {'a' * 40} Someone <s@example.com> 1700000000 +0000"
                    "\tcommit: first\n")
        _config(self.dir, f'[sources.git]\nroots = ["{self.dir}"]\n')
        self.assertEqual(sources.missing(git), [])
        self.assertEqual(len(git.collect()), 1)


if __name__ == "__main__":
    unittest.main()


class StandingAsideOnThePageTest(unittest.TestCase):
    """A source never set up must not sit in "Needs you" over failures from
    before it stood aside — but one that used to import and lost its setup
    still warns."""

    def fresh(self, off=(), unset=(), last_ok=None):
        import sqlite3
        from umbra import db, viz
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        if last_ok:
            db.record_import(con, "butler", ok=True)
        for _ in range(3):
            db.record_import(con, "butler", ok=False, error="env var not set")
        with mock.patch.object(sources, "aside", return_value=(set(off), set(unset))):
            rows = viz.page_fields(con)["fresh"]
        return next(f for f in rows if f["source"] == "butler")

    def test_never_set_up_never_imported_is_quiet(self):
        self.assertFalse(self.fresh(unset={"butler"})["bad"])

    def test_turned_off_is_quiet(self):
        self.assertFalse(self.fresh(off={"butler"}, last_ok=True)["bad"])

    def test_lost_its_setup_after_working_still_warns(self):
        self.assertTrue(self.fresh(unset={"butler"}, last_ok=True)["bad"])

    def test_set_up_and_failing_warns(self):
        self.assertTrue(self.fresh()["bad"])
