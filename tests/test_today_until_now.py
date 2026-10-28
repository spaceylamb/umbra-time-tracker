"""Today is drawn up to now, and nothing after (ux.md, States).

Two things broke that on real data:

  · infer.merge asked the model to compose the whole day, and the model
    filled the hours that had not happened with an "Unknown" event — which
    then counted as covered: "covered 1,440 of 1,440" at three in the
    afternoon.
  · the coverage line summed event lengths and then subtracted the failed
    windows, so an event lying over a failed window was taken away twice and
    "the model said nothing about" went negative (-30 min).

The minutes are now accounted for once each, on the server, at read time —
"how much of today has passed" changes every minute, so it cannot live in the
cached day.
"""

import datetime as dt
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import api, db, infer, tz, viz


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(
        tzinfo=dt.UTC).timestamp() * 1000)


LA = "America/Los_Angeles"
PDT = -25200
DAY = "2026-10-05"


def _con():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    con.execute("DELETE FROM tz_marks")
    tz.record(con, ms("2026-09-01T00:00"), PDT, LA)
    return con


class ElapsedMinutesTest(unittest.TestCase):

    def setUp(self):
        self.con = _con()
        self.lo, _ = tz.bounds(self.con, DAY)

    def test_today_counts_up_to_now(self):
        now = self.lo + (15 * 60 + 20) * 60_000
        self.assertEqual(tz.elapsed_minutes(self.con, DAY, now), 15 * 60 + 20)

    def test_a_past_day_is_whole(self):
        self.assertEqual(tz.elapsed_minutes(self.con, DAY, self.lo + 3 * tz.DAY_MS),
                         tz.day_minutes(self.con, DAY))

    def test_a_future_day_has_nothing_yet(self):
        self.assertEqual(tz.elapsed_minutes(self.con, DAY, self.lo - 1), 0)


class MergeStopsAtNowTest(unittest.TestCase):
    """The second pass composes today only up to now."""

    ANSWER = json.dumps([
        {"start": "09:00", "end": "11:00", "cat": "code", "title": "before"},
        {"start": "14:30", "end": "16:00", "cat": "code", "title": "across now"},
        {"start": "16:00", "end": "24:00", "cat": "unknown", "title": "Unknown"},
    ])

    def setUp(self):
        self.con = _con()
        self.lo, _ = tz.bounds(self.con, DAY)

    def _merge(self, now_ms):
        spans = [{"start": "09:00", "end": "10:00", "cat": "code", "activity": "x"}]
        with mock.patch("time.time", return_value=now_ms / 1000):
            return infer.merge(self.con, DAY, spans, "m",
                               call=lambda p: self.ANSWER, dirty=True)

    def test_nothing_after_now_is_composed(self):
        evs = self._merge(self.lo + 15 * 3600_000)            # 15:00
        self.assertEqual([e["title"] for e in evs], ["before", "across now"])
        self.assertEqual(evs[-1]["end"], "15:00")

    def test_a_past_day_keeps_the_whole_answer(self):
        evs = self._merge(self.lo + 2 * tz.DAY_MS)
        self.assertEqual(len(evs), 3)


class MinutesAccountingTest(unittest.TestCase):
    """Every minute of [0, end) is one of: covered by an event, in a failed
    call's window, or something the model said nothing about. Counted once."""

    def test_an_event_over_a_failed_window_is_not_taken_away_twice(self):
        m = viz.minutes_of([{"a": 0, "b": 1440}],
                           [{"a": 60, "b": 90, "kind": "call"}], 1440)
        self.assertEqual(m, {"end": 1440, "covered": 1440, "failed": 0, "silent": 0})

    def test_the_three_add_up_and_none_is_negative(self):
        m = viz.minutes_of([{"a": 0, "b": 600}, {"a": 700, "b": 800}],
                           [{"a": 600, "b": 630, "kind": "call"}], 1440)
        self.assertEqual(m["covered"] + m["failed"] + m["silent"], 1440)
        self.assertEqual((m["covered"], m["failed"], m["silent"]), (700, 30, 710))

    def test_overlapping_events_count_once(self):
        m = viz.minutes_of([{"a": 0, "b": 100}, {"a": 50, "b": 150}], [], 1440)
        self.assertEqual(m["covered"], 150)

    def test_nothing_past_the_end_counts(self):
        m = viz.minutes_of([{"a": 0, "b": 1440}],
                           [{"a": 1200, "b": 1230, "kind": "call"}], 900)
        self.assertEqual(m, {"end": 900, "covered": 900, "failed": 0, "silent": 0})

    def test_a_refused_key_is_not_a_failed_call(self):
        """kind=auth is a refused key, said once at the top of the page — the
        coverage line counts failed calls only, as it did before."""
        m = viz.minutes_of([], [{"a": 0, "b": 30, "kind": "auth"}], 60)
        self.assertEqual((m["failed"], m["silent"]), (0, 60))


class DayCarriesTheMinutesTest(unittest.TestCase):
    """api.day adds the accounting at read time: today's end is now."""

    def setUp(self):
        self.con = _con()
        self.lo, _ = tz.bounds(self.con, DAY)
        self.payload = {"ev": {"m": [{"a": 0, "b": 1440}]}, "evuse": "m",
                        "failed": {"windows": [{"a": 60, "b": 90, "kind": "call"}]}}

    def _day(self, now_ms):
        with mock.patch.object(viz, "day_cached", return_value=dict(self.payload)), \
             mock.patch("time.time", return_value=now_ms / 1000):
            return api.day(DAY, self.con)

    def test_today_counts_to_now(self):
        d = self._day(self.lo + 15 * 3600_000)
        self.assertEqual(d["minutes"], {"end": 900, "covered": 900, "failed": 0, "silent": 0})

    def test_a_past_day_counts_the_whole_day(self):
        d = self._day(self.lo + 2 * tz.DAY_MS)
        self.assertEqual(d["minutes"]["end"], 1440)

    def test_a_day_with_no_events_has_no_coverage_to_report(self):
        self.payload = {"failed": {"windows": []}}
        self.assertNotIn("minutes", self._day(self.lo + 2 * tz.DAY_MS))


if __name__ == "__main__":
    unittest.main()
