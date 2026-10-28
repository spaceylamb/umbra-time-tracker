"""Timezone history, and the "day" that follows from it.

events.ts is UTC epoch ms and always right. But "what time" and "which day" are
functions of the local timezone, and the machine's timezone travels with its
owner. This file pins three things:

  1. offset_at — the offset in force at a moment. "When did I get up, when did
     I sleep" rests on it, and it does not care how long the day was, so it
     compares across timezones.
  2. day_starts — days cut along the real time axis. **That they join end to
     end is guaranteed by construction**: no gap and no overlap, the two
     failures that computing each day's bounds on its own always produces.
  3. A day that is not 24 hours is not a travel quirk. Daylight saving gives
     two a year: 2026-03-08 is 23 hours and 2026-11-01 is 25.
"""

import datetime as dt
import os
import sqlite3
import time
import unittest
from unittest import mock

from umbra import tz


def ms(iso: str) -> int:
    """'2026-10-05T17:00' (UTC) -> epoch ms."""
    return int(dt.datetime.fromisoformat(iso).replace(
        tzinfo=dt.UTC).timestamp() * 1000)


LA = "America/Los_Angeles"
SH = "Asia/Shanghai"
PDT, PST, CST = -25200, -28800, 28800

HOUR = 3600_000


class OffsetHistoryTest(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(tz.SCHEMA)

    def test_the_change_applies_from_its_own_moment(self):
        h = [(ms("2026-10-05T17:00"), CST, SH)]
        self.assertEqual(tz.offset_at(h, ms("2026-10-05T16:59")), CST)  # before all → the earliest
        self.assertEqual(tz.offset_at(h, ms("2026-10-05T17:00")), CST)
        self.assertEqual(tz.offset_at(h, ms("2026-10-05T17:01")), CST)

    def test_before_a_change_the_previous_one_applies(self):
        h = [(ms("2026-10-01T00:00"), PDT, LA),
             (ms("2026-10-05T17:00"), CST, SH)]
        self.assertEqual(tz.offset_at(h, ms("2026-10-05T16:59")), PDT)
        self.assertEqual(tz.offset_at(h, ms("2026-10-05T17:00")), CST)

    def test_before_every_record_the_earliest_applies(self):
        """For history with no record the only assumption is "as when first
        seen" — the one choice that invents nothing."""
        h = [(ms("2026-10-01T00:00"), PDT, LA)]
        self.assertEqual(tz.offset_at(h, ms("2020-01-01T00:00")), PDT)

    def test_with_no_record_the_machine_timezone_applies(self):
        self.addCleanup(time.tzset)
        self.enterContext(mock.patch.dict(os.environ, {"TZ": "UTC"}))
        time.tzset()
        self.assertEqual(tz.offset_at([], ms("2026-10-05T12:00")), 0)

    def test_record_writes_only_on_a_change(self):
        """Called every sync. Writing a row with nothing changed would pile up
        noise, and "when did it change" is this table's only information."""
        self.assertTrue(tz.record(self.con, ms("2026-10-01T00:00"), PDT, LA))
        self.assertFalse(tz.record(self.con, ms("2026-10-01T06:00"), PDT, LA))
        self.assertFalse(tz.record(self.con, ms("2026-10-02T06:00"), PDT, LA))
        self.assertTrue(tz.record(self.con, ms("2026-10-05T17:00"), CST, SH))
        self.assertEqual(len(tz.history(self.con)), 2)

    def test_a_trip_entered_by_hand_slots_into_history(self):
        tz.record(self.con, ms("2026-10-01T00:00"), PDT, LA)
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH, src="config")
        self.assertEqual(tz.offset_at(tz.history(self.con),
                                      ms("2026-10-06T00:00")), CST)


class DaysAlongTheTimeAxisTest(unittest.TestCase):
    """The core constraint of the whole change."""

    def starts(self, hist, a, b):
        return tz.day_starts(hist, ms(a), ms(b))

    def test_without_travel_a_day_is_twenty_four_hours(self):
        h = [(ms("2026-09-01T00:00"), PDT, LA)]
        s = dict(self.starts(h, "2026-10-01T00:00", "2026-10-05T00:00"))
        # each day starts at local midnight (= 07:00 UTC this season)
        self.assertEqual(s["2026-10-02"], ms("2026-10-02T07:00"))
        self.assertEqual(s["2026-10-03"], ms("2026-10-03T07:00"))

    def test_days_join_end_to_end(self):
        """A gap and an overlap are a symmetric pair of failures, and bounds
        computed per day always have one. Scanning one way leaves only one
        possibility: continuous."""
        h = [(ms("2026-09-01T00:00"), PDT, LA),
             (ms("2026-10-05T17:00"), CST, SH),
             (ms("2026-10-20T08:00"), PDT, LA)]
        s = self.starts(h, "2026-09-28T00:00", "2026-10-28T00:00")
        for (_, a), (_, b) in zip(s, s[1:]):
            self.assertLess(a, b, "day starts must strictly increase")
        labels = [d for d, _ in s]
        self.assertEqual(labels, sorted(labels), "date labels must not go back")
        self.assertEqual(len(labels), len(set(labels)), "no day may appear twice")

    def test_flying_east_the_day_is_shorter(self):
        """Leaving at 10:00 PDT, it is already the next day on landing in
        Shanghai — that day lasted ten hours. Padding it to 24 would be false."""
        h = [(ms("2026-09-01T00:00"), PDT, LA),
             (ms("2026-10-05T17:00"), CST, SH)]
        s = dict(self.starts(h, "2026-10-01T00:00", "2026-10-10T00:00"))
        self.assertEqual(s["2026-10-05"], ms("2026-10-05T07:00"))
        self.assertEqual(s["2026-10-06"], ms("2026-10-05T17:00"))
        self.assertEqual((s["2026-10-06"] - s["2026-10-05"]) // HOUR, 10)

    def test_flying_back_west_the_day_is_longer(self):
        """Leaving Shanghai at 16:00, it is still the same morning on landing in
        California — thirty-nine hours of one day."""
        h = [(ms("2026-09-01T00:00"), CST, SH),
             (ms("2026-10-20T08:00"), PDT, LA)]
        s = dict(self.starts(h, "2026-10-16T00:00", "2026-10-24T00:00"))
        self.assertEqual(s["2026-10-20"], ms("2026-10-19T16:00"))
        self.assertEqual(s["2026-10-21"], ms("2026-10-21T07:00"))
        self.assertEqual((s["2026-10-21"] - s["2026-10-20"]) // HOUR, 39)


class DaylightSavingTest(unittest.TestCase):
    """Days that are not 24 hours were always in the store; a clamp of
    min(1440, …) hid them while those two days held almost nothing."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(tz.SCHEMA)

    def test_the_autumn_day_is_twenty_five_hours(self):
        tz.record(self.con, ms("2026-09-01T00:00"), PDT, LA)
        tz.record(self.con, ms("2026-11-01T09:00"), PST, LA)
        self.assertEqual(tz.day_minutes(self.con, "2026-11-01"), 25 * 60)

    def test_the_spring_day_is_twenty_three_hours(self):
        tz.record(self.con, ms("2026-01-01T00:00"), PST, LA)
        tz.record(self.con, ms("2026-03-08T10:00"), PDT, LA)
        self.assertEqual(tz.day_minutes(self.con, "2026-03-08"), 23 * 60)

    def test_daylight_saving_is_not_a_travel_day(self):
        """The test is **whether the zone changed**, not the offset. On the
        offset alone two days a year would be dropped from trends as travel."""
        tz.record(self.con, ms("2026-09-01T00:00"), PDT, LA)
        tz.record(self.con, ms("2026-11-01T09:00"), PST, LA)
        self.assertFalse(tz.is_travel_day(self.con, "2026-11-01"))


class DayBoundsTest(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(tz.SCHEMA)
        tz.record(self.con, ms("2026-09-01T00:00"), PDT, LA)
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)

    def test_bounds_agree_with_day_starts(self):
        lo, hi = tz.bounds(self.con, "2026-10-05")
        self.assertEqual(lo, ms("2026-10-05T07:00"))
        self.assertEqual(hi + 1, ms("2026-10-05T17:00"))

    def test_adjacent_days_meet_exactly(self):
        _, hi = tz.bounds(self.con, "2026-10-05")
        lo2, _ = tz.bounds(self.con, "2026-10-06")
        self.assertEqual(hi + 1, lo2)

    def test_day_minutes_follow_the_bounds(self):
        self.assertEqual(tz.day_minutes(self.con, "2026-10-05"), 600)
        self.assertEqual(tz.day_minutes(self.con, "2026-10-07"), 1440)

    def test_a_travel_day_is_recognised(self):
        self.assertTrue(tz.is_travel_day(self.con, "2026-10-05"))
        self.assertFalse(tz.is_travel_day(self.con, "2026-10-09"))


class SeedReproducesTheOldDaysTest(unittest.TestCase):
    """The most important regression guarantee of the change.

    A database with no travel record — every existing one — must cut days
    **exactly** as the old analyze.day_bounds() did, day by day. One day off
    means stored inferences and digests no longer match their day, and the
    digest is the inference cache key: a re-cut history would be re-inferred
    at real cost.
    """

    def setUp(self):
        self.addCleanup(time.tzset)
        self.enterContext(mock.patch.dict(os.environ, {"TZ": LA}))
        time.tzset()
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(tz.SCHEMA)

    def test_after_seeding_a_whole_year_matches_the_old_algorithm(self):
        """The idea: with no travel yet, **the machine's daylight-saving history
        is the whole truth**. Ask the OS for each day's offset and record it."""
        tz.seed(self.con, ms("2025-12-01T00:00"), ms("2026-12-31T00:00"))
        d, end = dt.date(2025, 12, 2), dt.date(2026, 12, 30)
        checked = 0
        while d <= end:
            day = d.isoformat()
            self.assertEqual(tz.bounds(self.con, day), tz.naive_bounds(day),
                             f"{day}'s bounds differ from the old algorithm")
            d += dt.timedelta(days=1)
            checked += 1
        self.assertGreater(checked, 360)

    def test_seeding_records_both_daylight_saving_days(self):
        tz.seed(self.con, ms("2025-12-01T00:00"), ms("2026-12-31T00:00"))
        self.assertEqual(tz.day_minutes(self.con, "2026-03-08"), 23 * 60)
        self.assertEqual(tz.day_minutes(self.con, "2026-11-01"), 25 * 60)
        self.assertEqual(tz.day_minutes(self.con, "2026-06-15"), 24 * 60)

    def test_seeding_runs_only_on_an_empty_table(self):
        """migrate runs on every connect. Without this gate a trip entered by
        hand (src=config) would be overwritten from the machine's zone each time."""
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH, src="config")
        tz.seed(self.con, ms("2025-12-01T00:00"), ms("2026-12-31T00:00"))
        self.assertEqual(tz.history(self.con),
                         [(ms("2026-10-05T17:00"), CST, SH)])


class DayBoundsDelegatesTest(unittest.TestCase):
    """analyze.day_bounds goes through the history when given a connection,
    and falls back to the machine's zone without one."""

    def test_with_a_connection_bounds_come_from_the_history(self):
        from umbra import analyze
        con = sqlite3.connect(":memory:")
        con.executescript(tz.SCHEMA)
        tz.record(con, ms("2026-09-01T00:00"), PDT, LA)
        tz.record(con, ms("2026-10-05T17:00"), CST, SH)
        self.assertEqual(analyze.day_bounds("2026-10-05", con),
                         tz.bounds(con, "2026-10-05"))

    def test_without_one_it_is_the_machine_zone(self):
        from umbra import analyze
        self.assertEqual(analyze.day_bounds("2026-10-05"),
                         tz.naive_bounds("2026-10-05"))

    def test_db_connect_seeds_the_history(self):
        """A database opened by this version has a history to cut by from the
        first connect — an empty table would fall back to the machine's zone
        for every day and record nothing a trip could later be told from."""
        import tempfile

        from umbra import db
        con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.assertGreater(len(tz.history(con)), 0)


if __name__ == "__main__":
    unittest.main()
