"""The one layer that calls a model: does this still need you, in one sentence.

Its rules come from the first batch of voice-dispatched tasks, where 18 of 22
verdicts were "already done": proposals not anchored in evidence, and no gate
to say no.

1. No inventing. An akey the model returns that was not in the input is dropped.
2. Asked once. A suggestion is keyed on its deterministic akey, never on the
   model's title, and an akey asked about is never sent again — not when you
   have not ruled on it, not when the model silently skipped it (519 sent, 457
   answered, once).
3. Same input, no call.
4. A failed call writes nothing: one network blip must not erase last round's.
"""

import json
import os
import tempfile
import unittest

from umbra import db
from umbra.plugins.review import detect, judge


def _cands():
    return [
        {"akey": "page:dash.example.com", "kind": "page", "n": 48,
         "name": "every 2-3 days", "median_gap": 2, "jitter": 1,
         "last": "2026-08-25", "next_expected": "2026-08-27",
         "evidence": [{"day": "2026-08-25", "text": "Daily Report"}]},
        {"akey": "mail:HN Daily Digest", "kind": "mail", "n": 90,
         "name": "daily", "median_gap": 1, "jitter": 0,
         "last": "2026-08-26", "next_expected": "2026-08-27",
         "evidence": [{"day": "2026-08-26", "text": "Daily Digest"}]},
    ]


def _reply(items):
    return "```json\n" + json.dumps(items) + "\n```"


def _item(akey, lever="push", needs_you=False):
    return {"akey": akey, "title": f"you keep doing {akey}", "lever": lever,
            "needs_you": needs_you, "why": "it does not need you"}


class Base(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        judge.ensure(self.con)
        self.calls = []

    def run_judge(self, cands=None, reply=None, period="2026-M08", **kw):
        def call(prompt):
            self.calls.append(prompt)
            if isinstance(reply, Exception):
                raise reply
            return reply(prompt) if callable(reply) else reply
        return judge.run(self.con, cands or _cands(), period=period, model="test",
                         call=call, **kw)


class NoInventingTest(Base):
    def test_an_akey_not_asked_about_is_dropped(self):
        got = self.run_judge(reply=_reply([_item("page:dash.example.com"),
                                           _item("page:made-up")]))
        self.assertEqual([g["akey"] for g in got], ["page:dash.example.com"])

    def test_only_invented_answers_leave_nothing(self):
        self.assertEqual(self.run_judge(reply=_reply([_item("page:made-up")])), [])

    def test_no_json_raises_rather_than_meaning_none(self):
        with self.assertRaises(RuntimeError):
            self.run_judge(reply="Looked at these, nothing special.")

    def test_a_failed_call_keeps_last_rounds(self):
        self.run_judge(reply=_reply([_item("mail:HN Daily Digest", "unsubscribe")]))
        more = _cands() + [dict(_cands()[0], akey="auth:google", kind="auth")]
        with self.assertRaises(RuntimeError):
            self.run_judge(more, reply=OSError("network"), period="2026-M09")
        self.assertEqual(len(judge.load(self.con)), 1)


class VerdictTest(Base):
    def setUp(self):
        super().setUp()
        self.run_judge(reply=_reply([_item("page:dash.example.com")]))
        self.sid = judge.load(self.con)[0]["id"]

    def test_a_ruled_suggestion_is_not_open(self):
        judge.verdict(self.con, self.sid, "not_worth")
        self.assertEqual(judge.load(self.con), [])

    def test_what_you_said_is_kept(self):
        judge.verdict(self.con, self.sid, "already", "did it last week")
        self.assertEqual(judge.by_id(self.con, self.sid)["verdict_note"], "did it last week")

    def test_an_unknown_verdict_is_refused(self):
        with self.assertRaises(ValueError):
            judge.verdict(self.con, self.sid, "meh")

    def test_an_unknown_id_is_refused(self):
        with self.assertRaises(LookupError):
            judge.verdict(self.con, 999, "useful")

    def test_the_tally(self):
        judge.verdict(self.con, self.sid, "useful")
        self.assertEqual(judge.hit_rate(self.con), {"useful": 1})


class OnceTest(Base):
    def test_same_input_no_call(self):
        self.run_judge(reply=_reply([_item("page:dash.example.com")]))
        self.run_judge(reply=_reply([]))
        self.assertEqual(len(self.calls), 1)

    def test_what_the_model_skipped_is_not_sent_again(self):
        self.run_judge(reply=_reply([_item("page:dash.example.com")]))
        self.assertEqual(judge.omitted(self.con), 1)
        more = _cands() + [dict(_cands()[0], akey="auth:google", kind="auth")]
        self.run_judge(more, reply=_reply([]), period="2026-M09")
        self.assertNotIn("HN Daily Digest", self.calls[-1])
        self.assertIn("auth:google", self.calls[-1])

    def test_nothing_new_returns_what_is_still_open(self):
        self.run_judge(reply=_reply([_item("page:dash.example.com")]))
        got = self.run_judge(reply=_reply([]), period="2026-M09")
        self.assertEqual([g["akey"] for g in got], ["page:dash.example.com"])

    def test_batches_one_failing_is_reported_and_retried(self):
        many = [dict(_cands()[0], akey=f"page:s{i}.example.com") for i in range(judge.BATCH + 5)]
        logs = []

        def reply(prompt):
            if "page:s0.example.com" in prompt:
                raise OSError("one batch down")
            return _reply([])
        got, failed = self.run_judge(many, reply=reply, with_failed=True,
                                     log=logs.append, workers=1)
        self.assertEqual(failed, 1)
        self.assertTrue(any("one batch down" in m for m in logs))
        self.calls.clear()
        self.run_judge(many, reply=_reply([]), workers=1)
        self.assertEqual(len(self.calls), 1)      # only the batch that failed


class PromptTest(Base):
    def test_the_prompt_lists_every_lever(self):
        self.run_judge(reply=_reply([]))
        for lev in detect.LEVERS:
            self.assertIn(lev, self.calls[0])

    def test_english_and_chinese_prompts(self):
        self.assertIn("{items}", judge.PROMPT_EN)
        self.assertIn("{items}", judge.PROMPT_ZH)
        self.assertIn("Output only a JSON array", judge.PROMPT_EN)


if __name__ == "__main__":
    unittest.main()
