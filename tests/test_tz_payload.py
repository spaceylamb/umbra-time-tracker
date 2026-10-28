"""The day payload says how long the day is and which clock it was read in.

The page hard-coded 1440 in about a dozen places. To stop that it needs, per
day: the length (n_min), the zone(s) the day passed through, whether it is a
travel day (left out of "against last week"), and — read-time — how much of it
has happened (end).
"""

import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from umbra import api, config, db, tz
from umbra.schema import validate as V


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(
        tzinfo=dt.UTC).timestamp() * 1000)


LA, SH = "America/Los_Angeles", "Asia/Shanghai"
PDT, PST, CST = -25200, -28800, 28800


class DayPayloadTest(unittest.TestCase):

    def setUp(self):
        self.enterContext(mock.patch.object(config, "load", return_value={}))
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(self.con.close)
        self.con.execute("DELETE FROM tz_marks")
        tz.record(self.con, ms("2026-09-01T00:00"), PDT, LA)
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)    # flew out 10:00 PDT
        tz.record(self.con, ms("2026-10-20T08:00"), PDT, LA)    # flew back
        tz.record(self.con, ms("2026-11-01T09:00"), PST, LA)    # clocks went back
        for i, day in enumerate(("2026-09-15", "2026-10-05", "2026-10-06", "2026-11-01")):
            lo, _ = tz.bounds(self.con, day)
            self.con.execute(
                "INSERT INTO events (uid, ts, source, kind, payload) VALUES (?,?,?,?,?)",
                (f"e{i}", lo + 60 * 60_000, "zsh", "command", '{"cmd":"ls"}'))
        self.con.commit()

    def day(self, d):
        out = api.day(d, self.con)
        V.validate(out, "Day")
        return out

    def test_an_ordinary_day(self):
        d = self.day("2026-09-15")
        self.assertEqual(d["n_min"], 1440)
        self.assertEqual(d["end"], 1440)
        self.assertFalse(d["travel"])
        self.assertEqual(d["tz"], [{"at": 0, "wall": 0, "zone": LA, "abbr": "PDT", "off_s": PDT}])
        self.assertEqual(len(d["bcat"]), 288)

    def test_the_day_you_flew_out(self):
        d = self.day("2026-10-05")
        self.assertEqual(d["n_min"], 600)
        self.assertTrue(d["travel"])
        # The switch to Shanghai is the moment the day ends, so the whole of it
        # was read on the California clock; a zero-length segment is not drawn.
        self.assertEqual(d["tz"], [{"at": 0, "wall": 0, "zone": LA, "abbr": "PDT", "off_s": PDT}])
        self.assertEqual(len(d["bcat"]), 120, "one slot per five minutes of this day")

    def test_the_day_after_landing_is_on_the_new_clock(self):
        d = self.day("2026-10-06")
        self.assertEqual([s["zone"] for s in d["tz"]], [SH])
        # It begins the moment the last day ended — 01:00 in Shanghai; the hour
        # before never happened to this person. The page's ticks need to know.
        self.assertEqual((d["n_min"], d["tz"][0]["wall"]), (1380, 60))

    def test_the_autumn_clock_change_is_two_clocks_but_not_travel(self):
        d = self.day("2026-11-01")
        self.assertEqual(d["n_min"], 1500)
        self.assertFalse(d["travel"])
        self.assertEqual([(s["abbr"], s["at"]) for s in d["tz"]], [("PDT", 0), ("PST", 120)])
        self.assertEqual([s["wall"] for s in d["tz"]], [0, 60], "01:00 again, on the new clock")

    def test_pct_is_against_the_day_s_own_length(self):
        """cov dropped n_min on its way into the payload, so pct was still
        divided by 1440 — the unit test of explained_pct was green throughout."""
        d = self.day("2026-10-05")
        self.assertEqual(d["cov"]["n_min"], 600)

    def test_today_ends_now(self):
        lo, _ = tz.bounds(self.con, "2026-11-01")
        with mock.patch("time.time", return_value=(lo + 300 * 60_000) / 1000):
            self.assertEqual(self.day("2026-11-01")["end"], 300)


if __name__ == "__main__":
    unittest.main()
