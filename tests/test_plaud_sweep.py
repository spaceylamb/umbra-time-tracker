"""Transcripts are **replaced in place**, so "fetched once, never look again"
does not hold.

The RECHECK_DAYS window was built for "transcription finishes after sync" — it
assumes transcription happens once, only late. Measured otherwise: press
re-generate in the app and the transcript is swapped, and the API carries **no
version marker at all**:

    the listing has only id / name / created_at / start_at / duration /
    serial_number — no status, no updated_at

The case that showed it: a recording came back with fewer segments, a different
text hash and a re-done diarization (one speaker cluster gone). It happened to
be inside the 7-day window and was caught; one re-generated after leaving the
window would **never be noticed**.

Widening the window to 30 days cannot mean "fetch every round": an hourly voice
round over a month of files is thousands of detail requests a day, and the
importer has burned the quota on far fewer and deadlocked itself. So two tiers:

    <= RECHECK_DAYS(7)   every round       hot data, the days transcription lands
    <= SWEEP_DAYS(30)    once a day         catches a re-transcribed old recording
    > SWEEP_DAYS         never again        older updates are not worth fetching

The throttle is kept **per file**, not as one global switch: a global switch
killed mid-run (launchd does this during darkwake) skips the whole round, while
per-file bookkeeping lets the next round finish the sweep.
"""

import datetime as dt
import unittest
import urllib.error
from unittest import mock

from umbra.importers import plaud

NOW = 1786800000000
DAY = 86400_000
HOUR = 3600_000


def f(fid, days_ago):
    t = dt.datetime.fromtimestamp((NOW - days_ago * DAY) / 1000, dt.UTC)
    return {"id": fid, "created_at": t.strftime("%Y-%m-%dT%H:%M:%S")}


def fetch(listing, known, checked=None):
    return plaud.needs_fetch(listing, known, now_ms=NOW, checked=checked)


class SweepWindowTest(unittest.TestCase):
    def test_inside_the_hot_window_every_round_fetches(self):
        """The first days after transcription change most; hourly is worth it."""
        self.assertEqual(
            fetch([f("a", 3)], {"a"}, {"a": NOW - 1000}), ["a"])

    def test_outside_the_hot_window_unchecked_for_a_day_fetches(self):
        """This is the "re-transcribed a two-week-old recording" path."""
        self.assertEqual(
            fetch([f("a", 14)], {"a"}, {"a": NOW - 25 * HOUR}), ["a"])

    def test_outside_the_hot_window_checked_today_does_not(self):
        """Once a day, not once a round — that would burn the quota."""
        self.assertEqual(
            fetch([f("a", 14)], {"a"}, {"a": NOW - 2 * HOUR}), [])

    def test_outside_the_hot_window_never_checked_fetches(self):
        """Right after upgrading `checked` is empty; the first round
        fingerprints everything inside 30 days once."""
        self.assertEqual(fetch([f("a", 14)], {"a"}, {}), ["a"])

    def test_beyond_the_sweep_window_is_not_fetched(self):
        old = plaud.SWEEP_DAYS + 5
        self.assertEqual(fetch([f("a", old)], {"a"}, {}), [])

    def test_never_imported_is_fetched_however_old(self):
        """The recheck / sweep windows only widen the "already imported" gate;
        they must never become a new filter.

        Sync delays up to 362 hours have been measured. Filtering by time loses
        late recordings for good, with no error — the store is just missing a
        piece.
        """
        self.assertEqual(fetch([f("a", 300)], set(), {}), ["a"])

    def test_without_checked_it_falls_back_to_the_hot_window_only(self):
        """Callers outside collect() (tests, debugging scripts) should not have
        to build a table first."""
        self.assertEqual(plaud.needs_fetch([f("a", 14)], {"a"}, now_ms=NOW), [])
        self.assertEqual(plaud.needs_fetch([f("a", 3)], {"a"}, now_ms=NOW), ["a"])


class QuotaTest(unittest.TestCase):
    """With the quota burned, the sweep must not drag down the fetch.

    Measured: after a few hundred detail requests in one round the API returns
    429, and net.get_json's backoff cannot wait out that window.

    The two tiers fail with **different meaning** and must be kept apart:

        hot    is **fetching**. Not getting it = today's recordings missing =
               real missing data; it has to propagate so cmd_import records a
               failure and doctor can see it.
        sweep  is **re-checking what is already stored**. Not getting it only
               means "this old recording went unchecked today" — check it
               tomorrow. Losing a round's new recordings over it is backwards.

    So the sweep comes **after** the hot tier, and stops on a 429 keeping what
    it already has.
    """

    def _run(self, fail_on, err=None):
        det = {"start_at": "2026-09-16T01:00:00",
               "source_list": [{"data_type": "transaction", "data_content":
                                '[{"start_time":0,"end_time":1,"content":"x"}]'}],
               "note_list": [{"data_type": "auto_sum_note"}]}
        hot, old = "of_" + "1" * 32, "of_" + "2" * 32
        # collect() uses the **real current time**, so the listing's created_at
        # must be built from real time too — the module's fixed NOW would put
        # both files outside the 30-day window, fetch neither, and pass falsely.
        import time
        now = int(time.time() * 1000)

        def at(fid, days_ago):
            t = dt.datetime.fromtimestamp((now - days_ago * DAY) / 1000, dt.UTC)
            return {"id": fid, "created_at": t.strftime("%Y-%m-%dT%H:%M:%S")}

        def fake(url, *a, **k):
            if "files/?" in url:
                return {"data": [at(hot, 1), at(old, 14)]}
            if fail_on in url:
                raise err or urllib.error.HTTPError(url, 429, "Too Many", {}, None)
            return {**det, "id": url.rsplit("/", 1)[-1]}

        with mock.patch.object(plaud.plaud_auth, "authed_get_json", fake), \
             mock.patch.object(plaud.plaud_auth, "ensure_access_token",
                               lambda *a, **k: "t"), \
             mock.patch.object(plaud, "_known_file_ids",
                               lambda con: {"1" * 32, "2" * 32}), \
             mock.patch.object(plaud, "_known_serials", lambda con: {}), \
             mock.patch.object(plaud, "_checked_map", lambda con: {}), \
             mock.patch.object(plaud, "_generating_map", lambda con: {}), \
             mock.patch.object(plaud.db, "connect", mock.MagicMock()):
            return plaud.collect()

    def test_a_429_in_the_sweep_keeps_the_hot_batch(self):
        rows = self._run(fail_on="2" * 32)
        self.assertEqual({r[5]["file_id"] for r in rows}, {"1" * 32})

    def test_a_429_in_the_hot_tier_still_raises(self):
        """Not getting this tier is real missing data; it must not be swallowed."""
        with self.assertRaises(urllib.error.HTTPError):
            self._run(fail_on="1" * 32)

    # A dropped line that outlasts net's whole retry chain is the same case as a
    # 429: the new recordings the hot tier already has must not be thrown away
    # along with the sweep.
    def _eof(self):
        import ssl
        return urllib.error.URLError(ssl.SSLEOFError(8, "UNEXPECTED_EOF_WHILE_READING"))

    def test_a_dropped_line_in_the_sweep_keeps_the_hot_batch(self):
        rows = self._run(fail_on="2" * 32, err=self._eof())
        self.assertEqual({r[5]["file_id"] for r in rows}, {"1" * 32})

    def test_a_dropped_line_in_the_hot_tier_still_raises(self):
        with self.assertRaises(urllib.error.URLError):
            self._run(fail_on="1" * 32, err=self._eof())


if __name__ == "__main__":
    unittest.main()
