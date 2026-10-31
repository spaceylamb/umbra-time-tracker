"""Incremental inference: call the model only for windows whose input changed.

From the 2026-08-19 open-source refactor. Re-running a day used to start at 48
model calls — while topping up data usually changes one or two windows' input.
The digest text was already stored first
(so "what was it given" can be answered later); now it does another job: **it
is the cache key**.
If last round's digest for a window is identical, its spans carry over and the
model is not called.

"Did last round answer this window" is judged by "is there a span in it". A
window whose call failed has none → retried automatically. The cost: a window
where the model really answered "nothing this half hour" is called again too —
"no answer" and "an empty answer" cannot be told apart here,
and calling once more beats caching a failure as a success.

When nothing in the day changed, the second pass (merge) is skipped too —
events stay as they were, zero calls.
"""

import os
import re
import tempfile
import unittest
from unittest import mock

from umbra import analyze, db, infer, inference as INF

DAY = "2026-08-14"

class IncrementalTest(unittest.TestCase):
    # Which prompt language is used comes from config. Tests must pin it —
    # otherwise the same code is green on a machine configured for zh and red
    # on CI with no config (seen).
    LANG = "en"

    def setUp(self):
        p = mock.patch.object(infer, "lang", return_value=self.LANG)
        p.start()
        self.addCleanup(p.stop)
        self.path = os.path.join(tempfile.mkdtemp(), "t.db")
        self.con = db.connect(self.path)
        self.lo, _ = analyze.day_bounds(DAY)
        # Data in two windows far apart (09:00 and 21:00 — contexts do not overlap)
        self._add("e1", 9 * 60)
        self._add("e2", 21 * 60)

    def _add(self, uid, minute, text="ls"):
        self.con.execute(
            "INSERT INTO events (uid, ts, source, kind, payload) "
            "VALUES (?,?,?,?,?)",
            (uid, self.lo + minute * 60000, "zsh", "command",
             '{"cmd": "%s"}' % text))
        self.con.commit()

    # Where the target window sits in the prompt: in both languages it is the
    # **first** HH:MM–HH:MM ("只判断 {a}–{b}" / "Judge only {a}–{b}"), with
    # the context range after it.
    # Pinning the wording bit once: the old English template said "Judge only the
    # … slot"; on 2026-08-20 the "the" was trimmed, a zh-configured machine
    # stayed green, and only CI caught it.
    TARGET_RE = re.compile(r"(\d\d:\d\d)–(\d\d:\d\d)")

    @classmethod
    def _fake(cls, calls):
        """Answer per the target window in the prompt — an answer outside it is
        clamped away by parse, no span in the window = treated as last round
        failing = called again next round, and the cache stops working."""
        def fake(prompt):
            calls.append(prompt)
            m = cls.TARGET_RE.search(prompt)
            if not m:                       # fail loudly when it cannot parse: an
                raise AssertionError(       # empty answer would read as "the model
                    "the fake model could not find the target window — did the prompt wording change?")  # answered nothing" and break the cache
            return ('[{"start":"%s","end":"%s","cat":"code","activity":"x"}]'
                    % (m.group(1), m.group(2)))
        return fake

    def _call(self):
        calls = []
        r = infer.run_day(self.con, DAY, "m", call=self._fake(calls))
        return calls, r

    def test_the_first_run_calls_only_windows_with_data(self):
        """Two events, each making the 3 windows around it non-empty (an event in
        a neighbour's context counts as content, existing TARGET_HAS_CONTENT
        behaviour) — the point is far fewer than 48."""
        calls, r = self._call()
        self.assertEqual(len(calls), 6)

    def test_unchanged_data_makes_no_calls_the_second_time(self):
        self._call()
        calls, r = self._call()
        self.assertEqual(calls, [])
        # The spans are still there — reuse is "carry the answer over", not "skip"
        self.assertTrue(r["spans"])

    def test_one_changed_window_recalls_only_that_area(self):
        self._call()
        self._add("e3", 21 * 60 + 3, "a new command")     # touches only the 21:00 window
        calls, _ = self._call()
        self.assertGreater(len(calls), 0)
        self.assertLess(len(calls), 48)
        for p in calls:                            # nothing around 09:00 is called again
            self.assertNotIn("09:00–09:30", p)

    def test_windows_that_failed_last_time_are_retried(self):
        def bad(prompt):
            raise RuntimeError("the network is down")
        infer.run_day(self.con, DAY, "m", call=bad)
        calls, _ = self._call()
        self.assertEqual(len(calls), 6)            # every window again

    def _covers(self, minute):
        return [s for s in INF.load(self.con, DAY).get("m", [])
                if INF._min(s["start"]) <= minute < INF._min(s["end"])]

    def test_a_failed_call_on_changed_input_keeps_the_old_answer(self):
        """A late recording landed, and re-calling the windows after it hit a
        dropped connection. save deletes the day and writes it again, and the
        failed windows were not in the new batch — so the page showed the day
        "done up to" that point. The old answer was judged on the old input, but
        it beats a blank, and the next round calls again (see the next test)."""
        self._call()
        self.assertTrue(self._covers(21 * 60 + 10))
        self._add("e3", 21 * 60 + 3, "a new command")

        def bad(prompt):
            raise RuntimeError("SSL: UNEXPECTED_EOF_WHILE_READING")
        r = infer.run_day(self.con, DAY, "m", call=bad)
        self.assertGreater(r["failed"], 0)
        self.assertTrue(self._covers(21 * 60 + 10))
        self.assertTrue(self._covers(9 * 60 + 10))     # the untouched band is still there

    def test_an_old_answer_kept_through_a_failure_is_called_again(self):
        """The digest is the cache key. Recorded as the new input for a failed
        window, the next round would see "input unchanged + an answer" and reuse
        the old answer as the new one for good."""
        self._call()
        self._add("e3", 21 * 60 + 3, "a new command")
        infer.run_day(self.con, DAY, "m",
                      call=lambda p: (_ for _ in ()).throw(RuntimeError("down")))
        calls, _ = self._call()
        self.assertTrue(any("21:00–21:30" in p for p in calls))

    def test_fresh_forces_everything(self):
        self._call()
        calls = []
        infer.run_day(self.con, DAY, "m", call=self._fake(calls), fresh=True)
        self.assertEqual(len(calls), 6)

    def test_an_unchanged_day_skips_merge_too(self):
        spans = self._call()[1]["spans"]
        infer.merge(self.con, DAY, spans, "m",
                    call=lambda p: '[{"start":"00:00","end":"24:00",'
                                   '"cat":"code","title":"t"}]')
        merged = []
        r = infer.run_day(self.con, DAY, "m",
                          call=lambda p: self.fail("the first pass must not be called"))
        out = infer.merge(self.con, DAY, r["spans"], "m",
                          call=lambda p: merged.append(p) or "[]",
                          dirty=r["fresh_calls"] > 0)
        self.assertEqual(merged, [])               # unchanged, nothing merged
        self.assertEqual(out[0]["title"], "t")     # the old events come back as they were
        self.assertTrue(INF.load_events(self.con, DAY)["m"])


class IncrementalZhTest(IncrementalTest):
    """The same assertions, run again with the zh template.

    The two templates are separate texts; testing one leaves the other without a
    net — and the English one was exactly that: a wording change broke the
    incremental cache while the local machine stayed green.
    """

    LANG = "zh"


if __name__ == "__main__":
    unittest.main()
