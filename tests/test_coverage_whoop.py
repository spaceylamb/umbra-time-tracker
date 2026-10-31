"""Where WHOOP events land in coverage.

Background: coverage()'s else branch was the fallback for content-level point
events like claude/git/zsh. With whoop added, a 7.5-hour sleep record fell into
the same branch and was marked **one minute** of TIER_A — the duration lost and
the class wrong (sleeping is not something done at the computer).

Sleep and workouts deserve their own tier: **away from the computer, but fully
known.**
That is the point of reading WHOOP — hours that were a hole become explained.
"""

import sqlite3
import unittest

from umbra import analyze, db

DAY = "2026-08-14"
HOUR = 3_600_000


class WhoopCoverageTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.lo, _ = analyze.day_bounds(DAY)

    def _add(self, source, kind, offset_min, dur_ms=None, payload="{}"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,?,?,?)",
            (f"{source}:{kind}:{offset_min}", self.lo + offset_min * 60_000,
             dur_ms, source, kind, payload))
        self.con.commit()

    def cov(self):
        return analyze.coverage(DAY, self.con)

    def test_sleep_covers_its_whole_duration(self):
        """A 7.5-hour sleep must not count as one minute."""
        self._add("whoop", "sleep", 30, dur_ms=7 * HOUR)
        self.assertGreaterEqual(self.cov()["off"], 7 * 60 - 1)

    def test_workout_covers_its_whole_duration(self):
        self._add("whoop", "workout", 600, dur_ms=HOUR)
        self.assertGreaterEqual(self.cov()["off"], 59)

    def test_sleep_is_not_counted_as_screen_time(self):
        """Sleeping is not "explained at the computer" — mixed into A it inflates
        coverage."""
        self._add("whoop", "sleep", 30, dur_ms=7 * HOUR)
        c = self.cov()
        self.assertEqual(c["A"], 0)
        self.assertEqual(c["B"], 0)

    def test_offscreen_time_reduces_the_unknown_bucket(self):
        """The whole point of reading WHOOP: the hole shrinks."""
        before = self.cov()["C"]
        self._add("whoop", "sleep", 30, dur_ms=7 * HOUR)
        self.assertLess(self.cov()["C"], before)

    def test_screen_activity_wins_over_offscreen(self):
        """Where they overlap, screen evidence wins.

        WHOOP's sleep boundaries are inferred and include "lying down, still on
        the phone or computer". A minute with real key presses must not be
        covered over by sleep.
        """
        self._add("whoop", "sleep", 60, dur_ms=2 * HOUR)
        self._add("zsh", "command", 90)
        c = self.cov()
        self.assertEqual(c["A"], 1, "the minute with a terminal command should be A")

    def test_cycle_and_recovery_do_not_cover_time(self):
        """A cycle is a whole-day summary and a recovery an instant score; neither
        says what a stretch of time was."""
        self._add("whoop", "cycle", 0, dur_ms=20 * HOUR)
        self._add("whoop", "recovery", 30)
        c = self.cov()
        self.assertEqual(c["off"], 0)
        self.assertEqual(c["A"], 0)

    def test_tiers_still_sum_to_the_day(self):
        self._add("whoop", "sleep", 0, dur_ms=6 * HOUR)
        self._add("zsh", "command", 500)
        c = self.cov()
        self.assertEqual(c["A"] + c["B"] + c["C"] + c["off"] + c["away"], 1440)

    def test_no_tier_goes_negative_when_spans_cross_midnight(self):
        """Durations crossing midnight are clamped to the day.

        app's mark() loop wrote minute keys ≥ 1440, which still counted as B, so
        A+B+away could exceed 1440 and C = 1440 - ... went negative. Measured on
        2026-08-14: C = -228 min.
        """
        self._add("app", "focus", 1439, dur_ms=24 * HOUR)   # almost entirely the next day
        self._add("idle", "idle", 0, dur_ms=10 * 60_000)
        c = self.cov()
        for k in ("A", "B", "C", "off", "away"):
            self.assertGreaterEqual(c[k], 0, f"{k} is negative: {c}")
        self.assertEqual(c["A"] + c["B"] + c["C"] + c["off"] + c["away"], 1440)

    def test_away_is_clipped_to_the_day(self):
        self._add("idle", "idle", 1430, dur_ms=10 * HOUR)
        self.assertLessEqual(self.cov()["away"], 10)

    def test_sleep_beats_idle(self):
        """This used to assert the opposite, on the grounds that "idle is the
        only certain sign of absence and must not be overridden by a sleep
        interval". The direction was reversed on 2026-08-16.

        The trouble was "certain". All idle is certain of is **no input on the
        keyboard**; what the person was doing it knows nothing about — it says
        "we don't know", not "nobody there". When it was made the two coincided:
        no other source could speak then.

        WHOOP saying you were asleep is knowledge. Letting "no keys pressed"
        override "asleep" lets the least informed source decide. Measured that
        night: idle 481 minutes, sleep 437 and at home 762 overlapping, and 886
        minutes removed from the denominator by idle.
        """
        self._add("whoop", "sleep", 0, dur_ms=2 * HOUR)
        self._add("idle", "idle", 0, dur_ms=HOUR)
        c = self.cov()
        self.assertEqual(c["away"], 0)
        self.assertGreaterEqual(c["off"], 119)

    def test_idle_still_counts_where_nothing_else_knows(self):
        """Demoted, not abolished: with no other source speaking, idle still
        excludes those minutes."""
        self._add("idle", "idle", 0, dur_ms=HOUR)
        self.assertGreaterEqual(self.cov()["away"], 59)


if __name__ == "__main__":
    unittest.main()
