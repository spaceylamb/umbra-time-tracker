"""The iPhone foreground-use importer.

The data is App.InFocus **enter/leave** pairs (field 3: 1 = enter, 0 = leave),
meeting end to end when switching apps. So durations are computed exactly, not
estimated — which is what it has over knowledgeC (app level only, no content).
"""

import os
import unittest
from unittest import mock

from umbra import segb
from umbra.importers import iphone

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "segb_iphone_sample.bin")
MIN = 60_000


def ev(bundle, apple_s, enter, version="1.0"):
    """Build one parsed protobuf record."""
    return {3: [1 if enter else 0], 4: [apple_s], 6: [bundle.encode()],
            9: [version.encode()]}


class PairingTest(unittest.TestCase):
    def test_enter_then_exit_becomes_one_interval(self):
        rows = iphone.to_rows([ev("com.x", 1000.0, True),
                               ev("com.x", 1060.0, False)])
        self.assertEqual(len(rows), 1)
        uid, ts, dur, src, kind, payload = rows[0]
        self.assertEqual(src, "iphone")
        self.assertEqual(kind, "focus")
        self.assertEqual(ts, segb.apple_ms(1000.0))
        self.assertEqual(dur, MIN)
        self.assertEqual(payload["bundle"], "com.x")

    def test_uid_is_stable_across_reruns(self):
        """Importing twice must be idempotent — db.insert dedupes on uid."""
        evs = [ev("com.x", 1000.0, True), ev("com.x", 1060.0, False)]
        self.assertEqual(iphone.to_rows(evs)[0][0], iphone.to_rows(evs)[0][0])

    def test_different_sessions_get_different_uids(self):
        rows = iphone.to_rows([ev("com.x", 1000.0, True),
                               ev("com.x", 1060.0, False),
                               ev("com.x", 2000.0, True),
                               ev("com.x", 2060.0, False)])
        self.assertEqual(len({r[0] for r in rows}), 2)

    def test_app_switch_is_two_intervals_not_one(self):
        """On a switch, leave and enter share a second; they must not merge."""
        rows = iphone.to_rows([ev("com.a", 100.0, True),
                               ev("com.a", 200.0, False),
                               ev("com.b", 200.0, True),
                               ev("com.b", 300.0, False)])
        self.assertEqual(sorted(r[5]["bundle"] for r in rows),
                         ["com.a", "com.b"])

    def test_exit_without_enter_is_dropped(self):
        """A file starting mid-session often begins with an orphan "leave"."""
        self.assertEqual(iphone.to_rows([ev("com.x", 1000.0, False)]), [])

    def test_enter_without_exit_is_dropped(self):
        """The app still open has no end; none is to be invented."""
        self.assertEqual(iphone.to_rows([ev("com.x", 1000.0, True)]), [])

    def test_interleaved_apps_pair_correctly(self):
        """iOS interleaves system apps mid-switch; pairing must be per bundle."""
        rows = iphone.to_rows([ev("com.a", 100.0, True),
                               ev("com.sys", 150.0, True),
                               ev("com.sys", 160.0, False),
                               ev("com.a", 300.0, False)])
        by = {r[5]["bundle"]: r for r in rows}
        self.assertEqual(by["com.a"][2], 200_000)
        self.assertEqual(by["com.sys"][2], 10_000)

    def test_duplicate_enter_keeps_the_earlier_one(self):
        """Two "enter"s with no "leave" — take the earlier, so the stretch is not
        cut short."""
        rows = iphone.to_rows([ev("com.x", 100.0, True),
                               ev("com.x", 150.0, True),
                               ev("com.x", 200.0, False)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 100_000)

    def test_zero_length_sessions_are_dropped(self):
        """Enter and leave at the same moment — not use, a stray touch."""
        self.assertEqual(iphone.to_rows([ev("com.x", 100.0, True),
                                         ev("com.x", 100.0, False)]), [])

    def test_absurdly_long_sessions_are_dropped(self):
        """One missing "leave" merges everything between two stretches into tens
        of hours.

        When a duration cannot be trusted, better not to keep it — one 40-hour
        "using WeChat" poisons segmentation for the whole timeline.
        """
        rows = iphone.to_rows([ev("com.x", 0.0, True),
                               ev("com.x", 40 * 3600.0, False)])
        self.assertEqual(rows, [])

    def test_version_is_kept(self):
        rows = iphone.to_rows([ev("com.x", 1.0, True, "9.9"),
                               ev("com.x", 61.0, False, "9.9")])
        self.assertEqual(rows[0][5]["version"], "9.9")


class SinceTest(unittest.TestCase):
    def test_since_filters_old_sessions(self):
        cutoff = segb.apple_ms(5000.0)
        rows = iphone.to_rows([ev("com.old", 1000.0, True),
                               ev("com.old", 1060.0, False),
                               ev("com.new", 9000.0, True),
                               ev("com.new", 9060.0, False)], since_ms=cutoff)
        self.assertEqual([r[5]["bundle"] for r in rows], ["com.new"])


class CollectTest(unittest.TestCase):
    def test_reads_the_real_fixture(self):
        with mock.patch.object(iphone, "device_dirs", return_value=[
                os.path.dirname(FIX)]):
            rows = iphone.collect()
        self.assertTrue(rows, "the real sample produced nothing")
        for uid, ts, dur, src, kind, payload in rows:
            self.assertEqual(src, "iphone")
            self.assertGreater(ts, 1_600_000_000_000)
            self.assertIn("bundle", payload)

    def test_missing_biome_dir_is_not_an_error(self):
        """No iPhone, or Share Across Devices off — an empty result, not a fault."""
        with mock.patch.object(iphone, "device_dirs", return_value=[]):
            self.assertEqual(iphone.collect(), [])

    def test_source_name(self):
        self.assertEqual(iphone.SOURCE, "iphone")

    def test_registered(self):
        from umbra.importers import ALL, SOURCE_OF
        self.assertIn("iphone", ALL)
        self.assertEqual(SOURCE_OF["iphone"], "iphone")

    def test_collect_takes_since_ms_first(self):
        import inspect
        params = list(inspect.signature(iphone.collect).parameters)
        self.assertEqual(params[0], "since_ms")


class DeviceDirsTest(unittest.TestCase):
    """The real path construction. The other tests mock device_dirs; this one
    has to check the real thing."""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp()
        for side, dev in (("remote", "PHONE-1"), ("remote", "PHONE-2"),
                          ("local", "THIS-MAC")):
            os.makedirs(os.path.join(self.root, iphone.STREAM, side, dev))
        p = mock.patch.object(iphone, "BIOME", self.root)
        p.start()
        self.addCleanup(p.stop)

    def test_reads_remote_not_local(self):
        """local/ is this Mac, already knowledgec's — reading it imports twice."""
        got = [os.path.basename(d) for d in iphone.device_dirs()]
        self.assertEqual(sorted(got), ["PHONE-1", "PHONE-2"])
        self.assertNotIn("THIS-MAC", got)

    def test_uses_the_app_infocus_stream(self):
        self.assertTrue(all(iphone.STREAM in d for d in iphone.device_dirs()))

    def test_missing_tree_returns_empty(self):
        with mock.patch.object(iphone, "BIOME", "/nope/nothing/here"):
            self.assertEqual(iphone.device_dirs(), [])

    def test_permission_error_is_raised_not_swallowed(self):
        """This used to assert the opposite, and that judgement was wrong.

        It said: "without Full Disk Access it is 'no data', not 'a fault'."
        That sounds forgiving but merges two things that must stay apart —
        "TCC won't let me read" and "this Mac never synced an iPhone" both
        returned [].

        It happened on 2026-08-16: in one import iphone, media and intent all
        reported "✓ 0 collected", and the real cause was a denied permission. The
        database already held 5,266 rows and the freshness report still showed
        coverage up to that day — perfectly healthy-looking. With permission
        really revoked it would show weeks of "✓ 0 new" while the iPhone half of
        the timeline quietly stopped.

        knowledgec raised from the start and showed a glaring ✗; the three Biome
        sources now match. Absence is still tolerated (see the previous test) —
        that is the real "normal state".
        """
        with mock.patch("os.listdir", side_effect=PermissionError(1, "nope")):
            with self.assertRaises(PermissionError):
                iphone.device_dirs()




class ScreenLockBlindnessTest(unittest.TestCase):
    """App.InFocus does not know whether the screen was locked.

    An app left in front with the phone in a pocket for three hours reads as
    "three hours of Gmail". A handful of stretches like that can make up a large
    share of the total — hours a day of a phone in a pocket.

    By Umbra's rule: **store the event, mark how far to trust it**, and let the
    analysis decide. Deleting loses information; taking it all treats a
    hallucination as fact.
    """

    def test_long_sessions_are_flagged(self):
        rows = iphone.to_rows([ev("com.x", 0.0, True),
                               ev("com.x", 3 * 3600.0, False)])
        self.assertTrue(rows[0][5]["suspect"])

    def test_short_sessions_are_not_flagged(self):
        rows = iphone.to_rows([ev("com.x", 0.0, True),
                               ev("com.x", 300.0, False)])
        self.assertFalse(rows[0][5]["suspect"])

    def test_flagged_sessions_are_still_stored(self):
        """Suspect is not deleted — raw events are append-only facts, and the
        criterion may change (with a screen-state stream, say)."""
        rows = iphone.to_rows([ev("com.x", 0.0, True),
                               ev("com.x", 3 * 3600.0, False)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 3 * 3600 * 1000)

    def test_threshold_is_documented(self):
        self.assertTrue(0 < iphone.SUSPECT_AFTER_MS <= 60 * 60_000)

if __name__ == "__main__":
    unittest.main()
