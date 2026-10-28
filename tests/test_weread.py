"""WeRead — how long you read each day.

**This source has no timeline.** `/readdata/detail` says "73 minutes on 1
August", not at what time. So it cannot draw a lane and must add no minute to
coverage.

Then what is it for? Umbra already knows from Biome **when** the WeRead app was
in use (media.py's sessions, with start and end). What is missing is the
other half: how much of that was really reading. media.py guesses "left on,
nobody there" with SUSPECT_AFTER_MS; this source can turn the guess into
arithmetic.
"""

import datetime as dt
import unittest
from unittest import mock

from umbra.importers import weread

# The keys in a real reply all land at 16:00 UTC — midnight in Beijing.
# 1785513600 = 2026-07-31 16:00 UTC = 2026-08-01 00:00 Beijing
AUG1 = 1785513600
AUG2 = 1785600000


class RowShapeTest(unittest.TestCase):
    def test_a_day_becomes_one_row(self):
        rows = weread.to_rows({str(AUG1): 4384})
        self.assertEqual(len(rows), 1)
        uid, ts, dur, src, kind, payload = rows[0]
        self.assertEqual(src, "weread")
        self.assertEqual(kind, "daily")

    def test_the_timestamp_is_the_start_of_the_weread_day(self):
        self.assertEqual(weread.to_rows({str(AUG1): 4384})[0][1], AUG1 * 1000)

    def test_the_seconds_are_kept(self):
        p = weread.to_rows({str(AUG1): 4384})[0][5]
        self.assertEqual(p["read_s"], 4384)
        self.assertEqual(p["read_min"], 73)

    def test_duration_is_zero_because_the_position_is_unknown(self):
        """dur_ms means "takes up this long from this moment". We know the
        amount, not the position — putting it in dur claims one sitting from
        Beijing midnight."""
        self.assertEqual(weread.to_rows({str(AUG1): 4384})[0][2], 0)

    def test_the_window_says_it_is_a_beijing_day(self):
        """Days are Beijing days, not local midnight. Unsaid, a later alignment by
        local date would be silently eight hours off — and look entirely
        reasonable."""
        p = weread.to_rows({str(AUG1): 4384})[0][5]
        self.assertEqual(p["window"], "beijing-day")
        self.assertEqual(p["window_ms"], 86_400_000)

    def test_zero_minutes_is_dropped(self):
        """A day with no reading needs no row — "no row" and "read for 0 s" mean
        the same."""
        self.assertEqual(weread.to_rows({str(AUG1): 0}), [])

    def test_uid_is_stable(self):
        a = weread.to_rows({str(AUG1): 4384})[0][0]
        b = weread.to_rows({str(AUG1): 4384})[0][0]
        self.assertEqual(a, b)

    def test_a_day_reimported_with_more_minutes_keeps_its_uid(self):
        """Today is re-imported while you are still reading. The uid must depend
        only on the date, or an hourly run piles up twenty rows for one day."""
        self.assertEqual(weread.to_rows({str(AUG1): 4384})[0][0],
                         weread.to_rows({str(AUG1): 9999})[0][0])

    def test_rows_are_sorted(self):
        rows = weread.to_rows({str(AUG2): 100, str(AUG1): 200})
        self.assertEqual([r[1] for r in rows], [AUG1 * 1000, AUG2 * 1000])


class SinceTest(unittest.TestCase):
    def test_old_days_are_filtered(self):
        rows = weread.to_rows({str(AUG1): 100, str(AUG2): 200},
                              since_ms=AUG2 * 1000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], AUG2 * 1000)

    def test_the_current_day_is_always_refetched(self):
        """Today is not over and the time is still growing. Filtering today out
        by since would freeze it at the morning's number."""
        rows = weread.to_rows({str(AUG2): 200}, since_ms=AUG2 * 1000)
        self.assertEqual(len(rows), 1)


class CollectTest(unittest.TestCase):
    def test_no_key_means_no_data_not_a_crash(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(weread.collect(), [])

    def test_it_never_calls_the_gateway_without_a_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(weread.net, "post_json") as post:
                weread.collect()
        self.assertFalse(post.called)

    def test_the_request_carries_the_skill_version(self):
        """The gateway requires it; without it comes the misleading "missing
        api_name parameter"."""
        seen = []

        def fake(url, body, headers=None, timeout=60):
            seen.append((url, body, headers))
            return {"readTimes": {}}

        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json", side_effect=fake):
                weread.collect(since_ms=AUG2 * 1000)
        url, body, headers = seen[0]
        self.assertEqual(body["api_name"], "/readdata/detail")
        self.assertIn("skill_version", body)
        self.assertEqual(headers["Authorization"], "Bearer wrk-x")

    def test_business_params_are_flat_not_nested(self):
        """The gateway does not unwrap params — nested, they are silently ignored,
        which looks like broken pagination."""
        seen = []

        def fake(url, body, headers=None, timeout=60):
            seen.append(body)
            return {"readTimes": {}}

        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json", side_effect=fake):
                weread.collect(since_ms=AUG2 * 1000)
        self.assertNotIn("params", seen[0])
        self.assertIn("mode", seen[0])

    def test_a_full_import_walks_back_through_history(self):
        """Incrementally, this month only; a full import brings back the months
        before too."""
        calls = []

        def fake(url, body, headers=None, timeout=60):
            calls.append(body.get("baseTime"))
            return {"readTimes": {}}

        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json", side_effect=fake):
                weread.collect(since_ms=None)
        self.assertGreater(len(calls), 6, "a full import fetched only this month")

    def test_an_incremental_import_asks_for_one_period(self):
        """Count only /readdata/detail requests.

        It used to count every request and assert exactly one — and went falsely
        red once collect() also fetched highlights. The intent was never "one
        request per import" but "incremental must not go back 14 months".
        """
        periods = []

        def fake(url, body, headers=None, timeout=60):
            if body["api_name"] == "/readdata/detail":
                periods.append(body)
            return {"readTimes": {}, "books": [], "hasMore": 0}

        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json", side_effect=fake):
                weread.collect(since_ms=AUG2 * 1000)
        self.assertEqual(len(periods), 1)

    def test_an_api_error_yields_nothing(self):
        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json",
                                   return_value={"errcode": -2003,
                                                 "errmsg": "参数格式错误"}):
                self.assertEqual(weread.collect(since_ms=AUG2 * 1000), [])

    def test_an_error_response_is_not_trusted_even_when_it_carries_data(self):
        """With a non-zero errcode, nothing in the reply is trusted.

        The test above only checks "an error reply with no data → empty", which
        holds even with the errcode check deleted — tested, but proving nothing.
        What must be pinned: when the gateway gives both an errcode and
        readTimes, the data stays out.
        """
        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json",
                                   return_value={"errcode": -2003,
                                                 "errmsg": "参数格式错误",
                                                 "readTimes": {str(AUG2): 9999}}):
                self.assertEqual(weread.collect(since_ms=AUG2 * 1000), [])

    def test_registered(self):
        from umbra.importers import ALL, SOURCE_OF
        self.assertIn("weread", ALL)
        self.assertEqual(SOURCE_OF["weread"], "weread")

    def test_collect_takes_since_ms_first(self):
        import inspect
        self.assertEqual(
            list(inspect.signature(weread.collect).parameters)[0], "since_ms")



# ── Highlights ──────────────────────────────────────────────────────

def mk(ts, book="示例书名", chapter="第I节", text="一句划线"):
    return {"ts": ts, "book": book, "author": "某作者",
            "chapter": chapter, "text": text}


T = 1786891436          # 2026-08-16 07:43:56, a real highlight's time


class MarksTest(unittest.TestCase):
    """Highlights are the only thing this source can put on the timeline — each

    has a createTime to the second, and they go back years — **nearly all of it
    outside Biome's coverage**; on many days a highlight is the only evidence of
    reading. Biome holds about a month, so this is not duplication; it fills in
    years.
    """

    def test_a_lone_mark_is_a_point_in_time(self):
        """One highlight is no interval — only "reading at that moment"."""
        rows = weread.marks_to_rows([mk(T)])
        self.assertEqual(len(rows), 1)
        uid, ts, dur, src, kind, p = rows[0]
        self.assertEqual((src, kind), ("weread", "reading"))
        self.assertEqual(ts, T * 1000)
        self.assertEqual(dur, 0)

    def test_marks_close_together_bracket_a_reading_session(self):
        """Three real ones: 07:34:00 / 07:35:44 / 07:43:56 — at least 9 minutes
        of reading."""
        rows = weread.marks_to_rows([mk(T - 596), mk(T - 492), mk(T)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], (T - 596) * 1000)
        self.assertEqual(rows[0][2], 596 * 1000)
        self.assertEqual(rows[0][5]["marks"], 3)

    def test_a_long_gap_starts_a_new_session(self):
        rows = weread.marks_to_rows([mk(T), mk(T + weread.MARK_GAP_S + 60)])
        self.assertEqual(len(rows), 2)

    def test_two_books_do_not_merge(self):
        """Two books in one stretch are two reading sessions."""
        rows = weread.marks_to_rows([mk(T, book="A"), mk(T + 60, book="B")])
        self.assertEqual(sorted(r[5]["book"] for r in rows), ["A", "B"])

    def test_the_book_and_chapter_are_kept(self):
        p = weread.marks_to_rows([mk(T)])[0][5]
        self.assertEqual(p["book"], "示例书名")
        self.assertEqual(p["author"], "某作者")
        self.assertEqual(p["chapter"], "第I节")

    def test_uid_is_stable(self):
        self.assertEqual(weread.marks_to_rows([mk(T)])[0][0],
                         weread.marks_to_rows([mk(T)])[0][0])

    def test_since_filters_old_sessions(self):
        rows = weread.marks_to_rows([mk(T - 999_999), mk(T)],
                                    since_ms=(T - 100) * 1000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], T * 1000)


class MarkPrivacyTest(unittest.TestCase):
    """**Events are stored, not the text.**

    "At 07:34, reading chapter I of a book, one highlight" is a record of
    behaviour; "at 07:34, highlighted <this passage>" is an excerpt. The first
    says what you were doing; the second would turn a page that opens on a
    double-click into years of reading notes. The same treatment as F8 in media.py.
    """

    def test_the_highlighted_text_is_never_stored(self):
        rows = weread.marks_to_rows([mk(T, text="这句原文不该被存下来"),
                                     mk(T + 60, text="这句也是")])
        blob = repr(rows)
        self.assertNotIn("不该被存", blob)
        self.assertNotIn("这句也是", blob)

    def test_only_the_count_survives(self):
        """What is kept is the fact of how many highlights, not what was
        highlighted."""
        p = weread.marks_to_rows([mk(T), mk(T + 60)])[0][5]
        self.assertEqual(p["marks"], 2)
        self.assertNotIn("text", p)


class MarkFetchTest(unittest.TestCase):
    def test_incremental_only_asks_for_books_touched_since(self):
        """A notebook's sort is the time of its latest note. Dozens of books over
        years; fetching all of them every time burns quota."""
        asked = []

        def fake(url, body, headers=None, timeout=60):
            if body["api_name"] == "/user/notebooks":
                return {"books": [
                    {"bookId": "old", "sort": T - 999_999},
                    {"bookId": "new", "sort": T},
                ], "hasMore": 0}
            asked.append(body.get("bookId"))
            return {"updated": [], "book": {}, "chapters": []}

        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json", side_effect=fake):
                weread.collect_marks(since_ms=(T - 100) * 1000)
        self.assertEqual(asked, ["new"])

    def test_a_full_import_asks_for_every_book(self):
        asked = []

        def fake(url, body, headers=None, timeout=60):
            if body["api_name"] == "/user/notebooks":
                return {"books": [{"bookId": "a", "sort": 1},
                                  {"bookId": "b", "sort": 2}], "hasMore": 0}
            asked.append(body.get("bookId"))
            return {"updated": [], "book": {}, "chapters": []}

        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json", side_effect=fake):
                weread.collect_marks(since_ms=None)
        self.assertEqual(sorted(asked), ["a", "b"])

    def test_one_unreadable_book_does_not_lose_the_rest(self):
        def fake(url, body, headers=None, timeout=60):
            if body["api_name"] == "/user/notebooks":
                return {"books": [{"bookId": "bad", "sort": 1},
                                  {"bookId": "ok", "sort": 1}], "hasMore": 0}
            if body.get("bookId") == "bad":
                raise OSError("boom")
            return {"book": {"title": "B"}, "chapters": [],
                    "updated": [{"createTime": T, "markText": "x",
                                 "chapterUid": "1"}]}

        with mock.patch.dict("os.environ", {"WEREAD_API_KEY": "wrk-x"}):
            with mock.patch.object(weread.net, "post_json", side_effect=fake):
                rows = weread.collect_marks(since_ms=None)
        self.assertEqual(len(rows), 1)

    def test_no_key_means_no_marks(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(weread.collect_marks(), [])


class MarkCoverageTest(unittest.TestCase):
    """Unlike the day totals, highlights **do** have a position in time and

    count towards coverage — at content level, too: which chapter of which book,
    alongside a Claude conversation or a terminal command.
    """

    def test_a_reading_session_claims_its_minutes(self):
        import sqlite3
        from umbra import analyze, db
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        start = int(dt.datetime(2026, 8, 16, 9, 0).timestamp())
        db.insert(con, weread.marks_to_rows([mk(start), mk(start + 540)]))
        got = analyze.coverage("2026-08-16", con)
        self.assertGreaterEqual(got["A"], 9)



class CoverageTest(unittest.TestCase):
    """It must add no minute to coverage.

    analyze.py ends with a fallback `else: mark(m0, TIER_A)` — for claude/git/zsh,
    whose point events are content. A daily aggregate falling into it becomes
    "reading at Beijing midnight": an invented position, invented every day.
    """

    def test_a_daily_total_claims_no_minute(self):
        import sqlite3
        from umbra import analyze, db
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        db.insert(con, [(f"weread:day:{AUG1}", AUG1 * 1000, 0, "weread",
                         "daily", {"read_s": 4384, "read_min": 73,
                                   "window": "beijing-day",
                                   "window_ms": 86_400_000})])
        for day in ("2026-07-31", "2026-08-01"):
            got = analyze.coverage(day, con)
            self.assertEqual(got["A"], 0, f"{day} was given {got['A']} invented minutes")
            self.assertEqual(got["B"], 0)


if __name__ == "__main__":
    unittest.main()
