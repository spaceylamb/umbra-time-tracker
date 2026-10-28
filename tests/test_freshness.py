"""One failure must not erase "this source was fine last time".

Hit on 2026-08-17: iphone / media / intent / knowledgec need Full Disk Access,
and TCC grants it to **the host GUI app**. launchd's scheduled job has it; an
interactive terminal session does not. So

    the launchctl run    ✓ succeeded, data in the database
    my manual run        ✗ permission denied

and record_import had a single last_run, so the second overwrote the first.
freshness showed "✗ last run failed" — looking like a broken pipeline while the
data was fine.

The reverse is more dangerous: WHOOP's authorisation expired and two scheduled
runs in a row failed, but each record only replaced the last — **nothing showed
"broken for two rounds in a row"**.
One failure may be a network blip; three in a row is really broken — the two
must be told apart.

So three separate quantities:
    last_run  the last **attempt** (old = the scheduled job is not running)
    last_ok   the last **success** (old = no data is coming in from this source)
    fails     consecutive failures (growing = stop treating it as a fluke)

Another variant of this project's old rule: "nothing wrong" and "something wrong
but invisible"
must not look the same on screen.
"""

import sqlite3
import unittest

from umbra import db


class RecordImportTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def row(self, src="whoop"):
        return dict(zip(
            ("last_run", "last_ok", "ok", "fails"),
            self.con.execute("select last_run, last_ok, ok, fails "
                             "from imports where source=?", (src,)).fetchone()))

    def test_success_records_when_it_succeeded(self):
        db.record_import(self.con, "whoop", ok=True, added=3)
        r = self.row()
        self.assertIsNotNone(r["last_ok"])
        self.assertEqual(r["last_ok"], r["last_run"])

    def test_a_failure_keeps_the_last_success_time(self):
        """The four sources' scene: the scheduled job succeeded, then one manual
        run lacked permission. The data is fine, and one unpermitted attempt must
        not show as "collection broken"."""
        db.record_import(self.con, "iphone", ok=True, added=609)
        was = self.row("iphone")["last_ok"]
        db.record_import(self.con, "iphone", ok=False, error="没有完全磁盘访问")
        r = self.row("iphone")
        self.assertEqual(r["last_ok"], was, "one failure erased the last success")
        self.assertGreaterEqual(r["last_run"], was)
        self.assertFalse(r["ok"])

    def test_consecutive_failures_accumulate(self):
        """The WHOOP case: two scheduled rounds failed in a row, each record
        overwriting the last, and nothing showed "broken for two rounds"."""
        for _ in range(3):
            db.record_import(self.con, "whoop", ok=False, error="授权失效")
        self.assertEqual(self.row()["fails"], 3)

    def test_one_success_resets_it(self):
        """Fixed is fixed. Without a reset the count only grows more alarming,
        and then nobody looks at it."""
        db.record_import(self.con, "whoop", ok=False, error="x")
        db.record_import(self.con, "whoop", ok=False, error="x")
        db.record_import(self.con, "whoop", ok=True, added=1)
        self.assertEqual(self.row()["fails"], 0)

    def test_succeeding_first_time_means_zero_failures(self):
        db.record_import(self.con, "git", ok=True, added=1)
        self.assertEqual(self.row("git")["fails"], 0)

    def test_failing_first_time_counts_as_one(self):
        db.record_import(self.con, "git", ok=False, error="x")
        self.assertEqual(self.row("git")["fails"], 1)
        self.assertIsNone(self.row("git")["last_ok"], "never succeeded should be empty")


class MigrationTest(unittest.TestCase):
    """An old database's imports table lacks these two columns.

    CREATE TABLE IF NOT EXISTS **does nothing** to an existing table — new code
    with new fields meets an old database with OperationalError, and that
    database is the user's only copy of their data.
    """

    def _old_db(self):
        con = sqlite3.connect(":memory:")
        con.executescript("""
            CREATE TABLE imports (
                source TEXT PRIMARY KEY, last_run INTEGER NOT NULL,
                ok INTEGER NOT NULL, added INTEGER, error TEXT);
            INSERT INTO imports VALUES ('whoop', 111, 1, 5, NULL);
        """)
        return con

    def test_an_old_database_gains_the_new_columns(self):
        con = self._old_db()
        db.migrate(con)
        cols = {c[1] for c in con.execute("PRAGMA table_info(imports)")}
        self.assertIn("last_ok", cols)
        self.assertIn("fails", cols)

    def test_migration_leaves_existing_data_alone(self):
        con = self._old_db()
        db.migrate(con)
        self.assertEqual(
            con.execute("select last_run, ok, added from imports").fetchone(),
            (111, 1, 5))

    def test_migration_can_run_again(self):
        """connect() calls it every time. A second ALTER would raise duplicate
        column."""
        con = self._old_db()
        db.migrate(con)
        db.migrate(con)                  # must not raise
        self.assertEqual(con.execute("select count(*) from imports").fetchone()[0], 1)

    def test_connect_migrates_an_old_database(self):
        """migrate() green on its own, connect() forgetting to call it — the
        user's database would never gain the columns, and the first record_import
        would blow up. The parts-right, whole-wrong kind of hole."""
        import os
        import tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "old.db")
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE imports (
                source TEXT PRIMARY KEY, last_run INTEGER NOT NULL,
                ok INTEGER NOT NULL, added INTEGER, error TEXT);
            INSERT INTO imports VALUES ('whoop', 111, 1, 5, NULL);
        """)
        old.commit(); old.close()
        con = db.connect(path)
        self.addCleanup(con.close)
        db.record_import(con, "whoop", ok=False, error="x")     # blows up if the migration did not run
        self.assertEqual(
            con.execute("select fails from imports where source='whoop'"
                        ).fetchone()[0], 1)

    def test_the_added_failure_count_cannot_be_null(self):
        """NULL + 1 is still NULL: the consecutive count would never grow, with
        no error. COALESCE in the SQL could guard it too, but two defences
        covering for each other means removing either turns nothing red in a
        mutation test. The schema one stays; it is harder."""
        con = self._old_db()
        db.migrate(con)
        col = next(c for c in con.execute("PRAGMA table_info(imports)")
                   if c[1] == "fails")
        self.assertEqual(col[3], 1, "the fails column allows NULL")   # notnull
        self.assertEqual(con.execute("select fails from imports").fetchone()[0], 0)

    def test_migration_backfills_old_successes(self):
        """Without a backfill, a healthy database comes out of migration as a
        screen of "never succeeded" — all 13 sources, measured. Worse than not
        having the field: once a diagnostic cries wolf, people stop reading it.

        For an old row with ok=1, its last_run **is** the last success — that
        much can be recovered.
        """
        con = self._old_db()
        db.migrate(con)
        self.assertEqual(
            con.execute("select last_ok from imports where source='whoop'"
                        ).fetchone()[0], 111)

    def test_old_failed_records_are_not_guessed_at(self):
        """For an ok=0 row we truly don't know when it last succeeded. Filling in
        last_run would show "failing all along" as "just succeeded" — what cannot
        be recovered stays empty."""
        con = sqlite3.connect(":memory:")
        con.executescript("""
            CREATE TABLE imports (
                source TEXT PRIMARY KEY, last_run INTEGER NOT NULL,
                ok INTEGER NOT NULL, added INTEGER, error TEXT);
            INSERT INTO imports VALUES ('whoop', 111, 0, 0, '授权失效');
        """)
        db.migrate(con)
        self.assertIsNone(
            con.execute("select last_ok from imports").fetchone()[0])

    def test_the_backfill_can_run_again(self):
        con = self._old_db()
        db.migrate(con)
        db.record_import(con, "whoop", ok=False, error="x")
        db.migrate(con)
        self.assertEqual(
            con.execute("select last_ok from imports").fetchone()[0], 111,
            "a second migration wrote the failed run's time in as a success")

    def test_old_records_have_zero_failures_not_null(self):
        """NULL + 1 is still NULL — a migrated row left empty would never count
        consecutive failures, with no error."""
        con = self._old_db()
        db.migrate(con)
        db.record_import(con, "whoop", ok=False, error="x")
        self.assertEqual(
            con.execute("select fails from imports where source='whoop'"
                        ).fetchone()[0], 1)


class FreshnessOutputTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def one(self, src):
        return next(r for r in db.freshness(self.con) if r["source"] == src)

    def test_the_last_success_and_consecutive_failures_are_both_reported(self):
        db.record_import(self.con, "whoop", ok=True, added=1)
        db.record_import(self.con, "whoop", ok=False, error="授权失效")
        r = self.one("whoop")
        self.assertIsNotNone(r["last_ok"])
        self.assertIsNotNone(r["ok_age_min"])
        self.assertEqual(r["fails"], 1)

    def test_never_succeeded_means_an_empty_last_success(self):
        """Empty is empty — filling in last_run would show "never succeeded" as
        "just succeeded"."""
        db.record_import(self.con, "whoop", ok=False, error="x")
        r = self.one("whoop")
        self.assertIsNone(r["last_ok"])
        self.assertIsNone(r["ok_age_min"])


class StaleThresholdTest(unittest.TestCase):
    """The "not run for too long" threshold must **derive from the scheduled
    interval**, not be a guess.

    Hit on the evening of 2026-08-17: all 13 sources wore ⚠ "importer not run for
    too long", while freshness showed every one "4.4 hours ago" with **last
    attempt = last success** — the job ran at 17:32, succeeded fully, nothing
    was broken.

    Two constants were each written on their own:
        STALE_MIN     = 120 minutes                the alarm threshold
        StartInterval = 21600 s = 360 minutes      the job interval

    **The alarm threshold was shorter than the interval**, so every gap between
    rounds had four hours of ⚠. It reported not a fault but "not time yet".

    Exactly the sentence written that same afternoon: once a diagnostic cries
    wolf, people stop reading it. The backfill was changed then; this constant
    was not looked at again.
    """

    def test_the_threshold_is_longer_than_the_interval(self):
        from umbra import __main__ as cli
        # The interval moved into ops on 2026-08-19 (one source with schedule's config)
        from umbra import ops
        self.assertGreater(cli.STALE_MIN, ops.interval_min(),
                           "a threshold shorter than the interval misfires between every round")

    def test_no_alarm_after_one_round(self):
        """4.4 hours after a run, the next one not due yet — the normal state."""
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        db.insert(con, [("x", 1, 0, "chrome", "visit", {})])
        db.record_import(con, "chrome", ok=True, added=1)
        con.execute("update imports set last_run = last_run - ?, "
                    "last_ok = last_ok - ?", (264 * 60_000, 264 * 60_000))
        import contextlib
        import io
        from unittest import mock
        from umbra import __main__ as cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), \
             mock.patch.object(cli.db, "connect", return_value=con):
            cli.cmd_freshness()
        self.assertIn("✓ all sources healthy", buf.getvalue())

    def test_alarm_after_two_missed_rounds(self):
        """Really broken must be reported — the threshold must not be raised until
        it never fires just to stop false alarms."""
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        db.insert(con, [("x", 1, 0, "chrome", "visit", {})])
        db.record_import(con, "chrome", ok=True, added=1)
        con.execute("update imports set last_run = last_run - ?, "
                    "last_ok = last_ok - ?", (13 * 3600_000, 13 * 3600_000))
        import contextlib
        import io
        from unittest import mock
        from umbra import __main__ as cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), \
             mock.patch.object(cli.db, "connect", return_value=con):
            cli.cmd_freshness()
        self.assertNotIn("✓ all sources healthy", buf.getvalue())


class DisplayTest(unittest.TestCase):
    """The screen must tell these three apart, or recording them is pointless.

        the job runs, the source brings data in            ✓
        the job runs, but the source keeps failing         ✗ N in a row
        one manual run failed, the last success is recent  the data has not stopped;
                                                           that one run lacked permission
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        db.insert(self.con, [("x", 1, 0, "iphone", "focus", {})])

    def out(self):
        import contextlib
        import io
        from unittest import mock
        from umbra import __main__ as cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), \
             mock.patch.object(cli.db, "connect", return_value=self.con):
            cli.cmd_freshness()
        return buf.getvalue()

    def line(self, src):
        """Look only at this source's own row.

        This used to assertIn("consecutive") on the whole output — and the
        footer's explanatory text contains the word too, so switching the alarm
        off entirely stayed green. A mutation test caught it.
        """
        return next(l for l in self.out().splitlines() if l.startswith(src))

    def test_consecutive_failures_are_written_out(self):
        for _ in range(3):
            db.record_import(self.con, "iphone", ok=False, error="没有完全磁盘访问")
        self.assertIn("3 consecutive", self.line("iphone"))

    def test_consecutive_is_reported_only_at_the_threshold(self):
        """Not on one failure (it may be a blip); only at FAIL_STREAK. A threshold
        that never fires would fall into the "never succeeded" branch and stay
        green above."""
        db.record_import(self.con, "iphone", ok=True, added=1)
        db.record_import(self.con, "iphone", ok=False, error="x")
        self.assertNotIn("consecutive", self.line("iphone"))
        db.record_import(self.con, "iphone", ok=False, error="x")
        self.assertIn("2 consecutive", self.line("iphone"))

    def test_one_failure_is_not_a_fault(self):
        """Those four sources exactly: the scheduled job succeeded, one manual run
        lacked permission. The data is fine; calling it a fault sends someone to
        fix something that is not broken."""
        db.record_import(self.con, "iphone", ok=True, added=609)
        db.record_import(self.con, "iphone", ok=False, error="没有完全磁盘访问")
        self.assertIn("✓ all sources healthy", self.out())

    def test_never_succeeded_is_reported(self):
        """The opposite: never succeeded, so the source was never connected."""
        db.record_import(self.con, "iphone", ok=False, error="授权失效")
        self.assertNotIn("✓ all sources healthy", self.out())

    def test_the_last_success_time_is_shown(self):
        """Recorded but unseen is useless — this column answers "has the data
        actually stopped", which "last attempt" cannot.

        (No separate alarm for "last success old but the job still running":
        fails == 0 implies last_ok == last_run, so that state is unreachable, and
        the existing "importer not run for too long" covers it. No code for
        unreachable branches.)
        """
        db.record_import(self.con, "iphone", ok=True, added=1)
        db.record_import(self.con, "iphone", ok=False, error="没有完全磁盘访问")
        self.assertIn("last ok", self.out())


if __name__ == "__main__":
    unittest.main()
