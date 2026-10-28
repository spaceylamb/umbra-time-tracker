"""What speech counts as in coverage and segmentation.

Speech is **content-level** evidence (tier A): what you said that minute, not
"which app was open".
What sets it apart from the other tier-A sources is when it covers — meetings,
meals, walks, conversations, all blind spots for the screen sources. That is
the reason to read it.

⚠️ Mark **only the minutes actually spoken**, never the whole recording's span.
In one measured 56-minute recording only 1.6 minutes were speech (3%); marking
all 56 as A would claim the other 54 minutes are explained — the inflation the
README's "share that can be named" warns against ("time known only by app is
exactly the blind spot this measure exposes").
"""

import sqlite3
import unittest

from umbra import analyze, db, segment

DAY = "2026-08-14"
MIN = 60_000


class PlaudCoverageTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.lo, _ = analyze.day_bounds(DAY)

    def _speech(self, at_min, dur_ms, text="说了点什么"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES (?,?,?,?,?,?)",
            (f"plaud:f:{at_min}", self.lo + at_min * MIN, dur_ms,
             "plaud", "speech",
             '{"text":"%s","speaker":"Speaker 1","file_id":"f"}' % text))
        self.con.commit()

    def cov(self):
        return analyze.coverage(DAY, self.con)

    def test_speech_is_content_level(self):
        self._speech(600, 5 * MIN)
        self.assertGreater(self.cov()["A"], 0)
        self.assertEqual(self.cov()["B"], 0)

    def test_covers_its_own_duration(self):
        self._speech(600, 30 * MIN)
        self.assertGreaterEqual(self.cov()["A"], 29)

    def test_only_the_spoken_minutes_count(self):
        """The real shape: 56 seconds + 26 + 12 spoken in a 56-minute recording.

        The three land on minutes 0, 55 and 56, so at most 3 minutes of A —
        never the 54 minutes between, silently, for being "in the same recording".
        """
        self._speech(600, 55_900)
        self._speech(655, 26_000)
        self._speech(656, 12_260)
        self.assertLessEqual(self.cov()["A"], 3)

    def test_a_single_utterance_still_counts(self):
        """The only evidence for that hour may be one sentence; short is no
        reason to drop it."""
        self._speech(600, 20_000)
        self.assertGreaterEqual(self.cov()["A"], 1)

    def test_shrinks_the_unknown_bucket(self):
        before = self.cov()["C"]
        self._speech(600, 30 * MIN)
        self.assertLess(self.cov()["C"], before - 25)

    def test_tiers_still_sum_to_the_day(self):
        self._speech(100, 40 * MIN)
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload) "
            "VALUES ('app:1',?,?,'app','focus','{\"app\":\"Lark\"}')",
            (self.lo + 700 * MIN, 60 * MIN))
        self.con.commit()
        c = self.cov()
        self.assertEqual(c["A"] + c["B"] + c["C"] + c["off"] + c["away"], 1440)

    def test_crossing_midnight_is_clipped(self):
        self._speech(1430, 60 * MIN)
        self.assertGreaterEqual(self.cov()["C"], 0)
        self.assertLessEqual(self.cov()["A"], 10)


class PlaudSegmentTest(unittest.TestCase):
    """The segmentation layer. "How much silence breaks a segment" lives here —
    POLICY['merge_gap_sec'], changeable and re-runnable any time, with no re-import."""

    def _ev(self, at_ms, dur_ms, text="话"):
        return {"ts": at_ms, "dur_ms": dur_ms, "source": "plaud", "kind": "speech",
                "text": text, "speaker": "Speaker 1", "file_id": "f"}

    def test_has_its_own_bucket(self):
        """Not the 'terminal' of the else branch — that would merge speech and zsh
        commands into one segment."""
        self.assertEqual(segment.bucket_key(self._ev(0, 1000)), "talk")

    def test_speaker_does_not_split_the_bucket(self):
        """Speaker 1/2 are per-recording labels; across recordings they are not
        the same person. Bucketing by them would invent identities."""
        a = self._ev(0, 1000)
        b = {**self._ev(0, 1000), "speaker": "Speaker 2"}
        self.assertEqual(segment.bucket_key(a), segment.bucket_key(b))

    def test_duration_is_trusted(self):
        """dur_ms is real speaking time, not tab lifetime like Chrome's.
        Not trusted, end_of() returns the bare ts and a 56-second sentence
        collapses to an instant."""
        self.assertIn("plaud", segment.trusted_duration_sources())
        self.assertEqual(segment.end_of(self._ev(1_000, 55_900)), 56_900)

    def test_nearby_utterances_merge_into_one_segment(self):
        base = 1_700_000_000_000
        segs = segment.segment([self._ev(base, 30_000),
                                self._ev(base + 60_000, 30_000),
                                self._ev(base + 120_000, 30_000)])
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["key"], "talk")

    def test_a_long_silence_splits_them(self):
        base = 1_700_000_000_000
        gap = (segment.POLICY["merge_gap_sec"] + 60) * 1000
        segs = segment.segment([self._ev(base, 30_000),
                                self._ev(base + gap, 30_000)])
        self.assertEqual(len(segs), 2)

    def test_a_lone_utterance_survives(self):
        """should_keep used to require "≥2 events or claude/git/zsh". A lone
        sentence may be the only evidence for that stretch; dropping it would
        make reading speech pointless."""
        segs = segment.segment([self._ev(1_700_000_000_000, 30_000)])
        self.assertEqual(len(segs), 1)


if __name__ == "__main__":
    unittest.main()
