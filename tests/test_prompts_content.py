"""The prompts' structure, and the rules that must not be lost.

Prompts are the one part of this system with "no compiler, no types, and no
error when wrong": a dropped rule shows as judgements quietly getting worse,
not as anything turning red. Before the 2026-08-20 rewrite (conversational →
concise) this net went up, watching specifically for **lost information**.

Two things are pinned:
  1. placeholders — one missing and format() blows up, or worse, leaves a gap
  2. the domain rules each bought with a real misjudgement
zh and en must match in structure: maintain only one and the other fossilises.
"""

import re
import unittest

from umbra import infer


class PlaceholdersTest(unittest.TestCase):
    def test_the_first_pass_has_all_it_needs(self):
        for tpl in (infer.PROMPT_ZH, infer.PROMPT_EN):
            for ph in ("{wa}", "{wb}", "{a}", "{b}", "{cats}", "{rules}",
                       "{text}"):
                self.assertIn(ph, tpl)

    def test_the_second_pass_has_all_it_needs(self):
        for tpl in (infer.MERGE_PROMPT_ZH, infer.MERGE_PROMPT_EN):
            for ph in ("{day}", "{cats}", "{lines}"):
                self.assertIn(ph, tpl)

    def test_they_fill_without_leaving_gaps(self):
        """format() does not blow up and no placeholder is left — a leftover
        sends the literal "{text}" to the model, no error, just a judgement with
        no data."""
        # The argument list is taken from the template itself — hard-coded, a new
        # placeholder would fail on "one argument short" rather than on what
        # this checks, "anything left over"
        need = set(re.findall(r"\{([a-z_]+)\}", infer.PROMPT_ZH))
        out = infer.PROMPT_ZH.format(**{k: "x" for k in need})
        self.assertEqual(re.findall(r"\{[a-z_]+\}", out), [])
        self.assertIn('"start"', out)      # {{ }} correctly escaped into JSON braces


class OutputFieldsTest(unittest.TestCase):
    """Field names are the contract parse/inference stores by; renaming one in
    a prompt breaks it."""

    def test_the_first_pass_has_eight_fields(self):
        for tpl in (infer.PROMPT_ZH, infer.PROMPT_EN):
            for k in ("start", "end", "cat", "activity", "confidence",
                      "with", "also", "evidence"):
                self.assertIn(f'"{k}"', tpl)

    def test_the_second_pass_has_six_fields(self):
        for tpl in (infer.MERGE_PROMPT_ZH, infer.MERGE_PROMPT_EN):
            for k in ("start", "end", "cat", "title", "detail", "from"):
                self.assertIn(f'"{k}"', tpl)


class RulesThatMustNotBeLostTest(unittest.TestCase):
    """Each comes from a real misjudgement, and losing one turns nothing red —
    except this."""

    def test_speaker_labels_do_not_cross_recordings(self):
        self.assertIn("Speaker", infer.PROMPT_ZH)
        self.assertIn("Speaker", infer.PROMPT_EN)

    def test_one_stretch_on_two_recordings_is_not_two_utterances(self):
        """The wearable and Plaud Desktop record the same meeting at once. One
        sentence appears twice in the digest (two transcriptions, slightly
        different), and a model not told would read "he said everything twice"
        or "two people repeating each other", and the with column would follow
        with someone else present.

        The digest already prefixes such lines with the recording id, but a prefix
        does not explain itself. The wording is "two recordings", not "two
        devices" — serial_number is per recording on the software side, and the
        digest has always counted recordings."""
        self.assertRegex(infer.PROMPT_ZH, r"两条录音|录音编号")
        self.assertRegex(infer.PROMPT_EN, r"(?i)two recordings|recording id")

    def test_talking_to_an_ai_is_work_not_chat(self):
        self.assertIn("对AI说", infer.PROMPT_ZH)
        self.assertRegex(infer.PROMPT_EN, r"(?i)\bAI\b")

    def test_more_records_is_not_more_important(self):
        self.assertRegex(infer.PROMPT_ZH, r"条数|数条数")
        self.assertRegex(infer.PROMPT_EN, r"(?i)count")

    def test_no_grounds_means_no_invention(self):
        for tpl in (infer.PROMPT_ZH, infer.MERGE_PROMPT_ZH):
            self.assertIn("不要编", tpl)
        for tpl in (infer.PROMPT_EN, infer.MERGE_PROMPT_EN):
            self.assertRegex(tpl, r"(?i)never invent")

    def test_only_the_target_stretch_is_judged(self):
        self.assertIn("只判断", infer.PROMPT_ZH)
        self.assertIn("Judge only", infer.PROMPT_EN)

    def test_the_merge_covers_the_whole_day_with_no_gaps(self):
        self.assertIn("00:00", infer.MERGE_PROMPT_ZH)
        self.assertIn("00:00", infer.MERGE_PROMPT_EN)

    def test_from_must_be_written_out_verbatim(self):
        self.assertIn("from", infer.MERGE_PROMPT_ZH)
        self.assertIn("from", infer.MERGE_PROMPT_EN)


class SplittingWithinAWindowTest(unittest.TestCase):
    """Added 2026-08-20. Before, the prompt only said "output a JSON array" and
    never said **when to output more than one element** — the model had to
    guess. Measured on 8/19: the 11:00 window, 7,385 characters, split into 5;
    the 11:30 window,
    7,321 characters, into 1 — the same density, five times the granularity.
    What was missing was not resolution (the model can split to the minute)
    but a criterion.
    """

    def test_it_says_a_change_of_activity_means_several_spans(self):
        self.assertRegex(infer.PROMPT_ZH, r"变化|换了|切成多段|多段")
        self.assertRegex(infer.PROMPT_EN, r"(?i)(changes|switch|separate span)")

    def test_it_gives_observable_signals_of_a_change(self):
        """"Split when it changes" alone says nothing — it needs signals one can
        observe."""
        self.assertRegex(infer.PROMPT_ZH, r"应用|前台|对象|主题")
        self.assertRegex(infer.PROMPT_EN, r"(?i)(app|foreground|topic|who)")


class ZhEnParityTest(unittest.TestCase):
    def test_the_same_number_of_rules(self):
        n = lambda t: len(re.findall(r"^[·\-] ", t, re.M))
        self.assertEqual(n(infer.PROMPT_ZH), n(infer.PROMPT_EN))
        self.assertEqual(n(infer.MERGE_PROMPT_ZH), n(infer.MERGE_PROMPT_EN))

    def test_neither_side_quietly_doubles(self):
        """By characters, Chinese is naturally about 2.3× more compact than
        English (measured 2.24 / 2.35). This guards against "a rule added to one
        side only", not style. Beyond 2.6, English has grown something or Chinese
        has lost a rule."""
        for zh, en in ((infer.PROMPT_ZH, infer.PROMPT_EN),
                       (infer.MERGE_PROMPT_ZH, infer.MERGE_PROMPT_EN)):
            self.assertLess(len(en) / len(zh), 2.6)


if __name__ == "__main__":
    unittest.main()
