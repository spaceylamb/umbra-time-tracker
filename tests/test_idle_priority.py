""""No input on the Mac" must not override what other sources already explain.

idle never meant "nobody there"; it meant **"we know nothing about this
stretch"**. When it was
created, no input on the Mac was close to knowing nothing — no other source
could speak then. So removing those minutes from the denominator made sense.

Not any more. On the night of 2026-08-16 three sources had something to say:

    22:54 → 06:55   idle       no input on the Mac
    23:12 → 06:29   whoop      asleep
    18:37 → 07:20   location   at home

And idle, ranked highest, overrode the other two: 886 minutes removed from the
denominator. **The source with the least information made the final call.**

analyze.py's comment said "priority: screen evidence > idle > known
off-screen", but its reasoning only argued "screen evidence > sleep" (WHOOP's
sleep boundaries are inferred and include "lying down, still on the phone").
"idle > sleep" came along by accident and was never argued — and while there
was only Mac data it did no harm.

Now: **idle applies only to minutes no other source speaks for.**
"""

import sqlite3
import unittest

from umbra import analyze, db

DAY = "2026-08-16"
LO, _ = analyze.day_bounds(DAY)
MIN = 60_000


class IdleYieldsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def add(self, uid, start_min, dur_min, src, kind, **payload):
        db.insert(self.con, [(uid, LO + start_min * MIN, dur_min * MIN,
                              src, kind, payload)])

    def idle(self, start_min, dur_min):
        self.add(f"idle:{start_min}", start_min, dur_min, "idle", "away")

    def test_idle_yields_to_a_known_location(self):
        """Location knows you were at home. Those 60 minutes are "known where",
        not "unknowable"."""
        self.idle(0, 60)
        self.add("stay", 0, 60, "location", "stay", place="家", named=True)
        got = analyze.coverage(DAY, self.con)
        self.assertEqual(got["B"], 60)
        self.assertEqual(got["away"], 0)

    def test_idle_yields_to_sleep(self):
        """WHOOP says asleep — "known off-screen", which says more than "no keys
        pressed"."""
        self.idle(0, 60)
        self.add("sleep", 0, 60, "whoop", "sleep")
        got = analyze.coverage(DAY, self.con)
        self.assertEqual(got["off"], 60)
        self.assertEqual(got["away"], 0)

    def test_idle_still_counts_where_nothing_else_knows(self):
        """With no other source speaking, idle still applies — its original use
        is unchanged."""
        self.idle(0, 60)
        got = analyze.coverage(DAY, self.con)
        self.assertEqual(got["away"], 60)
        self.assertEqual(got["B"], 0)

    def test_only_the_overlapping_part_yields(self):
        """Half with location, half without — only the located half is given up."""
        self.idle(0, 60)
        self.add("stay", 0, 30, "location", "stay", place="家", named=True)
        got = analyze.coverage(DAY, self.con)
        self.assertEqual(got["B"], 30)
        self.assertEqual(got["away"], 30)

    def test_screen_evidence_still_beats_sleep(self):
        """This priority is argued; don't change it: WHOOP's sleep boundaries are
        inferred and include "lying down but still scrolling". A minute with real
        activity must not be covered over."""
        self.add("sleep", 0, 60, "whoop", "sleep")
        self.add("cmd", 10, 0, "zsh", "command")
        got = analyze.coverage(DAY, self.con)
        self.assertEqual(got["A"], 1)
        self.assertEqual(got["off"], 59)


class DayStillAddsUpTest(unittest.TestCase):
    """The four tiers must add up to exactly one day. Double-counting overlaps
    goes over; over-subtracting makes the unknown negative."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def test_with_everything_overlapping(self):
        db.insert(self.con, [
            ("idle", LO - 66 * MIN, 481 * MIN, "idle", "away", {}),
            ("sleep", LO - 48 * MIN, 437 * MIN, "whoop", "sleep", {}),
            ("stay", LO - 323 * MIN, 762 * MIN, "location", "stay",
             {"place": "家", "named": True}),
            ("cmd", LO + 600 * MIN, 0, "zsh", "command", {}),
        ])
        g = analyze.coverage(DAY, self.con)
        self.assertEqual(g["A"] + g["B"] + g["off"] + g["away"] + g["C"], 1440)
        self.assertGreaterEqual(g["C"], 0)

    def test_the_morning_at_home_is_no_longer_thrown_away(self):
        """A real case: that night idle swallowed the 440 minutes at home."""
        db.insert(self.con, [
            ("idle", LO - 66 * MIN, 481 * MIN, "idle", "away", {}),
            ("stay", LO - 323 * MIN, 762 * MIN, "location", "stay",
             {"place": "家", "named": True}),
        ])
        self.assertGreaterEqual(analyze.coverage(DAY, self.con)["B"], 435)


if __name__ == "__main__":
    unittest.main()
