"""Running inference: digest → model → spans → calendar events.

This used to live in lab/run.py and lab/events.py, run and left in scratch,
which was cleared on 2026-08-18. Moving it into the repository split "calling
the model" from "handling what the model said": the latter is deterministic and
testable.

## One hard rule: store the digest first, then call the model

When a call fails, the input side must still be recorded. The other way round
(store only on success), a day that fails leaves nothing in the database —
and the whole reason the digests table exists is "what was it given" being
answerable later. The failed run is exactly the one that needs it.

## What the model says cannot be trusted whole

It puts spans into the context (the window is 09:00–09:30; it answers
08:30–09:45), wraps output in ```json fences, and now and then returns no JSON at
all. None of that may crash a whole day,
and **none of it may be swallowed silently** — swallowed, "the model said
nothing" and "we failed to catch it" look identical in the result.
"""

import unittest

from umbra import infer


class ParseTest(unittest.TestCase):
    OK = '[{"start":"09:00","end":"09:30","cat":"写代码","activity":"写测试"}]'

    def test_clean_json(self):
        self.assertEqual(len(infer.parse(self.OK, 540, 570)), 1)

    def test_json_in_a_json_fence(self):
        self.assertEqual(len(infer.parse(f"```json\n{self.OK}\n```", 540, 570)), 1)

    def test_json_in_a_plain_fence(self):
        self.assertEqual(len(infer.parse(f"```\n{self.OK}\n```", 540, 570)), 1)

    def test_chatter_before_and_after(self):
        """Models like to add "Sure, here are the results:" around the JSON."""
        txt = f"好的，这是结果：\n{self.OK}\n希望有帮助！"
        self.assertEqual(len(infer.parse(txt, 540, 570)), 1)

    def test_an_array_wrapped_in_an_object(self):
        txt = '{"spans": ' + self.OK + '}'
        self.assertEqual(len(infer.parse(txt, 540, 570)), 1)

    def test_no_json_at_all_raises(self):
        """Not an empty list — then "the model said nothing happened" and "we
        could not parse it" would merge. Raise, so the caller records the
        window as failed."""
        with self.assertRaises(ValueError):
            infer.parse("我不知道", 540, 570)


class ClampTest(unittest.TestCase):
    """The model sees an hour either side, so it puts spans into the context.

    The window is 09:00–09:30 and it answers 08:30–09:45 — that half hour
    **belongs to the 08:30 window**; accepting it would set two windows against
    each other, with two judgements for one stretch.
    """

    def test_clamped_to_the_target_window(self):
        got = infer.parse(
            '[{"start":"08:30","end":"09:45","cat":"写代码"}]', 540, 570)
        self.assertEqual((got[0]["start"], got[0]["end"]), ("09:00", "09:30"))

    def test_wholly_outside_the_window_is_dropped(self):
        got = infer.parse(
            '[{"start":"07:00","end":"08:00","cat":"睡觉"}]', 540, 570)
        self.assertEqual(got, [])

    def test_inside_the_window_is_kept_as_is(self):
        got = infer.parse(
            '[{"start":"09:05","end":"09:20","cat":"写代码"}]', 540, 570)
        self.assertEqual((got[0]["start"], got[0]["end"]), ("09:05", "09:20"))

    def test_a_bad_time_string_is_skipped_without_sinking_the_batch(self):
        got = infer.parse(
            '[{"start":"呃","end":"09:20","cat":"x"},'
            ' {"start":"09:05","end":"09:20","cat":"写代码"}]', 540, 570)
        self.assertEqual(len(got), 1)


class RunDayTest(unittest.TestCase):
    """Run one day, with a fake call — no network."""

    def setUp(self):
        import os
        import sqlite3
        import tempfile
        import time
        from umbra import db
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.db")
        self.con = db.connect(self.path)
        self.day = "2026-08-17"
        lo = int(time.mktime(time.strptime(self.day + " 00:00",
                                           "%Y-%m-%d %H:%M"))) * 1000
        db.insert(self.con, [
            (f"t:{i}", lo + (540 + i) * 60000, 0, "chrome", "visit",
             {"url": f"u{i}", "title": f"页面 {i}"}) for i in range(20)])
        self.sqlite3 = sqlite3
        self.addCleanup(self._clean)

    def _clean(self):
        import shutil
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call_ok(self, prompt):
        return '[{"start":"09:00","end":"09:30","cat":"查资料",' \
               '"activity":"翻网页","confidence":"high"}]'

    def test_only_windows_with_content_are_sent(self):
        """A day has 48 windows and perhaps a few with data. Calling the model
        on empty ones is waste — and it would certainly invent something."""
        seen = []
        infer.run_day(self.con, self.day, "fake",
                      call=lambda p: seen.append(p) or self.call_ok(p))
        self.assertLess(len(seen), 10, f"called the model for {len(seen)} windows")
        self.assertGreater(len(seen), 0)

    def test_the_digest_is_stored_first(self):
        """A failed call must still leave "what it was given". Storing only on
        success leaves nothing for the day that failed — the day that most
        needs it."""
        def boom(p):
            raise RuntimeError("the model is down")
        infer.run_day(self.con, self.day, "fake", call=boom)
        from umbra import inference as inf
        self.assertTrue(inf.load_digests(self.con, self.day).get("fake"),
                        "a failed model call left nothing behind")

    def test_one_failing_window_does_not_affect_the_others(self):
        n = [0]

        def flaky(p):
            n[0] += 1
            if n[0] == 1:
                raise RuntimeError("the first one failed")
            return self.call_ok(p)
        r = infer.run_day(self.con, self.day, "fake", call=flaky)
        self.assertTrue(r["spans"], "one failed window took the whole day down")
        self.assertEqual(r["failed"], 1)

    def test_failed_windows_are_reported(self):
        """Skipped silently, "the model said nothing this half hour" and "this
        half hour did not run" look the same in the result."""
        r = infer.run_day(self.con, self.day, "fake",
                          call=lambda p: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertGreater(r["failed"], 0)
        self.assertEqual(r["spans"], [])

    def test_spans_are_written_to_the_database(self):
        from umbra import inference as inf
        infer.run_day(self.con, self.day, "fake", call=self.call_ok)
        self.assertTrue(inf.load(self.con, self.day).get("fake"))

    def test_the_digest_keeps_the_count_before_compression(self):
        from umbra import inference as inf
        infer.run_day(self.con, self.day, "fake", call=self.call_ok)
        d = [x for x in inf.load_digests(self.con, self.day)["fake"] if x["n"]]
        self.assertTrue(d)
        self.assertGreater(d[0]["n"], 0)


class MergePromptTest(unittest.TestCase):
    """The second pass: spans into calendar events. No windows — the input is
    already two orders of magnitude smaller."""

    SPANS = [{"start": "09:00", "end": "09:30", "cat": "写代码", "activity": "A"},
             {"start": "09:30", "end": "10:00", "cat": "写代码", "activity": "B"}]

    def test_the_prompt_carries_every_span(self):
        p = infer.merge_prompt("2026-08-17", self.SPANS)
        self.assertIn("09:00", p)
        self.assertIn("10:00", p)
        self.assertIn("2026-08-17", p)

    def test_the_model_is_asked_to_cite_its_sources(self):
        """Without from, "which spans was this event made of" cannot be checked,
        and the whole drill-down breaks."""
        self.assertIn("from", infer.merge_prompt("2026-08-17", self.SPANS))

    def test_no_spans_means_no_call(self):
        called = []
        got = infer.merge(None, "2026-08-17", [], "fake",
                          call=lambda p: called.append(p) or "[]")
        self.assertEqual(got, [])
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
