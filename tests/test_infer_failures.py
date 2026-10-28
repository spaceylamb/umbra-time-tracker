"""A failed model call is recorded, and stops being recorded when it succeeds.

Before this, infer.run_day only counted failures. A failed half hour had no
spans, and the page drew it exactly like a half hour the model read and found
nothing to say about — an outage presented as a finding.
"""

import os
import tempfile
import unittest

from umbra import analyze, db, infer
from umbra import inference as INF

DAY = "2026-08-19"


class FailuresTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        lo, _ = analyze.day_bounds(DAY)
        # Two windows with something in them: 10:00 and 10:30.
        db.insert(self.con, [(f"z{i}", lo + (600 + i * 30 + 5) * 60000, None,
                              "zsh", "command", {"cmd": f"make {i}"})
                             for i in range(2)])

    def run_with(self, call):
        return infer.run_day(self.con, DAY, model="gemini-test", call=call)

    def test_failed_windows_are_stored(self):
        def call(prompt):
            raise RuntimeError("HTTP Error 503: overloaded")
        r = self.run_with(call)
        self.assertEqual(r["failed"], len(INF.load_failures(self.con, DAY)["gemini-test"]))
        self.assertGreater(r["failed"], 0)
        w = INF.load_failures(self.con, DAY)["gemini-test"][0]
        self.assertEqual(w["kind"], "call")

    def test_success_clears_last_rounds_failures(self):
        self.run_with(lambda p: (_ for _ in ()).throw(RuntimeError("timeout")))
        self.assertTrue(INF.load_failures(self.con, DAY))
        self.run_with(lambda p: '[{"start":"10:00","end":"10:30","cat":"code","activity":"x"}]')
        self.assertEqual(INF.load_failures(self.con, DAY), {})

    def test_a_rejected_key_is_auth(self):
        self.run_with(lambda p: (_ for _ in ()).throw(RuntimeError("HTTP Error 401: Unauthorized")))
        kinds = {w["kind"] for w in INF.load_failures(self.con, DAY)["gemini-test"]}
        self.assertEqual(kinds, {"auth"})

    def test_the_key_never_reaches_the_database(self):
        """The Gemini URL carries the key as a query parameter, and HTTP errors
        quote the URL."""
        self.run_with(lambda p: (_ for _ in ()).throw(RuntimeError(
            "403 for https://x/models/g:generateContent?key=AIzaSECRET")))
        reasons = " ".join(w["reason"] for w in INF.load_failures(self.con, DAY)["gemini-test"])
        self.assertNotIn("AIzaSECRET", reasons)


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_a_run_moves_through_its_sources(self):
        run = db.progress_start(self.con, ["chrome", "zsh"])
        p = db.progress_latest(self.con)
        self.assertTrue(p["running"])
        self.assertEqual([s["state"] for s in p["sources"]], ["next", "next"])
        db.progress_set(self.con, run, "chrome", "done", rows=12)
        db.progress_set(self.con, run, "zsh", "reading")
        p = db.progress_latest(self.con)
        self.assertEqual([s["state"] for s in p["sources"]], ["done", "reading"])
        self.assertEqual(p["sources"][0]["rows"], 12)

    def test_an_abort_never_leaves_a_run_reading(self):
        run = db.progress_start(self.con, ["chrome", "zsh", "git"])
        db.progress_set(self.con, run, "chrome", "reading")
        db.progress_abort(self.con, run)
        p = db.progress_latest(self.con)
        self.assertFalse(p["running"])
        self.assertEqual({s["state"] for s in p["sources"]}, {"aborted"})

    def test_a_new_run_replaces_the_old(self):
        db.progress_start(self.con, ["chrome"])
        db.progress_start(self.con, ["zsh"])
        self.assertEqual([s["source"] for s in db.progress_latest(self.con)["sources"]], ["zsh"])


if __name__ == "__main__":
    unittest.main()
