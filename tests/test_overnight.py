"""An event crossing midnight counts on both days.

load_range used `WHERE ts BETWEEN lo AND hi` — only events whose **start** falls
in the window. A stay that began at 18:37 last night and lasted 762 minutes
covers today 00:00–07:20, but its start is yesterday, so computing today's
coverage never read it.

The analysis was already right: span() in coverage() clamps events with a
negative m0 back into the day (its comment says durations cross midnight and
must be clamped), and mark() drops minutes out of range. What was missing was
**the query fetching those events at all**.

The bug was always there, but the longest overnight events used to be WHOOP
sleep, with limited effect. Once location arrived with a 762-minute stay, 440
minutes vanished at a stroke.
"""

import sqlite3
import unittest

from umbra import analyze, db

DAY = "2026-08-16"
LO, HI = analyze.day_bounds(DAY)
MIN = 60_000


class LoadRangeTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def add(self, uid, ts, dur, src="location", kind="stay", **payload):
        db.insert(self.con, [(uid, ts, dur, src, kind,
                              payload or {"place": "家"})])

    def test_an_event_starting_yesterday_is_loaded(self):
        """Starts 18:37, lasts 762 minutes — covers today 00:00 to 07:20."""
        self.add("overnight", LO - 323 * MIN, 762 * MIN)
        got = db.load_range(self.con, LO, HI)
        self.assertEqual(len(got), 1, "the overnight event was not read")

    def test_an_event_that_ended_before_the_window_is_not_loaded(self):
        self.add("earlier", LO - 300 * MIN, 60 * MIN)
        self.assertEqual(db.load_range(self.con, LO, HI), [])

    def test_an_event_starting_after_the_window_is_not_loaded(self):
        self.add("later", HI + MIN, 60 * MIN)
        self.assertEqual(db.load_range(self.con, LO, HI), [])

    def test_a_point_event_before_the_window_is_not_loaded(self):
        """A zero-length point event must not count as overlapping forever just
        because it has no end."""
        self.add("point", LO - MIN, 0)
        self.assertEqual(db.load_range(self.con, LO, HI), [])

    def test_a_null_duration_is_treated_as_a_point(self):
        db.insert(self.con, [("nodur", LO - MIN, None, "zsh", "command", {})])
        self.assertEqual(db.load_range(self.con, LO, HI), [])

    def test_results_stay_in_time_order(self):
        self.add("b", LO + 100 * MIN, 0)
        self.add("a", LO - 60 * MIN, 120 * MIN)
        self.assertEqual([e["ts"] for e in db.load_range(self.con, LO, HI)],
                         sorted(e["ts"] for e in db.load_range(self.con, LO, HI)))


class OvernightCoverageTest(unittest.TestCase):
    """A real case: at home from 2026-08-15 18:37 to 08-16 07:20."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        db.insert(self.con, [
            ("loc:stay:overnight", LO - 323 * MIN, 762 * MIN,
             "location", "stay", {"place": "家", "named": True,
                                  "points": 22, "inferred": True}),
        ])

    def test_the_morning_half_counts_today(self):
        """440 of the 762 minutes fall on today."""
        got = analyze.coverage(DAY, self.con)
        self.assertGreaterEqual(got["B"], 435)
        self.assertLessEqual(got["B"], 445)

    def test_it_does_not_overflow_the_day(self):
        """The clamping was already there; reading more events must not make a
        day 1,200 minutes long."""
        got = analyze.coverage(DAY, self.con)
        self.assertEqual(got["A"] + got["B"] + got["off"] + got["C"]
                         + got["away"], 1440)

    def test_unknown_never_goes_negative(self):
        got = analyze.coverage(DAY, self.con)
        self.assertGreaterEqual(got["C"], 0)


if __name__ == "__main__":
    unittest.main()
