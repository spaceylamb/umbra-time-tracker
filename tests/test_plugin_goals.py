"""Goal attribution, as a plugin.

This is the lane that reads Umbra's own output rather than a source's records,
and the one whose answer depends on something no window contains -- the goals
you wrote. Both of those are interface questions as much as goals questions, and
if either breaks the symptom is a number rather than an error.

The number is the whole point. "11.2 hours on Umbra last week" has to mean the
same thing next year as it did when it was computed, and it has to stop meaning
anything at all the moment you rewrite the goal it was counted against.
"""

import json
import sqlite3
import unittest

from umbra import analyzer, db, goals as G, inference as INF, registry
from umbra.plugins import goals as P

DAY = "2026-09-16"          # a Wednesday
MON = "2026-09-14"
FRI = "2026-09-18"
M = "m"
TEXTS = ["ship Umbra's plugin interfaces", "read a book", "prep for the trip"]


def _con():
    con = sqlite3.connect(":memory:")
    con.executescript(db.SCHEMA)
    return con


def _spans(con, day=DAY, model=M, rows=None):
    INF.save(con, day, model, rows if rows is not None else [
        {"start": "09:00", "end": "10:30", "cat": "code",
         "activity": "refactoring the analyzer interface",
         "also": ["reading the plan"]},
        {"start": "12:00", "end": "12:45", "cat": "meal", "activity": "lunch"},
        {"start": "20:00", "end": "21:00", "cat": "read",
         "activity": "reading Sapiens"},
    ])


def _run(con, day=DAY, reply="[]", model=M):
    seen = []

    def call(prompt):
        seen.append(prompt)
        return reply

    r = analyzer.run(con, G.PLUGIN, P, day, model, call=call)
    return r, seen


REPLY = json.dumps([{"i": 0, "g": [1], "why": "built the interface"},
                    {"i": 2, "g": [2], "why": "read a book"}])


class JudgingTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        G.save_goals(self.con, G.week_of(DAY), TEXTS, now_ms=0)
        _spans(self.con)

    def _rows(self):
        return G.load_attrib(self.con, DAY, M, G.gdigest(TEXTS))

    def test_each_stretch_is_credited_to_the_goal_it_advanced_with_a_reason(self):
        r, _ = _run(self.con, reply=REPLY)
        self.assertEqual(r["rows"], 2)
        got = self._rows()
        self.assertEqual((got[0]["a"], got[0]["b"], got[0]["g"]), (540, 630, 1))
        self.assertEqual(got[0]["why"], "built the interface")
        self.assertEqual((got[1]["a"], got[1]["b"], got[1]["g"]), (1200, 1260, 2))

    def test_a_stretch_advancing_two_goals_counts_fully_for_both(self):
        """Splitting it evenly would be invented precision: nothing establishes
        that a stretch advancing two things divided in half."""
        _run(self.con, reply=json.dumps([{"i": 0, "g": [1, 3], "why": "both"}]))
        t = G.totals(self.con, [DAY], M, G.gdigest(TEXTS))
        self.assertEqual((t[1], t[3]), (90, 90))

    def test_an_invented_stretch_index_is_dropped(self):
        """Crediting invented time to a real goal is the one failure here that
        makes the hours untrustworthy, and it flatters."""
        _run(self.con, reply=json.dumps([{"i": 9, "g": [1], "why": "invented"}]))
        self.assertEqual(self._rows(), [])

    def test_an_invented_goal_number_is_dropped(self):
        _run(self.con, reply=json.dumps([{"i": 0, "g": [7], "why": "invented"}]))
        self.assertEqual(self._rows(), [])

    def test_nothing_advanced_is_still_an_answer(self):
        """Distinct from "not judged yet" — see JudgedDaysTest below."""
        r, _ = _run(self.con, reply="[]")
        self.assertEqual((r["called"], r["rows"]), (1, 0))
        self.assertEqual(self._rows(), [])

    def test_a_long_reason_is_cut_not_dropped(self):
        _run(self.con, reply=json.dumps(
            [{"i": 0, "g": [1], "why": "x" * 200}]))
        self.assertEqual(len(self._rows()[0]["why"]), 60)


class WhatIsAskedTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        G.save_goals(self.con, G.week_of(DAY), TEXTS, now_ms=0)
        _spans(self.con)

    def test_the_goals_are_numbered_in_the_prompt(self):
        _, seen = _run(self.con)
        self.assertIn("1. ship Umbra's plugin interfaces", seen[0])
        self.assertIn("3. prep for the trip", seen[0])

    def test_one_line_per_stretch_with_index_time_and_length(self):
        _, seen = _run(self.con)
        self.assertIn("0\t09:00 90min\trefactoring the analyzer interface",
                      seen[0])
        self.assertIn("1\t12:00 45min\tlunch", seen[0])

    def test_parallel_activity_comes_along(self):
        """"also:" is what lets a stretch count for something that was not its
        headline. Dropping it silently undercounts."""
        _, seen = _run(self.con)
        self.assertIn("; also: reading the plan", seen[0])

    def test_a_week_without_goals_asks_nothing(self):
        con = _con()
        _spans(con)
        r, seen = _run(con)
        self.assertEqual(seen, [])
        self.assertEqual((r["called"], r["rows"]), (0, 0))

    def test_a_day_without_stretches_asks_nothing(self):
        con = _con()
        G.save_goals(con, G.week_of(DAY), TEXTS, now_ms=0)
        r, seen = _run(con)
        self.assertEqual(seen, [])
        self.assertEqual(r["called"], 0)


class NotRecomputedTest(unittest.TestCase):
    """The property the whole storage design exists for.

    Attribution has an input nobody controls -- the model itself. Read as a
    cache, reopening a week next year quietly returns a different number with
    nothing to say it changed.
    """

    def setUp(self):
        self.con = _con()
        G.save_goals(self.con, G.week_of(DAY), TEXTS, now_ms=0)
        _spans(self.con)
        _run(self.con, reply=REPLY)

    def test_reading_again_calls_no_model(self):
        r, seen = _run(self.con, reply=REPLY)
        self.assertEqual((r["called"], r["reused"]), (0, 1))
        self.assertEqual(seen, [])

    def test_new_wording_is_judged_again_and_the_old_number_stops_counting(self):
        """Between the edit and the next run, the stored rows are about goals
        that no longer exist. They do not count toward the ones you have now:
        goal 3 is a position, and rewriting it must not hand the new goal 3 the
        old one's hours. A 0.0 h that means "not judged yet" is the honest
        reading, and done_days() is what tells them apart.

        The superseded judgement is not kept. The core's row store holds one
        answer per window and re-judging replaces it; the frozen goal_attrib
        table kept every goal digest, and nothing ever read a superseded one."""
        old = G.gdigest(TEXTS)
        G.save_goals(self.con, G.week_of(DAY), ["something else entirely"],
                     now_ms=0)
        self.assertEqual(G.totals(self.con, [DAY], M, G.gdigest(
            ["something else entirely"])), {})
        r, _ = _run(self.con, reply=json.dumps(
            [{"i": 0, "g": [1], "why": "new goal"}]))
        self.assertEqual(r["called"], 1)
        self.assertEqual(G.totals(self.con, [DAY], M, old), {})

    def test_friday_s_new_data_does_not_rejudge_monday(self):
        """A week-wide key would re-judge Monday when Friday's recordings land,
        and a number read as 11.3 hours on Wednesday becomes 10.8 on Friday with
        nothing having changed."""
        _spans(self.con, day=MON)
        _run(self.con, day=MON, reply=REPLY)
        _spans(self.con, day=FRI, rows=[
            {"start": "09:00", "end": "09:30", "cat": "code",
             "activity": "late recording"}])
        r, seen = _run(self.con, day=FRI, reply="[]")
        self.assertEqual(r["called"], 1)
        mon, _ = _run(self.con, day=MON, reply=REPLY)
        self.assertEqual(mon["called"], 0)

    def test_new_stretches_on_a_day_mean_judging_it_again(self):
        _spans(self.con, rows=[
            {"start": "09:00", "end": "10:30", "cat": "code",
             "activity": "refactoring the analyzer interface",
             "also": ["reading the plan"]},
            {"start": "14:00", "end": "15:00", "cat": "code",
             "activity": "a recording that arrived late"}])
        r, _ = _run(self.con, reply=REPLY)
        self.assertEqual(r["called"], 1)


class JudgedDaysTest(unittest.TestCase):
    """"Nothing advanced a goal" and "nobody has asked yet" both read as 0.0 h.

    They are different facts and the page says so. Getting this wrong is how
    2026-W33 came to show zeros that nobody questioned.
    """

    def setUp(self):
        self.con = _con()
        G.save_goals(self.con, G.week_of(DAY), TEXTS, now_ms=0)
        _spans(self.con)

    def test_a_day_not_yet_judged_does_not_count(self):
        self.assertEqual(G.done_days(self.con, [DAY], M, G.gdigest(TEXTS)),
                         set())

    def test_a_judged_day_counts_even_with_nothing_credited(self):
        _run(self.con, reply="[]")
        self.assertEqual(G.done_days(self.con, [DAY], M, G.gdigest(TEXTS)),
                         {DAY})

    def test_changing_the_goals_makes_it_unjudged_again(self):
        """Stricter than the old table could be: its key held the goal digest
        and nothing about the stretches, so a recording arriving late changed a
        day's input and the stale number kept its tick."""
        _run(self.con, reply=REPLY)
        G.save_goals(self.con, G.week_of(DAY), ["something else"], now_ms=0)
        self.assertEqual(
            G.done_days(self.con, [DAY], M, G.gdigest(["something else"])),
            set())

    def test_new_stretches_make_it_unjudged_again(self):
        _run(self.con, reply=REPLY)
        _spans(self.con, rows=[{"start": "09:00", "end": "09:30", "cat": "code",
                                "activity": "late recording"}])
        self.assertEqual(G.done_days(self.con, [DAY], M, G.gdigest(TEXTS)),
                         set())


class WhichDaysTest(unittest.TestCase):
    def test_this_week_and_last(self):
        """Last week is for late-arriving recordings: Plaud uploads in a batch
        once a day, and a seven-hour delay across midnight was measured."""
        con = _con()
        G.save_goals(con, "2026-W38", TEXTS, now_ms=0)
        G.save_goals(con, "2026-W37", TEXTS, now_ms=0)
        days = G.attrib_days(con, week="2026-W38")
        self.assertEqual(len(days), 14)
        self.assertEqual(days[0], "2026-09-07")
        self.assertEqual(days[-1], "2026-09-20")

    def test_a_week_without_goals_is_skipped_whole(self):
        con = _con()
        G.save_goals(con, "2026-W38", TEXTS, now_ms=0)
        self.assertEqual(G.attrib_days(con, week="2026-W38"),
                         [f"2026-09-{d}" for d in range(14, 21)])

    def test_no_goals_anywhere_means_nothing_to_do(self):
        self.assertEqual(G.attrib_days(_con(), week="2026-W38"), [])


class ThePluginTest(unittest.TestCase):
    def test_the_registry_finds_it(self):
        self.assertIn(G.PLUGIN, registry.analyzers())

    def test_it_is_on_by_default(self):
        """The one bundled plugin that is on by default: it only reads and
        draws, and sends nothing anywhere."""
        self.assertTrue(registry.enabled(G.PLUGIN, {}))

    def test_it_declares_one_window_a_day(self):
        self.assertEqual(analyzer.resolve_window(P.WINDOW), [(0, 1440)])

    def test_it_reads_inferred_stretches(self):
        self.assertEqual(analyzer.resolve_input(P.INPUT), ("spans", None))


class NoNodeNoCloudflareTest(unittest.TestCase):
    def test_attribution_starts_no_subprocess(self):
        """It ran `node` on functions/api/attribute.js so that one prompt served
        both callers. There is one caller now."""
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.exists(os.path.join(root, "umbra", "functions")))
        self.assertFalse(os.path.exists(os.path.join(root, "umbra", "attrib.py")))

    def test_no_module_reads_cloudflare_credentials_any_more(self):
        import os
        root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "umbra")
        bad = []
        for cur, _dirs, files in os.walk(root):
            for f in files:
                if not f.endswith((".py", ".js")):
                    continue
                path = os.path.join(cur, f)
                with open(path, encoding="utf-8") as fh:
                    if "CLOUDFLARE_API_TOKEN" in fh.read():
                        bad.append(os.path.relpath(path, root))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
