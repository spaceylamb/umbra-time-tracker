"""Which tier listening to an audiobook or music counts as in coverage.

The only phone data that knows content: it knows you were listening to chapter
IV of a particular audiobook,
not just "in the Books app". So it is tier A, alongside a Claude conversation
or a terminal command.

But it differs from the other tier-A sources: **listening runs alongside other
things**. So it ranks last — in a minute with screen evidence the screen wins;
only without it does this count.
"""

import sqlite3
import unittest

from umbra import analyze, db

DAY = "2026-08-14"
HOUR = 3_600_000


class MediaCoverageTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.lo, _ = analyze.day_bounds(DAY)

    def _add(self, source, kind, at_min, dur_ms=None, payload="{}"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,?,?,?)",
            (f"{source}:{at_min}:{dur_ms}", self.lo + at_min * 60_000, dur_ms,
             source, kind, payload))
        self.con.commit()

    def _media(self, at_min, dur_ms, suspect=False):
        self._add("media", "playing", at_min, dur_ms,
                  '{"title":"书","track":"第一章","suspect":%s}'
                  % ("true" if suspect else "false"))

    def cov(self):
        return analyze.coverage(DAY, self.con)

    def test_covers_its_whole_duration(self):
        self._media(600, 2 * HOUR)
        self.assertGreaterEqual(self.cov()["A"], 119)

    def test_is_content_level(self):
        """Knowing which chapter of which book — that is "what exactly you were doing"."""
        self._media(600, HOUR)
        self.assertGreater(self.cov()["A"], 0)
        self.assertEqual(self.cov()["B"], 0)

    def test_suspect_sessions_are_skipped(self):
        """Left playing while you walked away — the same problem as the iPhone
        foreground, handled the same way."""
        self._media(600, 3 * HOUR, suspect=True)
        self.assertEqual(self.cov()["A"], 0)

    def test_shrinks_the_unknown_bucket(self):
        before = self.cov()["C"]
        self._media(600, 2 * HOUR)
        self.assertLess(self.cov()["C"], before - 110)

    def test_tiers_still_sum_to_the_day(self):
        self._media(0, 3 * HOUR)
        self._add("app", "focus", 700, dur_ms=HOUR, payload='{"app":"Lark"}')
        c = self.cov()
        self.assertEqual(c["A"] + c["B"] + c["C"] + c["off"] + c["away"], 1440)

    def test_crossing_midnight_is_clipped(self):
        self._media(1430, 5 * HOUR)
        self.assertGreaterEqual(self.cov()["C"], 0)
        self.assertLessEqual(self.cov()["A"], 10)


if __name__ == "__main__":
    unittest.main()
