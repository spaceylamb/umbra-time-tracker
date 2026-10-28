"""How iPhone use counts in coverage.

A real classification decision, not one more if:

Time on the phone is **not at the computer**, but it is **not sleep or exercise**
either — the "known off-screen" kind. It is screen time on another screen, and
what we know is as coarse as knowledgeC: an app name (`com.tencent.xin`), no
content (who, about what).

So it is **tier B (container level)**: which app, not what was being done in it.
"""

import sqlite3
import unittest

from umbra import analyze, db

DAY = "2026-08-14"
HOUR = 3_600_000


class IphoneCoverageTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.lo, _ = analyze.day_bounds(DAY)

    def _add(self, source, kind, at_min, dur_ms=None, payload="{}"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,?,?,?)",
            (f"{source}:{kind}:{at_min}:{dur_ms}", self.lo + at_min * 60_000,
             dur_ms, source, kind, payload))
        self.con.commit()

    def cov(self):
        return analyze.coverage(DAY, self.con)

    def test_covers_its_whole_duration(self):
        """Marking only one minute per point event would record two hours on the
        phone as one minute."""
        self._add("iphone", "focus", 600, dur_ms=2 * HOUR,
                  payload='{"bundle":"com.tencent.xin"}')
        self.assertGreaterEqual(self.cov()["B"], 119)

    def test_is_container_level_not_content_level(self):
        """Knowing only the bundle cannot say what happened inside it — not tier A."""
        self._add("iphone", "focus", 600, dur_ms=HOUR,
                  payload='{"bundle":"com.tencent.xin"}')
        self.assertEqual(self.cov()["A"], 0)

    def test_is_not_offscreen(self):
        """Known off-screen is sleep and exercise. Using a phone is screen time, on
        another screen."""
        self._add("iphone", "focus", 600, dur_ms=HOUR,
                  payload='{"bundle":"com.tencent.xin"}')
        self.assertEqual(self.cov()["off"], 0)

    def test_shrinks_the_unknown_bucket(self):
        """The reason to read it at all: the hole after the laptop closes shrinks."""
        before = self.cov()["C"]
        self._add("iphone", "focus", 600, dur_ms=3 * HOUR,
                  payload='{"bundle":"com.tencent.xin"}')
        self.assertLess(self.cov()["C"], before - 170)

    def test_mac_content_evidence_wins(self):
        """In a minute with content evidence on the Mac, that minute is A, not
        covered over as B by the phone."""
        self._add("iphone", "focus", 600, dur_ms=HOUR,
                  payload='{"bundle":"com.tencent.xin"}')
        self._add("zsh", "command", 610)
        self.assertEqual(self.cov()["A"], 1)

    def test_sleep_still_wins_over_phone(self):
        """WHOOP says asleep, the phone says in use — scrolling at night is real.

        The screen evidence wins: this minute is B (using the phone), not
        off-screen sleep.
        """
        self._add("whoop", "sleep", 600, dur_ms=2 * HOUR)
        self._add("iphone", "focus", 630, dur_ms=10 * 60_000,
                  payload='{"bundle":"com.tencent.xin"}')
        c = self.cov()
        self.assertGreaterEqual(c["B"], 9)

    def test_tiers_still_sum_to_the_day(self):
        self._add("iphone", "focus", 0, dur_ms=5 * HOUR,
                  payload='{"bundle":"com.x"}')
        self._add("whoop", "sleep", 700, dur_ms=3 * HOUR)
        c = self.cov()
        self.assertEqual(c["A"] + c["B"] + c["C"] + c["off"] + c["away"], 1440)

    def test_crossing_midnight_is_clipped(self):
        self._add("iphone", "focus", 1430, dur_ms=5 * HOUR,
                  payload='{"bundle":"com.x"}')
        c = self.cov()
        self.assertLessEqual(c["B"], 10)
        self.assertGreaterEqual(c["C"], 0)


class SuspectSessionsTest(unittest.TestCase):
    """A suspect phone session does not count towards coverage.

    "Three hours of Gmail in front" is usually a phone in a pocket with the
    screen long locked. Counting it as B would claim we know what those hours
    were — we don't. Leaving those minutes as "can't tell" is honest: not
    knowing is not knowing, and the measure must not improve on hallucination.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.lo, _ = analyze.day_bounds(DAY)

    def _add(self, at_min, dur_ms, suspect):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,'iphone','focus',?)",
            (f"i:{at_min}", self.lo + at_min * 60_000, dur_ms,
             '{"bundle":"com.x","suspect":%s}' % ("true" if suspect else "false")))
        self.con.commit()

    def test_suspect_session_is_not_counted(self):
        self._add(600, 3 * HOUR, suspect=True)
        self.assertEqual(analyze.coverage(DAY, self.con)["B"], 0)

    def test_trustworthy_session_is_counted(self):
        self._add(600, 20 * 60_000, suspect=False)
        self.assertGreaterEqual(analyze.coverage(DAY, self.con)["B"], 19)

    def test_suspect_minutes_stay_unknown(self):
        before = analyze.coverage(DAY, self.con)["C"]
        self._add(600, 3 * HOUR, suspect=True)
        self.assertEqual(analyze.coverage(DAY, self.con)["C"], before)

    def test_missing_flag_is_treated_as_trustworthy(self):
        """Old data has no such field — a missing field must not drop a whole batch."""
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES ('old', ?, ?, 'iphone', 'focus', '{\"bundle\":\"com.x\"}')",
            (self.lo + 600 * 60_000, 20 * 60_000))
        self.con.commit()
        self.assertGreaterEqual(analyze.coverage(DAY, self.con)["B"], 19)


if __name__ == "__main__":
    unittest.main()
