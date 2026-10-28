"""How location counts in coverage — each of the three kinds differently.

**transit → A (content level)**: "driving from home to work" is what you were
doing, the same level as "writing code". Commuting time used to be a hole.

**stay → B (container level)**: you were at home, not what you did there — the
same kind of knowledge as "Lark was in front".

**gap → not counted**: the data stopped and you could be anywhere. Not knowing
is not knowing.
"""

import sqlite3
import unittest

from umbra import analyze, db

DAY = "2026-08-14"
HOUR = 3_600_000


class LocationCoverageTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.lo, _ = analyze.day_bounds(DAY)

    def _add(self, kind, at_min, dur_ms, payload="{}"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,'location',?,?)",
            (f"loc:{kind}:{at_min}", self.lo + at_min * 60_000, dur_ms,
             kind, payload))
        self.con.commit()

    def cov(self):
        return analyze.coverage(DAY, self.con)

    def test_transit_is_content_level(self):
        """"Driving from home to work" is what you were doing."""
        self._add("transit", 480, HOUR,
                  '{"from":"家","to":"公司","mode":"driving"}')
        self.assertGreaterEqual(self.cov()["A"], 59)

    def test_stay_is_container_level(self):
        """At home is known; what you did there is not."""
        self._add("stay", 600, 2 * HOUR, '{"place":"家","named":true}')
        c = self.cov()
        self.assertGreaterEqual(c["B"], 119)
        self.assertEqual(c["A"], 0)

    def test_gap_counts_as_nothing(self):
        """The data stopped — those hours stay in "can't tell"."""
        before = self.cov()["C"]
        self._add("gap", 600, 3 * HOUR, '{"reason":"no points"}')
        self.assertEqual(self.cov()["C"], before)

    def test_screen_evidence_wins_over_stay(self):
        """At home plus a terminal command → that minute is A, not covered over as
        B by "at home"."""
        self._add("stay", 600, HOUR, '{"place":"家","named":true}')
        self.con.execute(
            "INSERT INTO events (uid, ts, source, kind, payload) "
            "VALUES ('z', ?, 'zsh', 'command', '{}')", (self.lo + 610 * 60_000,))
        self.con.commit()
        self.assertEqual(self.cov()["A"], 1)

    def test_shrinks_the_unknown_bucket(self):
        before = self.cov()["C"]
        self._add("stay", 600, 3 * HOUR, '{"place":"公司","named":true}')
        self.assertLess(self.cov()["C"], before - 170)

    def test_tiers_still_sum_to_the_day(self):
        self._add("stay", 0, 6 * HOUR, '{"place":"家"}')
        self._add("transit", 480, HOUR, '{"from":"家","to":"公司"}')
        self._add("stay", 540, 5 * HOUR, '{"place":"公司"}')
        c = self.cov()
        self.assertEqual(c["A"] + c["B"] + c["C"] + c["off"] + c["away"], 1440)

    def test_crossing_midnight_is_clipped(self):
        self._add("stay", 1430, 8 * HOUR, '{"place":"家"}')
        c = self.cov()
        self.assertGreaterEqual(c["C"], 0)
        self.assertLessEqual(c["B"], 10)


if __name__ == "__main__":
    unittest.main()
