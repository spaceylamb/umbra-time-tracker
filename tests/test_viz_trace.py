"""Calendar event → how it was judged. The chain is computed **at build time**,
not guessed in the browser.

Added when the page design was settled on 2026-08-18. The page went from
"three layers side by side" to "one layer plus drill-down": 23 calendar events by
default, open one to see the spans it was made of, open again for raw records.

Why the chain is computed in Python rather than JavaScript:

  · it is deterministic — the same kind of thing as inference.expand, and what
    can be tested should be; the JavaScript copy had no test net, and a mistake
    shows only as "two stretches missing from the detail", which looks exactly
    like "there was only one"
  · it really went wrong in the demo: a first-match miss silently returned []

`from` is written by the model and will not always match (time formats,
boundaries a minute off). On a mismatch it falls back to overlap in time —
**better too many than showing "no basis"**, this project's most dreaded
mistake: showing "data not displayed" as "no data".
"""

import unittest

from umbra import viz


class PickModelTest(unittest.TestCase):
    """Draw gemini only. The other two models' data stays in the database."""

    def test_gemini_is_used_when_present(self):
        self.assertEqual(
            viz.pick_model(["claude-opus-5", "gemini-3.7-flash", "gpt-5.6-sol"]),
            "gemini-3.7-flash")

    def test_without_gemini_the_first_is_used(self):
        """Not None — the whole AI section would vanish, looking like "the model
        said nothing". Runs with another model (a local open one, say) must
        still draw."""
        self.assertEqual(viz.pick_model(["claude-opus-5", "gpt-5.6-sol"]),
                         "claude-opus-5")

    def test_none_at_all_is_none(self):
        self.assertIsNone(viz.pick_model([]))

    def test_matched_by_prefix_not_full_name(self):
        """Model versions change (gemini-3.7-flash → 3.8). Pinning the full name
        would silently drop the preference on a minor upgrade, with no error."""
        self.assertEqual(viz.pick_model(["gemini-4.0-flash", "gpt-5.6-sol"]),
                         "gemini-4.0-flash")


SPANS = [
    {"a": 0,   "b": 543},
    {"a": 543, "b": 570},          # the 09:03–09:30 stretch
    {"a": 570, "b": 582},
    {"a": 582, "b": 600},
]


class SpansOfEventTest(unittest.TestCase):
    def test_matched_exactly_by_from(self):
        ev = {"a": 543, "b": 570, "from": ["09:03–09:30"]}
        self.assertEqual(viz.spans_of(SPANS, ev), [1])

    def test_one_event_from_several_spans(self):
        ev = {"a": 570, "b": 600, "from": ["09:30–09:42", "09:42–10:00"]}
        self.assertEqual(viz.spans_of(SPANS, ev), [2, 3])

    def test_a_hyphen_is_accepted_too(self):
        """The model writes an en dash sometimes and a hyphen others. Accept one
        and the other matches nothing — showing as "no basis", not an error."""
        ev = {"a": 543, "b": 570, "from": ["09:03-09:30"]}
        self.assertEqual(viz.spans_of(SPANS, ev), [1])

    def test_unmatched_from_falls_back_to_time_overlap(self):
        ev = {"a": 543, "b": 582, "from": ["瞎写的"]}
        self.assertEqual(viz.spans_of(SPANS, ev), [1, 2])

    def test_no_from_at_all_still_gives_a_basis(self):
        ev = {"a": 543, "b": 570}
        self.assertEqual(viz.spans_of(SPANS, ev), [1])

    def test_overlap_excludes_touching_ends(self):
        """A span ending at 543 does not count for an event starting at 543 —
        one off and every event carries the tail of the one before, looking like
        the model contradicting itself."""
        ev = {"a": 543, "b": 570, "from": ["对不上"]}
        self.assertNotIn(0, viz.spans_of(SPANS, ev))

    def test_no_spans_returns_empty(self):
        self.assertEqual(viz.spans_of([], {"a": 0, "b": 10}), [])


class WindowTest(unittest.TestCase):
    """The first pass looked at **an hour either side** of the target.

    The trace must report that honestly — "it only saw these 27 minutes"
    understates it; much of a judgement comes from context (09:03's "colleague"
    came from the run of work talk after 09:15).
    """

    def test_an_hour_either_side(self):
        self.assertEqual(viz.window_of([{"a": 543, "b": 570}]), (483, 630))

    def test_several_spans_are_unioned_then_extended(self):
        self.assertEqual(
            viz.window_of([{"a": 570, "b": 582}, {"a": 582, "b": 600}]),
            (510, 660))

    def test_it_stays_within_the_day(self):
        """A sleep at 00:16 extended an hour back reaches the previous day. Clamp
        it, or the trace reports a range of records this day cannot read."""
        self.assertEqual(viz.window_of([{"a": 16, "b": 100}]), (0, 160))
        self.assertEqual(viz.window_of([{"a": 1400, "b": 1440}]), (1340, 1440))

    def test_no_spans_returns_none(self):
        """No spans is no window. A hard-coded 0–1440 would make the trace show
        "the model saw all 5,038 records today" — false."""
        self.assertIsNone(viz.window_of([]))



class BuildLinkTest(unittest.TestCase):
    """Events must carry their digest indices — and a Python test must guard it.

    That line lives only in build() and at first no unit test covered it: a
    mutation to `e["dg"] = []` passed green. Only a CDP check on a scratch fake
    page caught it, and scratch gets cleared.

    "The fix that is never called" is a hole this project keeps falling into
    (migrate not wired into connect, authed_get_json not wired into the
    importer). So this is not decoration.
    """

    def setUp(self):
        import os
        import tempfile
        import time
        from umbra import db, inference as inf
        self.dir = tempfile.mkdtemp()
        path = os.path.join(self.dir, "t.db")
        con = db.connect(path)
        self.day = "2026-08-17"
        lo = int(time.mktime(time.strptime(self.day + " 00:00",
                                           "%Y-%m-%d %H:%M"))) * 1000
        db.insert(con, [(f"t:{i}", lo + (540 + i) * 60000, 0, "chrome", "visit",
                         {"url": f"u{i}", "title": f"页面 {i}"}) for i in range(10)])
        M = "gemini-3.7-flash"
        inf.save(con, self.day, M, [{"start": "09:00", "end": "09:30",
                                     "cat": "查资料", "activity": "翻网页"}])
        inf.save_events(con, self.day, M, [{"start": "09:00", "end": "09:30",
                                            "cat": "查资料", "title": "翻网页",
                                            "from": ["09:00–09:30"]}])
        inf.save_digests(con, self.day, M, [{"start": "09:00", "end": "09:30",
                                             "win": ("08:00", "10:30"),
                                             "n": 10, "text": "喂进去的那段"}])
        con.close()
        self.real = db.connect
        db.connect = lambda *a, **k: self.real(path)
        viz.db.connect = db.connect
        self.addCleanup(self._restore)

    def _restore(self):
        import shutil
        from umbra import db
        db.connect = self.real
        viz.db.connect = self.real
        shutil.rmtree(self.dir, ignore_errors=True)

    def day_payload(self):
        return viz.build(self.day)["days"][self.day]

    def test_the_digest_is_in_the_page_data(self):
        self.assertIn("dg", self.day_payload())

    def test_events_carry_their_digest(self):
        d = self.day_payload()
        self.assertEqual(d["ev"][d["evuse"]][0].get("dg"), [0],
                         "the event is not linked to a digest — the page would show \"never recorded\"")

    def test_events_carry_their_spans_and_window_too(self):
        e = self.day_payload()["ev"]["gemini-3.7-flash"][0]
        self.assertEqual(e["sp"], [0])
        self.assertEqual(e["win"], [480, 630])

    def test_no_digest_means_no_empty_one_forced_in(self):
        """Never stored means no such key — an empty array would show as
        "stored, but empty", which is not the same as never stored."""
        from umbra import db
        con = db.connect()
        con.execute("DELETE FROM digests")
        con.commit()
        con.close()
        d = self.day_payload()
        self.assertNotIn("dg", d)
        self.assertNotIn("dg", d["ev"][d["evuse"]][0])

if __name__ == "__main__":
    unittest.main()
