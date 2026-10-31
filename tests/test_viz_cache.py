"""Incremental viz: a day's payload is rebuilt only when that day's input changed.

From the 2026-08-19 open-source refactor. A full build took 3.1 seconds and
grew with history — mostly per-day load_range and gzip. The cache is per day,
and its fingerprint covers all
of that day's input:

    events      count / max(ts) / max(rowid) under the same overlap rule as load_range
    inference   count / max(rowid) of inferences / day_events / digests
    code        hashes of the modules that build the payload — changing code
                invalidates everything, **without anyone remembering to bump a
                version** (a forgotten bump shows as the page silently staying
                as it was, a cache's most dangerous failure)
    raw         whether the day carries raw records (see below)

Slimming the payload: by default only the last raw_days days embed raw records
— they are nearly all of the HTML. A day without raw must **say so** in the
page ("not included in this build"), never look like "no records that day".
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import analyze, db, viz

DAY1, DAY2 = "2026-08-13", "2026-08-14"


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.db")
        con = db.connect(self.path)
        for day, uid in ((DAY1, "a"), (DAY2, "b")):
            lo, _ = analyze.day_bounds(day)
            con.execute("INSERT INTO events (uid, ts, source, kind, payload)"
                        " VALUES (?,?,?,?,?)",
                        (uid, lo + 10 * 3600_000, "zsh", "command",
                         '{"cmd": "ls"}'))
        con.commit()
        con.close()
        p = mock.patch.object(db, "DB_PATH", self.path)
        p.start(); self.addCleanup(p.stop)

    def _build(self, **kw):
        # Hold the real connect first: viz.db is the same umbra.db module, and
        # calling db.connect after patching would call the mock itself, recursing
        real_connect = db.connect
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k:
                               real_connect(self.path)):
            calls = []
            real = viz._day_payload
            with mock.patch.object(viz, "_day_payload",
                                   side_effect=lambda *a, **k:
                                   calls.append(a[1]) or real(*a, **k)):
                data = viz.build(since="2026-08-01", **kw)
        return data, calls

    def test_the_first_build_computes_all_and_the_second_hits_all(self):
        _, calls1 = self._build()
        self.assertEqual(len(calls1), 2)
        _, calls2 = self._build()
        self.assertEqual(calls2, [])

    def test_a_cache_hit_is_byte_identical_to_a_fresh_build(self):
        """A cache's most dangerous failure is quietly serving the old. The whole
        payload is compared byte for byte."""
        fresh, _ = self._build(fresh=True)
        cached, calls = self._build()
        self.assertEqual(calls, [])
        self.assertEqual(fresh["days"], cached["days"])

    def test_only_the_changed_day_is_rebuilt(self):
        self._build()
        con = db.connect(self.path)
        lo, _ = analyze.day_bounds(DAY2)
        con.execute("INSERT INTO events (uid, ts, source, kind, payload)"
                    " VALUES ('c',?,?,?,?)",
                    (lo + 11 * 3600_000, "zsh", "command", '{"cmd":"pwd"}'))
        con.commit(); con.close()
        _, calls = self._build()
        self.assertEqual(calls, [DAY2])

    def test_changed_inference_counts_as_a_change(self):
        """Half of what the page draws is the model's judgement — watching only
        events, re-running infer would leave the page as it was, looking like
        "it ran and did nothing"."""
        self._build()
        con = db.connect(self.path)
        con.execute("INSERT INTO day_events (day,model,a_min,b_min,cat,title,"
                    "payload) VALUES (?,?,0,60,'code','x','{}')", (DAY2, "m"))
        con.commit(); con.close()
        _, calls = self._build()
        self.assertEqual(calls, [DAY2])

    def test_changing_the_build_code_invalidates_everything(self):
        self._build()
        with mock.patch.object(viz, "_code_fp", return_value="changed"):
            _, calls = self._build()
        self.assertEqual(len(calls), 2)

    def test_fresh_bypasses_the_cache(self):
        self._build()
        _, calls = self._build(fresh=True)
        self.assertEqual(len(calls), 2)


class RawDietTest(unittest.TestCase):
    """Raw records only for the last raw_days days."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.db")
        con = db.connect(self.path)
        for i, day in enumerate(("2026-06-05", "2026-08-14")):
            lo, _ = analyze.day_bounds(day)
            con.execute("INSERT INTO events (uid, ts, source, kind, payload)"
                        " VALUES (?,?,?,?,?)",
                        (f"u{i}", lo + 3600_000, "zsh", "command", '{"cmd":"x"}'))
        con.commit(); con.close()
        p = mock.patch.object(db, "DB_PATH", self.path)
        p.start(); self.addCleanup(p.stop)

    def _days(self, **kw):
        real_connect = db.connect
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k:
                               real_connect(self.path)):
            return viz.build(since="2026-06-01", **kw)["days"]

    def test_days_outside_the_window_carry_no_raw_and_say_so(self):
        days = self._days(raw_days=30)
        old, new = days["2026-06-05"], days["2026-08-14"]
        self.assertIn("raw", new)
        self.assertNotIn("raw", old)
        # "Not included" and "none" must stay apart — the page says so by this flag
        self.assertTrue(old.get("raw_omitted"))

    def test_raw_days_0_means_all(self):
        days = self._days(raw_days=0)
        self.assertIn("raw", days["2026-06-05"])

    def test_the_line_follows_the_newest_day_not_today(self):
        """Measured from today, opening the page after a few weeks without new
        data would drop every day out of the window — suddenly no raw anywhere,
        while not one row had changed."""
        days = self._days(raw_days=200)
        self.assertIn("raw", days["2026-06-05"])


if __name__ == "__main__":
    unittest.main()
