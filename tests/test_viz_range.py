"""The page's time window, and "the share that can be named".

Two things:
  - Only the last N days are drawn by default. 1,333 days of calendar scroll
    forever, and years with only WHOOP have empty lanes — almost no
    information, and the HTML swells to 1.8 MB.
  - Each day gets a share: of 1,440 minutes, how many can say **what exactly
    you were doing**.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import analyze, db, viz


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.db")
        con = db.connect(self.path)
        # From long ago to today, one every 30 days
        base, _ = analyze.day_bounds("2026-08-14")
        for i in range(0, 400, 30):
            ts = base - i * 86_400_000
            con.execute(
                "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
                "VALUES (?,?,?,?,?,?)",
                (f"z:{i}", ts + 36_000_000, None, "zsh", "command", '{"cmd":"ls"}'))
        con.commit()
        con.close()

    def _build(self, **kw):
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k: sqlite3.connect(self.path)):
            return viz.build(**kw)

    def test_default_window_is_not_everything(self):
        self.assertIsNotNone(viz.DEFAULT_SINCE)

    def test_since_filters_days_out(self):
        p = self._build(since="2026-07-01")
        self.assertTrue(p["order"])
        self.assertTrue(all(d >= "2026-07-01" for d in p["order"]),
                        f"days out of range: {[d for d in p['order'] if d < '2026-07-01']}")

    def test_since_none_keeps_everything(self):
        """--all still shows the whole history; it just is not the default."""
        p = self._build(since=None)
        self.assertLess(min(p["order"]), "2026-01-01")

    def test_window_is_recorded_in_payload(self):
        """The page must say "you are looking at a window, not everything" —
        otherwise missing data and data not drawn look the same."""
        p = self._build(since="2026-07-01")
        self.assertEqual(p["since"], "2026-07-01")


class ExplainedPercentTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.db")
        self.lo, _ = analyze.day_bounds("2026-08-14")
        self.con = db.connect(self.path)

    def _add(self, source, kind, at_min, dur_ms=None, payload="{}"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,?,?,?)",
            (f"{source}:{kind}:{at_min}", self.lo + at_min * 60_000, dur_ms,
             source, kind, payload))
        self.con.commit()

    def _day(self):
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k: sqlite3.connect(self.path)):
            p = viz.build(since=None)
        return p["days"]["2026-08-14"]

    def test_percent_is_out_of_the_whole_day(self):
        """The denominator is 1440, not "the part with data" — that would show a
        day with one hour at a computer as 100%."""
        self._add("zsh", "command", 600)
        d = self._day()
        # 1 minute / 1440 = 0.069%, rounded to the 0.1 shown
        self.assertEqual(d["pct"], round(100 * 1 / 1440, 1))

    def test_sleep_counts_as_explained(self):
        """Sleeping is one kind of "knowing what exactly you were doing"."""
        self._add("whoop", "sleep", 0, dur_ms=8 * 3600_000)
        self.assertGreater(self._day()["pct"], 30)

    def test_container_level_does_not_count(self):
        """Knowing only which app was in front is not knowing what you were doing."""
        self._add("app", "focus", 0, dur_ms=8 * 3600_000, payload='{"app":"Lark"}')
        d = self._day()
        self.assertEqual(d["pct"], 0)
        self.assertGreater(d["cov"]["B"], 400)

    def test_percent_never_exceeds_100(self):
        self._add("whoop", "sleep", 0, dur_ms=30 * 3600_000)
        self._add("whoop", "workout", 100, dur_ms=20 * 3600_000)
        self._add("zsh", "command", 700)
        self.assertLessEqual(self._day()["pct"], 100)

    def test_percent_is_rounded_for_display(self):
        self._add("zsh", "command", 600)
        self.assertIsInstance(self._day()["pct"], float)
        self.assertEqual(self._day()["pct"], round(self._day()["pct"], 1))


if __name__ == "__main__":
    unittest.main()
