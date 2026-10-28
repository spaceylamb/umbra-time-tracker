"""What each day's payload carries, and what it does not.

This file used to test the skeleton of the hand-written page (assets/viz.*);
that page was retired in 2026-10 for the app in umbra/web/, and its skeleton
tests went with it (the web side is checked by tests/test_web_build.py and by
screenshots). What remains is the payload half: don't put in what the page
never draws, and don't take out what the fallback needs.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import analyze, db, viz


class PayloadTest(unittest.TestCase):
    """What the page does not draw stays out of the payload — it is 7 MB already."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.db")
        con = db.connect(self.path)
        lo, _ = analyze.day_bounds("2026-08-14")
        con.executemany(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,?,?,?)",
            [("z:1", lo + 10 * 3600_000, None, "zsh", "command", '{"cmd": "ls"}'),
             ("w:s", lo + 3600_000, 7 * 3600_000, "whoop", "sleep", '{"nap": false}')])
        con.commit()
        con.close()
        p = mock.patch.object(db, "DB_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def _day(self):
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k: sqlite3.connect(self.path)):
            return viz.build()["days"]["2026-08-14"]

    def test_the_rule_based_segmentation_is_gone(self):
        """Its only reader was the segment detail; with that gone it is pure size."""
        self.assertNotIn("segs", self._day())

    def test_both_five_minute_forms_stay(self):
        """The fallback path uses them."""
        d = self._day()
        self.assertIn("bcat", d)
        self.assertIn("bruns", d)

    def test_the_coverage_the_calendar_uses_stays(self):
        """The calendar cell's tooltip still reports how much of the day can be named."""
        self.assertIn("pct", self._day())


if __name__ == "__main__":
    unittest.main()
