"""All sources in one time window compressed into one text — what the model is
fed.

This layer used to live in lab/digest.py, run and left in scratch, and it was
cleaned out on 2026-08-18. Moved into the repository, pinned together with the
traps it fell into.

The three most expensive lessons, all of the "silent fabrication" kind — no
error, nothing missing, just **a record that does not exist**, which the model
believes:

    WHOOP recovery  read as a workout  → a "workout" appeared from nowhere,
                                         gpt-5.6-sol believed it, and only
                                         claude-opus-5 saying "nothing else
                                         corroborates this" exposed it
    weread daily    read as a moment   → "reading" appeared at 09:00, when it
                                         was the whole day's reading total
    location gap    read as a place    → "at ?", when a gap means exactly
                                         "not known"

What they share: the source has a kind field that separates "this happened at
a moment" from "this is a day's total", and the code handled four kinds with a
two-way else. **A record without a position should not appear** — never give
it an invented one.

The fourth is window filtering: filtering by **start time** puts WHOOP's
seven-hour sleep only in the window it began in, invisible to the next 13 —
measured, sleep fell from 390 minutes to 90. Filter by **interval overlap**.
The same bug was once made in db.load_range.
"""

import unittest

from umbra import digest


def ev(ts_min, source, kind="", **kw):
    """One event in the database. ts is written as "minute of the day" to skip
    the arithmetic."""
    return {"ts": ts_min * 60000, "dur_ms": kw.pop("dur", 0),
            "source": source, "kind": kind, **kw}


def lines(events, a=540, b=570, ctx=60):
    return digest.render(events, a, b, ctx=ctx).splitlines()


class WindowOverlapTest(unittest.TestCase):
    """Filtered by interval overlap, not by start time."""

    SLEEP = ev(16, "whoop", "sleep", dur=455 * 60000)

    def test_a_long_record_reaching_into_the_window_appears(self):
        """A 7-hour sleep starting 00:16 means the person **is still asleep**
        during the 06:00-06:30 window. Filtered by start time there is nothing
        here, and the model can only answer "unsure"."""
        self.assertTrue(any("asleep" in x for x in lines([self.SLEEP], 360, 390)))

    def test_one_that_has_already_ended_does_not_appear(self):
        self.assertFalse(any("asleep" in x for x in lines([self.SLEEP], 600, 630, ctx=0)))

    def test_not_yet_started_does_not_appear(self):
        self.assertFalse(any("a later page" in x for x in lines([ev(600, "chrome", "visit",
                                                                   title="a later page")], 0, 30, ctx=0)))

    def test_the_context_window_counts_too(self):
        """The target is 09:00–09:30, but the model sees an hour either side. A
        record at 08:10 must be there."""
        got = lines([ev(490, "chrome", "visit", title="早一点的页面")])
        self.assertTrue(any("早一点的页面" in x for x in got))

    def test_beyond_the_context_does_not_count(self):
        self.assertEqual(lines([ev(400, "chrome", "visit", title="太早了")]), [])


class DayLevelRecordsTest(unittest.TestCase):
    """A day's total has no position in time. Giving it one invents a record."""

    def test_whoop_sleep_has_a_position(self):
        self.assertTrue(lines([ev(540, "whoop", "sleep", dur=60000)]))

    def test_whoop_workout_has_a_position(self):
        got = lines([ev(540, "whoop", "workout", dur=60000, sport="walking")])
        self.assertTrue(any("walking" in x for x in got))

    def test_whoop_recovery_has_no_position(self):
        """Recovery is **the day's**, not this moment's at 09:00.
        A two-way else renders it as a workout — and a workout appears from
        nowhere."""
        self.assertEqual(lines([ev(540, "whoop", "recovery", score=34)]), [])

    def test_whoop_cycle_has_no_position(self):
        self.assertEqual(lines([ev(540, "whoop", "cycle", strain=8)]), [])

    def test_weread_a_reading_session_has_a_position(self):
        got = lines([ev(540, "weread", "reading", book="某本书", marks=2)])
        self.assertTrue(any("某本书" in x for x in got))

    def test_weread_the_daily_total_has_no_position(self):
        """read_min=85 is the whole day, not 85 minutes read at 09:00."""
        self.assertEqual(lines([ev(540, "weread", "daily", read_min=85)]), [])

    def test_location_transit_has_a_position(self):
        got = lines([ev(540, "location", "transit", **{"from": "家"},
                        to="p1", mode="walking")])
        self.assertTrue(any("家" in x for x in got))

    def test_location_stay_has_a_position(self):
        self.assertTrue(lines([ev(540, "location", "stay", place="家")]))

    def test_location_gap_invents_no_place(self):
        """A gap means "where was unknown". Rendering it as "at ?" writes "no
        data" as a piece of data."""
        self.assertEqual(lines([ev(540, "location", "gap", reason="no points")]), [])


class SpeakerTest(unittest.TestCase):
    """Speaker numbers are kept as they are, **never hard-mapped to "me"**.

    The previous digest had a hard rule writing Speaker 2 as "me", 52 times in
    two days without exception. But segment.py says it plainly: "Speaker 1/2 are
    numbers **within each recording**" — not shared across files. Three files
    happening to fit says nothing about the next recording.

    A mismatch lands in the `with` column (alone / colleagues / family) — the
    very field that tells "a work Zoom" from "chatting with family". Better the
    model judge from content.
    """

    def test_speaker_numbers_are_kept_as_they_are(self):
        got = lines([ev(540, "plaud", "speech", speaker="Speaker 2",
                        text="我这个会啊。")])
        self.assertTrue(any("Speaker 2" in x for x in got), got)
        self.assertFalse(any("🗣 我" in x for x in got), got)

    def test_speech_is_never_truncated(self):
        """The transcript is the most valuable thing in this layer — truncating it
        cuts away the evidence."""
        long = "啊" * 300
        self.assertTrue(any(long in x for x in
                            lines([ev(540, "plaud", "speech", text=long)])))


class AppNameTest(unittest.TestCase):
    """A bundle id has to be turned into a name, or the model sees "xin"."""

    def test_phone_foreground_is_named_not_bundle_sliced(self):
        """The model must see "WeChat", never "xin".

        Asserts the canonical name specifically: digest text is the inference
        cache key, so it stays English regardless of interface language.
        """
        got = lines([ev(540, "iphone", "focus", bundle="com.tencent.xin",
                        app="WeChat")])
        self.assertTrue(any("WeChat" in x for x in got), got)

    def test_unrecognised_ones_are_kept_as_they_are(self):
        got = lines([ev(540, "iphone", "focus", bundle="com.unknown.thing")])
        self.assertTrue(any("thing" in x or "com.unknown" in x for x in got), got)


class OrderAndShapeTest(unittest.TestCase):
    def test_sorted_by_time(self):
        got = lines([ev(545, "chrome", "visit", title="后"),
                     ev(541, "chrome", "visit", title="先")])
        self.assertLess(got.index([x for x in got if "先" in x][0]),
                        got.index([x for x in got if "后" in x][0]))

    def test_every_record_carries_a_time(self):
        """The marker lines do not count — they are structure, not records.
        Without a time the model cannot say "when did this happen"."""
        got = [x for x in lines([ev(541, "chrome", "visit", title="页面"),
                                 ev(490, "plaud", "speech", text="话")])
               if x != digest.TARGET_MARK]
        self.assertTrue(got)
        for x in got:
            self.assertRegex(x, r"^\d\d:\d\d ")

    def test_target_and_context_can_be_told_apart(self):
        """The model is asked to judge only the target half hour, so it must know
        what is context. Otherwise it counts context activity too."""
        txt = digest.render([ev(490, "chrome", "visit", title="上下文里的"),
                             ev(545, "chrome", "visit", title="目标里的")], 540, 570)
        self.assertIn(digest.TARGET_MARK, txt)
        self.assertLess(txt.index("上下文里的"), txt.index(digest.TARGET_MARK))
        self.assertLess(txt.index(digest.TARGET_MARK), txt.index("目标里的"))

    def test_an_empty_window_returns_an_empty_string(self):
        """Empty ≠ error. When nothing happened in this half hour, it should be
        empty."""
        self.assertEqual(digest.render([], 540, 570), "")


class CrossMidnightTest(unittest.TestCase):
    """A record coming in across midnight must not show as the same day's time.

    Measured: an idle record starting at 23:17 the day before ran into the
    morning, and landing in the 08:00–10:30 window is **right** (no key was
    touched all along). But its minute came out as -43, and -43 // 60 % 24 = 23,
    -43 % 60 = 17 — rendered "23:17", which the model reads as that evening,
    turning the whole judgement around.

    Out of range is not silently corrected; it is labelled. The same principle as
    inference.expand's handling of out-of-range spans.
    """

    def test_a_record_from_the_previous_day_is_labelled(self):
        """Minute -43 renders as "23:17" if the sign is dropped, and the model
        reads it as that same evening."""
        got = lines([ev(-43, "idle", "away", dur=600 * 60000)], 480, 510)
        self.assertTrue(got)
        self.assertFalse(got[0].startswith("23:17"),
                         "the previous day was shown as today")
        self.assertIn("prev day", got[0])

    def test_one_crossing_into_the_next_day_is_labelled_too(self):
        got = lines([ev(1450, "chrome", "visit", title="a page after midnight")],
                    1410, 1440)
        self.assertTrue(any("next day" in x for x in got), got)


class OngoingTest(unittest.TestCase):
    """A window entirely covered by one long record must not have an empty
    target section.

    Hit on 2026-08-19: 00:00–06:00 on 8-18 was judged "not sure what was going
    on", while the 5-minute layer plainly said "sleeping".

    WHOOP's sleep began 23:30 the day before and lasted 422 minutes, **fully
    covering** 02:00–02:30. But render placed it only at its **start**, before
    the target, so the whole record fell in the context and the target had no
    line at all — TARGET_HAS_CONTENT said False, no model was called, and those
    six hours produced not one span.

    The third form of the "WHOOP sleep 390 → 90" bug:
        db.load_range   filtered by start time         → filtered out
        digest.render   filtered by start time         → filtered out (fixed)
        digest.render   filtered right, placed wrong   → this one

    The shared shape: **a record with a duration cannot be represented by its
    start alone.**
    """

    SLEEP = ev(-30, "whoop", "sleep", dur=422 * 60000)

    def test_a_fully_covered_window_has_target_content(self):
        txt = digest.render([self.SLEEP],
                            120, 150)
        after = txt.split(digest.TARGET_MARK)[-1].strip()
        self.assertTrue(after, f"the target section is empty:\n{txt}")

    def test_it_says_the_record_began_earlier_and_continues(self):
        """It must not read as "went to sleep at 02:00" — that is invented. It
        has to say where the record came from."""
        txt = digest.render([self.SLEEP], 120, 150)
        after = txt.split(digest.TARGET_MARK)[-1]
        self.assertIn("still ongoing", after)
        self.assertIn("prev day 23:30", after)

    def test_the_runtime_check_follows(self):
        from umbra import infer
        self.assertTrue(infer.TARGET_HAS_CONTENT(
            digest.render([self.SLEEP], 120, 150)))

    def test_one_starting_inside_the_target_is_not_ongoing(self):
        """A record starting at 02:10 happened in this stretch; it should not be
        marked "still ongoing"."""
        txt = digest.render([ev(130, "whoop", "sleep", dur=60 * 60000)], 120, 150)
        self.assertNotIn("still ongoing", txt)

    def test_one_already_over_is_not_ongoing(self):
        txt = digest.render([ev(-30, "whoop", "sleep", dur=100 * 60000)], 120, 150)
        after = txt.split(digest.TARGET_MARK)[-1]
        self.assertNotIn("still ongoing", after)

    def test_a_record_without_duration_is_never_ongoing(self):
        txt = digest.render([ev(60, "chrome", "visit", title="页面")], 120, 150)
        self.assertNotIn("still ongoing", txt)


class TrailingContextTest(unittest.TestCase):
    """The hour after the target is context too and must not mix into the
    target section.

    There used to be one marker only, and after it came both the target and the
    hour after — the model counted the next hour's activity in this half hour.
    """

    def test_the_following_context_is_marked_separately(self):
        txt = digest.render([ev(125, "chrome", "visit", title="目标里的"),
                             ev(200, "chrome", "visit", title="之后的")], 120, 150)
        self.assertIn(digest.AFTER_MARK, txt)
        self.assertLess(txt.index("目标里的"), txt.index(digest.AFTER_MARK))
        self.assertLess(txt.index(digest.AFTER_MARK), txt.index("之后的"))

    def test_no_marker_without_following_records(self):
        txt = digest.render([ev(125, "chrome", "visit", title="目标里的")], 120, 150)
        self.assertNotIn(digest.AFTER_MARK, txt)


class CompressionTest(unittest.TestCase):
    """The digest has to compress. A hundred thousand characters in one window
    cannot be fed.

    Measured, the first version rendered 09:00–09:30 on 8-17 as 99,995
    characters — the old one was about 4,500. Two causes:

    1. Chrome window titles in live/window **are the same thing** as chrome/visit,
       with " - Google Chrome - Pat (Personal)" appended. 1,743 a day, almost all
       duplicates. But non-Chrome window titles must stay — the "Zoom Meeting"
       at 09:03 was only in live/window, nowhere in chrome.
    2. Deduplication only looked at neighbours. A window title switching between
       two pages repeats every other line, which neighbour dedup cannot stop.
    """

    def test_chrome_window_titles_are_dropped_as_duplicates(self):
        got = lines([ev(541, "chrome", "visit", title="某页面"),
                     ev(541, "live", "window", app="Google Chrome",
                        title="某页面 - Google Chrome - Pat (Personal)")])
        self.assertEqual(len(got) - 1, 1, got)          # minus the marker line

    def test_non_chrome_window_titles_must_stay(self):
        """The hard evidence for judging 09:03 "in a work Zoom" is right here."""
        got = lines([ev(541, "live", "window", app="zoom.us", title="Zoom Meeting")])
        self.assertTrue(any("Zoom Meeting" in x for x in got), got)

    def test_separated_duplicates_are_removed_too(self):
        got = lines([ev(541, "chrome", "visit", title="A"),
                     ev(542, "chrome", "visit", title="B"),
                     ev(543, "chrome", "visit", title="A")])
        self.assertEqual(len([x for x in got if "A" in x]), 1, got)

    def test_dedup_keeps_the_first_time_seen(self):
        """Keeping the last loses "when did this start"."""
        got = lines([ev(541, "chrome", "visit", title="A"),
                     ev(555, "chrome", "visit", title="A")])
        self.assertTrue(any(x.startswith("09:01") for x in got), got)


class WindowsTest(unittest.TestCase):
    def test_forty_eight_windows_a_day(self):
        self.assertEqual(len(digest.windows()), 48)

    def test_ends_aligned(self):
        w = digest.windows()
        self.assertEqual(w[0], (0, 30))
        self.assertEqual(w[-1], (1410, 1440))

    def test_no_overlap_no_gaps(self):
        w = digest.windows()
        self.assertTrue(all(w[i][1] == w[i + 1][0] for i in range(len(w) - 1)))


class MultiRecordingTest(unittest.TestCase):
    """When two recordings capture the same stretch **at once**, speech lines say
    which one they came from; otherwise not one byte is added.

    Why label: a wearable and Plaud Desktop both run during meetings, so what you
    say is on both tracks (two ASR passes, worded slightly differently). Unlabelled,
    the model reads you saying everything twice, or two people repeating each
    other — and the with column follows into "someone else was there".

    Why file_id and not serial_number: measured on 2026-08-24, Desktop's
    serial_number is **one per recording** (start_at in milliseconds), so using
    it as a device number is wrong. file_id is "which recording".

    Why overlap in time matters too: two file_ids in one digest need not mean
    duplication — the wearable splits every five hours and a seam can fall in one
    150-minute render range; two back-to-back Desktop meetings likewise. None of
    that is repeated, and labelling it would mislead. What needs labelling is two
    recordings **overlapping in time**.

    Why not always label: infer is incremental on digest text (reuse means
    text == prev_text[a]). Prefixing every speech line would rejudge all history.
    """

    def test_one_recording_gets_no_prefix(self):
        got = lines([ev(540, "plaud", "speech", speaker="Speaker 1",
                        text="就这样吧", file_id="A")])
        self.assertTrue(any("🗣 Speaker 1: 就这样吧" in x for x in got), got)

    def test_historic_rows_without_file_id_get_none_either(self):
        got = lines([ev(540, "plaud", "speech", speaker="Speaker 1",
                        text="就这样吧")])
        self.assertTrue(any("🗣 Speaker 1: 就这样吧" in x for x in got), got)

    def test_overlapping_recordings_are_labelled(self):
        got = "\n".join(lines([
            ev(540, "plaud", "speech", speaker="Speaker 1", text="我先说一句",
               dur=120000, file_id="notepin-aaaa1111"),
            ev(541, "plaud", "speech", speaker="Speaker 1", text="我先说一句。",
               dur=120000, file_id="desktop-bbbb2222"),
        ]))
        self.assertIn("[1111] Speaker 1: 我先说一句", got)
        self.assertIn("[2222] Speaker 1: 我先说一句。", got)

    def test_back_to_back_recordings_are_not_labelled(self):
        """One at 10:00–10:02, one at 10:30–10:32, both in the render range but not
        overlapping. Nothing is repeated — labelling would tell the model it is."""
        got = "\n".join(lines([
            ev(540, "plaud", "speech", speaker="Speaker 1", text="上一场的结尾",
               dur=120000, file_id="AAAA1111"),
            ev(570, "plaud", "speech", speaker="Speaker 1", text="下一场的开头",
               dur=120000, file_id="BBBB2222"),
        ], a=540, b=600))
        self.assertIn("🗣 Speaker 1: 上一场的结尾", got)
        self.assertNotIn("[1111]", got)

    def test_an_unknown_recording_counts_as_one_so_it_cannot_fail_silently(self):
        """Old rows have no file_id. Overlapping a new recording they must still be
        labelled, or "two tracks here" goes undetected, with no error."""
        got = "\n".join(lines([
            ev(540, "plaud", "speech", speaker="Speaker 1", text="甲",
               dur=120000, file_id="AAAA1111"),
            ev(541, "plaud", "speech", speaker="Speaker 1", text="乙",
               dur=120000),
        ]))
        self.assertIn("[1111] Speaker 1: 甲", got)
        self.assertIn("[?] Speaker 1: 乙", got)

    def test_non_speech_lines_are_unaffected(self):
        got = "\n".join(lines([
            ev(540, "plaud", "speech", speaker="Speaker 1", text="甲",
               dur=120000, file_id="AAAA1111"),
            ev(541, "plaud", "speech", speaker="Speaker 1", text="乙",
               dur=120000, file_id="BBBB2222"),
            ev(542, "zsh", "", cmd="git status"),
        ]))
        self.assertIn("$ git status", got)


if __name__ == "__main__":
    unittest.main()
