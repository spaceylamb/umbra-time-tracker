"""A day is no longer always 1440 minutes.

`1440` appeared in a dozen places, playing **three different roles**, and each
needs a different fix:

    window tiling        digest.windows / analyzer.resolve_window / blocks /
                         inference block counts
    bounds checks        inference.expand / infer.merge
    coverage denominator analyze.coverage's C / viz.explained_pct

Days that are not 24 hours are not a travel quirk — daylight saving gives two a
year. A defensive `min(1440, …)` clamp hid them; it let an overflow pass without
an error, so nobody learned the assumption no longer held.
"""

import datetime as dt
import os
import sqlite3
import tempfile
import unittest

from umbra import analyze, analyzer, blocks, db, digest, inference, render, tz, viz


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(
        tzinfo=dt.UTC).timestamp() * 1000)


LA, SH = "America/Los_Angeles", "Asia/Shanghai"
PDT, PST, CST = -25200, -28800, 28800


def con_with(marks):
    con = sqlite3.connect(":memory:")
    con.executescript(db.SCHEMA)
    con.execute("DELETE FROM tz_marks")
    for t, off, z in marks:
        tz.record(con, t, off, z)
    return con


class WindowTilingTest(unittest.TestCase):

    def test_a_short_day_has_short_windows(self):
        self.assertEqual(digest.windows(30, 600)[-1], (570, 600))

    def test_a_long_day_is_tiled_to_the_end(self):
        w = digest.windows(30, 2340)
        self.assertEqual(w[-1], (2310, 2340))
        self.assertEqual(len(w), 78)

    def test_without_a_length_it_is_still_a_whole_day(self):
        """The default must stay 1440 — plenty of callers pass no length."""
        self.assertEqual(digest.windows(30)[-1], (1410, 1440))

    def test_a_twenty_five_hour_day_has_an_hour_more_of_windows(self):
        self.assertEqual(len(digest.windows(30, 1500)), 50)

    def test_a_day_not_a_multiple_of_the_window_ends_on_a_short_one(self):
        self.assertEqual(digest.windows(30, 1395)[-1], (1380, 1395))


class PluginWindowsTest(unittest.TestCase):
    """A plugin's WINDOW tiles the day it is judging, not a fixed 1440: on the
    long day the end would go unread, on the short one empty windows would be
    asked about."""

    def test_a_numeric_window_tiles_the_real_day(self):
        self.assertEqual(analyzer.resolve_window(30, 1500)[-1], (1470, 1500))
        self.assertEqual(len(analyzer.resolve_window(30, 600)), 20)

    def test_a_day_window_is_the_whole_real_day(self):
        self.assertEqual(analyzer.resolve_window("day", 2340), [(0, 2340)])

    def test_without_a_length_it_is_a_whole_ordinary_day(self):
        self.assertEqual(analyzer.resolve_window(30)[-1], (1410, 1440))
        self.assertEqual(analyzer.resolve_window("day"), [(0, 1440)])

    def test_a_window_that_does_not_divide_1440_is_still_refused(self):
        """The declaration is checked against an ordinary day — a plugin whose
        windows straddle midnight on ordinary days is a mistake whatever today
        is."""
        with self.assertRaises(ValueError):
            analyzer.resolve_window(7)


class BoundsChecksTest(unittest.TestCase):
    """An overflow is reported, not quietly corrected — but the bound itself
    has to be right, or every report is a false alarm."""

    def test_on_a_thirty_nine_hour_day_2340_is_not_out_of_bounds(self):
        out, bad = inference.expand(
            [{"start": 2280, "end": 2340, "cat": "sleep"}],
            report=True, n_min=2340)
        self.assertEqual(bad, [])
        self.assertIsNotNone(out[-1])

    def test_on_a_ten_hour_day_1400_is_out_of_bounds(self):
        _, bad = inference.expand(
            [{"start": 1300, "end": 1400, "cat": "code"}],
            report=True, n_min=600)
        self.assertEqual(len(bad), 1)

    def test_the_block_count_follows_the_day(self):
        self.assertEqual(len(inference.expand([], n_min=2340)), 2340 // 5)
        self.assertEqual(len(inference.expand([], n_min=600)), 600 // 5)
        self.assertEqual(len(inference.expand([])), 288)


class BlockGridTest(unittest.TestCase):
    """A 23-hour day (276 blocks) raised IndexError on real data: blocks._span
    clamped to the module constant BLOCKS (288). Green in unit tests and broken
    end to end, because in tests every day was 1440."""

    def test_a_short_day_stays_within_its_blocks(self):
        ev = [{"ts": 1380 * 60_000 - 60_000, "dur_ms": 30 * 60_000,
               "source": "whoop", "kind": "sleep"}]
        got = blocks.blocks(ev, 0, 1380)
        self.assertEqual(len(got), 276)

    def test_a_long_day_is_covered_to_the_end(self):
        ev = [{"ts": 2330 * 60_000, "dur_ms": 10 * 60_000,
               "source": "whoop", "kind": "sleep"}]
        got = blocks.blocks(ev, 0, 2340)
        self.assertEqual(len(got), 468)
        self.assertTrue(got[-1]["cat"], "the last block is covered, not clamped away")


class CoverageDenominatorTest(unittest.TestCase):
    """A wrong denominator makes coverage look better or worse out of nowhere,
    and nothing anywhere raises."""

    def test_on_a_short_day_C_is_not_negative(self):
        con = con_with([(ms("2026-09-01T00:00"), PDT, LA),
                        (ms("2026-10-05T17:00"), CST, SH)])
        c = analyze.coverage("2026-10-05", con)
        self.assertEqual(c["A"] + c["B"] + c["off"] + c["away"] + c["C"], 600)
        self.assertGreaterEqual(c["C"], 0)
        self.assertEqual(c["n_min"], 600)

    def test_on_a_long_day_the_denominator_is_2340(self):
        con = con_with([(ms("2026-09-01T00:00"), CST, SH),
                        (ms("2026-10-20T08:00"), PDT, LA)])
        c = analyze.coverage("2026-10-20", con)
        self.assertEqual(c["A"] + c["B"] + c["off"] + c["away"] + c["C"], 2340)

    def test_explained_pct_uses_the_day_s_length(self):
        """A 600-minute day with 300 minutes explained is 50%. With 1440 as the
        denominator it shows 20.8% — travel days systematically marked down."""
        self.assertEqual(viz.explained_pct({"A": 300, "off": 0, "n_min": 600}), 50.0)

    def test_an_old_cov_without_n_min_still_uses_1440(self):
        self.assertEqual(viz.explained_pct({"A": 720, "off": 0}), 50.0)


class DaylightSavingDaysTest(unittest.TestCase):

    def setUp(self):
        self.con = con_with([(ms("2026-09-01T00:00"), PDT, LA),
                             (ms("2026-11-01T09:00"), PST, LA)])

    def test_the_autumn_day_s_coverage_denominator_is_1500(self):
        c = analyze.coverage("2026-11-01", self.con)
        self.assertEqual(c["A"] + c["B"] + c["off"] + c["away"] + c["C"], 1500)

    def test_the_autumn_day_is_not_a_travel_day(self):
        self.assertFalse(tz.is_travel_day(self.con, "2026-11-01"))


class InferenceTilesTheRealDayTest(unittest.TestCase):
    """infer.run_day asks about windows of the day it is judging: a long day's
    last hours are asked about, a short day gets no window past its end."""

    def _run(self, marks, day, minutes):
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        con = db.connect(path)
        con.execute("DELETE FROM tz_marks")
        for t, off, z in marks:
            tz.record(con, t, off, z)
        lo, _ = tz.bounds(con, day)
        for m in minutes:
            con.execute("INSERT INTO events (uid, ts, source, kind, payload) "
                        "VALUES (?,?,?,?,?)",
                        (f"e{m}", lo + m * 60_000, "zsh", "command", '{"cmd":"ls"}'))
        con.commit()
        from unittest import mock

        from umbra import infer
        asked = []

        def fake(prompt):
            asked.append(prompt)
            return "[]"
        with mock.patch.object(infer, "lang", return_value="en"):
            infer.run_day(con, day, "m", call=fake)
        return con, asked

    def test_the_end_of_a_long_day_is_asked_about(self):
        """The 39-hour day: an event in its 38th hour lies past 1440 and used
        to be in no window at all."""
        _, asked = self._run([(ms("2026-09-01T00:00"), CST, SH),
                              (ms("2026-10-20T08:00"), PDT, LA)],
                             "2026-10-20", [2300])
        self.assertTrue(asked, "an event past minute 1440 reached no window")


class ClockLabelsTest(unittest.TestCase):
    """render labels a minute outside the day as "prev day" / "next day". On a
    long day minute 1500 is still the same day."""

    def test_past_1440_on_a_long_day_is_not_the_next_day(self):
        self.assertFalse(render.hm(1500, 2340).startswith("next day"))

    def test_past_the_day_s_end_it_is_the_next_day(self):
        self.assertTrue(render.hm(1450).startswith("next day"))


if __name__ == "__main__":
    unittest.main()
