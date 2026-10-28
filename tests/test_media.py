"""The now-playing (Media.NowPlaying) importer.

The only **content-level** phone source: App.InFocus says "you were in the
Books app"; this says "you were listening to chapter IV of an audiobook".

The raw data is sentence-by-sentence playback progress — one listening session
produces thousands of records and must be folded into intervals, or the events
table swells and the lane smears into one block.
"""

import os
import unittest
from unittest import mock

from umbra import segb
from umbra.importers import media

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "segb_media_sample.bin")


# A real Apple absolute-seconds base (2026-07-30). A small number like 1000.0
# would be judged parsing noise by in_apple_range — the check that exists to
# stop CRCs being misread.
BASE = 807152095.0


def rec(apple_s, bundle="com.tencent.weread", title="书", track="第一章",
        device="Speaker", state=1, text="正在读的句子"):
    return {2: [apple_s], 3: [state], 4: [title.encode()], 5: [track.encode()],
            8: [text.encode()], 14: [device.encode()], 15: [bundle.encode()]}


class CollapseTest(unittest.TestCase):
    def test_consecutive_records_become_one_interval(self):
        """A listening session of thousands of sentence records lands as one row."""
        rows = media.to_rows([rec(BASE + 1000.0 + i * 10) for i in range(60)])
        self.assertEqual(len(rows), 1)
        uid, ts, dur, src, kind, payload = rows[0]
        self.assertEqual(src, "media")
        self.assertEqual(ts, segb.apple_ms(BASE + 1000.0))
        self.assertEqual(dur, 590_000)

    def test_a_long_gap_starts_a_new_session(self):
        rows = media.to_rows([rec(BASE + 1000.0), rec(BASE + 1010.0),
                              rec(BASE + 50_000.0), rec(BASE + 50_010.0)])
        self.assertEqual(len(rows), 2)

    def test_changing_track_starts_a_new_session(self):
        """A new chapter is a new session, even when continuous in time."""
        rows = media.to_rows([rec(BASE + 1000.0, track="第一章"),
                              rec(BASE + 1010.0, track="第一章"),
                              rec(BASE + 1020.0, track="第二章"),
                              rec(BASE + 1030.0, track="第二章")])
        self.assertEqual([r[5]["track"] for r in rows], ["第一章", "第二章"])

    def test_changing_app_starts_a_new_session(self):
        rows = media.to_rows([rec(BASE + 1000.0, bundle="com.a"),
                              rec(BASE + 1010.0, bundle="com.a"),
                              rec(BASE + 1020.0, bundle="com.b"),
                              rec(BASE + 1030.0, bundle="com.b")])
        self.assertEqual(len(rows), 2)

    def test_uid_is_stable(self):
        evs = [rec(BASE + 1000.0), rec(BASE + 1010.0)]
        self.assertEqual(media.to_rows(evs)[0][0], media.to_rows(evs)[0][0])


class InterleavedTrackTest(unittest.TestCase):
    """On a track change this stream writes **the old track and the new one**
    with near-identical timestamps. Sorted by time, the two records of one song
    have another song between them.

    Real data (2026-08-02):

        09:12:37   Track A                <- starts
        09:15:43   Track B                <- cuts in
        09:15:43   Track A                <- ends, 3 min 06 s later = the song

    The segmentation compared keys only with **the session just before**, so one
    interruption broke the chain and those 3 minutes 06 became two zero-length
    sessions. Over two-thirds of stored sessions had zero duration, most of
    them tracks split this way. Audiobooks were fine (one chapter
    throughout, nothing cutting in), which is why the bug stayed hidden.
    """

    def test_a_track_split_by_another_still_forms_one_session(self):
        rows = media.to_rows([
            rec(BASE + 1000.0, track="Los Fantasmas"),
            rec(BASE + 1186.0, track="Eydie Gorme"),
            rec(BASE + 1186.0, track="Los Fantasmas"),
        ])
        lf = [r for r in rows if r[5]["track"] == "Los Fantasmas"]
        self.assertEqual(len(lf), 1, "one song was split into several sessions")
        self.assertEqual(lf[0][2], 186_000)

    def test_the_interleaved_track_is_kept_too(self):
        rows = media.to_rows([
            rec(BASE + 1000.0, track="A"),
            rec(BASE + 1186.0, track="B"),
            rec(BASE + 1186.0, track="A"),
        ])
        self.assertEqual(sorted(r[5]["track"] for r in rows), ["A", "B"])

    def test_rows_come_back_in_time_order(self):
        """After grouping by track, it must be sorted by time again.

        The construction matters: with A first and B after, group order happens
        to be time order and a missing sort shows nothing (a mutation test caught
        it). It takes **A in two parts with B between** — A's group yields its
        first and last part together, and B between them comes out of order.
        """
        rows = media.to_rows([
            rec(BASE + 1000.0, track="A"), rec(BASE + 1010.0, track="A"),
            rec(BASE + 2000.0, track="B"), rec(BASE + 2010.0, track="B"),
            rec(BASE + 90_000.0, track="A"), rec(BASE + 90_010.0, track="A"),
        ])
        self.assertEqual([r[5]["track"] for r in rows], ["A", "B", "A"])
        self.assertEqual([r[1] for r in rows], sorted(r[1] for r in rows))

    def test_the_same_track_played_again_much_later_stays_separate(self):
        """The same track again hours later is two plays, not one session."""
        rows = media.to_rows([
            rec(BASE + 1000.0, track="A"), rec(BASE + 1010.0, track="A"),
            rec(BASE + 90_000.0, track="A"), rec(BASE + 90_010.0, track="A"),
        ])
        self.assertEqual(len(rows), 2)


class PrivacyTest(unittest.TestCase):
    """f8 is **the book's text** being read aloud.

    Deliberately not stored: title and chapter already say what you were
    doing, the text adds little, and it would turn the local page into an
    excerpt of every book you have read.
    """

    def test_the_spoken_text_is_never_stored(self):
        rows = media.to_rows([rec(BASE + 1000.0, text="这句话不该被存下来"),
                              rec(BASE + 1010.0, text="这句也是")])
        blob = repr(rows)
        self.assertNotIn("不该被存", blob)
        self.assertNotIn("这句也是", blob)

    def test_field_8_is_not_in_the_kept_fields(self):
        self.assertNotIn(8, media.KEEP_FIELDS)

    def test_title_and_track_are_kept(self):
        rows = media.to_rows([rec(BASE + 1000.0, title="书名", track="章节"),
                              rec(BASE + 1010.0)])
        self.assertEqual(rows[0][5]["title"], "书名")
        self.assertEqual(rows[0][5]["track"], "章节")


class MetadataTest(unittest.TestCase):
    def test_output_device_is_kept(self):
        """AirPods connected = really listening, not music playing to an empty room."""
        rows = media.to_rows([rec(BASE + 1000.0, device="AA:BB:CC:DD:EE:FF-tacl"),
                              rec(BASE + 1010.0, device="AA:BB:CC:DD:EE:FF-tacl")])
        self.assertEqual(rows[0][5]["device"], "AA:BB:CC:DD:EE:FF-tacl")

    def test_app_is_kept(self):
        rows = media.to_rows([rec(BASE + 1000.0), rec(BASE + 1010.0)])
        self.assertEqual(rows[0][5]["bundle"], "com.tencent.weread")

    def test_records_without_a_title_are_dropped(self):
        """A record with no track or title says nothing and only pollutes the
        timeline."""
        r = rec(BASE + 1000.0)
        del r[4]
        del r[5]
        self.assertEqual(media.to_rows([r]), [])


class SuspectTest(unittest.TestCase):
    """As with iPhone foreground: an absurdly long session is not to be trusted.

    Music left playing while you walk away reads as "four hours of listening".
    The same test, for the same reason.
    """

    def test_long_sessions_are_flagged(self):
        # It must be a **continuous** stream of records: gaps over GAP_MS split it,
        # and two records five hours apart never make one five-hour session
        rows = media.to_rows([rec(BASE + i * 60.0) for i in range(300)])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][5]["suspect"])

    def test_normal_sessions_are_not_flagged(self):
        rows = media.to_rows([rec(BASE + i * 60.0) for i in range(30)])
        self.assertFalse(rows[0][5]["suspect"])


class SinceTest(unittest.TestCase):
    def test_filters_old_sessions(self):
        rows = media.to_rows([rec(BASE + 1000.0), rec(BASE + 1010.0),
                              rec(BASE + 90_000.0), rec(BASE + 90_010.0)],
                             since_ms=segb.apple_ms(BASE + 50_000.0))
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0][1], segb.apple_ms(BASE + 50_000.0))


class CollectTest(unittest.TestCase):
    def test_reads_the_real_fixture(self):
        with mock.patch.object(media, "device_dirs",
                               return_value=[os.path.dirname(FIX)]):
            rows = media.collect()
        self.assertTrue(rows, "the real sample produced nothing")
        for uid, ts, dur, src, kind, payload in rows:
            self.assertEqual(src, "media")
            self.assertGreater(ts, 1_600_000_000_000)
            self.assertIn("title", payload)
            self.assertNotIn("text", payload)

    def test_real_fixture_collapses_a_lot(self):
        """44 sentence records should fold into a handful of sessions."""
        with mock.patch.object(media, "device_dirs",
                               return_value=[os.path.dirname(FIX)]):
            rows = media.collect()
        self.assertLess(len(rows), 10)

    def test_missing_dir_is_not_an_error(self):
        with mock.patch.object(media, "device_dirs", return_value=[]):
            self.assertEqual(media.collect(), [])

    def test_registered(self):
        from umbra.importers import ALL, SOURCE_OF
        self.assertIn("media", ALL)
        self.assertEqual(SOURCE_OF["media"], "media")

    def test_collect_takes_since_ms_first(self):
        import inspect
        self.assertEqual(
            list(inspect.signature(media.collect).parameters)[0], "since_ms")


if __name__ == "__main__":
    unittest.main()
