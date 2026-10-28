"""taxonomy is wired into three places: normalised on save, on load, and in
the prompt.

Keys are stored and read; old rows with Chinese names read back as keys too —
no data migration, a smarter read.
"""

import os
import sqlite3
import tempfile
import unittest

from umbra import db, inference as INF


def _con():
    con = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    con.executescript(db.SCHEMA)
    return con


class NormaliseOnSaveAndLoadTest(unittest.TestCase):
    def test_save_normalises_chinese_to_a_key(self):
        con = _con()
        INF.save(con, "2026-08-19", "m", [
            {"start": "09:00", "end": "09:30", "cat": "睡觉"}])
        self.assertEqual(list(con.execute(
            "SELECT cat FROM inferences"))[0][0], "sleep")

    def test_load_normalises_old_chinese_rows_to_a_key(self):
        from umbra import tz
        con = _con()
        # A row as stored: keyed by the day's cut (see inference.py's schema).
        lo, hi = tz.bounds(con, "2026-08-19")
        con.execute("INSERT INTO inferences (day,model,a_min,b_min,cat,payload,"
                    "day_lo,day_n) VALUES ('2026-08-19','m',0,30,'写代码','{}',?,?)",
                    (lo, (hi - lo + 1) // 60_000))
        got = INF.load(con, "2026-08-19")["m"][0]
        self.assertEqual(got["cat"], "code")

    def test_events_are_normalised_too(self):
        con = _con()
        INF.save_events(con, "2026-08-19", "m", [
            {"start": "09:00", "end": "10:00", "cat": "查资料", "title": "x"}])
        got = INF.load_events(con, "2026-08-19")["m"][0]
        self.assertEqual(got["cat"], "research")

    def test_an_unrecognised_category_round_trips_unchanged(self):
        con = _con()
        INF.save(con, "2026-08-19", "m", [
            {"start": "09:00", "end": "09:30", "cat": "发呆"}])
        self.assertEqual(INF.load(con, "2026-08-19")["m"][0]["cat"], "发呆")

    def test_a_missing_cat_defaults_to_unknown_not_chinese(self):
        con = _con()
        INF.save(con, "2026-08-19", "m", [{"start": "09:00", "end": "09:30"}])
        self.assertEqual(list(con.execute(
            "SELECT cat FROM inferences"))[0][0], "unknown")


class Prompt(unittest.TestCase):
    def test_infer_s_category_list_comes_from_taxonomy(self):
        from umbra import infer, taxonomy
        self.assertEqual(infer.cats("zh"), taxonomy.prompt_cats("zh"))
        self.assertEqual(infer.cats("en"), taxonomy.prompt_cats("en"))

    def test_the_prompt_follows_the_language_english_by_default(self):
        from unittest import mock
        from umbra import config, infer
        with mock.patch.object(config, "load", return_value={}):
            self.assertIn("Judge only", infer.prompt_tpl())
        with mock.patch.object(config, "load",
                               return_value={"infer": {"lang": "zh"}}):
            self.assertIn("只判断", infer.prompt_tpl())


if __name__ == "__main__":
    unittest.main()
