"""Make "the way days are cut changed" detectable, and impossible to destroy
anything silently.

inference.save() used to delete the whole day before writing it again
(`DELETE … WHERE day=? AND model=?`). After a flight, "today" is a different
stretch of real time, sync re-infers the last two days, and the stored rows of
those days — whose a_min count from the old midnight — would be deleted and
replaced, with no sign of it.

events is append-only, so raw records can always be recomputed; inferences,
digests and day_events are not: they cost model calls.
"""

import datetime as dt
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from umbra import db, inference, tz, viz


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(
        tzinfo=dt.UTC).timestamp() * 1000)


LA = "America/Los_Angeles"
SH = "Asia/Shanghai"
PDT, CST = -25200, 28800


def _fresh_con():
    con = sqlite3.connect(":memory:")
    con.executescript(db.SCHEMA)
    db.migrate(con)
    con.execute("DELETE FROM tz_marks")
    tz.record(con, ms("2026-09-01T00:00"), PDT, LA)
    return con


class InferenceIsNotErasedAcrossCutsTest(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_con()

    def spans(self, a, b, cat):
        return [{"start": a, "end": b, "cat": cat}]

    def n_rows(self):
        return self.con.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]

    def test_a_day_whose_start_moved_keeps_its_old_rows(self):
        """The day after landing: Shanghai's midnight is earlier than
        California's, so the whole start moved."""
        inference.save(self.con, "2026-10-06", "m", self.spans("01:00", "02:00", "code"))
        before = self.n_rows()
        self.assertGreater(before, 0)

        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)
        inference.save(self.con, "2026-10-06", "m", self.spans("00:30", "01:30", "talk"))

        self.assertGreater(self.n_rows(), before,
                           "the new cut's rows must be added, not replace the old")

    def test_a_daytime_departure_keeps_its_old_rows_too(self):
        """The one most easily missed: leaving at 10:00 PDT, the day's **start
        does not move at all**, only its length (1440 → 600). Keyed on the start
        alone, exactly the day most worth keeping would be erased as usual."""
        inference.save(self.con, "2026-10-05", "m", self.spans("01:00", "02:00", "code"))
        before = self.n_rows()

        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)
        inference.save(self.con, "2026-10-05", "m", self.spans("00:30", "01:30", "talk"))

        self.assertGreater(self.n_rows(), before,
                           "same start, different length: still two cuts")

    def test_re_inferring_under_the_same_cut_still_replaces(self):
        """Idempotence must still hold: re-running under one cut piles up nothing."""
        inference.save(self.con, "2026-10-05", "m", self.spans("01:00", "02:00", "code"))
        n = self.n_rows()
        inference.save(self.con, "2026-10-05", "m", self.spans("01:00", "02:00", "talk"))
        self.assertEqual(self.n_rows(), n)

    def test_load_returns_only_the_current_cut(self):
        inference.save(self.con, "2026-10-05", "m", self.spans("01:00", "02:00", "code"))
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)
        inference.save(self.con, "2026-10-05", "m", self.spans("00:30", "01:30", "talk"))

        got = inference.load(self.con, "2026-10-05")["m"]
        self.assertEqual([s["cat"] for s in got], ["talk"],
                         "the old cut's rows stay in the store, but are not mixed in")

    def test_events_digests_and_failures_follow_the_same_rule(self):
        day = "2026-10-05"
        inference.save_events(self.con, day, "m", [
            {"start": "01:00", "end": "02:00", "cat": "code", "title": "old"}])
        inference.save_digests(self.con, day, "m", [
            {"start": 60, "end": 90, "win": (0, 150), "n_rec": 3, "text": "old"}])
        inference.save_failures(self.con, day, "m", [
            {"a": 60, "b": 90, "at": 1, "reason": "old"}])
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)
        self.assertEqual(inference.load_events(self.con, day), {})
        self.assertEqual(inference.load_digests(self.con, day), {})
        self.assertEqual(inference.load_failures(self.con, day), {})
        # and nothing was deleted
        for t in ("day_events", "digests", "infer_failures"):
            self.assertEqual(self.con.execute(
                f"SELECT COUNT(*) FROM {t}").fetchone()[0], 1, t)

    def test_task_context_reads_only_the_current_cut(self):
        """actions.span_context queries inferences directly; under a new cut an
        old row at the same minute is a different moment."""
        from umbra.plugins import actions
        inference.save(self.con, "2026-10-05", "m", self.spans("01:00", "02:00", "code"))
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)
        self.assertIsNone(actions.span_context(self.con, "2026-10-05", 90))


class MigratingAnOldDatabaseTest(unittest.TestCase):
    """An existing database has the four tables keyed (day, model, a_min). The
    key has to grow, and SQLite cannot change a primary key, so each table is
    rebuilt. This moves rows and computes nothing — and a half-migrated
    database is worse than none, so a failure must raise."""

    OLD = """
    CREATE TABLE inferences (day TEXT NOT NULL, model TEXT NOT NULL,
        a_min INTEGER NOT NULL, b_min INTEGER NOT NULL, cat TEXT NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY (day, model, a_min));
    CREATE TABLE day_events (day TEXT NOT NULL, model TEXT NOT NULL,
        a_min INTEGER NOT NULL, b_min INTEGER NOT NULL, cat TEXT NOT NULL,
        title TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (day, model, a_min));
    CREATE TABLE digests (day TEXT NOT NULL, model TEXT NOT NULL,
        a_min INTEGER NOT NULL, b_min INTEGER NOT NULL, w_a INTEGER NOT NULL,
        w_b INTEGER NOT NULL, n_rec INTEGER NOT NULL, text TEXT NOT NULL,
        PRIMARY KEY (day, model, a_min));
    CREATE TABLE infer_failures (day TEXT NOT NULL, model TEXT NOT NULL,
        a_min INTEGER NOT NULL, b_min INTEGER NOT NULL, at INTEGER NOT NULL,
        kind TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY (day, model, a_min));
    """

    def setUp(self):
        self.addCleanup(time.tzset)
        self.enterContext(mock.patch.dict(os.environ, {"TZ": LA}))
        time.tzset()
        self.path = os.path.join(tempfile.mkdtemp(), "old.db")
        con = sqlite3.connect(self.path)
        con.executescript(self.OLD)
        con.execute("INSERT INTO inferences VALUES ('2026-06-15','m',60,90,'code',?)",
                    (json.dumps({"activity": "x"}),))
        con.execute("INSERT INTO day_events VALUES "
                    "('2026-06-15','m',60,90,'code','t','{}')")
        con.execute("INSERT INTO digests VALUES ('2026-06-15','m',60,90,0,150,3,'txt')")
        con.execute("INSERT INTO infer_failures VALUES "
                    "('2026-06-15','m',90,120,1,'call','e')")
        con.commit()
        con.close()

    def test_every_row_survives_and_reads_back(self):
        con = db.connect(self.path)
        self.assertEqual(inference.load(con, "2026-06-15")["m"][0]["cat"], "code")
        self.assertEqual(inference.load_events(con, "2026-06-15")["m"][0]["title"], "t")
        self.assertIn("m", inference.load_digests(con, "2026-06-15"))
        self.assertEqual(len(inference.load_failures(con, "2026-06-15")["m"]), 1)

    def test_the_backfilled_cut_is_the_day_as_it_was(self):
        con = db.connect(self.path)
        lo, hi = tz.bounds(con, "2026-06-15")
        self.assertEqual((lo, hi), tz.naive_bounds("2026-06-15"))
        row = con.execute("SELECT day_lo, day_n FROM inferences").fetchone()
        self.assertEqual(row, (lo, 1440))

    def test_migrating_twice_changes_nothing(self):
        db.connect(self.path).close()
        con = db.connect(self.path)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM inferences").fetchone()[0], 1)


class PageCacheSeesATimezoneChangeTest(unittest.TestCase):
    """The day's fingerprint was code + event count / max ts / max rowid + the
    inference tables' row counts — **no timezone**. After a timezone change none
    of those move, and the page would silently keep the old shape."""

    def setUp(self):
        self.con = _fresh_con()

    def fp(self):
        lo, hi = tz.bounds(self.con, "2026-10-05")
        return viz._day_fp(self.con, "2026-10-05", lo, hi, True, "code")

    def test_a_changed_history_changes_the_fingerprint(self):
        before = self.fp()
        tz.record(self.con, ms("2026-10-05T17:00"), CST, SH)
        self.assertNotEqual(self.fp(), before)

    def test_an_unchanged_history_keeps_it(self):
        """The other way round too, or every sync would rebuild the whole page."""
        self.assertEqual(self.fp(), self.fp())


if __name__ == "__main__":
    unittest.main()
