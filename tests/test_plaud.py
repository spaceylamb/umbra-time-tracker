"""The PLAUD speech importer.

The fixture has the shape of a real recording (a 56-minute one from
2026-08-09): 56 seconds of talk, then **54.5 minutes of silence**, then two
more lines. The text is fake; not one time figure was changed —
this shape holds down three things at once that are easy to get wrong and fail
without an error:

  1. Silence must not be squeezed out. Segment offsets are wall-clock ms from
     the start of the recording; the 3,270-second gap must show in the second
     line's absolute time.
  2. start_at is **a UTC string with no zone suffix** ('2026-08-09T15:05:50').
     A bare fromisoformat() gives a naive datetime, read as local time the whole
     thing is off by seven hours.
  3. A recording crosses the hour. With the offset added it must still be the
     right absolute moment.
"""

import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock
import urllib.error

from umbra import db, plaud_auth
from umbra.importers import plaud

# ── A real recording's metadata (file_id and text are fake, times are real) ──
META = {
    "id": "42d4e7b33fffde3719ef41a14a49823d",
    "name": "morning sync",
    "start_at": "2026-08-09T15:05:50",      # UTC, no suffix — 08:05:50 local
    "created_at": "2026-08-10T07:26:38",    # when it synced, 16 hours after recording
    "duration": 3374000,                    # 56.2 minutes
    "serial_number": "PN000000000000",
}

# Three segments with 3,270.6 seconds (54.5 minutes) of silence between
SEGMENTS = [
    {"start_time": 790, "end_time": 56690,
     "speaker": "Speaker 1", "content": "第一句"},
    {"start_time": 3327290, "end_time": 3353290,
     "speaker": "Speaker 1", "content": "第二句"},
    {"start_time": 3358890, "end_time": 3371150,
     "speaker": "Speaker 2", "content": "第三句"},
]

START_MS = 1786287950_000     # 2026-08-09T15:05:50Z


class ParseUtcTest(unittest.TestCase):
    def test_no_suffix_is_utc_not_local(self):
        """Get this wrong and the whole timeline is hours off, with no error at
        all."""
        self.assertEqual(plaud._parse_utc("2026-08-09T15:05:50"), START_MS)

    def test_explicit_z_gives_the_same_answer(self):
        self.assertEqual(plaud._parse_utc("2026-08-09T15:05:50Z"), START_MS)

    def test_none_is_none(self):
        self.assertIsNone(plaud._parse_utc(None))
        self.assertIsNone(plaud._parse_utc(""))


class ToEventsTest(unittest.TestCase):
    def rows(self):
        return plaud.to_events(META, SEGMENTS)

    def test_one_event_per_segment(self):
        """Not merged on import — merging belongs to analysis, and changing it
        needs no recollection."""
        self.assertEqual(len(self.rows()), 3)

    def test_absolute_timestamp_is_start_plus_offset(self):
        uid, ts, dur, src, kind, payload = self.rows()[0]
        self.assertEqual(ts, START_MS + 790)
        self.assertEqual(dur, 55900)
        self.assertEqual(src, "plaud")
        self.assertEqual(kind, "speech")

    def test_silence_is_not_compressed(self):
        """54.5 minutes with nobody speaking: the second line must land 55.45

        minutes after the recording began. Were the offset read as "ms into the
        speech sequence" rather than wall clock, the second line would land 56
        seconds in — the whole recording collapsed to 1.6 minutes.
        """
        rows = self.rows()
        gap_ms = rows[1][1] - (rows[0][1] + rows[0][2])
        self.assertEqual(gap_ms, 3270600)

    def test_last_segment_stays_inside_the_recording(self):
        rows = self.rows()
        end = rows[-1][1] + rows[-1][2]
        self.assertLessEqual(end, START_MS + META["duration"])

    def test_crosses_the_hour_correctly(self):
        """A recording starting 15:05 has its second line at 16:01 — across an
        hour."""
        ts = self.rows()[1][1]
        when = dt.datetime.fromtimestamp(ts / 1000, dt.UTC)
        self.assertEqual(when.strftime("%H:%M:%S"), "16:01:17")

    def test_uid_is_stable_and_unique(self):
        """Re-importing must be idempotent — INSERT OR IGNORE skips uid conflicts."""
        uids = [r[0] for r in self.rows()]
        self.assertEqual(len(set(uids)), 3)
        self.assertEqual(uids, [r[0] for r in plaud.to_events(META, SEGMENTS)])
        self.assertTrue(all(u.startswith(f"plaud:{META['id']}:") for u in uids))

    def test_payload_carries_text_and_speaker(self):
        payload = self.rows()[2][5]
        self.assertEqual(payload["text"], "第三句")
        self.assertEqual(payload["speaker"], "Speaker 2")
        self.assertEqual(payload["file_id"], META["id"])

    def test_segments_out_of_order_are_sorted(self):
        rows = plaud.to_events(META, list(reversed(SEGMENTS)))
        self.assertEqual([r[1] for r in rows], sorted(r[1] for r in rows))

    def test_empty_content_is_dropped(self):
        """A segment with no text only pollutes the timeline — it is no evidence
        of anything."""
        rows = plaud.to_events(META, [{"start_time": 0, "end_time": 100,
                                       "speaker": "Speaker 1", "content": "  "}])
        self.assertEqual(rows, [])

    def test_missing_start_at_yields_nothing(self):
        """Without an absolute start it cannot be placed on the timeline; skip
        the whole thing rather than guess."""
        bad = {**META, "start_at": None}
        self.assertEqual(plaud.to_events(bad, SEGMENTS), [])


class NeedsFetchTest(unittest.TestCase):
    """Listing is cheap, details are expensive — the gate belongs at details.

    db.since_for() cannot be used here (it takes max(events.ts), **when the
    recording happened**). Measured sync delay: median 40 hours, max 362, 16 of
    41 over 72 hours —
    an incremental window on recording time would miss late recordings for good,
    with no error.
    """

    def test_skips_already_imported_files(self):
        listing = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        self.assertEqual(plaud.needs_fetch(listing, {"b"}), ["a", "c"])

    def test_nothing_known_means_fetch_everything(self):
        listing = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(plaud.needs_fetch(listing, set()), ["a", "b"])

    def test_files_without_id_are_ignored(self):
        self.assertEqual(plaud.needs_fetch([{"name": "x"}], set()), [])


class TranscriptBlockTest(unittest.TestCase):
    def test_picks_the_transaction_block(self):
        detail = {"source_list": [
            {"data_type": "outline", "data_content": "[]"},
            {"data_type": "transaction", "data_content": '[{"content":"hi"}]'},
        ]}
        self.assertEqual(plaud.transcript_of(detail), [{"content": "hi"}])

    def test_missing_transcript_is_empty_not_an_error(self):
        """Synced but not yet transcribed — the normal in-between state every
        import runs into."""
        self.assertEqual(plaud.transcript_of({"source_list": []}), [])
        self.assertEqual(plaud.transcript_of({}), [])

    def test_ignores_the_polished_block(self):
        """The first version stores only transaction: it is ground truth, and all
        41 of 41 have it."""
        detail = {"source_list": [
            {"data_type": "transaction_polish", "data_content": '[{"content":"polished"}]'}]}
        self.assertEqual(plaud.transcript_of(detail), [])


# ── Tokens ──────────────────────────────────────────────────────────
class AuthTest(unittest.TestCase):
    """Umbra reads and writes the CLI's ~/.plaud/tokens.json rather than
    keeping its own copy.

    A refresh response may carry a **new** refresh_token (rotation). Two
    independent holders each refreshing invalidate each other — showing up as
    "suddenly asks to sign in again after a few days".
    A single holder is the only arrangement that cannot fight; the price is
    coupling to the CLI's file format.
    """

    def test_valid_token_is_reused(self):
        tok = {"access_token": "good", "refresh_token": "r",
               "expires_at": 10_000_000}
        got = plaud_auth.ensure_access_token(
            tok=tok, now_ms=9_000_000, refresh=self.fail)
        self.assertEqual(got, "good")

    def test_expiring_soon_triggers_refresh(self):
        """Leave a margin: a token "two seconds from expiring" expires on the way,
        showing up as random 401s — the same reason as whoop_auth's
        REFRESH_MARGIN_MS."""
        saved = {}
        tok = {"access_token": "old", "refresh_token": "r",
               "expires_at": 10_000_000}
        got = plaud_auth.ensure_access_token(
            tok=tok, now_ms=10_000_000 - plaud_auth.REFRESH_MARGIN_MS + 1,
            refresh=lambda rt: {"access_token": "new", "expires_in": 86400},
            save=saved.update)
        self.assertEqual(got, "new")
        self.assertEqual(saved["access_token"], "new")

    def test_refresh_token_is_kept_when_response_omits_it(self):
        """A plain update would write None, and the next refresh must fail."""
        saved = {}
        plaud_auth.ensure_access_token(
            tok={"access_token": "old", "refresh_token": "keepme", "expires_at": 0},
            now_ms=1_000, refresh=lambda rt: {"access_token": "new", "expires_in": 60},
            save=saved.update)
        self.assertEqual(saved["refresh_token"], "keepme")

    def test_rotated_refresh_token_is_stored(self):
        saved = {}
        plaud_auth.ensure_access_token(
            tok={"access_token": "old", "refresh_token": "old_r", "expires_at": 0},
            now_ms=1_000,
            refresh=lambda rt: {"access_token": "n", "refresh_token": "new_r",
                                "expires_in": 60},
            save=saved.update)
        self.assertEqual(saved["refresh_token"], "new_r")

    def test_no_token_at_all_is_a_hard_failure(self):
        with self.assertRaises(plaud_auth.PlaudAuthExpired):
            plaud_auth.ensure_access_token(tok=None, now_ms=0)

    def test_expiry_is_computed_from_expires_in(self):
        saved = {}
        plaud_auth.ensure_access_token(
            tok={"access_token": "o", "refresh_token": "r", "expires_at": 0},
            now_ms=5_000, refresh=lambda rt: {"access_token": "n", "expires_in": 100},
            save=saved.update)
        self.assertEqual(saved["expires_at"], 5_000 + 100_000)


class RecheckWindowTest(unittest.TestCase):
    """Files just arrived in the cloud get a few more looks — transcription
    finishes **after sync**.

    A hole found reviewing this source on 2026-08-17. First, what is not a hole:
    when transcription is unfinished transcript_of() returns [], the file yields
    0 events, so it is not in _known_file_ids() at all (that is derived from
    event uids) — it is naturally fetched again. That path always worked:
    measured, 43 of 43 files had the same segment count locally and in the cloud.

    What really leaks is **fetched but incomplete**: once one segment has been
    seen the file is in known for good, and later completion in the cloud is
    never looked at. And the listing has only
    created_at / duration / id / name / serial_number / start_at —
    **no transcription status and no update time** — no cheap way to tell
    whether the cloud changed.

    So time is the backstop: a look-back window on created_at (when the file
    reached the cloud, not when it was recorded); files inside it are fetched
    again every time. The cost is a few detail requests per run; the gain is a
    source that heals itself.

    The anchor must be created_at, not start_at: with sync delay median 40 hours,
    max 362, a recording two weeks late would arrive already outside a window on
    recording time — exactly the kind most likely still untranscribed.
    """

    NOW = 1786800000000                      # some "now"
    DAY = 86400_000

    def _f(self, fid, days_ago, key="created_at"):
        t = dt.datetime.fromtimestamp((self.NOW - days_ago * self.DAY) / 1000,
                                      dt.UTC)
        return {"id": fid, key: t.strftime("%Y-%m-%dT%H:%M:%S")}

    def fetch(self, listing, known):
        return plaud.needs_fetch(listing, known, now_ms=self.NOW)

    def test_a_new_file_is_fetched_again_even_if_imported(self):
        self.assertEqual(self.fetch([self._f("a", 1)], {"a"}), ["a"])

    def test_an_imported_file_outside_the_window_is_not_fetched(self):
        old = plaud.RECHECK_DAYS + 5
        self.assertEqual(self.fetch([self._f("a", old)], {"a"}), [])

    def test_a_never_imported_file_is_fetched_however_old(self):
        """The look-back window only loosens the "already imported" gate; it must
        not turn into a new filter — then a recording 15 days late would be
        missed for good, with no error."""
        self.assertEqual(self.fetch([self._f("a", 300)], set()), ["a"])

    def test_without_created_at_it_falls_back_to_start_at(self):
        self.assertEqual(self.fetch([self._f("a", 1, key="start_at")], {"a"}), ["a"])

    def test_with_neither_time_it_counts_as_old(self):
        """An imported file of unknown time is not fetched again — when unsure,
        one request fewer, rather than picking it up every round (a permanent
        fixed cost)."""
        self.assertEqual(self.fetch([{"id": "a"}], {"a"}), [])

    def test_a_broken_time_string_does_not_crash(self):
        self.assertEqual(self.fetch([{"id": "a", "created_at": "not a time"}], {"a"}), [])

    def test_now_is_optional(self):
        """cmd_import calls the two-argument form. The default is the current time."""
        self.assertEqual(plaud.needs_fetch([{"id": "a"}], set()), ["a"])


class RewriteTest(unittest.TestCase):
    """Rows fetched again must overwrite the old, or the look-back window is for
    nothing.

    plaud is a 1:1 source through db.insert(); that path defaults to INSERT OR
    IGNORE, and the uid plaud:{file_id}:{index} never changes. So a complete
    transcript fetched again would be ignored entirely, leaving the incomplete
    rows in place.

    This source can overwrite freely, because collect() **returns only files it
    really fetched this time** (nothing outside needs_fetch is requested) — no
    write amplification. A source like chrome, whose collect returns 80,000 rows
    every time, cannot do this.
    """

    def test_plaud_declares_that_it_overwrites(self):
        self.assertTrue(getattr(plaud, "REWRITE", False),
                        "rows fetched again would be dropped by INSERT OR IGNORE")

    def test_a_source_returning_everything_every_time_must_not_overwrite(self):
        """The opposite anchor: chrome returns 80,000 rows per collect; REPLACE-ing
        them every round is pure write amplification."""
        from umbra.importers import chrome
        self.assertFalse(getattr(chrome, "REWRITE", False))


if __name__ == "__main__":
    unittest.main()


# ── The server is the authority ─────────────────────────────────────
class ServerRejectedTokenTest(unittest.TestCase):
    """The local expires_at is **a cache**, not a fact.

    Hit on 2026-08-18: tokens.json said it expired at 12:00, it was 09:17, and
    ensure_access_token handed the old token over at a glance — the server
    answered ACCESS_TOKEN_INVALID. Not one line of speech came in all day.

    The server can invalidate early at any time (a password change, signing in
    elsewhere, revocation), while all we hold is the expiry **it stated when
    issuing**. Using that as the gate
    lets a cache answer a question only the server knows.

    Two separate holes, both to close:

      1. No way to recover — the only refresh trigger was "local time is up",
         so an early invalidation can never be caught up with: 401 however many
         reruns.
      2. The wrong error — the 401 comes from **the data endpoint**, not the
         refresh endpoint, so refresh()'s 400/401/403 handling never runs.
         A bare HTTPError falls into cmd_import's generic except, logs "failed"
         and carries on. The policy set on 2026-08-15 is: expired authorisation
         must stop hard and say to run `plaud login`. The policy was written;
         this path went round it.

    The fix hands authority back to the server: 401 = force one refresh and
    retry, and only then is it really expired.
    """

    def setUp(self):
        self.tok = {"access_token": "dead", "refresh_token": "r",
                    # locally there is plenty of time — exactly that day's scene
                    "expires_at": 99_000_000_000_000}
        self.refreshed = []

    def _ensure(self, path=None, force=False):
        if force:
            self.refreshed.append(True)
            return "fresh"
        return self.tok["access_token"]

    @staticmethod
    def _401(url="u"):
        return urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    def test_a_401_from_the_data_endpoint_forces_a_refresh_and_retries(self):
        """Refresh even though it has not expired locally — otherwise the only
        recovery is waiting for local time to run out."""
        seen = []

        def get(url, headers):
            seen.append(headers["Authorization"])
            if len(seen) == 1:
                raise self._401()
            return {"data": "ok"}

        got = plaud_auth.authed_get_json("u", get=get, ensure=self._ensure)
        self.assertEqual(got, {"data": "ok"})
        self.assertEqual(self.refreshed, [True], "no forced refresh")
        self.assertEqual(seen, ["Bearer dead", "Bearer fresh"],
                         "the retry used the token already rejected")

    def test_still_401_after_refreshing_means_really_expired(self):
        """It must be PlaudAuthExpired, not a bare HTTPError — the AUTH_EXPIRED
        hard-stop policy only recognises the former."""
        def get(url, headers):
            raise self._401()

        with self.assertRaises(plaud_auth.PlaudAuthExpired) as cm:
            plaud_auth.authed_get_json("u", get=get, ensure=self._ensure)
        self.assertIn("plaud login", str(cm.exception), "did not say how to fix it")

    def test_only_one_retry(self):
        """The refresh endpoint must not be hit 900 times. The first failure
        should stop the whole import."""
        n = []

        def get(url, headers):
            n.append(1)
            raise self._401()

        with self.assertRaises(plaud_auth.PlaudAuthExpired):
            plaud_auth.authed_get_json("u", get=get, ensure=self._ensure)
        self.assertEqual(len(n), 2, "wrong number of retries")
        self.assertEqual(len(self.refreshed), 1)

    def test_other_errors_are_raised_as_they_are(self):
        """A 500 is a temporary server fault; treating it as expired would send
        someone to `plaud login` for nothing. 403 likewise — that is a missing
        permission, not a dead token."""
        for code in (403, 429, 500, 503):
            with self.subTest(code=code):
                def get(url, headers, code=code):
                    raise urllib.error.HTTPError("u", code, "x", {}, None)
                with self.assertRaises(urllib.error.HTTPError):
                    plaud_auth.authed_get_json("u", get=get, ensure=self._ensure)
                self.assertEqual(self.refreshed, [], f"{code} triggered a refresh")

    def test_success_first_time_does_not_refresh(self):
        got = plaud_auth.authed_get_json(
            "u", get=lambda url, headers: {"ok": 1}, ensure=self._ensure)
        self.assertEqual(got, {"ok": 1})
        self.assertEqual(self.refreshed, [], "an unneeded refresh rotates the refresh_token for nothing")

    def test_extra_headers_are_not_pushed_out_by_the_auth_header(self):
        seen = {}
        plaud_auth.authed_get_json(
            "u", {"X-Trace": "1"},
            get=lambda url, headers: seen.update(headers) or {},
            ensure=self._ensure)
        self.assertEqual(seen.get("X-Trace"), "1")
        self.assertEqual(seen.get("Authorization"), "Bearer dead")


class ForceRefreshTest(unittest.TestCase):
    """force=True skips the local expiry check."""

    def test_force_refreshes_even_when_not_expired_locally(self):
        saved = {}
        got = plaud_auth.ensure_access_token(
            tok={"access_token": "old", "refresh_token": "r",
                 "expires_at": 99_000_000_000_000},
            now_ms=1_000, force=True,
            refresh=lambda rt: {"access_token": "new", "expires_in": 86400},
            save=saved.update)
        self.assertEqual(got, "new")
        self.assertEqual(saved["access_token"], "new", "not written to disk after refreshing")

    def test_without_force_behaviour_is_unchanged(self):
        got = plaud_auth.ensure_access_token(
            tok={"access_token": "good", "refresh_token": "r",
                 "expires_at": 99_000_000_000_000},
            now_ms=1_000, refresh=self.fail)
        self.assertEqual(got, "good")

    def test_force_without_a_refresh_token_is_a_hard_failure(self):
        with self.assertRaises(plaud_auth.PlaudAuthExpired):
            plaud_auth.ensure_access_token(
                tok={"access_token": "x"}, now_ms=0, force=True)


class ImporterUsesRetryTest(unittest.TestCase):
    """A fixed function nobody calls = not fixed.

    This project fell into it: migrate() tested green alone, connect() forgot to
    call it, and the user's database was never upgraded. So one test pins "is it
    wired in".

    Both listing and details are pinned — wired in halfway, a token invalidated
    mid-pagination (900 detail requests in a long run, crossing the server's
    invalidation moment) is still a bare 401.
    """

    def test_listing_requests_go_through_the_retrying_path(self):
        calls = []
        with mock.patch.object(plaud.plaud_auth, "authed_get_json",
                               lambda url, *a, **k: calls.append(url) or {"data": []}):
            plaud._listing()
        self.assertTrue(calls, "_listing still calls net.get_json directly")

    def test_detail_requests_go_through_the_retrying_path(self):
        seen = []

        def fake(url, *a, **k):
            seen.append(url)
            if "files/?" in url:
                return {"data": [{"id": "F1", "start_at": "2026-08-17T10:00:00",
                                  "created_at": "2026-08-17T10:00:00"}]}
            return {}

        with mock.patch.object(plaud.plaud_auth, "authed_get_json", fake), \
             mock.patch.object(plaud.plaud_auth, "ensure_access_token",
                               lambda *a, **k: "t"), \
             mock.patch.object(plaud, "_known_file_ids", lambda con: set()), \
             mock.patch.object(plaud.db, "connect", mock.MagicMock()):
            plaud.collect()
        self.assertTrue(any("files/F1" in u for u in seen),
                        "detail requests still call net.get_json directly")


class HighlightTest(unittest.TestCase):
    """Hardware marks: a short press of the button mid-recording.

    Confirmed on 2026-08-22 that this API **does** return them — undocumented,
    unused by the official CLI, not exposed over MCP, but files with a press
    have two extra blocks in their details and files without do not. (Before
    that, "the API does not give it" and "never pressed" could not be told apart.)

        source_list  an extra mark_memo    a placeholder before transcription
        note_list    an extra high_light   PLAUD's summary around the mark

    Both blocks have an empty data_content, with the content behind a presigned
    data_link URL — the same branch transcript_of ran into.

    Only high_light is collected: mark_memo's mark_content is
    "Highlighted. Available after transcription.", a placeholder with no
    information. Once transcribed, the same mark appears in high_light with a
    title and bulleted content.
    """

    LINK = "https://s3.example/hl.json"
    HL = [{
        "timestamp": 1930000,
        "mark_type": 4,
        "mark_type_string": "hardware",
        "title": "周末去山里露营",
        "content": "• 定在山里而非海边\n• 周五下班出发",
    }]

    def _detail(self, **over):
        d = {"id": "F1",
             "source_list": [{"data_type": "mark_memo", "data_content": "",
                              "data_link": "https://s3.example/mm.json"}],
             "note_list": [{"data_type": "high_light", "data_content": "",
                            "data_link": self.LINK}]}
        d.update(over)
        return d

    def test_marks_are_fetched_from_the_presigned_url(self):
        with mock.patch.object(plaud.net, "get_text",
                               return_value=json.dumps(self.HL)):
            got = plaud.marks_of(self._detail())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["title"], "周末去山里露营")

    def test_inline_content_works_too(self):
        d = self._detail(note_list=[{"data_type": "high_light",
                                     "data_content": json.dumps(self.HL)}])
        self.assertEqual(len(plaud.marks_of(d)), 1)

    def test_a_file_without_a_press_has_no_such_block(self):
        self.assertEqual(plaud.marks_of({"note_list": [
            {"data_type": "auto_sum_note", "data_content": "..."}]}), [])
        self.assertEqual(plaud.marks_of({}), [])

    def test_mark_memo_is_not_collected(self):
        """Its mark_content is "Highlighted. Available after transcription." — a
        placeholder. Collected, it would add noise with no information."""
        d = {"source_list": [{"data_type": "mark_memo",
                              "data_content": json.dumps([{
                                  "timestamp": 1930000, "mark_type": 4,
                                  "mark_content": "Highlighted. Available after transcription.",
                                  "content": None, "title": None}])}]}
        self.assertEqual(plaud.marks_of(d), [])

    # ── the format since 2026-09-15 ───────────────────────────────
    # high_light became Markdown ("## title" plus bullets) with no timestamp;
    # only mark_memo has the moment now. The old code's json.loads failed and
    # returned [], so a month of marks never reached the store, with no error.
    PLACEHOLDER = "Highlighted. Available after transcription."
    MD = ("## Renew the passport\n• book the appointment\n• bring two photos\n\n"
          "## Try a standing desk\n• check the return policy\n")

    def _new(self, memo, md=MD):
        return {"source_list": [{"data_type": "mark_memo",
                                 "data_content": json.dumps(memo)}],
                "note_list": [{"data_type": "high_light", "data_content": md}]}

    def test_new_format_moment_from_mark_memo_content_from_markdown(self):
        """Equal counts pair in order. A failed upload leaves only the
        placeholder in memo, but its section is still in the Markdown — pairing
        in order recovers it."""
        got = plaud.marks_of(self._new([
            {"timestamp": 4133520, "mark_type": 4,
             "mark_content": "Renew the passport", "mark_status": 3},
            {"timestamp": 9490600, "mark_type": 4,
             "mark_content": self.PLACEHOLDER, "mark_status": 4},
        ]))
        self.assertEqual([m["timestamp"] for m in got], [4133520, 9490600])
        self.assertEqual([m["title"] for m in got],
                         ["Renew the passport", "Try a standing desk"])
        self.assertEqual(got[0]["content"], "• book the appointment\n• bring two photos")
        rows = plaud.mark_events(META, got)
        self.assertEqual(len(rows), 2)

    def test_new_format_unequal_counts_keep_only_matching_titles(self):
        """Pairing in order assumes a one-to-one match. Without it, forcing a
        pair hangs A's content on B's moment — better one mark fewer than one
        at the wrong time."""
        got = plaud.marks_of(self._new([
            {"timestamp": 100, "mark_content": self.PLACEHOLDER},
            {"timestamp": 200, "mark_content": "Try a standing desk"},
            {"timestamp": 300, "mark_content": self.PLACEHOLDER},
        ]))
        self.assertEqual([(m["timestamp"], m["title"]) for m in got],
                         [(200, "Try a standing desk")])
        self.assertIn("return policy", got[0]["content"])

    def test_new_format_without_a_moment_is_dropped(self):
        """Offline for days: the Markdown has content, memo is empty. A record
        that cannot be placed on the timeline must not appear."""
        self.assertEqual(plaud.marks_of(self._new([])), [])

    def test_a_mark_lands_at_an_absolute_moment(self):
        """timestamp shares the transcript segments' frame (ms offset from the
        recording's start), so it aligns exactly with particular lines."""
        rows = plaud.mark_events(META, self.HL)
        self.assertEqual(len(rows), 1)
        uid, ts, dur, src, kind, payload = rows[0]
        start = plaud._parse_utc(META["start_at"])
        self.assertEqual(ts, start + 1930000)
        self.assertEqual(kind, "mark")
        self.assertEqual(payload["title"], "周末去山里露营")
        self.assertIn("山里", payload["content"])
        self.assertEqual(payload["mark_source"], "hardware")

    def test_its_uid_does_not_collide_with_the_transcript(self):
        rows = plaud.mark_events(META, self.HL)
        self.assertTrue(rows[0][0].startswith("plaud:mark:"))
        self.assertNotIn(rows[0][0], {r[0] for r in plaud.to_events(META, SEGMENTS)})

    def test_no_absolute_start_skips_it_entirely(self):
        """The transcript's discipline: a record that cannot be placed on the
        timeline should not appear."""
        self.assertEqual(plaud.mark_events({"id": "F1"}, self.HL), [])

    def test_bad_data_does_not_crash(self):
        with mock.patch.object(plaud.net, "get_text", return_value="not json"):
            self.assertEqual(plaud.marks_of(self._detail()), [])


class SerialTest(unittest.TestCase):
    """serial_number stays in the row, but **it is not a device id**.

    Measured on 2026-08-24: the wearable's is '8820B50000000000' (a hardware
    serial), while a Plaud Desktop recording's is '1787619095251' — read as
    milliseconds it is exactly that record's start_at (2026-08-25T00:51:35.251).
    So the software serial_number is **one per recording**, a new value each
    time.

    So it proves "recorded by the software", but **cannot** count "how many
    devices". Count recordings by file_id.

    Stored all the same: it is the only field that tells the wearable from
    Desktop, which recalibrating the echo threshold will need (the wearable
    hears the room, Desktop captures system audio — their echo rates are
    nothing alike).
    """

    def test_transcript_rows_carry_the_serial(self):
        rows = plaud.to_events(META, SEGMENTS)
        self.assertEqual({r[5]["serial"] for r in rows}, {"PN000000000000"})

    def test_mark_rows_carry_it_too(self):
        hl = [{"timestamp": 1930000, "mark_type_string": "hardware",
               "title": "t", "content": "c"}]
        rows = plaud.mark_events(META, hl)
        self.assertEqual(rows[0][5]["serial"], "PN000000000000")

    def test_no_serial_is_an_empty_string_neither_crash_nor_guess(self):
        meta = {k: v for k, v in META.items() if k != "serial_number"}
        rows = plaud.to_events(meta, SEGMENTS)
        self.assertEqual({r[5]["serial"] for r in rows}, {""})


class DesktopShapeTest(unittest.TestCase):
    """Plaud Desktop's record shape — worked out from a real recording on
    2026-08-24.

    It differs from the wearable in three ways, each able to bite silently:

      start_at   has fractional seconds '2026-08-25T00:51:35.251000' (the
                 wearable's are whole)
      name       is a local time string, not an AI-written title
      serial     equals start_at in milliseconds, different per recording
    """

    # The real one, with the text replaced
    DESKTOP = {
        "id": "88dbd79ee0a3b9fec4f1b6f2e158e50a",
        "name": "2026-08-24 17:51:35",              # local time
        "start_at": "2026-08-25T00:51:35.251000",   # UTC, fractional seconds
        "created_at": "2026-08-25T00:52:19",
        "duration": 41000,
        "serial_number": "1787619095251",
    }

    def test_fractional_start_at_parses(self):
        """A bare fromisoformat accepts fractional seconds, but pin it — if it
        failed to_events would skip the whole thing and a meeting would vanish,
        with no error."""
        ms = plaud._parse_utc(self.DESKTOP["start_at"])
        self.assertEqual(ms, 1787619095251)

    def test_start_at_is_still_utc(self):
        """name's '2026-08-24 17:51:35' is local (PDT); start_at is 00:51 UTC the
        next day — seven hours apart, the wearable's convention. A free
        cross-check."""
        self.assertEqual(
            plaud._parse_utc(self.DESKTOP["start_at"]) // 1000 % 86400,
            (0 * 3600 + 51 * 60 + 35))

    def test_the_software_serial_is_start_at_in_milliseconds(self):
        """The only test for "recorded by the software", and it proves itself —
        a real hardware serial would not equal the recording's start time."""
        self.assertEqual(int(self.DESKTOP["serial_number"]),
                         plaud._parse_utc(self.DESKTOP["start_at"]))

    def test_two_recordings_have_different_file_ids(self):
        """Count recordings by file_id, not serial."""
        a = plaud.to_events(META, SEGMENTS)
        b = plaud.to_events(self.DESKTOP, SEGMENTS)
        self.assertNotEqual({r[5]["file_id"] for r in a},
                            {r[5]["file_id"] for r in b})


class FileIdPrefixTest(unittest.TestCase):
    """2026-09-15: PLAUD added an `of_` prefix to file_id.

    One round worked; from the next one, round after round got 429 and no
    speech arrived for two days. The chain:

      prefix added -> needs_fetch compares the API's id with the known_ids
      recovered from uids -> no file matches -> every file judged new -> a
      detail request per file every round (normally a handful) -> quota burned,
      429 -> the exception reaches cmd_import's catch-all and the whole source
      continues -> collect() returns nothing, nothing is written -> the next
      round sees the same files again

    **It cannot climb out on its own**; waiting for the quota to reset does not
    help. So this is not a rate-limit problem, it is an id problem.

    More dangerous than the 429 is the path that did not blow up: a uid is
    `plaud:{file_id}:{n}`, and the prefixed one (`plaud:of_xxx:0`) **does not
    collide** with the stored `plaud:xxx:0`, so REWRITE's overwrite stops
    working. Had it been made to "run through", every old file would have been
    inserted again, doubling its events and polluting segment / tasks / claims.

    The fix normalises at the boundary: the detail request keeps the API's own
    id, while uids and the known-id comparison use the bare hex. uids become
    immune to how PLAUD's ids look.

    Stripping must check that what remains is 32 hex characters — an unknown
    shape is left alone to fail loudly, rather than stripped wrongly and two
    different files merged into one (silent corruption).
    """

    HEX = "42d4e7b33fffde3719ef41a14a49823d"          # 32 hex

    # ── normalisation itself ──────────────────────────────────────
    def test_strips_the_of_prefix(self):
        self.assertEqual(plaud._norm_id(f"of_{self.HEX}"), self.HEX)

    def test_bare_hex_is_unchanged(self):
        self.assertEqual(plaud._norm_id(self.HEX), self.HEX)

    def test_a_future_prefix_strips_too(self):
        """The whole point of matching any prefix: the next change needs no code."""
        self.assertEqual(plaud._norm_id(f"od_{self.HEX}"), self.HEX)
        self.assertEqual(plaud._norm_id(f"file_{self.HEX}"), self.HEX)

    def test_an_unknown_shape_is_left_alone(self):
        """The safety net. A wrong strip merges two files, far worse than an error."""
        for weird in ("a", "F1", "of_short", "of_" + "z" * 32,
                      "of_" + self.HEX[:31], "OF_" + self.HEX, "_" + self.HEX):
            self.assertEqual(plaud._norm_id(weird), weird, weird)

    def test_empty_values_do_not_raise(self):
        self.assertIsNone(plaud._norm_id(None))
        self.assertEqual(plaud._norm_id(""), "")

    # ── needs_fetch: compare normalised, return the original ──────
    def test_a_known_file_with_a_prefix_is_not_fetched_again(self):
        """This is what caused the 429s directly."""
        listing = [{"id": f"of_{self.HEX}"}]
        self.assertEqual(plaud.needs_fetch(listing, {self.HEX}), [])

    def test_files_to_fetch_keep_the_api_id(self):
        """The detail request's URL uses this value — stripped, it 404s."""
        listing = [{"id": f"of_{self.HEX}"}]
        self.assertEqual(plaud.needs_fetch(listing, set()), [f"of_{self.HEX}"])

    def test_mixed_old_and_new_formats(self):
        other = "88dbd79ee0a3b9fec4f1b6f2e158e50a"
        listing = [{"id": f"of_{self.HEX}"}, {"id": f"of_{other}"}]
        self.assertEqual(plaud.needs_fetch(listing, {self.HEX}), [f"of_{other}"])

    # ── uids: the prefix must not leak in ──────────────────────────
    def test_transcript_uid_ignores_the_prefix(self):
        """The same uid before and after, or REWRITE fails and history is inserted again."""
        before = plaud.to_events(META, SEGMENTS)
        after = plaud.to_events({**META, "id": f"of_{META['id']}"}, SEGMENTS)
        self.assertEqual([r[0] for r in after], [r[0] for r in before])
        self.assertTrue(all(r[0].startswith(f"plaud:{META['id']}:") for r in after))

    def test_mark_uid_ignores_the_prefix(self):
        marks = [{"timestamp": 1930000, "mark_type_string": "hardware",
                  "title": "t", "content": "c"}]
        before = plaud.mark_events(META, marks)
        after = plaud.mark_events({**META, "id": f"of_{META['id']}"}, marks)
        self.assertEqual([r[0] for r in after], [r[0] for r in before])

    def test_payload_file_id_is_normalised_too(self):
        """Recordings are counted by file_id downstream; two spellings count one as two."""
        rows = plaud.to_events({**META, "id": f"of_{META['id']}"}, SEGMENTS)
        self.assertEqual({r[5]["file_id"] for r in rows}, {META["id"]})


class SerialFallbackTest(unittest.TestCase):
    """2026-09-15's second regression: PLAUD returns serial_number as null.

    Shipped the same day as the `of_` prefix: None in the listing **and** the
    detail, with no replacement field (no device_sn, sn or device). The API
    will never give this value again.

    Why it cannot just be dropped: the serial is the only way to tell a
    recording made by the desktop software (its serial equals start_at in ms)
    from one made by the wearable (a hardware serial). The wearable hears the
    room and the desktop app captures system audio, so their echo rates differ
    and recalibrating the echo threshold needs them apart.

    With the API silent, **the stored copy is the only one**. The RECHECK_DAYS
    window re-fetches the last 7 days of details every round and REWRITE
    overwrites, so without a fallback each pass over an old recording blanks
    its serial — within a week all of them, with no error.

    (Measured: the first import after the `of_` fix blanked the serial on every
    row in the recheck window. Not a word of the text changed — exactly the
    kind of silent corruption that raises nothing.)

    Rule: **a non-empty value from the API wins (in case it comes back); an
    empty one falls back to the stored copy.**
    """

    HEX = "42d4e7b33fffde3719ef41a14a49823d"
    SERIAL = "8820B50000000000"

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.db")
        self.con = db.connect(self.path)
        db.insert(self.con, plaud.to_events(
            {**META, "id": self.HEX, "serial_number": self.SERIAL}, SEGMENTS))

    def tearDown(self):
        self.con.close()

    # ── reading known serials back from the database ──────────────
    def test_reads_serial_back_by_file_id(self):
        self.assertEqual(plaud._known_serials(self.con).get(self.HEX), self.SERIAL)

    def test_an_empty_serial_is_not_mapped(self):
        """An empty string is not a known value and must not fill another empty one."""
        db.insert(self.con, plaud.to_events(
            {**META, "id": "0" * 32, "serial_number": ""}, SEGMENTS))
        self.assertNotIn("0" * 32, plaud._known_serials(self.con))

    # ── collect()'s fallback ───────────────────────────────────────
    def _collect(self, api_serial, known):
        """One collect() run: the listing returns api_serial, the database knows known."""
        def fake(url, *a, **k):
            if "files/?" in url:
                return {"data": [{"id": f"of_{self.HEX}",
                                  "serial_number": api_serial,
                                  "start_at": META["start_at"],
                                  "created_at": META["created_at"]}]}
            return {"id": f"of_{self.HEX}", "start_at": META["start_at"],
                    "duration": META["duration"], "serial_number": None,
                    "source_list": [{"data_type": plaud.TRANSCRIPT_BLOCK,
                                     "data_content": json.dumps(SEGMENTS)}],
                    # A finished transcript always comes with a summary block.
                    # Without it is_generating reads the file as being
                    # re-generated and skips it this round — real behaviour, so
                    # the fixture has to have the real shape.
                    "note_list": [{"data_type": "auto_sum_note"}]}

        with mock.patch.object(plaud.plaud_auth, "authed_get_json", fake), \
             mock.patch.object(plaud.plaud_auth, "ensure_access_token",
                               lambda *a, **k: "t"), \
             mock.patch.object(plaud, "_known_file_ids", lambda con: set()), \
             mock.patch.object(plaud, "_known_serials", lambda con: known), \
             mock.patch.object(plaud.db, "connect", lambda *a, **k: mock.MagicMock()):
            return plaud.collect()

    def test_a_null_from_the_api_falls_back_to_the_stored_value(self):
        """This is what blanked the serials."""
        rows = self._collect(None, {self.HEX: self.SERIAL})
        self.assertTrue(rows)
        self.assertEqual({r[5]["serial"] for r in rows}, {self.SERIAL})

    def test_a_non_empty_api_value_wins(self):
        """If PLAUD ever restores it, the new value must replace the stored one."""
        rows = self._collect("NEW123", {self.HEX: self.SERIAL})
        self.assertEqual({r[5]["serial"] for r in rows}, {"NEW123"})

    def test_a_new_file_without_a_fallback_does_not_raise(self):
        rows = self._collect(None, {})
        self.assertTrue(rows)
        self.assertEqual({r[5]["serial"] for r in rows}, {""})

    def test_fallback_looks_up_the_normalised_id(self):
        """Stored keys are bare hex and the API sends of_ — unnormalised, it never matches."""
        rows = self._collect(None, {self.HEX: self.SERIAL})
        self.assertEqual({r[5]["serial"] for r in rows}, {self.SERIAL})
