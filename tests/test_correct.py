"""Your own correction: "that hour I was actually doing something else".

The cause is a sensor blind spot, not a model misreading: an hour reading on a
separate e-reader over dinner leaves no record anywhere — not in app usage, not
in the browser, not in the reading app's highlights. So the model wrote "not
sure" for half of it and invented a nap for the other half, at high confidence.

Three design decisions:

1. **A correction is an event, source "me".** No new table. It is not a guess
   but something you recorded, the same in kind as any other source, only the
   recorder is you. That buys a whole pipeline for free: db.load_range brings
   it along → the digest writes it into the windows it covers → **the digest
   changed, so infer's cache key changed** → the next round re-asks only those
   windows. When you said it (`said_at`) and which minutes it covers (`ts`) are
   stored apart: testimony after the fact is not a recording at the time.

2. **inferences / day_events are not touched.** They are the model's own words
   and every model comparison rests on them. A correction is a layer on top
   (apply): the page draws the result, and what the model said stays.

3. **What you said wins, always.** If the model, re-asked, still disagrees, the
   page still shows yours, with a disagree mark. The mark is the valuable part:
   it points at a blind spot that will come back.

And the one that matters most: **a day with no correction has a byte-identical
digest.** One byte different is every past day re-asked.
"""

import json
import os
import re
import tempfile
import unittest
from unittest import mock

from umbra import analyze, correct, db, digest, infer, inference as INF

DAY = "2026-09-12"


def ev(a, b, cat, title, **kw):
    """A calendar event (the day_events shape)."""
    return {"a": a, "b": b, "cat": cat, "title": title,
            "start": f"{a // 60:02d}:{a % 60:02d}",
            "end": f"{b // 60:02d}:{b % 60:02d}", **kw}


def _con():
    return db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))


class StoreTest(unittest.TestCase):
    """Written, read back, and what it looks like in the events table."""

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY, self.con)

    def test_saved_as_an_event_not_a_new_table(self):
        correct.save(self.con, DAY, 1080, 1140, "read",
                     "reading on the e-reader over dinner", said_at=7)
        row = self.con.execute(
            "SELECT ts, dur_ms, source, kind, payload FROM events "
            "WHERE source='me'").fetchone()
        self.assertIsNotNone(row, "a correction is a row in events")
        ts, dur, src, kind, payload = row
        self.assertEqual(ts, self.lo + 1080 * 60000)
        self.assertEqual(dur, 60 * 60000)
        self.assertEqual(kind, "correct")
        p = json.loads(payload)
        self.assertEqual(p["cat"], "read")
        # which minutes (ts) and when you said it (said_at) are kept apart
        self.assertEqual(p["said_at"], 7)

    def test_load_reads_it_back(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=7)
        got = correct.load(self.con, DAY)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["a"], got[0]["b"]), (1080, 1140))
        self.assertEqual((got[0]["cat"], got[0]["text"]), ("read", "reading"))

    def test_cat_is_normalised(self):
        """A display name in either language, or the key — one table decides."""
        correct.save(self.con, DAY, 60, 120, "Reading", "x", said_at=1)
        self.assertEqual(correct.load(self.con, DAY)[0]["cat"], "read")

    def test_two_corrections_both_survive(self):
        """Append-only: changing your mind twice leaves both, so it shows."""
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        correct.save(self.con, DAY, 1080, 1140, "meal", "dinner really", said_at=2)
        n = self.con.execute(
            "SELECT COUNT(*) FROM events WHERE source='me'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_later_correction_wins(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        correct.save(self.con, DAY, 1080, 1140, "meal", "dinner really", said_at=2)
        eff = correct.effective(correct.load(self.con, DAY))
        self.assertEqual([c["cat"] for c in eff], ["meal"])

    def test_later_correction_wins_only_where_it_overlaps(self):
        """"18:00–19:00 reading", then "actually a call from 18:40": the first
        still holds for the first 40 minutes."""
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        correct.save(self.con, DAY, 1120, 1140, "social", "a call", said_at=2)
        eff = correct.effective(correct.load(self.con, DAY))
        self.assertEqual([(c["a"], c["b"], c["cat"]) for c in eff],
                         [(1080, 1120, "read"), (1120, 1140, "social")])

    def test_rejects_bad_range(self):
        for a, b in ((1140, 1080), (0, 0), (-5, 60), (1400, 1500)):
            with self.assertRaises(ValueError, msg=f"{a}-{b}"):
                correct.save(self.con, DAY, a, b, "read", "x", said_at=1)

    def test_rejects_an_empty_or_unknown_category(self):
        """The category is required: it is what makes the page change at once,
        and one nobody recognises would draw as grey — which reads as the
        model saying it was unsure."""
        for cat in ("", "no-such-thing"):
            with self.subTest(cat=cat), self.assertRaises(ValueError):
                correct.save(self.con, DAY, 60, 120, cat, "x", said_at=1)

    def test_a_span_reads_both_ways_of_writing_it(self):
        self.assertEqual(correct.parse_span("18:00-19:00"), (1080, 1140))
        self.assertEqual(correct.parse_span("18:00–19:30"), (1080, 1170))
        with self.assertRaises(ValueError):
            correct.parse_span("six to seven")


class DigestTest(unittest.TestCase):
    """The correction reaches the digest: the entry to the whole incremental chain."""

    def setUp(self):
        self.con = _con()
        self.lo, self.hi = analyze.day_bounds(DAY, self.con)
        self.con.execute(
            "INSERT INTO events (uid, ts, source, kind, payload) VALUES (?,?,?,?,?)",
            ("z1", self.lo + 1000 * 60000, "zsh", "command", '{"cmd": "ls"}'))
        self.con.commit()

    def _render(self, a, b):
        return digest.render(db.load_range(self.con, self.lo, self.hi),
                             a, b, lo_ms=self.lo)

    def test_correction_appears_inside_the_judged_stretch(self):
        correct.save(self.con, DAY, 1080, 1140, "read",
                     "reading on the e-reader over dinner", said_at=1)
        _, _, body = self._render(1080, 1110).partition(digest.TARGET_MARK)
        self.assertIn("reading on the e-reader", body,
                      "written into the context part, it is only background")

    def test_correction_says_it_is_authoritative_and_which_span(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        line = next(l for l in self._render(1080, 1110).splitlines()
                    if "reading" in l and "correct" in l.lower())
        self.assertRegex(line, r"18:00.*19:00", "it must say which span it covers")

    def test_correction_covers_every_window_it_overlaps(self):
        """18:00–19:00 is in the 18:00 window and in the 18:30 one."""
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        for a in (1080, 1110):
            with self.subTest(win=a):
                _, _, body = self._render(a, a + 30).partition(digest.TARGET_MARK)
                self.assertIn("reading", body)

    def test_no_correction_means_a_byte_identical_digest(self):
        before = self._render(1000, 1030)
        correct.save(self.con, "2026-09-11", 1080, 1140, "read", "reading", said_at=1)
        self.assertEqual(self._render(1000, 1030), before)

    def test_it_is_not_a_source_anyone_imports(self):
        """It renders like a source, but nothing collects it: it is not in
        `umbra sources`, nor in a round's import."""
        from umbra import registry, sources
        self.assertIsNotNone(registry.hook("me", "render"))
        self.assertNotIn("me", sources.ALL)


class OverlayTest(unittest.TestCase):
    """The model's events + your corrections → what the page draws. The model's
    stay as they were: apply writes nothing and changes nothing it is given."""

    C = [{"a": 1080, "b": 1140, "cat": "read", "text": "reading", "said_at": 1}]

    def test_one_correction_swallows_two_events(self):
        evs = [ev(1047, 1080, "fun", "on the tablet"),
               ev(1080, 1110, "unknown", "not sure"),
               ev(1110, 1140, "sleep", "evening nap"),
               ev(1140, 1164, "code", "testing an app")]
        out = correct.apply(evs, self.C)
        self.assertEqual([(e["a"], e["b"]) for e in out],
                         [(1047, 1080), (1080, 1140), (1140, 1164)])
        self.assertEqual((out[1]["mine"], out[1]["cat"], out[1]["title"]),
                         (True, "read", "reading"))

    def test_partial_overlap_splits_the_model_event(self):
        evs = [ev(1047, 1140, "sleep", "evening nap")]
        c = [{"a": 1070, "b": 1090, "cat": "read", "text": "reading", "said_at": 1}]
        out = correct.apply(evs, c)
        self.assertEqual([(e["a"], e["b"], e.get("mine", False)) for e in out],
                         [(1047, 1070, False), (1070, 1090, True), (1090, 1140, False)])

    def test_model_event_fully_inside_is_dropped(self):
        out = correct.apply([ev(1090, 1100, "sleep", "nap")], self.C)
        self.assertEqual([(e["a"], e["b"]) for e in out], [(1080, 1140)])

    def test_what_the_model_said_is_carried_along(self):
        evs = [ev(1080, 1110, "unknown", "not sure"), ev(1110, 1140, "sleep", "evening nap")]
        self.assertEqual([w["title"] for w in correct.apply(evs, self.C)[0]["was"]],
                         ["not sure", "evening nap"])

    def test_only_the_category_changed_keeps_the_models_title(self):
        c = [{**self.C[0], "text": ""}]
        out = correct.apply([ev(1080, 1140, "fun", "on the tablet")], c)
        self.assertEqual((out[0]["cat"], out[0]["title"]), ("read", "on the tablet"))

    def test_apply_does_not_change_the_model_events(self):
        evs = [ev(1080, 1140, "sleep", "evening nap")]
        snapshot = json.loads(json.dumps(evs))
        correct.apply(evs, self.C)
        self.assertEqual(evs, snapshot)

    def test_no_corrections_returns_the_same_list(self):
        evs = [ev(1080, 1140, "sleep", "evening nap")]
        self.assertEqual(correct.apply(evs, []), evs)

    def test_correction_on_a_day_the_model_said_nothing_still_shows(self):
        out = correct.apply([], self.C)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["mine"])


def span(a, b, cat, act="x", **kw):
    """A first-pass span (the inferences shape). Agreement is judged here."""
    return {"a": a, "b": b, "cat": cat, "act": act, **kw}


class AgreementTest(unittest.TestCase):
    """Whether the model, re-asked, agrees.

    **Against the first pass, not the calendar.** The first pass read the records
    and the correction, and has `also` — "mainly reading, eating too" can only be
    said there. The calendar has to pick one category per event, and comparing
    against it raised a disagreement where the model agreed completely.

    Four answers, not three: "the same thing, filed the other way round" (soft)
    must not look like a real disagreement, or the marks become noise and the
    real ones drown.
    """

    C = {"a": 1080, "b": 1140, "cat": "read", "text": "reading", "said_at": 1}

    def test_agrees_when_the_model_comes_around(self):
        self.assertFalse(correct.disagrees(self.C, [span(1080, 1140, "read")]))

    def test_disagrees_when_it_still_says_sleep(self):
        d = correct.disagrees(self.C, [span(1080, 1140, "sleep", "evening nap")])
        self.assertEqual((d["cat"], d["title"], d["soft"]), ("sleep", "evening nap", False))

    def test_also_means_the_same_thing_filed_differently(self):
        d = correct.disagrees(self.C, [
            span(1080, 1140, "meal", "dinner and a book", also=["Reading"])])
        self.assertEqual((d["cat"], d["soft"]), ("meal", True))

    def test_unrelated_also_is_still_a_real_disagreement(self):
        d = correct.disagrees(self.C, [span(1080, 1140, "sleep", "nap", also=["Music"])])
        self.assertFalse(d["soft"])

    def test_the_most_minutes_decide(self):
        self.assertFalse(correct.disagrees(self.C, [
            span(1080, 1130, "read"), span(1130, 1140, "sleep")]))

    def test_silence_is_not_disagreement(self):
        self.assertIsNone(correct.disagrees(self.C, []))

    def test_unknown_is_abstaining_not_disagreeing(self):
        self.assertIsNone(correct.disagrees(self.C, [span(1080, 1140, "unknown")]))

    def test_unknown_does_not_dilute_a_real_disagreement(self):
        d = correct.disagrees(self.C, [span(1080, 1110, "unknown"),
                                       span(1110, 1140, "sleep", "evening nap")])
        self.assertEqual(d["cat"], "sleep")

    def test_accepts_the_start_end_shape_too(self):
        d = correct.disagrees(self.C, [{"start": "18:00", "end": "19:00",
                                        "cat": "sleep", "activity": "evening nap"}])
        self.assertEqual((d["cat"], d["title"]), ("sleep", "evening nap"))


class IncrementalTest(unittest.TestCase):
    """A correction re-asks the windows it reaches, and no others."""

    TARGET_RE = re.compile(r"(\d\d:\d\d)–(\d\d:\d\d)")

    def setUp(self):
        p = mock.patch.object(infer, "lang", return_value="en")
        p.start()
        self.addCleanup(p.stop)
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY, self.con)
        for uid, minute in (("a", 9 * 60), ("b", 18 * 60), ("c", 21 * 60)):
            self.con.execute(
                "INSERT INTO events (uid, ts, source, kind, payload) VALUES (?,?,?,?,?)",
                (uid, self.lo + minute * 60000, "zsh", "command", '{"cmd": "ls"}'))
        self.con.commit()

    def _fake(self, calls):
        def fake(prompt):
            calls.append(prompt)
            m = self.TARGET_RE.search(prompt)
            if not m:
                raise AssertionError("the fake model cannot find the target window")
            return ('[{"start":"%s","end":"%s","cat":"code","activity":"x"}]'
                    % (m.group(1), m.group(2)))
        return fake

    def _targets(self, calls):
        return sorted(self.TARGET_RE.search(p).group(1) for p in calls)

    def test_a_correction_re_asks_a_bounded_set_of_windows(self):
        """More than the two it covers — each window reads an hour either side,
        and the neighbours reading it is the point (the 13 minutes next to the
        corrected hour are the same thing) — but bounded: the morning window is
        never asked again. One correction re-asking the whole day would make
        filling in one evening dozens of calls nobody notices."""
        infer.run_day(self.con, DAY, "m", call=self._fake([]))
        again = []
        infer.run_day(self.con, DAY, "m", call=self._fake(again))
        self.assertEqual(again, [], "nothing changed, nothing asked")

        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        calls = []
        infer.run_day(self.con, DAY, "m", call=self._fake(calls))
        third = self._targets(calls)
        for w in ("18:00", "18:30", "17:30"):
            self.assertIn(w, third)
        self.assertNotIn("09:00", third)
        self.assertLessEqual(len(third), 8)

    def test_a_second_round_after_a_correction_is_free(self):
        infer.run_day(self.con, DAY, "m", call=self._fake([]))
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        infer.run_day(self.con, DAY, "m", call=self._fake([]))
        again = []
        infer.run_day(self.con, DAY, "m", call=self._fake(again))
        self.assertEqual(again, [])


class PayloadTest(unittest.TestCase):
    """What the page gets: the drawn events with your corrections laid over."""

    def setUp(self):
        from umbra import viz
        self.viz = viz
        self.con = _con()
        self.lo, self.hi = analyze.day_bounds(DAY, self.con)
        self.con.execute(
            "INSERT INTO events (uid, ts, source, kind, payload) VALUES (?,?,?,?,?)",
            ("z1", self.lo + 1080 * 60000, "zsh", "command", '{"cmd": "ls"}'))
        self.con.commit()
        INF.save_events(self.con, DAY, "gemini-x", [
            {"start": "18:00", "end": "18:30", "cat": "unknown", "title": "not sure"},
            {"start": "18:30", "end": "19:00", "cat": "sleep", "title": "evening nap"}])
        # The first-pass spans too: agreement is judged against them, and
        # without them the payload would say "the model said nothing here" —
        # which is not the same as disagreeing.
        INF.save(self.con, DAY, "gemini-x", [
            {"start": "18:00", "end": "18:30", "cat": "unknown",
             "activity": "not sure", "confidence": "low"},
            {"start": "18:30", "end": "19:00", "cat": "sleep",
             "activity": "lying down", "confidence": "high"}])

    def _day(self):
        return self.viz._day_payload(self.con, DAY, self.lo, self.hi, True)

    def _mine(self, d=None):
        d = d or self._day()
        return [e for e in d["ev"][d["evuse"]] if e.get("mine")]

    def test_a_correction_replaces_the_drawn_events(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        d = self._day()
        mine = self._mine(d)
        self.assertEqual([(e["a"], e["b"], e["cat"]) for e in mine], [(1080, 1140, "read")])
        self.assertNotIn("evening nap", [e["title"] for e in d["ev"][d["evuse"]]])

    def test_what_the_model_said_is_still_in_the_payload(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        self.assertEqual([w["title"] for w in self._mine()[0]["was"]],
                         ["not sure", "evening nap"])

    def test_disagreement_is_marked(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        self.assertEqual(self._mine()[0]["vs"]["cat"], "sleep")

    def test_agreement_leaves_no_mark(self):
        correct.save(self.con, DAY, 1080, 1140, "sleep", "asleep indeed", said_at=1)
        self.assertNotIn("vs", self._mine()[0])

    def test_the_other_way_round_is_marked_soft(self):
        INF.save(self.con, DAY, "gemini-x", [
            {"start": "18:00", "end": "19:00", "cat": "meal",
             "activity": "dinner and a book", "also": ["Reading"]}])
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        vs = self._mine()[0]["vs"]
        self.assertEqual((vs["cat"], vs["soft"]), ("meal", True))

    def test_no_first_pass_at_all_is_silence_not_disagreement(self):
        self.con.execute("DELETE FROM inferences")
        self.con.commit()
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        mine = self._mine()[0]
        self.assertNotIn("vs", mine)
        self.assertTrue(mine["nosay"])

    def test_a_day_the_model_never_judged_still_draws_it(self):
        """Correcting and seeing nothing change, with every log green, is the
        most maddening failure there is."""
        self.con.execute("DELETE FROM day_events")
        self.con.commit()
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        self.assertTrue(self._mine())

    def test_the_cache_watches_the_overlay_code(self):
        self.assertIn("correct", self.viz._FP_MODULES)

    def test_a_day_without_corrections_is_unchanged(self):
        self.assertFalse([e for e in self._day()["ev"]["gemini-x"] if e.get("mine")])

    def test_the_corrected_minutes_count_as_covered(self):
        """api.day counts covered minutes from the drawn events, so an hour you
        filled in is no longer "the model said nothing" (here the model's own
        events were deleted, as on a day it never judged)."""
        from umbra import api
        self.con.execute("DELETE FROM day_events")
        self.con.commit()
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        with mock.patch("umbra.tz.elapsed_minutes", return_value=1440):
            m = api.day(DAY, self.con)["minutes"]
        self.assertEqual(m["covered"], 60)


class ReadYetTest(unittest.TestCase):
    """Whether the model has read a correction yet — told apart from whether it
    agrees. Right after you say it, the model's judgement is the one it made
    before; "it still disagrees" would be false, and reads as the model arguing
    with you. The digest each window was asked with is kept: when every window
    the correction covers was asked with it in the text, the model has read it."""

    C = {"a": 1080, "b": 1140, "cat": "read", "text": "reading", "said_at": 1}

    def _row(self, a, text):
        return {"a": a, "b": a + 30, "text": text}

    def test_read_when_every_window_it_covers_carried_it(self):
        line = "18:00 ✎ your own correction, this is what happened 18:00–19:00: Reading — reading"
        self.assertTrue(correct.read_by_model(self.C, [self._row(1080, line), self._row(1110, line)]))

    def test_not_yet_when_any_window_was_asked_without_it(self):
        line = "✎ your own correction, this is what happened 18:00–19:00: Reading — reading"
        self.assertFalse(correct.read_by_model(self.C, [self._row(1080, line),
                                                        self._row(1110, "18:30 zsh: ls")]))

    def test_cannot_tell_without_any_digest(self):
        self.assertIsNone(correct.read_by_model(self.C, [self._row(600, "x")]))

    def test_the_page_says_not_yet_instead_of_disagreeing(self):
        t = PayloadTest("test_disagreement_is_marked")
        t.setUp()
        INF.save_digests(t.con, DAY, "gemini-x", [
            {"start": "18:00", "end": "18:30", "n": 1, "text": "18:00 zsh: ls"},
            {"start": "18:30", "end": "19:00", "n": 1, "text": "18:30 zsh: ls"}])
        correct.save(t.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        mine = t._mine()[0]
        self.assertTrue(mine["pending"])
        self.assertNotIn("vs", mine)


class UndoTest(unittest.TestCase):
    """Undo takes a correction back: the minutes return to whatever was there
    before it — an earlier correction, or the model. The row stays, marked
    withdrawn, so how often you changed your mind still shows; but it leaves
    the digest, so the model reads the stretch as if it had never been said."""

    def setUp(self):
        self.con = _con()
        self.lo, self.hi = analyze.day_bounds(DAY, self.con)

    def test_an_undone_correction_no_longer_counts(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        self.assertTrue(correct.withdraw(self.con, DAY, 1))
        self.assertEqual(correct.load(self.con, DAY), [])

    def test_the_row_stays_marked(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        correct.withdraw(self.con, DAY, 1)
        (p,) = self.con.execute("SELECT payload FROM events WHERE source='me'").fetchone()
        self.assertIn("withdrawn_at", json.loads(p))

    def test_the_one_before_shows_again(self):
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        correct.save(self.con, DAY, 1080, 1140, "meal", "dinner", said_at=2)
        correct.withdraw(self.con, DAY, 2)
        eff = correct.effective(correct.load(self.con, DAY))
        self.assertEqual([c["cat"] for c in eff], ["read"])

    def test_the_digest_is_as_if_it_was_never_said(self):
        render = lambda: digest.render(db.load_range(self.con, self.lo, self.hi),
                                       1080, 1110, lo_ms=self.lo)
        before = render()
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        self.assertNotEqual(render(), before)
        correct.withdraw(self.con, DAY, 1)
        self.assertEqual(render(), before)

    def test_the_page_sees_an_undo_at_once(self):
        """The day's payload is cached by a fingerprint of its rows. Undo marks
        a row rather than adding one, so a fingerprint of counts and ids misses
        it, and the page kept showing the correction until something else
        changed (found by clicking through it in a browser)."""
        from umbra import api
        PayloadTest.setUp(self)
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        mine = lambda: [e for e in (lambda d: d["ev"][d["evuse"]])(api.day(DAY, self.con))
                        if e.get("mine")]
        self.assertTrue(mine())
        correct.withdraw(self.con, DAY, 1)
        self.assertEqual(mine(), [])

    def test_nothing_to_undo_says_so(self):
        self.assertFalse(correct.withdraw(self.con, DAY, 99))

    def test_from_the_page(self):
        from umbra import api
        correct.save(self.con, DAY, 1080, 1140, "read", "reading", said_at=1)
        self.assertEqual(api.correct(DAY, {"undo": 1}, self.con), {"ok": True})
        self.assertEqual(correct.load(self.con, DAY), [])
        with self.assertRaises(api.NotFound):
            api.correct(DAY, {"undo": 1}, self.con)


class RejudgeTest(unittest.TestCase):
    """The model is re-asked about a corrected day in the next round, even an
    old one. A round re-judges the last couple of days and those an import
    wrote to; a correction is neither, so without this a day corrected last
    week would never be re-read, and its agreement mark would stay as it was."""

    def test_the_days_corrections_were_said_about_since_a_moment(self):
        con = _con()
        correct.save(con, "2026-09-10", 60, 120, "read", "x", said_at=1_000)
        correct.save(con, "2026-09-12", 60, 120, "read", "x", said_at=5_000)
        correct.save(con, "2026-09-13", 60, 120, "read", "x", said_at=9_000)
        self.assertEqual(correct.days_said_since(con, 5_000), ["2026-09-12", "2026-09-13"])

    def test_a_round_rejudges_a_corrected_day_within_the_lookback(self):
        import datetime as dt
        from umbra import ops
        ago = lambda n: str(dt.date.today() - dt.timedelta(days=n))
        inferred, since = [], []
        d = tempfile.mkdtemp()
        open(os.path.join(d, "config.toml"), "w").close()
        with mock.patch("umbra.config.CONFIG_DIR", d):
            ops.sync(_tz=lambda: None, _import=lambda: [],
                     _infer=lambda days: inferred.extend(days) or True,
                     _corrected=lambda ms: since.append(ms) or [ago(5), ago(40)],
                     _viz=lambda: True, _plugins={}, _deploy=lambda **k: True,
                     log=lambda *a: None)
        self.assertIn(ago(5), inferred)
        self.assertNotIn(ago(40), inferred, "the lookback still bounds it")
        self.assertTrue(since and since[0] < __import__("time").time() * 1000)


class CliTest(unittest.TestCase):
    """umbra correct 2026-09-12 18:00-19:00 read "reading over dinner"
    umbra correct --list 2026-09-12"""

    def setUp(self):
        self.con = _con()

    def _run(self, *argv):
        import io
        from contextlib import redirect_stdout
        from umbra import __main__ as cli
        args = cli.build_parser().parse_args(["correct", *argv])
        buf = io.StringIO()
        with mock.patch.object(db, "connect", return_value=self.con), redirect_stdout(buf):
            args.func(args)
        return buf.getvalue()

    def test_writes_a_correction(self):
        out = self._run(DAY, "18:00-19:00", "read", "reading", "over", "dinner")
        got = correct.load(self.con, DAY)
        self.assertEqual([(c["a"], c["b"], c["cat"], c["text"]) for c in got],
                         [(1080, 1140, "read", "reading over dinner")])
        self.assertIn("18:00-19:00", out)

    def test_the_words_are_optional(self):
        self._run(DAY, "18:00-19:00", "read")
        self.assertEqual(correct.load(self.con, DAY)[0]["text"], "")

    def test_an_unknown_category_or_a_bad_span_is_refused(self):
        for argv in ((DAY, "18:00-19:00", "meditation"), (DAY, "19:00-18:00", "read")):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self._run(*argv)
        self.assertEqual(correct.load(self.con, DAY), [])

    def test_the_list_says_so_when_the_model_agrees(self):
        self._run(DAY, "18:00-19:00", "read", "reading")
        line = "18:00 ✎ your own correction, this is what happened 18:00–19:00: Reading — reading"
        INF.save_digests(self.con, DAY, infer.MODEL, [
            {"start": "18:00", "end": "18:30", "n": 1, "text": line},
            {"start": "18:30", "end": "19:00", "n": 1, "text": line}])
        INF.save(self.con, DAY, infer.MODEL, [
            {"start": "18:00", "end": "19:00", "cat": "read", "activity": "reading"}])
        self.assertIn("the model agrees", self._run("--list", DAY))

    def test_the_list_says_whether_the_model_came_around(self):
        self._run(DAY, "18:00-19:00", "read", "reading")
        out = self._run("--list", DAY)
        self.assertIn("18:00–19:00", out)
        self.assertIn("reading", out)
        self.assertIn("has not said", out)


class ApiTest(unittest.TestCase):
    """POST /api/day/{date}/correct — the page's way in."""

    def setUp(self):
        self.con = _con()

    def test_a_correction_from_the_page(self):
        from umbra import api
        got = api.correct(DAY, {"span": "18:00-19:00", "cat": "read", "text": "reading"}, self.con)
        self.assertTrue(got["ok"])
        self.assertEqual(correct.load(self.con, DAY)[0]["cat"], "read")

    def test_minutes_work_as_well_as_a_span(self):
        from umbra import api
        api.correct(DAY, {"a": 1080, "b": 1110, "cat": "read"}, self.con)
        self.assertEqual((correct.load(self.con, DAY)[0]["a"],
                          correct.load(self.con, DAY)[0]["b"]), (1080, 1110))

    def test_a_bad_one_is_refused_and_nothing_is_stored(self):
        from umbra import api
        for body in ({"span": "19:00-18:00", "cat": "read"}, {"span": "18:00-19:00", "cat": "x"},
                     {"cat": "read"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                api.correct(DAY, body, self.con)
        self.assertEqual(correct.load(self.con, DAY), [])

    def test_not_a_date_is_not_found(self):
        from umbra import api
        with self.assertRaises(api.NotFound):
            api.correct("not-a-day", {"span": "18:00-19:00", "cat": "read"}, self.con)


if __name__ == "__main__":
    unittest.main()
