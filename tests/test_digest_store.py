"""**The text actually given to the model** has to be kept.

Found on 2026-08-18 while chasing "why did Gemini judge 09:03–09:30 a work
meeting on Zoom": all there was to go on was the model's output (spans,
evidence) and the window's 1,427 raw records — the layer between, the text
digest compressed those 1,427 into, **was thrown away after use**.

So "on what grounds" could only be worked backwards, from the sentences the
model cited to the raw records. Doing that turned up something else — `Speaker
2` in the evidence had been written as "me", 52 times over two days without
exception. A hard rule in the digest, or the model's own rewording?
**No way to tell, because the digest was gone.**

Two numbers have to be kept with it, or the layer is pointless:

    n_rec   records in the window **before** compression   1427
    text    what was **actually sent**, after compression   ~4,500 characters

With only text, the page could say "1,427 records in the window" while the
model never saw them — it saw the compressed result. Side by side, what was
lost becomes visible.

Three tables, apart from inferences and day_events: those two are the model's
**output**, this is its **input**. Mixed, "what the model said" and "what we
gave it" could no longer be told apart.
"""

import sqlite3
import unittest

from umbra import db, inference as inf


class DigestStoreTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def one(self, **kw):
        base = {"start": "09:00", "end": "09:30", "win": ("08:00", "10:30"),
                "text": "09:03 ▢ 看/开着: Zoom Meeting", "n": 1427}
        return {**base, **kw}

    def test_what_is_stored_reads_back_unchanged(self):
        inf.save_digests(self.con, "2026-08-17", "gemini", [self.one()])
        got = inf.load_digests(self.con, "2026-08-17")["gemini"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["text"], "09:03 ▢ 看/开着: Zoom Meeting")
        self.assertEqual(got[0]["start"], "09:00")
        self.assertEqual(got[0]["end"], "09:30")

    def test_the_count_before_compression_is_kept_too(self):
        """With only text, the page could say "1,427 records in the window" while
        the model never saw them. Both numbers together show what was lost."""
        inf.save_digests(self.con, "2026-08-17", "gemini", [self.one()])
        d = inf.load_digests(self.con, "2026-08-17")["gemini"][0]
        self.assertEqual(d["n"], 1427)
        self.assertEqual(d["chars"], len("09:03 ▢ 看/开着: Zoom Meeting"))

    def test_the_window_range_is_kept_too(self):
        """The target is 09:00–09:30, but the model saw 08:00–10:30. Without the
        window, a later change of context width could never be matched to how
        it ran."""
        d = self.save_and_get()
        self.assertEqual((d["wstart"], d["wend"]), ("08:00", "10:30"))

    def save_and_get(self, **kw):
        inf.save_digests(self.con, "2026-08-17", "gemini", [self.one(**kw)])
        return inf.load_digests(self.con, "2026-08-17")["gemini"][0]

    def test_rerunning_replaces_rather_than_piles_up(self):
        for t in ("第一版", "第二版"):
            inf.save_digests(self.con, "2026-08-17", "gemini", [self.one(text=t)])
        got = inf.load_digests(self.con, "2026-08-17")["gemini"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["text"], "第二版")

    def test_an_empty_batch_deletes_nothing(self):
        """The same rule as save / save_events: when a call fails or produces
        nothing, "delete then write" would silently clear the last result. One
        network blip is enough."""
        inf.save_digests(self.con, "2026-08-17", "gemini", [self.one()])
        self.assertEqual(inf.save_digests(self.con, "2026-08-17", "gemini", []), 0)
        self.assertEqual(len(inf.load_digests(self.con, "2026-08-17")["gemini"]), 1)

    def test_a_window_with_nothing_in_it_still_gets_a_row(self):
        """Empty text is not "never stored". The first means "nothing happened
        this half hour", the second "we have no record" — the page must keep
        them apart; it is this project's oldest rule."""
        d = self.save_and_get(text="", n=0)
        self.assertEqual(d["text"], "")
        self.assertEqual(d["n"], 0)
        self.assertEqual(len(inf.load_digests(self.con, "2026-08-17")["gemini"]), 1)

    def test_sorted_by_time(self):
        inf.save_digests(self.con, "2026-08-17", "gemini", [
            self.one(start="14:00", end="14:30"),
            self.one(start="09:00", end="09:30"),
            self.one(start="11:30", end="12:00")])
        got = inf.load_digests(self.con, "2026-08-17")["gemini"]
        self.assertEqual([d["start"] for d in got], ["09:00", "11:30", "14:00"])

    def test_models_do_not_interfere(self):
        inf.save_digests(self.con, "2026-08-17", "gemini", [self.one(text="G")])
        inf.save_digests(self.con, "2026-08-17", "opus", [self.one(text="O")])
        got = inf.load_digests(self.con, "2026-08-17")
        self.assertEqual(got["gemini"][0]["text"], "G")
        self.assertEqual(got["opus"][0]["text"], "O")

    def test_no_records_is_an_empty_dict(self):
        self.assertEqual(inf.load_digests(self.con, "2026-08-17"), {})

    def test_minutes_can_be_passed_directly(self):
        """The pass producing the digest works in minutes; forcing it through
        HH:MM and back only adds a round trip to get wrong."""
        inf.save_digests(self.con, "2026-08-17", "gemini",
                         [self.one(start=540, end=570, win=(480, 630))])
        d = inf.load_digests(self.con, "2026-08-17")["gemini"][0]
        self.assertEqual((d["a"], d["b"]), (540, 570))
        self.assertEqual(d["start"], "09:00")


class DigestsForTest(unittest.TestCase):
    """Which windows one calendar event spans.

    Windows are fixed half hours; events are not (09:03–09:30, 12:32–15:52). So
    **by overlap, not containment** — by containment, any event crossing a
    boundary would show "no digest", which looks exactly like truly not stored.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        inf.save_digests(self.con, "2026-08-17", "gemini", [
            {"start": h * 60, "end": h * 60 + 30, "win": (h * 60 - 60, h * 60 + 90),
             "text": f"窗口{h*60}", "n": 10}
            for h in (8, 9, 10)] + [
            {"start": 9 * 60 + 30, "end": 10 * 60, "win": (8 * 60 + 30, 11 * 60),
             "text": "窗口570", "n": 10}])

    def test_inside_one_window(self):
        got = inf.digests_for(self.con, "2026-08-17", "gemini", 543, 570)
        self.assertEqual([d["text"] for d in got], ["窗口540"])

    def test_across_two_windows(self):
        got = inf.digests_for(self.con, "2026-08-17", "gemini", 550, 590)
        self.assertEqual([d["text"] for d in got], ["窗口540", "窗口570"])

    def test_touching_ends_do_not_overlap(self):
        """An event ending at 570 must not take the window starting at 570 — one
        too many makes every event carry an extra window, as if the model had
        seen another half hour."""
        got = inf.digests_for(self.con, "2026-08-17", "gemini", 540, 570)
        self.assertEqual([d["text"] for d in got], ["窗口540"])

    def test_an_uncovered_stretch_returns_empty(self):
        self.assertEqual(
            inf.digests_for(self.con, "2026-08-17", "gemini", 1300, 1400), [])

    def test_another_model_finds_nothing(self):
        self.assertEqual(
            inf.digests_for(self.con, "2026-08-17", "opus", 543, 570), [])


if __name__ == "__main__":
    unittest.main()
