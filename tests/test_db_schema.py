"""The schema survives deleting the modules that used to define it.

db.py assembled the one SCHEMA constant by importing actions and claims at
module load, mid-load, with a noqa on each line. That worked, and it meant
neither module could ever be deleted without breaking db.connect() for every
user on every command — which is precisely what moving those lanes into plugins
does.

The legacy definitions now live in db.py as frozen history. They stay for two
reasons: they hold results that cost money to produce, and reads union the old
shape with the new one, which needs the tables to exist even on a fresh install
that has no legacy rows in them. A union against a table that was never created
raises `no such table` rather than returning nothing.
"""

import sqlite3
import unittest

from umbra import analyzer, db

# The eight tables that predate the Analyzer and Sink interfaces.
LEGACY = ("proposals", "task_digests", "claims", "claim_cards",
          "claim_card_items", "claim_digests", "goal_attrib", "goal_runs")


def _fresh():
    con = sqlite3.connect(":memory:")
    con.executescript(db.SCHEMA)
    return con


def _tables(con):
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


class LegacyTablesTest(unittest.TestCase):
    def test_a_fresh_database_has_every_legacy_table(self):
        have = _tables(_fresh())
        missing = [t for t in LEGACY if t not in have]
        self.assertEqual(missing, [], f"missing legacy tables: {missing}")

    def test_reading_a_legacy_table_on_a_fresh_database_returns_nothing(self):
        """Not an error. A fresh install has no legacy rows, and the union read
        still has to run over the table."""
        con = _fresh()
        for t in LEGACY:
            with self.subTest(t):
                self.assertEqual(
                    con.execute(f"SELECT count(*) FROM {t}").fetchone()[0], 0)

    def test_a_union_over_both_shapes_returns_from_whichever_is_populated(self):
        """KTD4's read path. New rows go to analyzer_rows and old rows stay
        where they are, so a reader has to see both."""
        con = _fresh()
        con.execute(
            "INSERT INTO claims (day, claim, quote) VALUES (?,?,?)",
            ("2026-09-08", "old claim", "said the old thing"))
        con.commit()
        analyzer.save_rows(con, "claims", "2026-09-08", "m", 540, "d",
                           [{"a": 545, "b": 546, "claim": "new claim"}], now_ms=0)

        legacy = [r[0] for r in con.execute(
            "SELECT claim FROM claims WHERE day=?", ("2026-09-08",))]
        modern = [r["claim"] for r in
                  analyzer.load_rows(con, "claims", "2026-09-08", "m")]
        self.assertEqual(legacy + modern, ["old claim", "new claim"])

    def test_the_union_works_when_only_the_legacy_side_has_rows(self):
        con = _fresh()
        con.execute("INSERT INTO claims (day, claim, quote) VALUES (?,?,?)",
                    ("2026-09-08", "only old", "q"))
        con.commit()
        self.assertEqual(analyzer.load_rows(con, "claims", "2026-09-08", "m"), [])
        self.assertEqual(
            con.execute("SELECT count(*) FROM claims").fetchone()[0], 1)

    def test_the_union_works_when_only_the_new_side_has_rows(self):
        con = _fresh()
        analyzer.save_rows(con, "claims", "2026-09-08", "m", 540, "d",
                           [{"a": 1, "b": 2, "claim": "only new"}], now_ms=0)
        self.assertEqual(
            con.execute("SELECT count(*) FROM claims").fetchone()[0], 0)
        self.assertEqual(len(analyzer.load_rows(con, "claims", "2026-09-08", "m")), 1)


class NoPluginImportsTest(unittest.TestCase):
    def test_db_imports_no_lane_module(self):
        """The whole point. While db.py imported them, deleting either broke
        every command for every user."""
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "umbra", "db.py"), encoding="utf-8") as f:
            src = f.read()
        for mod in ("actions", "claims"):
            with self.subTest(mod):
                self.assertNotIn(f"from . import {mod}", src)
                self.assertNotIn(f"import {mod} as", src)

    def test_the_lane_modules_no_longer_define_a_schema(self):
        """Two definitions of one table would drift, and the one db.py did not
        read would be the one somebody edited."""
        from umbra.plugins import actions
        from umbra.plugins import claims
        for mod in (actions, claims):
            with self.subTest(mod.__name__):
                self.assertFalse(hasattr(mod, "SCHEMA"))


class MigrateTest(unittest.TestCase):
    def test_migrate_is_repeatable(self):
        """connect() calls it every time, so it has to be safe to repeat."""
        con = sqlite3.connect(":memory:")
        db.migrate(con)
        before = _tables(con)
        db.migrate(con)
        self.assertEqual(_tables(con), before)

    def test_migrate_creates_tables_not_only_columns(self):
        """db.py's own comment records why: a migrate that only adds columns
        hands anything calling it directly a half-migrated database that looks
        entirely normal until some new table is written for the first time — and
        the missing table is always the newest, which is the least exercised."""
        con = sqlite3.connect(":memory:")
        db.migrate(con)
        have = _tables(con)
        for t in ("events", "analyzer_rows", "sink_dispatch", *LEGACY):
            with self.subTest(t):
                self.assertIn(t, have)


if __name__ == "__main__":
    unittest.main()
