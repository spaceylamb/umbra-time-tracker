"""Tests for the page builder.

The most important one is privacy: the HTML embeds browsing history, clipboard
text and AI conversations in full, and written into the repository by default
it would sooner or later be picked up by `git add -A`. The reason .gitignore
excludes *.db applies to its rendered form too.
"""

import json
import os
import re
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import db, viz


class OutputLocationTest(unittest.TestCase):
    def test_default_output_is_next_to_the_database(self):
        self.assertEqual(os.path.dirname(viz.OUT), db.DB_DIR)

    def test_default_output_is_outside_any_repo_checkout(self):
        """Physically outside the source tree beats trusting .gitignore.

        The page embeds the full raw history. A .gitignore rule only holds
        until someone runs `git add -f` or edits the ignore file; a path that
        is not under the checkout cannot be committed by accident at all.

        Asserted structurally, not against a literal path — the previous
        version pinned "Library/Application Support", which is a string that
        os.path.expanduser happily produces on Linux too. So it passed on the
        Ubuntu CI runner while pointing at a directory no Linux user has.
        """
        repo = os.path.dirname(os.path.dirname(os.path.abspath(viz.__file__)))
        self.assertFalse(
            os.path.commonpath([repo, os.path.abspath(viz.OUT)]) == repo,
            f"{viz.OUT} is inside the checkout at {repo}")


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.db")
        con = db.connect(self.path)
        # Local day boundaries: day_bounds is local, and UTC midnight would push
        # events near either end into the neighbouring day
        from umbra import analyze
        lo, _ = analyze.day_bounds("2026-08-14")
        rows = [
            ("w:s", lo + 3600_000, 7 * 3600_000, "whoop", "sleep",
             '{"nap": false}'),
            ("w:w", lo + 20 * 3600_000, 3600_000, "whoop", "workout",
             '{"sport": "swimming"}'),
            ("w:c", lo, 20 * 3600_000, "whoop", "cycle", '{"strain": 12}'),
            ("z:1", lo + 10 * 3600_000, None, "zsh", "command", '{"cmd": "ls"}'),
        ]
        con.executemany(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        p = mock.patch.object(db, "DB_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def _payload(self):
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k: sqlite3.connect(self.path)):
            return viz.build()

    def test_sleep_and_workout_are_separate_lanes(self):
        """One lane would draw "lay down for 8 hours" and "trained for an hour"
        as the same thing."""
        day = next(iter(self._payload()["days"].values()))
        self.assertIn("sleep", day["lanes"])
        self.assertIn("workout", day["lanes"])

    def test_workout_carries_its_sport_name(self):
        day = next(iter(self._payload()["days"].values()))
        self.assertEqual(day["lanes"]["workout"][0][2], "swimming")

    def test_cycle_is_not_drawn_as_a_lane(self):
        """A cycle is a whole-day summary; drawn as a 20-hour band it means nothing."""
        day = next(iter(self._payload()["days"].values()))
        self.assertNotIn("cycle", day["lanes"])

    def test_lanes_never_exceed_the_day(self):
        """Sleep crossing midnight, unclamped, draws off the track."""
        for day in self._payload()["days"].values():
            for key, runs in day["lanes"].items():
                for a, b, _ in runs:
                    self.assertGreaterEqual(a, 0, key)
                    self.assertLessEqual(b, 1440, key)

    def test_coverage_includes_the_offscreen_tier(self):
        day = next(iter(self._payload()["days"].values()))
        self.assertIn("off", day["cov"])
        self.assertGreater(day["cov"]["off"], 0)


class HtmlTest(unittest.TestCase):

    def test_every_lane_key_has_a_label(self):
        for key in viz.SOURCES:
            self.assertIn(key, viz.LABEL, f"no {key} in LABEL")



if __name__ == "__main__":
    unittest.main()
