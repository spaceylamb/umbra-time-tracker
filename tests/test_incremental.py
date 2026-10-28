"""Tests for incremental import.

Background: cmd_import used to pass no since to any source, re-reading
everything each time. Harmless for local files, but WHOOP burned about 180 API
requests and hit the rate limit twice.

The key one here is the **contract test**: the importers' `collect()`
signatures used to differ
(`collect()` / `collect(since=...)` / `collect(since_ms=0)` /
`collect(min_seconds=3)`). A positional call would feed a timestamp into
knowledgec's `min_seconds` — no error, just wrong data.
"""

import inspect
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import db
from umbra.importers import ALL

DAY = 86_400_000
NOW = 1_786_748_400_000


class CollectContractTest(unittest.TestCase):
    """Every importer must follow one calling contract, or a shared loop cannot
    pass arguments safely."""

    def test_every_importer_accepts_since_ms_first(self):
        for name, mod in ALL.items():
            with self.subTest(importer=name):
                params = list(inspect.signature(mod.collect).parameters)
                self.assertTrue(params, f"{name}.collect() takes no parameters at all")
                self.assertEqual(
                    params[0], "since_ms",
                    f"{name}.collect()'s first parameter is {params[0]!r}; "
                    f"a positional call would feed the timestamp to the wrong place")

    def test_since_ms_is_optional(self):
        """It must run without since — a first import has no baseline."""
        for name, mod in ALL.items():
            with self.subTest(importer=name):
                p = inspect.signature(mod.collect).parameters["since_ms"]
                self.assertIsNot(p.default, inspect.Parameter.empty,
                                 f"{name}.collect() has no default for since_ms")


class SinceForTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def _add(self, source, ts):
        self.con.execute(
            "INSERT INTO events (uid, ts, source, kind, payload) VALUES (?,?,?,?,'{}')",
            (f"{source}:{ts}", ts, source, "k"))

    def test_none_when_source_has_no_events(self):
        """A first import must fetch everything, not skip for lack of a baseline."""
        self.assertIsNone(db.since_for(self.con, "whoop"))

    def test_returns_latest_minus_overlap(self):
        self._add("whoop", NOW)
        self.assertEqual(db.since_for(self.con, "whoop", overlap_ms=3 * DAY),
                         NOW - 3 * DAY)

    def test_overlap_window_is_not_zero_by_default(self):
        """There must be an overlap window.

        WHOOP backfills and revises records — recovery is only scored after the
        sleep ends (PENDING_SCORE). Fetching only "since last time" loses those
        for good, with no error.
        """
        self._add("whoop", NOW)
        self.assertLess(db.since_for(self.con, "whoop"), NOW)

    def test_other_sources_do_not_leak(self):
        self._add("chrome", NOW)
        self.assertIsNone(db.since_for(self.con, "whoop"))

    def test_never_returns_negative(self):
        self._add("whoop", 1000)
        self.assertGreaterEqual(db.since_for(self.con, "whoop"), 0)


class ApiImportersConvertMsToIsoTest(unittest.TestCase):
    """butler and whoop are HTTP; the parameter must be an ISO string, not ms."""

    def test_whoop_sends_iso_start(self):
        from umbra.importers import whoop
        seen = []

        def fake_get(url, headers=None, timeout=60):
            seen.append(url)
            return {"records": [], "next_token": None}

        with mock.patch.object(whoop.whoop_auth, "ensure_access_token",
                               return_value="at"):
            with mock.patch.object(whoop.net, "get_json", side_effect=fake_get):
                whoop.collect(since_ms=NOW)

        self.assertTrue(seen)
        self.assertIn("start=2026-08-14T", seen[0])
        self.assertNotIn(str(NOW), seen[0])

    def test_butler_sends_iso_since(self):
        from umbra.importers import butler
        seen = []

        def fake_get(url, headers=None, timeout=60):
            seen.append(url)
            return {"tasks": [], "messages": []}

        with mock.patch.dict("os.environ", {"UMBRA_BUTLER_PAT": "pat",
                                            "UMBRA_BUTLER_URL": "https://b.example"}):
            with mock.patch.object(butler.net, "get_json", side_effect=fake_get):
                butler.collect(since_ms=NOW)

        self.assertTrue(seen)
        self.assertIn("since=2026-08-14T", seen[0])


class CmdImportWiringTest(unittest.TestCase):
    """The shared loop really passes since_ms down."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp() + "/t.db"
        con = db.connect(self.tmp)
        con.execute(
            "INSERT INTO events (uid, ts, source, kind, payload) "
            "VALUES ('x', ?, 'whoop', 'k', '{}')", (NOW,))
        con.commit()
        con.close()

    def _run(self, **flags):
        from umbra import __main__ as m
        fake = mock.MagicMock()
        fake.collect.return_value = []
        fake.SOURCE = "whoop"
        fake.PLATFORM = None       # MagicMock would invent a non-None PLATFORM
        args = mock.MagicMock(only=["whoop"], **flags)
        # Hold on to the real connect first: m.db is the same umbra.db module,
        # and calling db.connect after patching would call the mock itself,
        # recursing forever.
        real_connect = db.connect
        with mock.patch.object(m.db, "connect",
                               side_effect=lambda *a, **k: real_connect(self.tmp)):
            # The list now comes from sources.discover() (plugins since
            # 2026-08-19), so the patch goes there rather than on ALL — ALL is
            # only the built-ins.
            with mock.patch.object(m.sources, "discover",
                                   return_value=({"whoop": fake}, {})):
                with mock.patch.object(m, "cmd_stats", lambda *a: None):
                    with mock.patch("builtins.print"):    # keep the test output clean
                        m.cmd_import(args)
        return fake

    def test_passes_since_ms_as_keyword(self):
        fake = self._run(full=False)
        fake.collect.assert_called_once()
        self.assertIn("since_ms", fake.collect.call_args.kwargs)
        self.assertLess(fake.collect.call_args.kwargs["since_ms"], NOW)

    def test_full_flag_forces_none(self):
        fake = self._run(full=True)
        self.assertIsNone(fake.collect.call_args.kwargs["since_ms"])



class TouchedDaysTest(unittest.TestCase):
    """The days cmd_import returns are only the days **new rows were written to**.

    sync decides which days to rejudge from them (a source offline for days
    catches up later, outside the "last two days" window). And chrome hands its
    whole history over again every round — counted by rows pulled, every day of
    the last half year would be reported as touched.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp() + "/t.db"
        con = db.connect(self.tmp)
        con.execute(
            "INSERT INTO events (uid, ts, source, kind, payload) "
            "VALUES ('whoop:old', ?, 'whoop', 'sleep', '{}')", (NOW - 30 * DAY,))
        con.commit()
        con.close()

    def _run(self, rows):
        from umbra import __main__ as m
        fake = mock.MagicMock()
        fake.collect.return_value = rows
        fake.SOURCE = "whoop"
        fake.PLATFORM = None
        fake.SEGMENTED = fake.GROUPED = fake.REWRITE = False
        args = mock.MagicMock(only=["whoop"], full=False, exclude=None)
        real_connect = db.connect
        with mock.patch.object(m.db, "connect",
                               side_effect=lambda *a, **k: real_connect(self.tmp)), \
             mock.patch.object(m.sources, "discover",
                               return_value=({"whoop": fake}, {})), \
             mock.patch.object(m.sources, "missing", return_value=[]), \
             mock.patch.object(m, "cmd_stats", lambda *a: None), \
             mock.patch("builtins.print"):
            return m.cmd_import(args)

    def test_rows_already_stored_do_not_count_as_touched(self):
        import datetime as dt
        day = lambda ms: dt.datetime.fromtimestamp(ms / 1000).date().isoformat()
        got = self._run([
            ("whoop:old", NOW - 30 * DAY, None, "whoop", "sleep", {}),
            ("whoop:new", NOW - 5 * DAY, None, "whoop", "sleep", {}),
        ])
        self.assertEqual(got, [day(NOW - 5 * DAY)])

if __name__ == "__main__":
    unittest.main()
