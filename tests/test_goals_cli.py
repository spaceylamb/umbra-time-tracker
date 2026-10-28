"""`umbra goals` — setting and reading priorities from the terminal.

Setting goals and attribution used to live on Cloudflare (the page plus Pages
Functions), so "runs locally, no dependencies" quietly shrank for this one
feature. With both back on the machine, this command is a first-class path.

What is pinned here is the numbers it reads: "no time spent this week" and "not
judged yet" must not look alike.
"""

import argparse
import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest import mock

from umbra import __main__ as m
from umbra import db, goals


def _con():
    con = sqlite3.connect(":memory:")
    con.executescript(db.SCHEMA)
    return con


def _run(**kw):
    con = kw.pop("con")
    d = {"action": "show", "text": [], "week": None}
    d.update(kw)
    args = argparse.Namespace(**d)
    buf = io.StringIO()
    with mock.patch.object(db, "connect", return_value=con), redirect_stdout(buf):
        m.cmd_goals(args)
    return buf.getvalue()


class SetGoalsTest(unittest.TestCase):
    def test_set_writes_to_the_database(self):
        con = _con()
        _run(con=con, action="set", text=["把 Umbra 做完", "推进 BFS"],
             week="2026-W34")
        self.assertEqual(goals.load_goals(con, "2026-W34"),
                         ["把 Umbra 做完", "推进 BFS"])

    def test_no_week_means_this_week(self):
        con = _con()
        _run(con=con, action="set", text=["本周的事"])
        import datetime as dt
        wk = goals.week_of(dt.date.today().isoformat())
        self.assertEqual(goals.load_goals(con, wk), ["本周的事"])

    def test_more_than_three_is_refused(self):
        """A fourth "most important thing" means none of them is."""
        con = _con()
        out = _run(con=con, action="set", text=list("abcd"), week="2026-W34")
        self.assertIn("three", out)
        self.assertEqual(goals.load_goals(con, "2026-W34"), [])


def _attrib(con, day, model, gdig, rows):
    """Seed attribution the way the goals plugin writes it.

    Through the core's row store rather than the frozen goal_attrib table: that
    is what a database gets today, and a test that seeds the old shape would
    keep passing long after the live path had broken. Reading the frozen rows is
    covered in test_goals.py.
    """
    from umbra import analyzer, goals as _g
    analyzer.save_rows(con, _g.PLUGIN, day, model, 0, "seeded",
                       [{"a": a, "b": b, "goal": g, "gdig": gdig, "why": w}
                        for a, b, g, w in rows], now_ms=0)


class ShowGoalsTest(unittest.TestCase):
    def test_show_lists_goals_and_hours(self):
        con = _con()
        goals.save_goals(con, "2026-W34", ["把 Umbra 做完"])
        from umbra import infer
        _attrib(con, "2026-08-17", infer.MODEL,
                          goals.gdigest(["把 Umbra 做完"]), [(600, 690, 1, "x")])
        out = _run(con=con, action="show", week="2026-W34")
        self.assertIn("把 Umbra 做完", out)
        self.assertIn("1.5", out)          # 90 minutes

    def test_not_yet_attributed_shows_0_not_blank(self):
        """"No time spent this week" and "not judged yet" must be told apart —
        the former shows 0."""
        con = _con()
        goals.save_goals(con, "2026-W34", ["还没干的事"])
        out = _run(con=con, action="show", week="2026-W34")
        self.assertIn("还没干的事", out)
        self.assertIn("0.0", out)

    def test_no_goals_says_what_to_do(self):
        out = _run(con=_con(), action="show", week="2026-W01")
        self.assertIn("goals set", out)     # tells you the next step


if __name__ == "__main__":
    unittest.main()
