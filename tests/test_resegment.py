"""After the segmentation algorithm changes, old results in the database must
follow.

Umbra has two kinds of source, with opposite storage semantics:

    1:1         chrome / zsh / git / claude / butler / whoop
                one source record = one row. The uid is the natural key and the
                content never changes.
                INSERT OR IGNORE is entirely right; rerunning ten thousand times
                has no side effect.

    segmenting  location / media / iphone / intent / weread
                fold raw records **into intervals**: GPS points → stays, playback
                progress → listening sessions, highlights → reading sessions. A
                new algorithm turns the same raw data into different rows.

Hit on 2026-08-16: the fix merging same-place round trips was written, green
and committed, yet the database still held the old 154-minute stay. The
recomputed row kept its uid (deliberately, for stability), so INSERT OR IGNORE
ignored it entirely — and the false transit the new algorithm no longer
produces stayed behind as an orphan.

db.py's comment says "mutable state is deliberately not stored, so changing
segmentation needs no data migration" — which assumes **segmentation happens on
read**. These sources segment on write and store the result, so a changed
algorithm means recomputing.
"""

# argparse.Namespace rather than mock.Mock: a Mock invents a truthy object for
# **any** attribute, so every new cmd_import option (--exclude, say) would blow
# up here with a baffling TypeError while the behaviour under test had not
# changed. Namespace is what argparse really passes in.
import argparse
import sqlite3
import unittest

from umbra import db
from umbra.importers import ALL

S = "location"


def row(uid, ts, dur=0, src=S, kind="stay", payload=None):
    return (uid, ts, dur, src, kind, payload or {"place": "家"})


class ReplaceRangeTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def rows(self, src=S):
        return {r[0]: (r[1], r[2]) for r in self.con.execute(
            "select uid, ts, dur_ms from events where source=?", (src,))}

    def test_a_changed_row_is_updated(self):
        """Same uid, recomputed with a different duration — exactly what INSERT
        OR IGNORE misses."""
        db.insert(self.con, [row("loc:stay:100", 100, dur=154 * 60_000)])
        db.replace_range(self.con, S, 0, 1000,
                         [row("loc:stay:100", 100, dur=176 * 60_000)])
        self.assertEqual(self.rows()["loc:stay:100"][1], 176 * 60_000)

    def test_a_row_the_new_run_no_longer_produces_is_removed(self):
        """That false "beach → beach" trip. The new algorithm no longer produces
        it, so it must go."""
        db.insert(self.con, [row("loc:stay:100", 100),
                             row("loc:transit:200", 200, kind="transit")])
        db.replace_range(self.con, S, 0, 1000, [row("loc:stay:100", 100)])
        self.assertEqual(list(self.rows()), ["loc:stay:100"])

    def test_rows_outside_the_range_survive(self):
        """Recompute only the span this batch covers. An incremental import
        brings only recent days and must not clear last year's records."""
        db.insert(self.con, [row("old", 50), row("new", 500)])
        db.replace_range(self.con, S, 100, 1000, [row("new2", 500)])
        self.assertIn("old", self.rows())
        self.assertNotIn("new", self.rows())

    def test_other_sources_are_untouched(self):
        """The batch must be non-empty — an empty one returns early and never
        reaches the DELETE, so this would test nothing (a mutation test caught
        it once)."""
        db.insert(self.con, [row("chrome:1", 500, src="chrome", kind="visit")])
        db.replace_range(self.con, S, 0, 1000, [row("loc:a", 500)])
        self.assertIn("chrome:1", self.rows("chrome"))

    def test_an_empty_batch_deletes_nothing(self):
        """When collection fails or there was no data for the span, existing rows
        must never be cleared.

        The most dangerous one: one network blip + one "delete then write" =
        history silently lost.
        """
        db.insert(self.con, [row("loc:stay:100", 100)])
        db.replace_range(self.con, S, 0, 1000, [])
        self.assertIn("loc:stay:100", self.rows())

    def test_a_row_outside_the_deleted_range_is_still_updated(self):
        """Rows outside the deleted range are written too, and replace the old

        value of the same uid. Delete-then-insert makes REPLACE and IGNORE
        identical inside the range — the old row is gone either way. Only a uid
        conflict outside the range tells them apart.
        """
        db.insert(self.con, [row("loc:stay:9", 9_000, dur=1)])
        db.replace_range(self.con, S, 100, 1000,
                         [row("loc:stay:9", 9_000, dur=999)])
        self.assertEqual(self.rows()["loc:stay:9"][1], 999)

    def test_it_reports_how_many_rows_it_wrote(self):
        db.insert(self.con, [row("a", 100)])
        n = db.replace_range(self.con, S, 0, 1000, [row("a", 100), row("b", 200)])
        self.assertEqual(n, 2)


class WhichSourcesResegmentTest(unittest.TestCase):
    """Which sources recompute must be declared on the source itself, not in a
    list kept elsewhere — whoever adds a source will not think to edit it."""

    SEGMENTED = {"location", "media", "iphone", "intent", "weread"}
    # plaud is 1:1: Plaud's transcript segments **carry real start and end
    # times** and need no aggregation the way GPS points do, so one segment is
    # one row with a fixed uid. "This stretch was a meeting" is segment.py's job
    # at analysis time — marking it SEGMENTED would delete data by time window
    # on every import for nothing.
    ONE_TO_ONE = {"chrome", "zsh", "git", "claude", "butler", "whoop", "plaud"}

    def test_segmented_sources_say_so(self):
        for name in self.SEGMENTED:
            self.assertTrue(getattr(ALL[name], "SEGMENTED", False),
                            f"{name} resegments but is not marked SEGMENTED")

    def test_one_to_one_sources_do_not(self):
        for name in self.ONE_TO_ONE:
            self.assertFalse(getattr(ALL[name], "SEGMENTED", False),
                             f"{name} is 1:1; SEGMENTED would delete data for nothing")

    def test_every_importer_is_accounted_for(self):
        """A new source added without deciding which kind it is — this flags it."""
        unknown = set(ALL) - self.SEGMENTED - self.ONE_TO_ONE - {"knowledgec"}
        self.assertEqual(unknown, set(), f"new sources not classified: {unknown}")


class ImportWiringTest(unittest.TestCase):
    """The wiring needs testing too.

    db.replace_range green, SEGMENTED set, the branch in cmd_import forgotten —
    every part right and the whole not working. A mutation turning that line in
    cmd_import into `if False:` turned no test red; that was the hole.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def _run(self, rows):
        import contextlib
        import io
        from unittest import mock
        from umbra import __main__ as cli
        # cmd_import prints progress to stdout, which would bury unittest's summary
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["location"], "collect",
                               return_value=rows), \
             mock.patch.object(cli, "cmd_stats"):
            cli.cmd_import(argparse.Namespace(only=["location"], full=True, exclude=None))

    def test_a_segmented_source_gets_its_changed_rows_updated(self):
        db.insert(self.con, [row("loc:stay:100", 100, dur=154 * 60_000)])
        self._run([row("loc:stay:100", 100, dur=176 * 60_000)])
        got = self.con.execute(
            "select dur_ms from events where uid='loc:stay:100'").fetchone()
        self.assertEqual(got[0], 176 * 60_000, "the segmenting source's fix did not reach the database")

    def test_a_segmented_source_drops_rows_it_no_longer_produces(self):
        db.insert(self.con, [row("loc:stay:100", 100),
                             row("loc:transit:150", 150, kind="transit")])
        self._run([row("loc:stay:100", 100)])
        left = [r[0] for r in self.con.execute(
            "select uid from events where source='location'")]
        self.assertEqual(left, ["loc:stay:100"])


class FullReimportRefreshesPayloadTest(unittest.TestCase):
    """When a 1:1 source's payload changes shape, what about the tens of
    thousands of stored rows?

    Hit on 2026-08-16 adding the synced field to chrome (marking visits synced
    from other devices): the uid is Chrome's visit id and never changes, so
    INSERT OR IGNORE ignored every one — importer written, tests green, not one
    row in the database touched.

    The same hole as location above, which SEGMENTED fixed; 1:1 sources take
    another road, and nobody thought they would change too.

    The fix: `--full` already means "read again from the source", so the
    source's payload should win. Incremental runs stay IGNORE — that path writes
    tens of thousands of rows a day, and conflict checks would be wasted.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def _payload(self, uid):
        import json
        return json.loads(self.con.execute(
            "select payload from events where uid=?", (uid,)).fetchone()[0])

    def _run(self, rows, full):
        import contextlib
        import io
        from unittest import mock
        from umbra import __main__ as cli
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["chrome"], "collect", return_value=rows), \
             mock.patch.object(cli.ALL["chrome"], "PLATFORM", None), \
             mock.patch.object(cli, "cmd_stats"):
            # PLATFORM forced to None: this tests --full's overwrite, not the
            # platform gate — otherwise chrome (darwin only) is set aside by
            # roster on CI's Ubuntu and collect never runs (seen on the first run).
            cli.cmd_import(argparse.Namespace(only=["chrome"], full=full, exclude=None))

    OLD = ("chrome:Default:7", 500, 0, "chrome", "visit", {"title": "手机上搜的"})
    NEW = ("chrome:Default:7", 500, 0, "chrome", "visit",
           {"title": "手机上搜的", "synced": True})

    def test_full_writes_the_new_field_in(self):
        db.insert(self.con, [self.OLD])
        self._run([self.NEW], full=True)
        self.assertTrue(self._payload("chrome:Default:7").get("synced"),
                        "after a --full rerun the new field is still missing")

    def test_an_incremental_run_leaves_stored_rows_alone(self):
        """The incremental path writes tens of thousands of rows a day; comparing
        each is waste — and it only receives records since last time, so a
        collision with a stored row means something else is wrong."""
        db.insert(self.con, [self.OLD])
        self._run([self.NEW], full=False)
        self.assertNotIn("synced", self._payload("chrome:Default:7"))

    def test_a_source_declaring_rewrite_need_not_wait_for_full(self):
        """plaud re-fetches recent files (cloud transcription finishes after
        sync). A re-fetched complete transcript must replace the partial one —
        and scheduled jobs run incrementally, without --full.

        `plaud.REWRITE = True` was tested alone, `db.insert(replace=)` too, and
        the getattr between them could be missing with everything green — a
        mutation test caught it, the same kind of hole as ImportWiringTest above.
        """
        import contextlib
        import io
        from unittest import mock
        from umbra import __main__ as cli
        old = ("plaud:f1:0", 500, 0, "plaud", "speech", {"text": "残缺的"})
        new = ("plaud:f1:0", 500, 0, "plaud", "speech", {"text": "补全了"})
        db.insert(self.con, [old])
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["plaud"], "collect", return_value=[new]), \
             mock.patch.object(cli, "cmd_stats"):
            cli.cmd_import(argparse.Namespace(only=["plaud"], full=False, exclude=None))
        self.assertEqual(self._payload("plaud:f1:0")["text"], "补全了",
                         "the re-fetched transcript was dropped by INSERT OR IGNORE")

    def test_rewrite_tells_real_changes_from_unchanged_rewrites_and_returns_changed_days(self):
        import contextlib
        import io
        import os
        import time as _time
        from unittest import mock
        from umbra import __main__ as cli

        # touched holds **local calendar days** (cmd_import uses fromtimestamp on
        # purpose), so the time zone must be pinned first, or this asserts where
        # the machine running it is. A hard-coded 1970-01-01 held only on the US
        # west coast; all four jobs on CI's UTC runners went red.
        #
        # addCleanup is LIFO: tzset registered first runs last, so TZ is restored
        # before libc rereads it. The other way round libc keeps UTC cached and
        # leaks it into other tests.
        self.addCleanup(_time.tzset)
        self.enterContext(mock.patch.dict(os.environ, {"TZ": "UTC"}))
        _time.tzset()

        day = 86_400_000
        same = ("plaud:f1:0", 500, 0, "plaud", "speech", {"text": "没变"})
        old = ("plaud:f2:0", day, 0, "plaud", "speech", {"text": "残缺"})
        changed = ("plaud:f2:0", day, 0, "plaud", "speech", {"text": "补全"})
        new = ("plaud:f3:0", day + 1, 0, "plaud", "speech", {"text": "新录音"})
        db.insert(self.con, [same, old])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["plaud"], "collect",
                               return_value=[same, changed, new]), \
             mock.patch.object(cli, "cmd_stats"):
            touched = cli.cmd_import(argparse.Namespace(
                only=["plaud"], full=False, exclude=None))

        # changed and new both fall on 1970-01-02 (UTC); same is unchanged, and
        # its day (1970-01-01) must not be in the set.
        self.assertEqual(touched, ["1970-01-02"])
        text = out.getvalue()
        self.assertIn("rewritten", text)
        self.assertIn("new 1", text)
        self.assertIn("changed 1", text)
        self.assertIn("unchanged 1", text)

    def test_full_does_not_delete_records_outside_the_batch(self):
        """1:1 sources must not use the segmenting "delete by window then write" —
        chrome returns the whole database per collect, and other sources' rows
        must never be caught up in it."""
        db.insert(self.con, [("git:abc", 500, 0, "git", "reflog", {"x": 1})])
        self._run([self.NEW], full=True)
        self.assertIsNotNone(self.con.execute(
            "select 1 from events where uid='git:abc'").fetchone(),
            "other sources were deleted along with a --full")


if __name__ == "__main__":
    unittest.main()
