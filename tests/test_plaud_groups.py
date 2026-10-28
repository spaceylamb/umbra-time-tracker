"""After re-transcription the stored copy must be replaced **whole**, not just
overwritten from the front.

A recording re-generated in the app came back shorter than the stored copy.
plaud is a REWRITE source and writes with INSERT OR REPLACE — and REPLACE
**only touches the uids this batch brings**:

    uid = plaud:{file_id}:{n}         n is the segment's position, not its identity

    :0 … :new-1      overwritten by the new content       ✓
    :new … :old-1    not in the new batch at all
                     → REPLACE never reaches them, and nothing deletes them
                     → they stay forever

The trap is that **they are not garbage, they are duplicates**. Search the new
version for an orphan's text and it is there, at the same moment — the new
version merged segments, the array shifted, and the same sentence moved to a
new index. So one sentence is stored twice with the same moment and different
uids: dedupe cannot remove it, COUNT(*) doubles, and every row looks fine.

**What this bug produces passes every "is this row right" check.** It only
shows when the whole transcript is lined up against the source by position —
so it has to be solved in the write layer; nothing downstream can catch it.

A group is "the uid minus its last segment":

    plaud:{file_id}:{i}        → plaud:{file_id}         transcript
    plaud:mark:{file_id}:{i}   → plaud:mark:{file_id}    hardware marks

**Only groups present in the new batch are replaced**, the same discipline as
replace_range. The mark block is empty until transcription finishes (only a
placeholder), so marks_of returning [] is a normal intermediate state every
round — treating "not fetched this round" as "gone" would erase history on one
network blip.
"""

import sqlite3
import unittest

from umbra import db

S = "plaud"
FID = "42d4e7b33fffde3719ef41a14a49823d"
DAY_MS = 86_400_000


def seg(i, ts, text="said something"):
    return (f"plaud:{FID}:{i}", ts, 1000, S, "speech",
            {"text": text, "speaker": "Speaker 1", "file_id": FID})


def mark(i, ts):
    return (f"plaud:mark:{FID}:{i}", ts, None, S, "mark",
            {"title": "mark", "content": "", "file_id": FID})


class ReplaceGroupsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def uids(self):
        return sorted(u for (u,) in self.con.execute(
            "select uid from events where source=?", (S,)))

    def test_shorter_new_version_leaves_no_orphans(self):
        """Old 5 segments → new 3. The 2 left over are the smallest repro."""
        db.insert(self.con, [seg(i, 1000 + i) for i in range(5)])
        db.replace_groups(self.con, [seg(i, 1000 + i, "new version") for i in range(3)])
        self.assertEqual(self.uids(), [f"plaud:{FID}:{i}" for i in range(3)])

    def test_the_day_a_row_was_deleted_from_needs_recompute(self):
        """A deletion is a change.

        Hand only new and changed rows downstream and "this day lost some
        sentences" never triggers a recompute — the page and the digest stay on
        the version with the duplicates, with no error anywhere.
        """
        old_day = 1_757_000_000_000              # the tail falls on another day
        db.insert(self.con, [seg(0, 1000), seg(1, old_day)])
        out = db.replace_groups(self.con, [seg(0, 1000)])
        self.assertEqual(out["deleted"], 1)
        self.assertIn(old_day, [r[1] for r in out["rows"]])

    def test_a_group_absent_from_the_batch_is_untouched(self):
        """Marks are not fetchable until transcription finishes, so marks_of
        returning [] is a normal intermediate state.

        "Not fetched this round" is not "gone". Treated as the latter, one
        network blip would erase every hardware mark — the highest-signal rows
        in the store.
        """
        db.insert(self.con, [seg(0, 1000), mark(0, 1500)])
        db.replace_groups(self.con, [seg(0, 1000, "new version")])
        self.assertIn(f"plaud:mark:{FID}:0", self.uids())

    def test_an_empty_batch_deletes_nothing(self):
        """The replace_range discipline: a failed collection must not look like
        history being wiped."""
        db.insert(self.con, [seg(i, 1000 + i) for i in range(3)])
        db.replace_groups(self.con, [])
        self.assertEqual(len(self.uids()), 3)

    def test_other_files_are_not_touched(self):
        """One recording at a time. Other recordings' rows are left alone."""
        other = ("plaud:9e1c0000000000000000000000000000:0", 2000, 1000, S,
                 "speech", {"text": "someone else", "file_id": "9e1c"})
        db.insert(self.con, [seg(0, 1000), seg(1, 1001), other])
        db.replace_groups(self.con, [seg(0, 1000, "new version")])
        self.assertIn(other[0], self.uids())

    def test_unchanged_rows_are_not_reported_as_dirty(self):
        """Rewriting the same content must not trigger a recompute.

        plaud re-fetches recent files every round and most of them have not
        changed by a character. Handing them on as changes would re-infer the
        whole last week every round.
        """
        db.insert(self.con, [seg(0, 1000), seg(1, 1001)])
        out = db.replace_groups(self.con, [seg(0, 1000), seg(1, 1001)])
        self.assertEqual((out["new"], out["changed"], out["deleted"]), (0, 0, 0))
        self.assertEqual(out["rows"], [])

    def test_unchanged_rows_are_not_rewritten_either(self):
        """Not reporting them is not enough; **the store must not be touched**.

        Measured: every hourly round reported "changed 0", yet re-wrote every
        row with INSERT OR REPLACE — REPLACE deletes then inserts, each row gets
        a new rowid, and thousands of pages are rewritten with the content
        unchanged. Anything that copies the database page by page (a backup, a
        replica) then moves megabytes every hour for nothing.
        """
        db.insert(self.con, [seg(0, 1000), seg(1, 1001)])
        self.con.commit()
        before = self.con.total_changes
        db.replace_groups(self.con, [seg(0, 1000), seg(1, 1001)])
        self.assertEqual(self.con.total_changes, before)

    def test_only_the_changed_row_is_written(self):
        """One sentence of a transcript changed: write that one, leave the rest
        — and the changed one must really land."""
        db.insert(self.con, [seg(0, 1000), seg(1, 1001), seg(2, 1002)])
        self.con.commit()
        before = self.con.total_changes
        out = db.replace_groups(self.con, [seg(0, 1000), seg(1, 1001, "edited"),
                                           seg(2, 1002)])
        self.assertEqual(self.con.total_changes - before, 1)
        self.assertEqual(out["changed"], 1)
        (payload,) = self.con.execute(
            "select payload from events where uid=?", (f"plaud:{FID}:1",)).fetchone()
        self.assertIn("edited", payload)


class ImportWiringTest(unittest.TestCase):
    """The wiring needs its own test.

    db.replace_groups green, GROUPED set, and the branch in cmd_import never
    written: every part right, the whole not working. Testing them apart lets
    "forgot to wire it" go red on its own.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def _run(self, rows):
        import argparse
        import contextlib
        import io
        from unittest import mock

        from umbra import __main__ as cli
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(cli.db, "connect", return_value=self.con), \
             mock.patch.object(cli.sources, "missing", return_value=[]), \
             mock.patch.object(cli.ALL["plaud"], "collect", return_value=rows), \
             mock.patch.object(cli, "cmd_stats"):
            return cli.cmd_import(argparse.Namespace(
                only=["plaud"], full=False, exclude=None))

    def test_a_reimported_recording_leaves_no_orphans(self):
        db.insert(self.con, [seg(i, 1_757_000_000_000 + i) for i in range(5)])
        self._run([seg(i, 1_757_000_000_000 + i, "new version") for i in range(3)])
        left = sorted(u for (u,) in self.con.execute(
            "select uid from events where source='plaud'"))
        self.assertEqual(left, [f"plaud:{FID}:{i}" for i in range(3)],
                         "the old version's tail survived re-transcription")

    def test_the_deleted_rows_day_is_returned_for_recompute(self):
        """The days cmd_import returns must include a day where only a deletion
        happened.

        ops.voice decides which days to scan from this list. Miss it and that
        day's judgement stays on the old version with the duplicates — and a
        deletion is the one change that adds no row at all.
        """
        import datetime as dt
        gone_ms = 1_757_000_000_000                # a different day from row 0
        keep_ms = gone_ms + 3 * DAY_MS
        db.insert(self.con, [seg(0, keep_ms), seg(1, gone_ms)])
        days = self._run([seg(0, keep_ms)])
        self.assertIn(
            dt.datetime.fromtimestamp(gone_ms / 1000).date().isoformat(),
            days or [])


if __name__ == "__main__":
    unittest.main()
