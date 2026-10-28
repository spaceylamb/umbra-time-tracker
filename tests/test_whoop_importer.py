"""Tests for the WHOOP importer.

They cover the shapes that real data always has and hand-made samples miss:
unscored records with a null score, the current cycle that has not ended, and
recovery's own field names.
"""

import unittest
import urllib.error
from unittest import mock

from umbra import whoop_auth
from umbra.importers import whoop

T0 = "2026-08-14T23:00:00.000Z"
T1 = "2026-08-15T07:00:00.000Z"
T0_MS = 1786748400000          # 2026-08-14T23:00:00Z
EIGHT_H = 8 * 3600 * 1000


def _pages(*batches):
    """Turn batches of records into a sequence of paged responses with cursors."""
    out = []
    for i, b in enumerate(batches):
        out.append({"records": b,
                    "next_token": f"tok{i}" if i < len(batches) - 1 else None})
    return out


def _run(collect_pages: dict, since=None):
    """Dispatch fake responses by URL path."""
    def fake_get(url, headers=None, timeout=60):
        for path, seq in collect_pages.items():
            if path in url:
                return seq.pop(0) if seq else {"records": [], "next_token": None}
        return {"records": [], "next_token": None}

    with mock.patch.object(whoop.whoop_auth, "ensure_access_token",
                           return_value="at"):
        with mock.patch.object(whoop.net, "get_json", side_effect=fake_get):
            return whoop.collect(since)


class SleepTest(unittest.TestCase):
    def test_maps_to_event_row(self):
        rows = _run({"/activity/sleep": _pages([{
            "id": "s1", "start": T0, "end": T1, "nap": False,
            "score": {"sleep_performance_percentage": 91.0,
                      "stage_summary": {"total_rem_sleep_time_milli": 5000}}}])})
        self.assertEqual(len(rows), 1)
        uid, ts, dur, src, kind, payload = rows[0]
        self.assertEqual(uid, "whoop:sleep:s1")
        self.assertEqual(ts, T0_MS)
        self.assertEqual(dur, EIGHT_H)
        self.assertEqual(src, "whoop")
        self.assertEqual(kind, "sleep")
        self.assertEqual(payload["performance_pct"], 91.0)
        self.assertEqual(payload["rem_ms"], 5000)

    def test_null_score_does_not_crash(self):
        """PENDING_SCORE records have a null score and cluster in the last 24
        hours — exactly the ones each import is most likely to meet."""
        rows = _run({"/activity/sleep": _pages([
            {"id": "s1", "start": T0, "end": T1, "score": None}])})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][5]["performance_pct"])

    def test_missing_start_is_skipped(self):
        rows = _run({"/activity/sleep": _pages([{"id": "s1", "score": {}}])})
        self.assertEqual(rows, [])


class RecoveryTest(unittest.TestCase):
    def test_uses_cycle_id_and_created_at(self):
        """Recovery has no id and no start — another endpoint's field names would
        lose the whole type."""
        rows = _run({"/recovery": _pages([{
            "cycle_id": 99, "created_at": T0,
            "score": {"recovery_score": 42, "hrv_rmssd_milli": 31.5,
                      "resting_heart_rate": 54}}])})
        self.assertEqual(len(rows), 1)
        uid, ts, dur, _, kind, payload = rows[0]
        self.assertEqual(uid, "whoop:recovery:99")
        self.assertEqual(ts, T0_MS)
        self.assertIsNone(dur)
        self.assertEqual(kind, "recovery")
        self.assertEqual(payload["score"], 42)
        self.assertEqual(payload["hrv_ms"], 31.5)


class CycleTest(unittest.TestCase):
    def test_in_progress_cycle_has_no_duration(self):
        """The current cycle's end is null; it must not become negative or crash."""
        rows = _run({"/cycle": _pages([
            {"id": 7, "start": T0, "end": None, "score": {"strain": 12.3}}])})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][2])
        self.assertEqual(rows[0][5]["strain"], 12.3)


class WorkoutTest(unittest.TestCase):
    def test_sport_name_and_strain(self):
        rows = _run({"/activity/workout": _pages([
            {"id": "w1", "start": T0, "end": T1, "sport_name": "Running",
             "score": {"strain": 15.1, "max_heart_rate": 172}}])})
        self.assertEqual(rows[0][4], "workout")
        self.assertEqual(rows[0][5]["sport"], "Running")
        self.assertEqual(rows[0][5]["max_hr"], 172)


class PaginationTest(unittest.TestCase):
    def test_follows_next_token_until_empty(self):
        rows = _run({"/activity/workout": _pages(
            [{"id": "w1", "start": T0, "end": T1, "score": {}}],
            [{"id": "w2", "start": T0, "end": T1, "score": {}}],
            [{"id": "w3", "start": T0, "end": T1, "score": {}}])})
        self.assertEqual([r[0] for r in rows],
                         ["whoop:workout:w1", "whoop:workout:w2",
                          "whoop:workout:w3"])

    def test_cursor_replaces_since_after_first_page(self):
        """Sending start while paging pulls back pages already read — an endless loop."""
        seen = []

        def fake_get(url, headers=None, timeout=60):
            seen.append(url)
            if len(seen) == 1:
                return {"records": [], "next_token": "tok0"}
            return {"records": [], "next_token": None}

        with mock.patch.object(whoop.whoop_auth, "ensure_access_token",
                               return_value="at"):
            with mock.patch.object(whoop.net, "get_json", side_effect=fake_get):
                whoop.collect(since_ms=1785542400000)   # 2026-08-01

        self.assertIn("start=", seen[0])
        self.assertIn("nextToken=tok0", seen[1])
        self.assertNotIn("start=", seen[1])

    def test_page_limit_is_within_api_max(self):
        """v2 collection endpoints cap at 25; more is truncated silently."""
        self.assertLessEqual(whoop.PAGE, 25)


class AuthPropagationTest(unittest.TestCase):
    def test_auth_expired_propagates(self):
        """Must not be swallowed — cmd_import decides to stop the whole import by
        this type."""
        with mock.patch.object(whoop.whoop_auth, "ensure_access_token",
                               side_effect=whoop_auth.WhoopAuthExpired("gone")):
            with self.assertRaises(whoop_auth.WhoopAuthExpired):
                whoop.collect()

    def test_network_error_is_not_auth_expired(self):
        """A WHOOP 500 goes to per-source fault isolation; it must not push the
        person to re-authorise."""
        err = urllib.error.HTTPError("u", 503, "err", {}, None)
        with mock.patch.object(whoop.whoop_auth, "ensure_access_token",
                               return_value="at"):
            with mock.patch.object(whoop.net, "get_json", side_effect=err):
                with self.assertRaises(urllib.error.HTTPError):
                    whoop.collect()


class RegistrationTest(unittest.TestCase):
    def test_registered_in_all(self):
        from umbra.importers import ALL, SOURCE_OF
        self.assertIn("whoop", ALL)
        self.assertEqual(SOURCE_OF["whoop"], "whoop")


if __name__ == "__main__":
    unittest.main()
