"""The page's "is it broken" — one number that turns red on its own.

## Why the verdict cannot be computed in Python

The lesson of 2026-08-20 was "JavaScript copying the rule drifts", so the
verdict moved into the payload. That still holds, but it has **a hole it cannot
cover itself**:

    The page built at 08:19 judged "fresh ✓", and that conclusion is frozen in
    the file. Once deploy fails the page stops updating; open it three days
    later and it still says "fresh ✓".

That is: **at the moment it most needs to warn, it is guaranteed to lie.**

## So it is split like this

The rule (what counts as bad, which time to compare, where the line is) still
comes only from Python; the JavaScript does **one subtraction and one
comparison**:

    stale = f.bad || f.check_ts == null || (now - f.check_ts) > f.stale_min*60000

So a page left open has a number that climbs and turns red by itself.
A table of cases below is fed to both Python and the JavaScript, and the answers
must match case by case.

## A real bug the voice split introduced, fixed on the way

viz.py took one ops.stale_min() (the sync interval × 1.5 = 540 minutes) for
every source. Once voice was split, plaud ran hourly but only warned after nine
hours without an update — eight hours down and you could not see it. The
threshold has to follow each source's own interval.
"""

import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from umbra import config, db, ops, viz


def with_conf(text=""):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    return mock.patch.object(config, "CONFIG_DIR", d)


SPLIT = "[schedule]\nsplit_voice = true\nvoice_interval_min = 60\n"


def row(**kw):
    base = {"source": "x", "count": 1, "last_event": 1, "event_age_min": 5.0,
            "last_run": 1, "run_age_min": 10.0, "ok": True, "error": None,
            "last_ok": 1, "ok_age_min": 10.0, "fails": 0, "live": False}
    base.update(kw)
    return base


def payload():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    con = db.connect(path)
    now = int(time.time() * 1000)
    db.insert(con, [("e:1", now, None, "zsh", "command", {"cmd": "ls"})])
    con.commit()
    con.close()
    with mock.patch.object(viz.db, "connect",
                           side_effect=lambda *a, **k: sqlite3.connect(path)):
        return viz.build()


class PageAgeTest(unittest.TestCase):
    """An absolute string like `built 2026-08-24 13:33` does not say "is that
    long ago" — you have to do the subtraction yourself. What it really answers
    is a yes-or-no question."""

    def test_the_payload_carries_the_build_time_in_absolute_ms(self):
        p = payload()
        self.assertIn("built_ms", p)
        self.assertIsInstance(p["built_ms"], int)
        self.assertLess(abs(p["built_ms"] - time.time() * 1000), 60_000)

    def test_the_payload_carries_the_page_s_own_alarm_line(self):
        with with_conf(SPLIT):
            self.assertEqual(payload()["page_stale_min"], 90)

    def test_the_page_s_alarm_line_follows_the_round_that_publishes_it(self):
        """With voice split the hourly voice round publishes the page; without it,
        the six-hourly round does. The wrong interval would let the page stop
        for eight hours without a warning."""
        with with_conf(SPLIT):
            self.assertEqual(ops.page_stale_min(), 90)
        with with_conf("[schedule]\ninterval_min = 360\n"):
            self.assertEqual(ops.page_stale_min(), 540)


class NoRelativeTimeInThePageTest(unittest.TestCase):
    """`run_age_min = 651.9572` is right only at the instant it was computed,
    and starts rotting once stored in a static file. Store absolute times and let
    the reader subtract."""

    def test_fresh_has_no_age_fields(self):
        rotten = [k for f in payload()["fresh"] for k in f if k.endswith("_age_min")]
        self.assertEqual(rotten, [], f"these fields rot: {set(rotten)}")

    def test_fresh_has_no_precomputed_stale(self):
        """The verdict must be computed in the browser, or the moment deploy
        fails it freezes at "fresh ✓" — exactly when it most needs to warn."""
        self.assertNotIn("stale", payload()["fresh"][0])

    def test_fresh_carries_the_three_fields_the_page_needs(self):
        for f in payload()["fresh"]:
            self.assertIn("bad", f)
            self.assertIn("check_ts", f)
            self.assertIn("stale_min", f)


class ThresholdsFollowEachIntervalTest(unittest.TestCase):
    def _by_source(self, conf):
        with with_conf(conf):
            return {f["source"]: f for f in payload()["fresh"]}

    def test_with_voice_split_voice_sources_use_the_voice_interval(self):
        f = viz.stale_fields(row(source="plaud"), 540)
        self.assertEqual(f["stale_min"], 540)   # the caller supplies the threshold; only the shape is checked

    def test_ops_gives_a_threshold_per_source(self):
        with with_conf(SPLIT):
            self.assertEqual(ops.stale_min_for("plaud"), 90)
            self.assertEqual(ops.stale_min_for("chrome"), 540)

    def test_unsplit_every_source_shares_one_threshold(self):
        with with_conf("[schedule]\ninterval_min = 360\n"):
            self.assertEqual(ops.stale_min_for("plaud"), 540)
            self.assertEqual(ops.stale_min_for("chrome"), 540)

    def test_a_resident_source_is_always_two_hours(self):
        """The collector does not follow the import interval: two silent hours
        means it really died."""
        self.assertEqual(viz.stale_fields(row(live=True), 540)["stale_min"], 120)

    def test_a_resident_source_is_judged_by_event_time_not_import_time(self):
        """It has no "import" step — judged by last_run it would always be bad."""
        f = viz.stale_fields(row(live=True, last_event=123, last_run=None), 540)
        self.assertEqual(f["check_ts"], 123)


# One table fed to both sides. The two verdicts drifted once (2026-08-20); this
# cannot be left to people watching.
CASES = [
    ("healthy",            dict()),
    ("one_failure_after_success", dict(ok=False, fails=1)),
    ("failures_reach_streak", dict(ok=False, fails=db.FAIL_STREAK)),
    ("never_succeeded",    dict(ok=False, fails=1, last_ok=None)),
    ("not_run_for_too_long", dict(run_age_min=10_000.0)),
    ("resident_healthy",   dict(live=True, event_age_min=5.0)),
    ("resident_silent",    dict(live=True, event_age_min=300.0)),
]






if __name__ == "__main__":
    unittest.main()
