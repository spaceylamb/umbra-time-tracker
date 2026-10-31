"""The page has to show at a glance "is this new" and "where is today".

A real incident on 2026-08-21: the scheduled job fine, the HTML rebuilt at 10:04,
today's 8 events really in the file — and the user certain "it hasn't updated".
Two things stacked up:

  1. the page opened on **the day with the most events** (8/20), not the latest
  2. the calendar was 52 identical 12px squares, no date labels, no "today"
     marker — finding 8/21 meant hovering one by one for tooltips

The data was fine and the pipeline was fine; there was simply **no way in**.
Chasing it took far longer than adding a "built at X" line to the page would.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import db, viz


class BuildTimeInPayloadTest(unittest.TestCase):
    def _payload(self):
        import time
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        con = db.connect(path)
        db.insert(con, [("e:1", int(time.time() * 1000), None, "zsh",
                         "command", {"cmd": "ls"})])
        con.commit()
        con.close()
        with mock.patch.object(viz.db, "connect",
                               side_effect=lambda *a, **k: sqlite3.connect(path)):
            return viz.build()

    def test_the_payload_carries_when_it_was_built(self):
        """Without it, "the page is old" and "no data today" look the same."""
        self.assertIn("generated", self._payload())

    def test_the_build_time_is_readable_local_time(self):
        g = self._payload()["generated"]
        self.assertRegex(g, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_the_payload_carries_what_day_today_is(self):
        """"Today" is computed at build time — left to the browser, a time-zone
        difference marks the wrong cell, and a wrong mark looks like no mark."""
        import datetime as dt
        self.assertEqual(self._payload()["today"],
                         dt.date.today().isoformat())




if __name__ == "__main__":
    unittest.main()
