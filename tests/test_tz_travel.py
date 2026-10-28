"""Bringing the timezone history in.

Two sources, and both are needed:

  · observed   every sync asks the machine for its offset now, and records a
               row when it changed. No configuration, right from today on, but
               it only covers the future.
  · entered    config.toml's [[travel]], for trips before observation began, and
               for "abroad, but the Mac had not switched its timezone yet".
"""

import datetime as dt
import sqlite3
import unittest
from unittest import mock

from umbra import db, ops, tz


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(
        tzinfo=dt.UTC).timestamp() * 1000)


LA, SH = "America/Los_Angeles", "Asia/Shanghai"
PDT, CST = -25200, 28800


class SyncRecordsTheTimezoneTest(unittest.TestCase):
    """Without this step the feature is dead: tz_marks would keep only its seed
    rows, and no trip would ever show."""

    def _sync(self, order):
        return ops.sync(_tz=lambda: order.append("tz"),
                        _import=lambda: order.append("import") or [],
                        _infer=lambda d: order.append("infer"),
                        _viz=lambda: None, _plugins={}, _deploy=lambda **k: None,
                        log=lambda *a: None)

    def test_sync_has_a_tz_step(self):
        order = []
        self._sync(order)
        self.assertIn("tz", order, "every sync must record the current timezone")

    def test_it_comes_before_inference(self):
        """infer writes rows keyed by the day's current cut, so the cut has to
        be right first."""
        order = []
        self._sync(order)
        self.assertLess(order.index("tz"), order.index("infer"))

    def test_the_step_really_writes_tz_marks(self):
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        con.execute("DELETE FROM tz_marks")
        with mock.patch.object(db, "connect", lambda *a, **k: con), \
             mock.patch.object(ops, "_conf", lambda: {}):
            ops._run_tz()
        self.assertEqual(len(tz.history(con)), 1)


class TripsEnteredByHandTest(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(tz.SCHEMA)

    def test_a_configured_trip_reaches_the_history(self):
        tz.apply_config(self.con, [
            {"from": "2026-10-05T10:00", "tz": "Asia/Shanghai"}])
        self.assertEqual(tz.zone_at(tz.history(self.con),
                                    ms("2026-10-06T00:00")), SH)

    def test_from_is_read_in_its_own_zone(self):
        """"Leaving 10-05 at 10:00" is the clock **where you left from**. Read
        in the destination's zone the mark moves fifteen hours and that day's
        length is wrong."""
        tz.record(self.con, ms("2026-09-01T00:00"), PDT, LA)
        tz.apply_config(self.con, [
            {"from": "2026-10-05T10:00", "tz": "Asia/Shanghai"}])
        self.assertEqual(tz.history(self.con)[-1][0], ms("2026-10-05T17:00"))

    def test_applying_again_piles_up_nothing(self):
        rows = [{"from": "2026-10-05T10:00", "tz": "Asia/Shanghai"}]
        tz.apply_config(self.con, rows)
        tz.apply_config(self.con, rows)
        self.assertEqual(len(tz.history(self.con)), 1)

    def test_seeding_does_not_wash_out_what_was_entered(self):
        tz.apply_config(self.con, [
            {"from": "2026-10-05T10:00", "tz": "Asia/Shanghai"}])
        tz.seed(self.con, ms("2026-01-01T00:00"), ms("2026-12-01T00:00"))
        self.assertEqual([r[3] for r in self.con.execute(
            "SELECT ts, off_s, zone, src FROM tz_marks")], ["config"])

    def test_a_mistyped_zone_raises(self):
        """Skipped silently, the config change would not take effect and the
        symptom ("that day is still 24 hours") points nowhere near the cause."""
        with self.assertRaises(RuntimeError):
            tz.apply_config(self.con, [{"from": "2026-10-05T10:00", "tz": "Asia/Shangai"}])

    def test_sync_applies_the_configured_trips(self):
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        con.execute("DELETE FROM tz_marks")
        conf = {"travel": [{"from": "2026-10-05T10:00", "tz": "Asia/Shanghai"}]}
        with mock.patch.object(db, "connect", lambda *a, **k: con), \
             mock.patch.object(ops, "_conf", lambda: conf):
            ops._run_tz()
        self.assertIn("config", [r[0] for r in con.execute("SELECT src FROM tz_marks")])


if __name__ == "__main__":
    unittest.main()
