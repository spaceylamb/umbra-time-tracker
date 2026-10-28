"""Chrome History is a **synced, multi-device** database, not this Mac's record
of what was done on it.

Dug up on 2026-08-16 while asking "why is there browsing while idle says nobody
touched the keyboard": of 51 chrome records inside idle stretches, **33 (65%)
came from other devices**.

The most striking: a few searches in a row one afternoon — it looks like a
person actively working, while idle said this machine had no input for five
minutes. Neither was wrong: the searches happened on a phone, and Chrome sync
wrote them into this Mac's History SQLite.

Chrome does mark them; we were not reading it:

    visits.originator_cache_guid   non-empty = produced on another device
    visit_source.source            a row with != 1 (BROWSED) = not browsed here

The latter is the older mechanism: Chrome **writes rows only for visits that
were not browsed here**, so local browsing is not in that table at all. "No row"
means browsed here, not missing data.

It is the mirror image of the dictation bug —
    dictation       the person is at this machine, the signal says not
    synced records  the person is not at this machine, the signal says they are
Both fabricate conflicts, in opposite directions.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra.importers import chrome

WEBKIT = 11644473600


def webkit(unix_s):
    return int((unix_s + WEBKIT) * 1e6)


class FakeHistory:
    """Build a database shaped like Chrome's History. The real one is locked and
    changes daily."""

    def __init__(self, with_originator=True):
        self.dir = tempfile.TemporaryDirectory()
        prof = os.path.join(self.dir.name, "Default")
        os.makedirs(prof)
        self.path = os.path.join(prof, "History")
        con = sqlite3.connect(self.path)
        orig = ", originator_cache_guid TEXT" if with_originator else ""
        con.executescript(f"""
            CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT);
            CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER,
                                visit_time INTEGER, visit_duration INTEGER{orig});
            CREATE TABLE visit_source(id INTEGER PRIMARY KEY, source INTEGER NOT NULL);
        """)
        self.con = con
        self.n = 0

    def visit(self, title, ts=1786800000, guid=None, source=None):
        self.n += 1
        i = self.n
        self.con.execute("INSERT INTO urls VALUES (?,?,?)",
                         (i, f"https://example.com/{i}", title))
        if "originator_cache_guid" in [c[1] for c in
                                       self.con.execute("PRAGMA table_info(visits)")]:
            self.con.execute("INSERT INTO visits VALUES (?,?,?,?,?)",
                             (i, i, webkit(ts), 0, guid or ""))
        else:
            self.con.execute("INSERT INTO visits VALUES (?,?,?,?)",
                             (i, i, webkit(ts), 0))
        if source is not None:
            self.con.execute("INSERT INTO visit_source VALUES (?,?)", (i, source))
        self.con.commit()
        return i

    def rows(self):
        with mock.patch.object(chrome, "CHROME_DIR", self.dir.name):
            return {r[5]["title"]: r[5] for r in chrome.collect()}

    def close(self):
        self.con.close()
        self.dir.cleanup()


class SyncedVisitTest(unittest.TestCase):
    def setUp(self):
        self.h = FakeHistory()
        self.addCleanup(self.h.close)

    def test_local_browsing_is_not_marked(self):
        """Nearly every record is local. A synced: False on each would only add
        size — measured, 40 of 3,776 records were synced."""
        self.h.visit("本机搜的")
        self.assertNotIn("synced", self.h.rows()["本机搜的"])

    def test_records_synced_from_another_device_are_marked(self):
        """A non-empty originator_cache_guid = not produced on this machine."""
        self.h.visit("手机上搜的", guid="ezMJGr6/abc")
        self.assertTrue(self.h.rows()["手机上搜的"].get("synced"))

    def test_an_empty_string_is_local(self):
        """Chrome writes an empty string for local visits, not NULL — measured,
        63,912 empty strings and 0 NULLs. Testing `is not None` would mark the
        whole database as synced."""
        self.h.visit("本机的", guid="")
        self.assertNotIn("synced", self.h.rows()["本机的"])

    def test_visit_source_saying_not_browsed_is_marked_too(self):
        """The older mechanism, covering records from before the originator field.
        Measured: all 1,882 visit_source rows in one real database were source=0
        (SOURCE_SYNCED)."""
        self.h.visit("老的同步记录", source=0)
        self.assertTrue(self.h.rows()["老的同步记录"].get("synced"))

    def test_visit_source_saying_browsed_is_not_marked(self):
        self.h.visit("本机浏览的", source=1)
        self.assertNotIn("synced", self.h.rows()["本机浏览的"])

    def test_no_row_is_not_missing_data(self):
        """Chrome **writes visit_source rows only for visits not browsed here**.
        Local browsing is not in that table — reading "not found" as "unknown"
        would mark the whole database suspect."""
        self.h.visit("普通浏览")
        self.assertNotIn("synced", self.h.rows()["普通浏览"])


class OldChromeTest(unittest.TestCase):
    """Older Chrome versions lack the originator_cache_guid columns.

    Selecting them raises OperationalError and the whole chrome source is lost —
    giving up the main data for a nice-to-have field is the worst trade. Not
    found falls back to how it was: nothing marked, as before the change.
    """

    def test_it_still_imports_without_the_column(self):
        h = FakeHistory(with_originator=False)
        self.addCleanup(h.close)
        h.visit("老库里的记录")
        rows = h.rows()
        self.assertIn("老库里的记录", rows)
        self.assertNotIn("synced", rows["老库里的记录"])

    def test_visit_source_still_works_without_the_column(self):
        h = FakeHistory(with_originator=False)
        self.addCleanup(h.close)
        h.visit("同步来的", source=0)
        self.assertTrue(h.rows()["同步来的"].get("synced"))


class TabLifetimeIsNotAttentionTest(unittest.TestCase):
    """visit_duration is **how long the tab lived**, not how long you looked.

    chrome.py's module docstring said "never read it as focus time" from day one,
    and it still went into dur_ms — the warning written, not enforced.

    It blew up on 2026-08-16: once REPLACE was added to --full for 1:1 sources,
    stored durations were refreshed to their **accumulated-until-now** values, and
    a tab left open overnight became

        22:40 → 14:17   936 minutes   one tab

    blocks.py lays slots by dur_ms, so this one record covered the night and
    pushed WHOOP's sleep out of 99 slots. 1,665 chrome records in the database
    had dur_ms over an hour.

    segment.py's TRUSTED_DURATION_SOURCES had long excluded chrome, and viz.py
    followed it — only blocks.py used it unconditionally. Rather than have every
    consumer remember to avoid it, the field should never hold the wrong thing:
    dur_ms means "how long this lasted", and how long a tab lived is not that.

    The lifetime itself is kept, in the payload — occasionally useful (it shows a
    tab stayed open) — just not passing for the event's duration.
    """

    def setUp(self):
        self.h = FakeHistory()
        self.addCleanup(self.h.close)

    def _row(self, seconds):
        i = self.h.visit("Butler")
        self.h.con.execute("UPDATE visits SET visit_duration=? WHERE id=?",
                           (seconds * 1_000_000, i))
        self.h.con.commit()
        with mock.patch.object(chrome, "CHROME_DIR", self.h.dir.name):
            return chrome.collect()[0]

    def test_a_tab_open_for_fifteen_hours_does_not_take_fifteen_hours(self):
        r = self._row(936 * 60)
        self.assertEqual(r[2], 0, f"tab lifetime passing for event duration again: {r[2]}ms")

    def test_the_lifetime_is_not_lost(self):
        self.assertEqual(self._row(936 * 60)[5].get("tab_s"), 936 * 60)

    def test_short_ones_are_handled_the_same(self):
        """Don't make it "dropped only above N minutes" — no basis for any N, and
        a tab open for 3 minutes does not mean 3 minutes of looking either."""
        self.assertEqual(self._row(180)[2], 0)


if __name__ == "__main__":
    unittest.main()
