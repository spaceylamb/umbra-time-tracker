"""Activity spans inferred by a model — storing them, expanding them into
slots, and measuring agreement between models.

What the 2026-08-17 experiment found: letting a model read **every source for
the same stretch** to judge "what was going on" is far more accurate than
blocks.py's priority ladder. The hardest number is a ground truth — 17 slots
where speech matched an agent prompt sentence for sentence (moments we know
the person was dictating to an AI): the rules got **none** right; three models
got 13 / 11 / 10.

The rules were wrong for a reason: talk outranks code on the ladder, so any
speech means "talking". It looks at **which source is active**, never **what
was said** — and what was said was
"merge this branch into main". The same source and the same event mean opposite
things in different contexts, which a constant priority table cannot express.

Three design decisions, all so that inference **does not pass for observation**:

1. **Inferences stay out of the events table.** db.py's founding rule is "point
   events only" — every row in events is something a source really recorded.
   Mixed in, "measured" and "guessed" could never be told apart again. So they
   get their own table, with the model's name; re-running with another model or
   prompt leaves raw data untouched.

2. **The rules are kept, not deleted.** They are a second, independent
   estimate. Where the two disagree is where to look — the same idea as the ⚠
   conflict marker. All-AI, there would be no way to find where the AI is wrong.

3. **Agreement is measured between models only, not with the rules.** Mixing
   them stirs two questions into one number: "do the AIs agree" (is the
   evidence firm) and "do the AIs and rules agree" (how different the methods
   are). A 3/4 could mean either.
"""

import sqlite3
import unittest

from umbra import db, inference as inf

SPAN = {"start": "09:00", "end": "10:00", "cat": "写代码",
        "activity": "和 AI 协作调试", "confidence": "high",
        "with": "对AI说话", "evidence": ["09:05 把这个分支合进 main"]}


def span(a, b, cat="写代码", **kw):
    return {"start": a, "end": b, "cat": cat, "activity": "x", **kw}


class ExpandTest(unittest.TestCase):
    """Spans → 288 slots. This step **must be deterministic**.

    The model says "22:00–22:08 on a video call with family"; cutting slots is
    arithmetic. Handed to a model it would cut on 5-minute boundaries
    differently each time — and "today versus yesterday" would become
    impossible.
    """

    def test_a_day_is_288_slots(self):
        self.assertEqual(len(inf.expand([])), inf.BLOCKS)

    def test_a_span_fills_the_slots_it_covers(self):
        cells = inf.expand([span("09:00", "09:20")])
        self.assertEqual([c["cat"] if c else None for c in cells[108:112]],
                         ["写代码"] * 4)
        self.assertIsNone(cells[112])

    def test_uncovered_slots_are_empty(self):
        """Empty is empty. A stretch the model said nothing about must not be
        filled from its neighbours — that would be inventing for it."""
        self.assertIsNone(inf.expand([span("09:00", "09:20")])[0])

    def test_less_than_a_slot_still_takes_one(self):
        """Something at 22:03–22:04 really happened and must not vanish for
        being under five minutes."""
        self.assertIsNotNone(inf.expand([span("22:03", "22:04")])[264])

    def test_the_first_is_not_overwritten_by_a_later_one(self):
        """Neighbouring windows' targets meet end to end and should not overlap.
        If they do, the model crossed a boundary — keep the first, and make it
        detectable (see the next test)."""
        cells = inf.expand([span("09:00", "10:00", "写代码"),
                            span("09:30", "10:00", "娱乐")])
        self.assertEqual(cells[114]["cat"], "写代码")

    def test_an_out_of_range_span_is_recorded(self):
        """Clamping and clamping silently are different. A model answering 25:00
        has miscalculated, and correcting it quietly would hide that for good —
        the kind of handling this project avoids most."""
        cells, bad = inf.expand([span("23:00", "25:00")], report=True)
        self.assertTrue(bad)
        self.assertIsNotNone(cells[287], "clamped, it should reach the day's last slot")

    def test_a_bad_time_string_does_not_crash(self):
        cells, bad = inf.expand([{"start": "??", "end": "10:00", "cat": "x"}],
                                report=True)
        self.assertTrue(bad)
        self.assertEqual(cells, [None] * inf.BLOCKS)


class AgreementTest(unittest.TestCase):
    def test_all_agree(self):
        a = inf.expand([span("09:00", "10:00", "写代码")])
        r = inf.agreement([a, a, a])
        self.assertEqual((r[108]["n"], r[108]["tot"]), (3, 3))
        self.assertEqual(r[108]["same"], "写代码")

    def test_disagreement(self):
        a = inf.expand([span("09:00", "10:00", "写代码")])
        b = inf.expand([span("09:00", "10:00", "娱乐")])
        self.assertEqual(inf.agreement([a, a, b])[108]["n"], 2)

    def test_a_silent_model_still_counts(self):
        """One model saying "coding" and another covering nothing is not 1/1
        agreement — that would show "only one model spoke" as confidently as
        "all three agree"."""
        a = inf.expand([span("09:00", "10:00", "写代码")])
        r = inf.agreement([a, inf.expand([])])
        self.assertEqual((r[108]["n"], r[108]["tot"]), (1, 2))

    def test_no_models_at_all_does_not_crash(self):
        self.assertEqual(len(inf.agreement([])), inf.BLOCKS)


class StoreTest(unittest.TestCase):
    """Inferences get a table of their own."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def test_saved_spans_read_back(self):
        inf.save(self.con, "2026-08-16", "gemini-3.7-flash", [SPAN])
        got = inf.load(self.con, "2026-08-16")
        self.assertEqual(list(got), ["gemini-3.7-flash"])
        s = got["gemini-3.7-flash"][0]
        # Since 2026-08-19 both save and load normalise to keys (taxonomy);
        # Chinese is only a display name
        self.assertEqual(s["cat"], "code")
        self.assertEqual(s["evidence"], ["09:05 把这个分支合进 main"])

    def test_rerunning_a_model_replaces_rather_than_adds(self):
        """Re-run after a prompt change, the database holds the new result, not

        new and old mixed. The construction matters: the second run must
        **start differently**. With 09:00 both times, INSERT OR REPLACE replaces
        on the key by itself and the DELETE makes no difference — tested but
        proving nothing (a mutation test caught it).

        What the DELETE really prevents is **orphans**: re-run with different
        boundaries, the old 09:00 row would stay and one stretch would have two
        contradicting answers — the same hole as replace_range's false
        "beach → beach" trip.
        """
        inf.save(self.con, "2026-08-16", "m", [SPAN])
        inf.save(self.con, "2026-08-16", "m", [span("14:00", "15:00", "娱乐")])
        got = inf.load(self.con, "2026-08-16")["m"]
        self.assertEqual(len(got), 1, f"the old row was not deleted and became an orphan: {got}")
        self.assertEqual(got[0]["cat"], "fun")

    def test_models_do_not_affect_each_other(self):
        inf.save(self.con, "2026-08-16", "a", [SPAN])
        inf.save(self.con, "2026-08-16", "b", [span("12:00", "13:00", "吃饭")])
        self.assertEqual(sorted(inf.load(self.con, "2026-08-16")), ["a", "b"])

    def test_days_do_not_affect_each_other(self):
        inf.save(self.con, "2026-08-16", "a", [SPAN])
        inf.save(self.con, "2026-08-15", "a", [span("12:00", "13:00", "吃饭")])
        self.assertEqual(inf.load(self.con, "2026-08-15")["a"][0]["cat"], "meal")

    def test_an_empty_batch_clears_nothing(self):
        """A failed collection or call must never erase the last result — the
        same reason as replace_range; one network blip is enough."""
        inf.save(self.con, "2026-08-16", "m", [SPAN])
        inf.save(self.con, "2026-08-16", "m", [])
        self.assertTrue(inf.load(self.con, "2026-08-16")["m"])

    def test_never_written_to_events(self):
        """The whole reason this table exists. Every row in events is something
        a source really recorded; mixed in, measured and guessed could never be
        told apart."""
        inf.save(self.con, "2026-08-16", "m", [SPAN])
        n = self.con.execute("select count(*) from events").fetchone()[0]
        self.assertEqual(n, 0)

    def test_no_inference_returns_empty(self):
        self.assertEqual(inf.load(self.con, "2026-08-16"), {})


class DayEventTest(unittest.TestCase):
    """The second pass: a day's activity spans → calendar events.

    The first pass answers "what happened this half hour", close to the
    evidence, 1–30 minutes long (**30 is a ceiling imposed by the window, not
    judged**). The second answers "what was this day made of".

    It needs no window: its input is the first pass's output, already two
    orders of magnitude smaller —

        raw data      207,000 characters   must be windowed
        first pass    ~10,000 characters   fits at once (measured prompt 4,190)

    so it sees the whole day at once, with no window and so no 30-minute
    ceiling. Measured on 8-16: 17 events, the longest 390 minutes (a night's
    sleep across 13 windows).

    ⚠️ from must be kept: events are **composed**, and without the source layer
       nothing can be checked — dropping sources while merging has bitten more
       than once.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    EV = {"start": "14:25", "end": "15:21", "cat": "查资料",
          "title": "网购户外装备",
          "detail": "几家店比价后下单",
          "from": ["14:25–14:30", "14:30–15:00", "15:00–15:21"]}

    def test_saved_events_read_back(self):
        inf.save_events(self.con, "2026-08-16", "m", [self.EV])
        got = inf.load_events(self.con, "2026-08-16")["m"]
        self.assertEqual(got[0]["title"], "网购户外装备")
        self.assertEqual(len(got[0]["from"]), 3)

    def test_an_event_can_run_far_beyond_thirty_minutes(self):
        """The **whole reason** for the second pass. First-pass spans are capped
        by the 30-minute window, so a night's sleep would be cut into 13."""
        inf.save_events(self.con, "2026-08-16", "m",
                        [{"start": "00:00", "end": "06:30", "cat": "睡觉",
                          "title": "睡觉", "from": ["00:00–00:30"]}])
        e = inf.load_events(self.con, "2026-08-16")["m"][0]
        self.assertEqual(e["b"] - e["a"], 390)

    def test_rerunning_replaces_rather_than_adds(self):
        """The start must differ for the DELETE to be tested — with the same
        start the key replaces it anyway."""
        inf.save_events(self.con, "2026-08-16", "m", [self.EV])
        inf.save_events(self.con, "2026-08-16", "m",
                        [{**self.EV, "start": "09:00", "end": "10:00"}])
        self.assertEqual(len(inf.load_events(self.con, "2026-08-16")["m"]), 1)

    def test_saving_one_model_does_not_clear_another(self):
        """Each of three models runs its own second pass; their different
        divisions are what gets compared. A DELETE without the model would make
        the later erase the earlier entirely — looking on the page as if that
        model "never ran" (a mutation test caught it)."""
        inf.save_events(self.con, "2026-08-16", "a", [self.EV])
        inf.save_events(self.con, "2026-08-16", "b",
                        [{**self.EV, "start": "09:00", "end": "10:00"}])
        got = inf.load_events(self.con, "2026-08-16")
        self.assertEqual(sorted(got), ["a", "b"])

    def test_an_empty_batch_clears_nothing(self):
        inf.save_events(self.con, "2026-08-16", "m", [self.EV])
        inf.save_events(self.con, "2026-08-16", "m", [])
        self.assertTrue(inf.load_events(self.con, "2026-08-16")["m"])

    def test_never_written_to_events(self):
        inf.save_events(self.con, "2026-08-16", "m", [self.EV])
        self.assertEqual(
            self.con.execute("select count(*) from events").fetchone()[0], 0)

    def test_stored_apart_from_the_first_pass(self):
        """The two granularities differ entirely; one table would make spans and
        events impossible to tell apart."""
        inf.save(self.con, "2026-08-16", "m", [SPAN])
        inf.save_events(self.con, "2026-08-16", "m", [self.EV])
        self.assertEqual(len(inf.load(self.con, "2026-08-16")["m"]), 1)
        self.assertEqual(len(inf.load_events(self.con, "2026-08-16")["m"]), 1)

    def test_no_events_returns_empty(self):
        self.assertEqual(inf.load_events(self.con, "2026-08-16"), {})


if __name__ == "__main__":
    unittest.main()
