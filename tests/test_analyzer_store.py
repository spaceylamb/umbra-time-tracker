"""Generic Analyzer storage, and the cache that decides what a round costs.

Two tables, because they answer questions a single table cannot both answer.
`analyzer_rows` holds what a window produced. `analyzer_runs` holds that the
window ran and what it read — and without that second record, a window where the
model genuinely found nothing looks exactly like a window that was never called.
The next round then pays to ask again, every round, forever, and nothing reports
it.

The empty-batch rule here is the **opposite** of the one on db.replace_range and
inference.save, and the inversion is deliberate. There, an empty batch means
collection failed and deleting would destroy data that cannot be recovered.
Here, an empty batch means the model was asked and answered nothing — a result,
and one worth remembering.
"""

import sqlite3
import unittest

from umbra import analyzer, db

PLUGIN, DAY, MODEL = "claims", "2026-08-27", "gemini-3.7-flash"


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def save(self, rows, win_a=540, plugin=PLUGIN, day=DAY, model=MODEL,
             digest="d1"):
        return analyzer.save_rows(self.con, plugin, day, model, win_a, digest,
                                  rows, now_ms=0)

    def test_rows_read_back_in_window_then_row_order(self):
        self.save([{"a": 545, "b": 546, "claim": "second"}], win_a=570)
        self.save([{"a": 541, "b": 542, "claim": "first"},
                   {"a": 543, "b": 544, "claim": "also first"}], win_a=540)
        got = analyzer.load_rows(self.con, PLUGIN, DAY, MODEL)
        self.assertEqual([r["claim"] for r in got],
                         ["first", "also first", "second"])

    def test_a_row_keeps_what_it_is_about_separately_from_its_window(self):
        """The span a row describes is not the window it came from.

        A goal attribution names the stretch it credits; a claim names the minute
        it was said. Both sit inside the window and neither equals it, so storing
        only one of the two loses information the reader needs.
        """
        self.save([{"a": 551, "b": 553, "claim": "x"}], win_a=540)
        got = analyzer.load_rows(self.con, PLUGIN, DAY, MODEL)[0]
        self.assertEqual((got["win_a"], got["a"], got["b"]), (540, 551, 553))

    def test_several_rows_from_one_window_all_survive(self):
        """One span credited to two goals is two rows at the same minute, so the
        minute alone cannot be the key."""
        self.save([{"a": 600, "b": 630, "goal": 1},
                   {"a": 600, "b": 630, "goal": 2}], win_a=600)
        got = analyzer.load_rows(self.con, PLUGIN, DAY, MODEL)
        self.assertEqual(sorted(r["goal"] for r in got), [1, 2])

    def test_rewriting_a_window_replaces_rather_than_duplicates(self):
        self.save([{"a": 541, "claim": "old"}], win_a=540)
        self.save([{"a": 541, "claim": "new"}], win_a=540)
        got = analyzer.load_rows(self.con, PLUGIN, DAY, MODEL)
        self.assertEqual([r["claim"] for r in got], ["new"])

    def test_rewriting_one_window_leaves_the_others_alone(self):
        self.save([{"a": 541, "claim": "keep"}], win_a=540)
        self.save([{"a": 571, "claim": "replace"}], win_a=570)
        self.save([{"a": 571, "claim": "replaced"}], win_a=570)
        got = analyzer.load_rows(self.con, PLUGIN, DAY, MODEL)
        self.assertEqual([r["claim"] for r in got], ["keep", "replaced"])

    def test_two_plugins_do_not_see_each_others_rows(self):
        self.save([{"a": 541, "x": "claims"}], plugin="claims")
        self.save([{"a": 541, "x": "actions"}], plugin="actions")
        self.assertEqual(
            [r["x"] for r in analyzer.load_rows(self.con, "claims", DAY, MODEL)],
            ["claims"])

    def test_two_models_do_not_see_each_others_rows(self):
        self.save([{"a": 541, "x": "m1"}], model="m1")
        self.save([{"a": 541, "x": "m2"}], model="m2")
        self.assertEqual(
            [r["x"] for r in analyzer.load_rows(self.con, PLUGIN, DAY, "m1")],
            ["m1"])

    def test_reading_without_a_model_returns_every_models_rows(self):
        self.save([{"a": 541, "x": "m1"}], model="m1")
        self.save([{"a": 541, "x": "m2"}], model="m2")
        self.assertEqual(len(analyzer.load_rows(self.con, PLUGIN, DAY)), 2)

    def test_a_day_with_no_rows_reads_as_empty_not_as_an_error(self):
        self.assertEqual(analyzer.load_rows(self.con, PLUGIN, "2020-01-01"), [])


class RunMarkerTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def test_an_empty_batch_still_records_that_the_window_ran(self):
        """The whole reason the second table exists.

        Without this, a window where the model found nothing is recomputed every
        round — no error, no visible symptom, just a bill.
        """
        analyzer.save_rows(self.con, PLUGIN, DAY, MODEL, 540, "d1", [], now_ms=0)
        self.assertEqual(analyzer.load_rows(self.con, PLUGIN, DAY, MODEL), [])
        self.assertEqual(analyzer.ran(self.con, PLUGIN, DAY, MODEL), {540})

    def test_a_window_that_never_ran_is_distinguishable_from_an_empty_one(self):
        analyzer.save_rows(self.con, PLUGIN, DAY, MODEL, 540, "d1", [], now_ms=0)
        ran = analyzer.ran(self.con, PLUGIN, DAY, MODEL)
        self.assertIn(540, ran)
        self.assertNotIn(570, ran)

    def test_run_markers_are_per_plugin(self):
        analyzer.save_rows(self.con, "claims", DAY, MODEL, 540, "d1", [], now_ms=0)
        self.assertEqual(analyzer.ran(self.con, "actions", DAY, MODEL), set())


class CacheKeyTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def test_an_unchanged_input_is_a_hit(self):
        d = analyzer.digest_of("window text")
        analyzer.save_rows(self.con, PLUGIN, DAY, MODEL, 540, d, [], now_ms=0)
        self.assertTrue(analyzer.cached(self.con, PLUGIN, DAY, MODEL, 540, d))

    def test_one_changed_character_is_a_miss(self):
        analyzer.save_rows(self.con, PLUGIN, DAY, MODEL, 540,
                           analyzer.digest_of("window text"), [], now_ms=0)
        self.assertFalse(analyzer.cached(
            self.con, PLUGIN, DAY, MODEL, 540, analyzer.digest_of("window texu")))

    def test_a_window_never_run_is_a_miss(self):
        self.assertFalse(analyzer.cached(
            self.con, PLUGIN, DAY, MODEL, 540, analyzer.digest_of("x")))

    def test_params_participate_in_the_key(self):
        """The gap that only appeared when goals was planned.

        Attribution depends on the three goal sentences, which appear in no
        window. A key over the window alone would keep serving numbers computed
        against goal text the user has since rewritten, and nothing would say so.
        """
        a = analyzer.digest_of("same window", {"goals": ["ship Umbra"]})
        b = analyzer.digest_of("same window", {"goals": ["read a book"]})
        self.assertNotEqual(a, b)

    def test_params_key_order_does_not_change_the_digest(self):
        """A dict's iteration order is not part of the input."""
        self.assertEqual(analyzer.digest_of("w", {"a": 1, "b": 2}),
                         analyzer.digest_of("w", {"b": 2, "a": 1}))

    def test_no_params_and_empty_params_agree(self):
        self.assertEqual(analyzer.digest_of("w"), analyzer.digest_of("w", None))

    def test_window_text_and_params_cannot_be_rearranged_into_a_collision(self):
        """The separator between them is a byte JSON cannot emit."""
        self.assertNotEqual(analyzer.digest_of("ab", {"c": 1}),
                            analyzer.digest_of("a", {"bc": 1}))


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def test_dropping_one_plugin_leaves_the_others(self):
        """Uninstalling is a delete by name, not a hunt through the filesystem."""
        for p in ("claims", "actions"):
            analyzer.save_rows(self.con, p, DAY, MODEL, 540, "d", [{"a": 1}],
                               now_ms=0)
        analyzer.drop(self.con, "claims")
        self.assertEqual(analyzer.load_rows(self.con, "claims", DAY), [])
        self.assertEqual(len(analyzer.load_rows(self.con, "actions", DAY)), 1)

    def test_dropping_removes_run_markers_too(self):
        """Leaving them behind would make every window read as already answered
        while its rows are gone."""
        analyzer.save_rows(self.con, "claims", DAY, MODEL, 540, "d", [], now_ms=0)
        analyzer.drop(self.con, "claims")
        self.assertEqual(analyzer.ran(self.con, "claims", DAY, MODEL), set())


class OwnTablesEscapeHatchTest(unittest.TestCase):
    """A plugin that genuinely needs its own schema declares it.

    A declaration rather than DDL run at import time, because the point is that
    "this plugin owns a table" is readable by someone opening the plugin. A
    plugin that quietly executes CREATE TABLE owns one invisibly, and the next
    person to wonder where a table came from has no way to find out.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def _tables(self):
        return {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def test_a_declared_table_is_created(self):
        analyzer.ensure_tables(
            self.con, "CREATE TABLE IF NOT EXISTS mine (id INTEGER PRIMARY KEY);")
        self.assertIn("mine", self._tables())

    def test_declaring_nothing_creates_nothing(self):
        before = self._tables()
        analyzer.ensure_tables(self.con, None)
        analyzer.ensure_tables(self.con, "")
        self.assertEqual(self._tables(), before)

    def test_declaring_twice_is_a_no_op(self):
        decl = "CREATE TABLE IF NOT EXISTS mine (id INTEGER PRIMARY KEY);"
        analyzer.ensure_tables(self.con, decl)
        analyzer.ensure_tables(self.con, decl)
        self.assertIn("mine", self._tables())

    def test_the_core_writes_nothing_to_a_declared_table(self):
        """What a plugin takes into its own hands, it keeps in its own hands."""
        analyzer.ensure_tables(
            self.con, "CREATE TABLE IF NOT EXISTS mine (id INTEGER PRIMARY KEY);")
        analyzer.save_rows(self.con, PLUGIN, DAY, MODEL, 540, "d",
                           [{"a": 1, "x": "y"}], now_ms=0)
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM mine").fetchone()[0], 0)


class SchemaAssemblyTest(unittest.TestCase):
    def test_the_tables_come_from_the_one_assembled_constant(self):
        """Hanging a table off connect() alone produces green unit tests and a
        broken runtime — db.py's own comment records that happening."""
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("analyzer_rows", names)
        self.assertIn("analyzer_runs", names)


if __name__ == "__main__":
    unittest.main()
