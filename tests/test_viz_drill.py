"""Each lane says where it comes from, and clicking a moment shows the raw
records of those minutes.

Two things asked for on 2026-08-16:

  1. where "location" comes from, where "sleep" comes from — every lane names
     its source
  2. click a moment, and every source's **raw records** around it are listed,
     readable in full, so you can judge for yourself

The second puts raw events in the HTML. A whole day is 4,767 records, a 728 KB
payload; gzipped about 120 KB a day, one block per day and decoded on click,
so opening time does not grow with the number of days.
"""

import base64
import gzip
import json
import unittest

from umbra import viz


class OriginTest(unittest.TestCase):
    def test_every_lane_says_where_it_comes_from(self):
        for key, _, _ in viz.LANES:
            self.assertIn(key, viz.ORIGIN, f"{key} has no origin")

    def test_the_origin_is_two_parts(self):
        """The short line goes under the lane name; the long one is for the
        drill-down panel."""
        for key, pair in viz.ORIGIN.items():
            self.assertEqual(len(pair), 2, key)
            who, how = pair
            self.assertTrue(who and how, key)

    def test_the_short_form_is_not_the_lane_name_again(self):
        """"Claude Code ← Claude Code" or "Terminal ← Terminal" says nothing.
        The note must say **where it came from**, not repeat the name."""
        for key, (who, _) in viz.ORIGIN.items():
            self.assertNotEqual(who, viz.LABEL.get(key), f"{key}'s origin restates its name")

    def test_the_short_form_stays_short(self):
        """It sits under the lane name; too long, it breaks the left column."""
        for key, (who, _) in viz.ORIGIN.items():
            self.assertLessEqual(len(who), 18, f"{key}'s origin is too long: {who}")


class WereadLaneTest(unittest.TestCase):
    """Reading sessions bounded by highlights were never drawn — LANES lacked them."""

    def test_reading_has_a_lane(self):
        self.assertIn(("reading", "weread", "reading"), viz.LANES)

    def test_the_daily_total_has_no_lane(self):
        """A day total has no position in time. A lane would draw it as a block,
        claiming one sitting from Beijing midnight — the reason analyze.py
        excludes it."""
        self.assertNotIn("daily", [k for k, _, _ in viz.LANES])


class RawEventsTest(unittest.TestCase):
    """The drill-down wants raw records, not summaries."""

    EVENTS = [
        {"ts": 1000, "dur_ms": 0, "source": "claude", "kind": "prompt",
         "text": "很长很长的一段话" * 40, "project": "Umbra"},
        {"ts": 2000, "dur_ms": 0, "source": "zsh", "kind": "command",
         "cmd": "git push origin main"},
    ]

    def test_it_round_trips(self):
        blob = viz.pack(self.EVENTS, lo_ms=0)
        got = json.loads(gzip.decompress(base64.b64decode(blob)))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[1]["cmd"], "git push origin main")

    def test_the_text_is_not_truncated(self):
        """Reading the full text is the whole point. Truncated, it is pointless."""
        blob = viz.pack(self.EVENTS, lo_ms=0)
        got = json.loads(gzip.decompress(base64.b64decode(blob)))
        self.assertEqual(got[0]["text"], self.EVENTS[0]["text"])

    def test_timestamps_become_seconds_from_midnight(self):
        """Absolute milliseconds waste size — ten more digits per record, thousands
        a day."""
        blob = viz.pack(self.EVENTS, lo_ms=1000)
        got = json.loads(gzip.decompress(base64.b64decode(blob)))
        self.assertEqual(got[0]["ts"], 0)
        self.assertEqual(got[1]["ts"], 1)

    def test_it_actually_compresses(self):
        big = [dict(self.EVENTS[0], ts=i * 1000) for i in range(200)]
        blob = viz.pack(big, lo_ms=0)
        raw = len(json.dumps(big, ensure_ascii=False).encode())
        self.assertLess(len(base64.b64decode(blob)) * 3, raw, "compression did nothing")

    def test_an_empty_day_is_not_a_crash(self):
        self.assertEqual(json.loads(gzip.decompress(
            base64.b64decode(viz.pack([], lo_ms=0)))), [])


class PayloadTest(unittest.TestCase):
    def test_each_day_carries_its_own_blob(self):
        """One block per day is what lets a click decode only that day. One big
        block would decode 48 days on opening."""
        import inspect
        # Per-day building moved into _day_payload on 2026-08-19 (for per-day caching)
        src = inspect.getsource(viz._day_payload)
        self.assertIn("pack(", src)


if __name__ == "__main__":
    unittest.main()
