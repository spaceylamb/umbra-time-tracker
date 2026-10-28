"""The ⚠ at the top of the page — decided in Python, only displayed by the page.

The lesson of 2026-08-20: the CLI (cmd_freshness) and the page each had their
own "is this source stale", and they drifted. The CLI knew "one failure may be a
manual run without permission; FAIL_STREAK in a row is really broken"; the
JavaScript used ok === false — so four sources whose scheduled job had
succeeded 42 minutes earlier, touched once by a manual sync in a terminal
without Full Disk Access, all wore ⚠ on the page.

Another reason the verdict moved into the payload (fresh[i]["stale"]): logic in
JavaScript is out of the Python tests' reach, and a drift turns nothing red —
which is exactly how this slipped through.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import db, viz


def row(**kw):
    """One freshness record, its defaults "completely healthy"."""
    base = {"source": "x", "count": 1, "last_event": 1, "event_age_min": 5.0,
            "last_run": 1, "run_age_min": 10.0, "ok": True, "error": None,
            "last_ok": 1, "ok_age_min": 10.0, "fails": 0, "live": False}
    base.update(kw)
    return base


class VerdictRulesTest(unittest.TestCase):
    S = 540  # stale_min (a 6 h interval × 1.5)

    def test_one_failure_after_earlier_success_is_not_reported(self):
        """The scene of this bug: a manual sync without Full Disk Access failed
        four sources once, while the scheduled job had succeeded under an hour
        before — the data had not stopped, and flagging it would send someone to
        fix something that was not broken."""
        f = row(ok=False, fails=1, ok_age_min=42.0,
                error="[Errno 1] cannot read .../Biome/streams/restricted")
        self.assertFalse(viz.stale(f, self.S))

    def test_reported_once_failures_reach_fail_streak(self):
        self.assertFalse(viz.stale(row(ok=False, fails=db.FAIL_STREAK - 1),
                                   self.S))
        self.assertTrue(viz.stale(row(ok=False, fails=db.FAIL_STREAK),
                                  self.S))

    def test_never_succeeded_is_reported(self):
        f = row(ok=False, fails=1, last_ok=None, ok_age_min=None)
        self.assertTrue(viz.stale(f, self.S))

    def test_not_run_for_too_long_is_reported(self):
        """An old last_run means the scheduled job itself is not running — a
        different illness from "ran and failed", and both must be visible."""
        self.assertTrue(viz.stale(row(run_age_min=self.S + 1), self.S))
        self.assertTrue(viz.stale(row(last_run=None, run_age_min=None),
                                  self.S))

    def test_a_live_source_is_judged_by_event_time_two_silent_hours(self):
        """The collector is resident and does not follow the import interval."""
        self.assertFalse(viz.stale(row(live=True, event_age_min=100.0),
                                   self.S))
        self.assertTrue(viz.stale(row(live=True, event_age_min=121.0),
                                  self.S))

    def test_a_healthy_source_is_not_reported(self):
        self.assertFalse(viz.stale(row(), self.S))


class ConstantsShareOneSourceTest(unittest.TestCase):
    def test_fail_streak_lives_in_db_and_the_cli_uses_it(self):
        """Two hand-written 2s, one changed and one forgotten = this bug in a new coat."""
        from umbra import __main__ as cli
        self.assertIs(cli.FAIL_STREAK, db.FAIL_STREAK)


class InPayloadTest(unittest.TestCase):
    def test_fresh_rows_carry_the_verdict_inputs(self):
        import sqlite3
        import time
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        con = db.connect(path)
        now_ms = int(time.time() * 1000)
        db.insert(con, [("e:1", now_ms, None, "zsh", "command",
                         {"cmd": "ls"})])
        db.record_import(con, "zsh", ok=True, added=1)
        con.commit()
        con.close()
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k:
                               sqlite3.connect(path)), \
             mock.patch("umbra.ops.stale_min", return_value=540):
            payload = viz.build()
        zsh = next(f for f in payload["fresh"] if f["source"] == "zsh")
        # "One copy of the rule, in Python" still holds; what changed is **the
        # form sent down**: no longer a computed stale (which would freeze with
        # the page — after a failed deploy it would show "fresh ✓" forever, lying
        # exactly when it most needs to warn) but a conclusive bad, an absolute
        # time and a threshold, with one subtraction left to the page.
        self.assertNotIn("stale", zsh)
        self.assertIn("bad", zsh)
        self.assertIn("check_ts", zsh)
        self.assertIn("stale_min", zsh)
        self.assertFalse(zsh["bad"])
        self.assertFalse(viz.stale(
            next(f for f in db.freshness(db.connect(path))
                 if f["source"] == "zsh"), 540))

    def test_the_page_derives_nothing_itself(self):
        """The generated JS must not contain ok === false — that was the half that
        drifted."""
        import inspect
        src = inspect.getsource(viz)
        self.assertNotIn("ok === false", src)



if __name__ == "__main__":
    unittest.main()
