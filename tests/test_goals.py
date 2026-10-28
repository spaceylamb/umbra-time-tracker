"""Three priorities a week, and the model crediting time to them.

Three layers; do not conflate them (the easiest thing to get wrong here):

    goals         the three sentences you wrote    once a week    goals table
    attribution   which goals each stretch served  model-judged   analyzer_rows
    hours         the attribution, summed          arithmetic     not stored

The judging is the umbra/plugins/goals/ plugin, tested in test_plugin_goals.py.
This file covers the goal texts themselves and the path that reads attribution
— including rows written to goal_attrib before the move, which must still read.

Attribution **is a record, not a cache**: one of its inputs is the model itself,
which you do not control. As a cache, reopening this week next year with
another model would quietly turn "11.2 hours that week" into some other number,
with no warning. So it is stored with the model, like inferences, and history
never changes on its own.
"""

import os
import sqlite3
import unittest

from umbra import db, goals

def with_conf(text=""):
    """Pin the configuration to an empty temp directory.

    Orchestration tests **must** do this, or they drift with the machine's own
    configuration: with [schedule].split_voice on, sync runs only import + infer
    (the rest belongs to `umbra voice`), so an assertion like "sync pulls goals"
    goes red with the behaviour under test unchanged. A test that drifts with the
    environment is harder to debug than no test.
    """
    import tempfile
    from unittest import mock

    from umbra import config
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    return mock.patch.object(config, "CONFIG_DIR", d)



def _con():
    con = sqlite3.connect(":memory:")
    con.executescript(db.SCHEMA)
    return con


class WeeksTest(unittest.TestCase):
    def test_iso_weeks_start_on_monday(self):
        # 2026-08-17 is a Monday and 08-23 a Sunday — the same week
        self.assertEqual(goals.week_of("2026-08-17"), goals.week_of("2026-08-23"))
        # one day later is the next week
        self.assertNotEqual(goals.week_of("2026-08-23"), goals.week_of("2026-08-24"))

    def test_weeks_sort_as_strings(self):
        """Plain string comparison must hold — the page and the CLI sort by it."""
        ws = [goals.week_of(d) for d in ("2026-01-05", "2026-08-17", "2026-12-28")]
        self.assertEqual(ws, sorted(ws))

    def test_the_turn_of_the_year_does_not_go_backwards(self):
        """2027-01-01 is a Friday and in ISO terms the last week of 2026 — but
        sorting must not go backwards."""
        a, b = goals.week_of("2026-12-28"), goals.week_of("2027-01-04")
        self.assertLess(a, b)


class GoalsTest(unittest.TestCase):
    def test_saved_goals_read_back(self):
        con = _con()
        goals.save_goals(con, "2026-W34", ["把 Umbra 做完", "推进 BFS", "打磨 Butler"])
        self.assertEqual(goals.load_goals(con, "2026-W34")[0], "把 Umbra 做完")

    def test_a_week_never_set_is_empty(self):
        """Empty is not an error. A week without goals yet is simply empty."""
        self.assertEqual(goals.load_goals(_con(), "2026-W01"), [])

    def test_setting_again_replaces_the_week_rather_than_adding(self):
        con = _con()
        goals.save_goals(con, "2026-W34", ["a", "b", "c"])
        goals.save_goals(con, "2026-W34", ["x"])
        self.assertEqual(goals.load_goals(con, "2026-W34"), ["x"])

    def test_weeks_do_not_affect_each_other(self):
        con = _con()
        goals.save_goals(con, "2026-W34", ["旧的"])
        goals.save_goals(con, "2026-W35", ["新的"])
        self.assertEqual(goals.load_goals(con, "2026-W34"), ["旧的"])


class GoalDigestTest(unittest.TestCase):
    def test_the_same_goals_give_the_same_digest(self):
        self.assertEqual(goals.gdigest(["a", "b"]), goals.gdigest(["a", "b"]))

    def test_one_changed_character_changes_the_digest(self):
        """The digest is the attribution cache key. Changed goals must be judged
        again, never reuse the old judgement."""
        self.assertNotEqual(goals.gdigest(["把 Umbra 做完"]),
                            goals.gdigest(["把 Umbra 做完了"]))

    def test_a_different_order_is_a_different_set(self):
        """Goal 1 and goal 3 are not the same thing — attribution is stored by
        position."""
        self.assertNotEqual(goals.gdigest(["a", "b"]), goals.gdigest(["b", "a"]))


class AttributionTest(unittest.TestCase):
    """Reading attribution, from both stores.

    Everything written since the plugin move is in the core's analyzer_rows;
    everything before it is in goal_attrib and is never rewritten. Both have to
    read back the same shape, and a day must never be counted from both.
    """

    ROWS = [  # (a, b, goal_idx, why)
        (600, 630, 1, "调试 Shadow"),
        (600, 630, 3, "同时在做 Butler"),      # one stretch credited to two goals
        (900, 930, 1, "开发 Umbra"),
    ]

    def _seed(self, con, model="m1", gdig="d1", day="2026-08-17", rows=None):
        """Write rows the way the old Cloudflare path did, straight into the
        frozen tables. Nothing in umbra writes them any more, which is the
        point: these are somebody's existing database, and they still have to
        read."""
        rows = self.ROWS if rows is None else rows
        with con:
            con.executemany(
                "INSERT OR REPLACE INTO goal_attrib "
                "(day, a_min, b_min, goal_idx, model, gdig, why) "
                "VALUES (?,?,?,?,?,?,?)",
                [(day, a, b, g, model, gdig, w) for a, b, g, w in rows])
            con.execute("INSERT OR REPLACE INTO goal_runs "
                        "(day, model, gdig, at_ms) VALUES (?,?,?,0)",
                        (day, model, gdig))

    def test_rows_written_before_the_move_still_read(self):
        con = _con(); self._seed(con)
        got = goals.load_attrib(con, "2026-08-17", "m1", "d1")
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]["why"], "调试 Shadow")

    def test_a_stretch_credited_to_two_goals_counts_fully_for_both(self):
        """Decided by the user on 2026-08-23: overlap is allowed, not split.
        10:00–10:30 advancing two things counts 30 minutes for each — splitting
        it 15 + 15 would be invented precision."""
        con = _con(); self._seed(con)
        t = goals.totals(con, ["2026-08-17"], "m1", "d1")
        self.assertEqual(t[1], 60)      # goal 1: two stretches of 30
        self.assertEqual(t[3], 30)      # goal 3: one stretch

    def test_an_uncredited_goal_is_0_not_a_missing_key(self):
        """"No time at all this week" and "not found" must be told apart — the
        former shows 0."""
        con = _con(); self._seed(con)
        self.assertEqual(goals.totals(con, ["2026-08-17"], "m1", "d1").get(2, 0), 0)

    def test_another_model_does_not_overwrite_the_old_judgement(self):
        con = _con(); self._seed(con)
        self._seed(con, model="m2", rows=[(600, 630, 1, "别的模型")])
        self.assertEqual(goals.totals(con, ["2026-08-17"], "m1", "d1")[1], 60)
        self.assertEqual(goals.totals(con, ["2026-08-17"], "m2", "d1")[1], 30)

    def test_changed_goals_do_not_overwrite_the_old_judgement(self):
        """A new gdig is a separate judgement. The old one stays, so "what was it
        before I changed them" can be answered."""
        con = _con(); self._seed(con)
        self._seed(con, gdig="d2", rows=[(600, 630, 1, "新目标下")])
        self.assertEqual(goals.totals(con, ["2026-08-17"], "m1", "d1")[1], 60)
        self.assertEqual(goals.totals(con, ["2026-08-17"], "m1", "d2")[1], 30)

    def test_a_day_judged_in_the_new_store_no_longer_reads_the_old_table(self):
        """Strategy C in one assertion: once the plugin has judged a day, that
        day comes from the new store alone. Reading both and adding them up
        would double every stretch judged under each."""
        from umbra import analyzer
        con = _con(); self._seed(con)
        analyzer.save_rows(con, goals.PLUGIN, "2026-08-17", "m1", 0, "dig",
                           [{"a": 600, "b": 610, "goal": 1, "gdig": "d1",
                             "why": "重判过了"}], now_ms=0)
        t = goals.totals(con, ["2026-08-17"], "m1", "d1")
        self.assertEqual(t[1], 10)
        self.assertNotIn(3, t)

    def test_new_store_rows_for_other_goal_text_do_not_count(self):
        """A judgement made against goals you have since rewritten is about a
        goal 3 that no longer exists. It stays on disk; it does not get summed
        into the goals you have now."""
        from umbra import analyzer
        con = _con()
        analyzer.save_rows(con, goals.PLUGIN, "2026-08-17", "m1", 0, "dig",
                           [{"a": 600, "b": 630, "goal": 1, "gdig": "old",
                             "why": "旧目标下"}], now_ms=0)
        self.assertEqual(goals.totals(con, ["2026-08-17"], "m1", "new"), {})


class JudgedDaysTest(unittest.TestCase):
    """Judged and unjudged days must be told apart, or "0.0 h" reads as an answer."""

    def test_old_markers_count_including_days_with_an_empty_result(self):
        con = _con()
        with con:
            con.executemany(
                "INSERT INTO goal_runs (day, model, gdig, at_ms) VALUES (?,?,?,0)",
                [("2026-08-17", "m1", "d1"), ("2026-08-18", "m1", "d1")])
        done = goals.done_days(con, ["2026-08-17", "2026-08-18", "2026-08-19"],
                               "m1", "d1")
        self.assertEqual(done, {"2026-08-17", "2026-08-18"})

    def test_old_markers_respect_the_goal_text(self):
        con = _con()
        with con:
            con.execute("INSERT INTO goal_runs (day, model, gdig, at_ms) "
                        "VALUES ('2026-08-17', 'm1', 'd1', 0)")
        self.assertEqual(goals.done_days(con, ["2026-08-17"], "m1", "d2"), set())


if __name__ == "__main__":
    unittest.main()


class SessionsTest(unittest.TestCase):
    def test_adjacent_half_hours_are_one_session(self):
        from umbra import goals as G
        rows = [{"a": 1080, "b": 1110}, {"a": 1110, "b": 1140}, {"a": 1140, "b": 1155}]
        self.assertEqual(G.sessions(rows), 1)

    def test_a_real_break_makes_two(self):
        from umbra import goals as G
        self.assertEqual(G.sessions([{"a": 420, "b": 480}, {"a": 1080, "b": 1140}]), 2)
