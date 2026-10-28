"""The counting layers under the review plugin: nothing here calls a model.

    fold      records -> actions        (a search is 29 rows of visits)
    recur     actions -> a rhythm       (three days at least; next expected)
    burst     a few dense days on one site, then gone
    project   a batch of new sites that came up together
    offline   what happened away from the screen
    behavior  the role you play in an instruction, not its content

Every number these produce can be traced back to raw records, which is the
point of keeping the model out of them.
"""

import datetime as dt
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from umbra import db
from umbra.plugins.review import behavior, burst, fold, offline, project, recur

MIN = 60_000
D = dt.date


def _ms(day: str, hour: int = 12, minute: int = 0) -> int:
    return int(dt.datetime.fromisoformat(
        f"{day}T{hour:02d}:{minute:02d}").timestamp() * 1000)


def _days(start, step, n):
    return [D.fromisoformat(start) + dt.timedelta(days=step * i) for i in range(n)]


def _run(start, n, step=1):
    return [_ms((D.fromisoformat(start) + dt.timedelta(days=i * step)).isoformat())
            for i in range(n)]


class FoldTest(unittest.TestCase):
    """Counting rows counts records; people care about actions. One search was
    29 visits in 87 seconds, and a number fourteen times too big looks exactly
    as believable as the right one."""

    def test_rows_within_the_gap_are_one_action(self):
        rows = [(1_000_000 + i * 3_000, "google/search") for i in range(29)]
        f = fold.fold(rows, gap_min=10)
        self.assertEqual(f.actions["google/search"], [1_000_000])
        self.assertEqual((f.n_actions, f.n_raw), (1, 29))

    def test_across_the_gap_is_two(self):
        f = fold.fold([(0, "a"), (5 * MIN, "a"), (60 * MIN, "a")], gap_min=10)
        self.assertEqual(f.actions["a"], [0, 60 * MIN])

    def test_the_gap_counts_from_the_previous_row(self):
        """A click every nine minutes for an hour is one long action, not seven."""
        f = fold.fold([(i * 9 * MIN, "a") for i in range(7)], gap_min=10)
        self.assertEqual(f.n_actions, 1)

    def test_the_ratio_comes_with_the_count(self):
        self.assertEqual(fold.fold([(i * 1_000, "a") for i in range(10)], 10).ratio, 10.0)

    def test_empty_is_a_ratio_of_one(self):
        f = fold.fold([], gap_min=10)
        self.assertEqual((f.n_raw, f.n_actions, f.ratio), (0, 0, 1.0))

    def test_out_of_order_input_is_folded_in_time_order(self):
        f = fold.fold([(60 * MIN, "a"), (0, "a"), (3 * MIN, "a")], gap_min=10)
        self.assertEqual(f.actions["a"], [0, 60 * MIN])

    def test_the_same_sentence_from_butler_and_locally_is_kept_once(self):
        kept = fold.dedupe_agent([(1_000_000, "merge this branch into main")],
                                 [(1_060_000, "merge this branch into main")])
        self.assertEqual([k[2] for k in kept], ["local"])

    def test_outside_the_window_it_is_said_twice(self):
        self.assertEqual(len(fold.dedupe_agent([(0, "run it again")],
                                               [(3600_000, "run it again")])), 2)

    def test_days_are_local_calendar_days(self):
        self.addCleanup(time.tzset)
        with mock.patch.dict("os.environ", {"TZ": "UTC"}):
            time.tzset()
            self.assertEqual(str(fold.day_of(86_400_000 - 1)), "1970-01-01")
            self.assertEqual(str(fold.day_of(86_400_000)), "1970-01-02")


class RecurTest(unittest.TestCase):
    """Three points make a rhythm. Two are one gap, and "bought on 4 July and 17
    August" looks exactly like "every six weeks" on two points."""

    def test_fewer_than_three_days_is_no_rhythm(self):
        self.assertIsNone(recur.cadence(_days("2026-08-01", 7, 2)))

    def test_many_times_on_one_day_is_one_point(self):
        self.assertIsNone(recur.cadence([D(2026, 8, 1)] * 9))

    def test_weekly(self):
        c = recur.cadence(_days("2026-08-01", 7, 5))
        self.assertEqual((c["name"], c["median_gap"], c["regular"]), ("weekly", 7, True))
        self.assertEqual(c["next_expected"], D(2026, 9, 5))

    def test_every_two_weeks_and_monthly(self):
        self.assertEqual(recur.cadence(_days("2026-06-01", 14, 5))["name"], "every two weeks")
        self.assertEqual(recur.cadence(_days("2026-04-01", 30, 4))["name"], "monthly")

    def test_two_clusters_are_not_daily(self):
        """1st, 2nd, 20th, 21st: the median gap is 1, but there is no rhythm."""
        ds = [D(2026, 8, 1), D(2026, 8, 2), D(2026, 8, 20), D(2026, 8, 21)]
        self.assertFalse(recur.cadence(ds)["regular"])

    def test_irregular_is_returned_and_marked(self):
        """"Cannot tell the rhythm" and "it does not happen" must not look alike."""
        self.assertIsNotNone(recur.cadence([D(2026, 8, 1), D(2026, 8, 2), D(2026, 8, 20)]))

    def test_a_four_week_window_cannot_see_every_two_weeks(self):
        """Arithmetic, not a bug: whoever shortens the window sees this first."""
        full = _days("2026-06-01", 14, 6)
        self.assertIsNotNone(recur.cadence(full))
        self.assertIsNone(recur.cadence([d for d in full if d >= D(2026, 8, 3)]))

    def test_most_recent_first_not_most_frequent(self):
        base = dt.datetime(2026, 8, 1, 12)

        def mk(start, step, n):
            return [int((base + dt.timedelta(days=start + step * i)).timestamp() * 1000)
                    for i in range(n)]
        got = [r["key"] for r in recur.detect({"old": mk(0, 7, 6), "new": mk(40, 7, 3)})]
        self.assertEqual(got[0], "new")


class BurstTest(unittest.TestCase):
    """A visa application, a trip, a clinic: a few dense days, then gone. The
    rhythm detector throws these away as irregular."""

    def test_a_few_dense_days_is_a_burst(self):
        got = burst.detect({"visa": _run("2026-08-04", 8)}, today="2026-08-26")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["days"], 8)

    def test_sparse_is_not(self):
        self.assertEqual(burst.detect({"x": _run("2026-04-01", 8, step=18)},
                                      today="2026-08-26"), [])

    def test_a_long_habit_is_not_a_burst(self):
        self.assertEqual(burst.detect({"dash": _run("2026-05-01", 100)},
                                      today="2026-08-26"), [])

    def test_too_few_days_is_not(self):
        self.assertEqual(burst.detect({"x": _run("2026-08-04", 3)}, today="2026-08-26"), [])

    def test_ongoing_and_finished_are_told_apart(self):
        got = {b["key"]: b for b in burst.detect(
            {"done": _run("2026-07-01", 6), "live": _run("2026-08-20", 6)},
            today="2026-08-26")}
        self.assertEqual((got["done"]["state"], got["live"]["state"]), ("done", "active"))

    def test_many_on_one_day_is_one_day(self):
        one = [_ms("2026-08-04", 9, m) for m in range(30)]
        self.assertEqual(burst.detect({"x": one}, today="2026-08-26"), [])


class ProjectTest(unittest.TestCase):
    """Sites that came up together and stayed active together are one thing,
    even when no single site is enough on its own."""

    def _cluster(self, n=4):
        return {f"s{i}.example.com": [_ms((D(2026, 7, 15) + dt.timedelta(days=d * 2))
                                          .isoformat()) for d in range(4)]
                for i in range(n)}

    def test_new_sites_together_are_a_project(self):
        got = project.detect(self._cluster(), today="2026-08-26")
        self.assertEqual(len(got), 1)
        self.assertEqual(len(got[0]["keys"]), 4)

    def test_two_sites_are_not_enough(self):
        self.assertEqual(project.detect(self._cluster(2), today="2026-08-26"), [])

    def test_a_long_lived_site_is_not_glue(self):
        """A three-month site overlaps everything and once pulled 129 sites
        into one "project"."""
        acts = self._cluster(2)
        acts["daily.example.com"] = _run("2026-05-01", 110)
        self.assertEqual(project.detect(acts, today="2026-08-26"), [])

    def test_local_addresses_are_not_glue(self):
        acts = self._cluster(2)
        acts["localhost:3000"] = self._cluster(1)["s0.example.com"]
        self.assertEqual(project.detect(acts, today="2026-08-26"), [])

    def test_sites_already_there_at_the_window_start_are_not_new(self):
        acts = {f"s{i}.example.com": _run("2026-05-01", 4, step=2) for i in range(4)}
        self.assertEqual(project.detect(acts, today="2026-08-26",
                                        window_start="2026-05-01"), [])


class OfflineTest(unittest.TestCase):
    """Away from the screen: places, workouts, calls, what you listened to and
    read. Without it every conclusion would be about the screen."""

    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))

    def add(self, source, kind, payload, day="2026-08-03", n=0):
        self.con.execute("INSERT INTO events (uid, ts, source, kind, payload) "
                         "VALUES (?,?,?,?,?)",
                         (f"{source}:{day}:{n}", _ms(day, 12, n), source, kind,
                          payload if isinstance(payload, str) else json.dumps(payload)))

    def all(self):
        return offline.actions(self.con, _ms("2026-08-01"), _ms("2026-08-31"))

    def test_places_sports_calls_messages_listening_reading(self):
        self.add("location", "stay", {"place": "Gym", "named": True})
        self.add("whoop", "workout", {"sport": "swimming"}, n=1)
        self.add("intent", "intent", {"verb": "StartCall", "app": "mobilephone"}, n=2)
        self.add("intent", "intent", {"verb": "SendMessage", "app": "MobileSMS"}, n=3)
        self.add("media", "playing", {"title": "An audiobook"}, n=4)
        self.add("weread", "reading", {"book": "A book"}, n=5)
        self.assertEqual(set(self.all()), {"at:Gym", "workout:swimming", "call",
                                           "message:MobileSMS", "listening:An audiobook",
                                           "reading:A book"})

    def test_an_unnamed_stay_is_not_an_action(self):
        self.add("location", "stay", {"place": "p:9f2c", "named": False})
        self.assertEqual(self.all(), {})

    def test_once_a_day(self):
        self.add("location", "stay", {"place": "Gym", "named": True}, n=0)
        self.add("location", "stay", {"place": "Gym", "named": True}, n=1)
        self.assertEqual(len(self.all()["at:Gym"]), 1)

    def test_a_bad_row_is_skipped(self):
        self.add("whoop", "workout", "{not json", n=0)
        self.add("whoop", "workout", {"sport": "swimming"}, n=1)
        self.assertEqual(set(self.all()), {"workout:swimming"})


class BehaviorTest(unittest.TestCase):
    """"What's the status" was asked 186 times in nine weeks, never about the
    same thing. What repeats is the role, not the content."""

    def test_asking_for_status(self):
        for t in ("进度怎么样了", "跑完了吗", "what's the status of the deploy",
                  "is it done yet?", "any progress on this?"):
            self.assertEqual(behavior.classify(t), "ask_status", t)

    def test_approving(self):
        for t in ("按你说的来", "就这么做", "sounds good, go ahead", "approve"):
            self.assertEqual(behavior.classify(t), "approve", t)

    def test_nudging(self):
        for t in ("继续", "好的，继续跑", "continue", "keep going"):
            self.assertEqual(behavior.classify(t), "nudge", t)

    def test_fix_or_retry(self):
        for t in ("重新跑一下", "再试一次", "try again", "roll back that change"):
            self.assertEqual(behavior.classify(t), "fix_retry", t)

    def test_real_work_is_not_misread(self):
        for t in ("帮我写一个解析 CSV 的函数", "write a parser for these CSV files"):
            self.assertEqual(behavior.classify(t), "unclassified", t)

    def test_order_is_priority(self):
        self.assertEqual(behavior.classify("继续跑吧，跑完了吗？"), "ask_status")

    def test_the_unclassified_share_is_reported(self):
        s = behavior.summarize([(0, "进度如何"), (1, "write a parser")])
        self.assertEqual(s["unclassified_share"], 0.5)

    def test_weekly_counts_show_a_trend(self):
        s = behavior.summarize([(_ms("2026-08-05"), "status?"),
                                (_ms("2026-08-11"), "status?"),
                                (_ms("2026-08-12"), "进度呢")])
        self.assertEqual(s["weekly"]["ask_status"], {"2026-W32": 1, "2026-W33": 2})

    def test_every_type_has_a_lever(self):
        for t in behavior.TYPES:
            self.assertTrue(behavior.LEVERS[t], t)


if __name__ == "__main__":
    unittest.main()
