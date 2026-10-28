"""PLAUD speech importer — "when was what said", on the same timeline.

Source: https://platform.plaud.ai/developer/api/open/third-party/*
(the official third-party OAuth API, the one `@plaud-ai/cli` and the official MCP use)

Why it belongs here: every other source describes what you did at a screen.
Meetings, meals, walks, conversations are a hole in the event stream — and they
are often the densest part of a day. Speech is the only content-level evidence
that reaches them.

⚠️ Don't expect it to fill the timeline. In one measured 56-minute recording,
1.6 minutes were speech (3%). This source gives **sparse points with a very
high signal**, not coverage.

One event row per speech segment, not merged at import. Merging ("this stretch
was a meeting") is segment.py's job at analysis time, so changing the policy
needs no re-import. That is the rule at the top of db.py.

First authorisation: npm i -g @plaud-ai/cli && plaud login
"""

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import urllib.error

from .. import db, net, plaud_auth

SOURCE = "plaud"
# Something every normal day: this many days with nothing new means it has
# stopped, and STALE_HINT is where to look first (plugins/staleness.py).
STALE_DAYS = 2
STALE_HINT = ('Has the Plaud app synced recordings to the cloud? Then run `umbra import '
              '--only plaud` to see whether the sign-in expired (`plaud login` again).')
REQUIRES = ["file:~/.plaud/tokens.json"]


def check():
    """The token PLAUD's own CLI leaves behind (see plaud_auth). Said as what
    to run, not as a missing file — nobody knows where that file comes from."""
    ok = os.path.exists(plaud_auth.TOKEN_PATH)
    yield ("plaud token", ok, "" if ok else
           "not authorized yet — run: npm i -g @plaud-ai/cli && plaud login")
BASE = "https://platform.plaud.ai/developer/api"

# page_size is at least 10 and at most 100 (5 is refused with 422, measured)
PAGE = 100
MAX_PAGES = 100          # a backstop against a paging bug looping forever

# The raw transcript only. source_list also has transaction_polish (filler
# words cleaned by AI) and outline (a section outline), but transaction is the
# ground truth and was present in 41 of 41 files, while polish is not always
# generated. Switching is this one constant.
TRANSCRIPT_BLOCK = "transaction"

# Hardware marks: a short press of the NotePin S button mid-recording. Confirmed
# 2026-08-22 that this API **does** return them — undocumented, unused by the
# official CLI, not exposed by the MCP, but files with a press have two extra
# blocks in their details and files without do not.
#
# Content comes from high_light (PLAUD's own title plus itemised content). Since
# 2026-09-15 it no longer carries the moment; that now comes from mark_memo —
# see marks_of. mark_memo on its own is not taken: an upload that failed has
# mark_content "Highlighted. Available after transcription.", a placeholder with
# no information.
MARK_BLOCK = "high_light"
MEMO_BLOCK = "mark_memo"

# Files that reached the cloud within this many days have their details
# fetched again every time — transcription finishes **after** sync, and the
# list endpoint has no field saying whether it has (see needs_fetch). Seven days
# is a guess, but the cost is bounded: a week's recordings are a handful of
# extra requests a round.
RECHECK_DAYS = 7

# The fingerprint sweep. **Transcripts are replaced in place**, so "fetched once,
# never look again" does not hold: press re-generate in the app and the content
# is swapped, and the API carries no version marker (the listing's six fields
# have no status and no updated_at).
#
# Beyond 30 days an updated recording is not worth going back for.
#
# This cannot be done by raising RECHECK_DAYS to 30: an hourly voice round over a
# month of files is thousands of detail requests a day, and far fewer have burned
# the quota. So this tier is fetched **once a day**, throttled per file in
# plaud_files.checked_ms.
SWEEP_DAYS = 30
SWEEP_EVERY_MS = 86400_000

# How long "generating" may hold a file back. Still empty after this, it is
# imported anyway.
#
# The test is "note_list empty", and that shape has a cause that is **not
# temporary**: automatic summaries switched off in the app. Then every recording
# matches forever — skipping without a limit means silently losing everything,
# without a single log line.
#
# Six hours is conservative: a real re-generation was measured filling in within
# half an hour. One sample, so an order of magnitude of headroom;
# generating_since records each observed duration so this can be tightened later.
GENERATING_GRACE_MS = 6 * 3600_000

# This source's rows are overwritten, not INSERT OR IGNOREd. The uid is
# plaud:{file_id}:{n} and never changes, so a re-fetched, complete transcript
# would be ignored outright and the recheck window would be pointless.
#
# Overwriting blindly is safe here: collect() **returns only the files it
# actually fetched this time**; anything outside needs_fetch was not touched,
# so there is no write amplification. A source like chrome, which returns
# 80,000 rows on every collect, could not do this.
REWRITE = True

# One recording = one group of rows (`plaud:{file_id}:{n}`), and **the group's
# size changes** — re-generate in the app and the new version may merge dozens of
# segments.
#
# REWRITE alone is not enough: an overwrite touches only the uids the new batch
# brings, so the old version's tail is never touched and stays in the store. And
# those rows are not garbage, they are **duplicates** — the same sentence stored
# again under a shifted index, at the same moment; dedupe cannot remove them,
# COUNT(*) doubles, and every row looks fine on its own.
#
# GROUPED makes cmd_import use db.replace_groups: the group is replaced whole.
GROUPED = True


# PLAUD's file_id gained an `of_` prefix on 2026-09-15 (`of_b842…`); before that
# it was a bare 32-character hex. uids **must** store the normalised form.
#
# Not doing so was measured once, and it fails at two levels:
#   surface — needs_fetch compares the API's id with the known_ids recovered from
#             uids, so with the prefix every file looked new: a detail request per
#             file every round instead of a handful, the quota burned, 429. The
#             exception made the whole source `continue`, nothing was written, and
#             the next round saw the same files again. **A deadlock that waiting
#             for the quota to reset does not break.**
#   deeper  — worse: `plaud:of_xxx:0` does not collide with the stored
#             `plaud:xxx:0`, so REWRITE's overwrite silently stops working and
#             every old file would be inserted a second time, doubling its events.
#
# Strip only the shape "lowercase prefix + exactly 32 lowercase hex". Anything
# unrecognised is left alone — a wrong strip merges two different files into one,
# and silent corruption is far worse than a loud failure.
_ID_RE = re.compile(r"^[a-z]+_([0-9a-f]{32})$")


def _norm_id(fid: str | None) -> str | None:
    """The API's file_id -> the stable id stored in uids. Unknown shapes pass through."""
    if not fid:
        return fid
    m = _ID_RE.match(fid)
    return m.group(1) if m else fid


def _parse_utc(s: str | None) -> int | None:
    """ISO string -> UTC epoch milliseconds.

    ⚠️ The API returns '2026-08-09T15:05:50' — **no time-zone suffix, but it is
    UTC**. A bare fromisoformat() gives a naive datetime, read as local time
    off by hours, and nothing raises.

    Not a guess: checked against 41 real recordings. Assuming start_at is local
    time, 9 of them "synced before they finished recording", which is
    impossible; assuming UTC, none.
    """
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.UTC)
    return int(d.timestamp() * 1000)


def transcript_of(detail: dict) -> list[dict]:
    """The transcript segments from a file's details.

    Nothing found is not an error but **the normal in-between state every
    import meets** — the recording has synced and the cloud has not finished
    transcribing (or Generate / AutoFlow has not been set up in the app). The
    next run picks it up.
    """
    for block in detail.get("source_list") or []:
        if block.get("data_type") != TRANSCRIPT_BLOCK:
            continue
        raw = block.get("data_content")
        if not raw:
            # Content may also sit behind a presigned URL instead of inline
            # (inline in every file measured so far, but the CLI has this
            # branch, so large files presumably use data_link)
            link = block.get("data_link")
            if not link:
                return []
            raw = net.get_text(link)
        try:
            segs = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        return segs if isinstance(segs, list) else []
    return []


def _block_text(detail: dict, data_type: str) -> str | None:
    """One block's text; None when the block is absent.

    The same branch the transcript hit: content may sit behind a presigned URL.
    Measured, both mark blocks **do** use data_link; data_content is always
    empty.
    """
    for block in (detail.get("note_list") or []) + (detail.get("source_list") or []):
        if block.get("data_type") != data_type:
            continue
        raw = block.get("data_content")
        if not raw:
            link = block.get("data_link")
            return net.get_text(link) if link else ""
        return raw
    return None


def _md_sections(md: str) -> list[tuple[str, str]]:
    """"## title\\n• bullets…" -> [(title, bullet text)]."""
    out: list[tuple[str, str]] = []
    for part in ("\n" + md).split("\n## ")[1:]:
        title, _, body = part.partition("\n")
        out.append((title.strip(), body.strip()))
    return out


def marks_of(detail: dict) -> list[dict]:
    """The hardware marks from a file's details.

    No such block is the usual case — no press that day, or transcription not
    finished (before it, only mark_memo's placeholder). As with transcript_of,
    not finding any is not an error.

    ## Two formats

    Before 2026-09-15 high_light was a JSON list, each item with its own
    timestamp / title / content. Since then it is Markdown ("## title" plus
    bullets) and **carries no moment**; only mark_memo has it (its mark_content
    is the title, or the placeholder for a failed upload). The old code's
    json.loads failed and returned [], so a month of marks never reached the
    store, with no error.

    Pairing the new format: when both sides have the same count they pair in
    order (measured to agree, and a failed upload still has its section in the
    Markdown); otherwise only a mark whose mark_content matches a section title
    is kept. Forcing a pair would hang A's content on B's moment — better one
    mark fewer.
    """
    hl = _block_text(detail, MARK_BLOCK)
    if hl:
        try:
            marks = json.loads(hl)
        except (json.JSONDecodeError, TypeError):
            marks = None
        if isinstance(marks, list):
            return [m for m in marks if isinstance(m, dict)]
    sections = _md_sections(hl or "")

    try:
        memo = json.loads(_block_text(detail, MEMO_BLOCK) or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(memo, list):
        return []
    memo = sorted((m for m in memo
                   if isinstance(m, dict) and m.get("timestamp") is not None),
                  key=lambda m: m["timestamp"])

    def mark(m, title, content):
        return {"timestamp": m["timestamp"], "title": title, "content": content,
                "mark_type_string": m.get("mark_type_string") or ""}

    if len(memo) == len(sections):
        return [mark(m, t, c) for m, (t, c) in zip(memo, sections)]
    by_title = dict(sections)
    return [mark(m, m["mark_content"], by_title[m["mark_content"]])
            for m in memo if m.get("mark_content") in by_title]


def is_generating(detail: dict) -> bool:
    """Is this recording **being re-generated** (the app shows Generating…)?

    The API has no status field, so it can only be read from the blocks' shape.
    Measured:

        generating   source_list=[transaction, …]   note_list=[]
        normal       source_list=[transaction, …]   note_list=[auto_sum_note, …]

    Every normal file sampled had an auto_sum_note; the generating one had none,
    and it filled in within half an hour — so "skip until the next round" costs
    little, while importing a half-finished transcript costs a wasted infer and a
    wrong page for a while.

    The test is pinned to transaction, not "source_list non-empty": a file not
    yet transcribed may hold only a mark_memo placeholder, which says someone
    pressed the button and nothing about transcription.

    **Both lists empty is not generating** — that is "should have been
    transcribed and was not". Mixing them up would let a really broken file be
    skipped forever as "still generating".
    """
    has_transcript = any(b.get("data_type") == TRANSCRIPT_BLOCK
                         for b in detail.get("source_list") or [])
    return has_transcript and not (detail.get("note_list") or [])


def fingerprint(segments: list[dict]) -> str:
    """The transcript's content fingerprint. Changed = the transcript was replaced.

    Text only, no speaker — renaming a speaker in the app changes no content and
    must not read as "re-transcribed" and re-infer whole days.
    """
    body = "".join((s.get("content") or s.get("topic") or "").strip()
                   for s in sorted(segments,
                                   key=lambda s: s.get("start_time") or 0))
    return hashlib.md5(body.encode("utf-8")).hexdigest()


def mark_events(meta: dict, marks: list[dict]) -> list[tuple]:
    """Marks -> event rows.

    `timestamp` is in **the same coordinates** as a transcript segment's
    `start_time` (milliseconds from the start of the recording), so a mark lines
    up with the exact sentences around it without further arithmetic.

    No absolute start skips the whole file — the transcript's rule: a record
    that cannot be placed on the timeline does not appear on it.
    """
    start = _parse_utc(meta.get("start_at"))
    if start is None:
        return []
    file_id = _norm_id(meta.get("id"))
    rows = []
    for i, m in enumerate(sorted(marks, key=lambda x: x.get("timestamp") or 0)):
        off = m.get("timestamp")
        if off is None:
            continue
        title = (m.get("title") or "").strip()
        content = (m.get("content") or "").strip()
        if not (title or content):
            continue        # not transcribed yet, only the placeholder — the recheck window catches it
        rows.append((
            f"plaud:mark:{file_id}:{i}", start + int(off), None, SOURCE, "mark",
            {"title": title, "content": content,
             # Tells a hardware press from one added in the app, for later
             "mark_source": m.get("mark_type_string") or "",
             "serial": meta.get("serial_number") or "",
             "file_id": file_id},
        ))
    return rows


def to_events(meta: dict, segments: list[dict]) -> list[tuple]:
    """One recording's transcript -> event rows.

    A segment's start_time / end_time are **wall-clock milliseconds from the
    start of the recording**, and silence takes up room (that 56-minute
    recording has a 3,270-second gap; the second sentence's offset is 3,327,290,
    not 56,690). So absolute time = start_at + offset, and nothing more.
    """
    start = _parse_utc(meta.get("start_at"))
    if start is None:
        # No absolute start means no place on the timeline. Better to skip the
        # file than to pass off created_at (the sync time, measured up to 15
        # days after the recording) as when it was said.
        return []

    file_id = _norm_id(meta.get("id"))
    rows = []
    for i, seg in enumerate(sorted(segments,
                                   key=lambda s: s.get("start_time") or 0)):
        text = (seg.get("content") or seg.get("topic") or "").strip()
        if not text:
            continue                  # a segment with no text is no evidence
        a = seg.get("start_time") or 0
        b = seg.get("end_time") or a
        rows.append((
            f"plaud:{file_id}:{i}", start + a, max(0, b - a), SOURCE, "speech",
            {"text": text,
             "speaker": seg.get("speaker") or "",
             # ⚠️ serial_number **is not a device id**. Measured 2026-08-24: the
             # wearable reports a hardware serial number, while a recording
             # made by Plaud Desktop reports a value that, read as milliseconds,
             # equals that record's own start_at.
             # The software side gets a new value per recording.
             #
             # Kept anyway: it is currently the only field that identifies a
             # recording as the software's (serial == start_at in ms ⇒ software,
             # and the test proves itself: a real serial number does not equal
             # the start of a recording). Re-tuning the echo threshold will need
             # it — the wearable hears the room, Desktop captures system audio,
             # and their echo rates are not the same thing.
             #
             # But count recordings by file_id, never by this.
             #
             # The API has returned null here since 2026-09-15, so the value is
             # usually one collect() filled in from the database — see
             # _known_serials.
             "serial": meta.get("serial_number") or "",
             "file_id": file_id},
        ))
    return rows


def needs_fetch(listing: list[dict], known_ids: set,
                now_ms: int | None = None,
                checked: dict | None = None) -> list[str]:
    """Which files need their details fetched.

    db.since_for() is deliberately **not** used. It takes max(events.ts) — when
    the recording happened — and this source's arrival order is unrelated to
    event order: across 41 recordings the upload delay was median 40 hours, P90
    240, maximum 362; 16 of 41 arrived later than 72 hours (OVERLAP_MS). An
    incremental window on recording time misses late recordings for good, with
    no error — the database is just quietly missing a piece.

    The list is cheap (a few requests), details are expensive (one per file), so
    the full list is fetched and the gate sits on details. All-day recording is
    about 1,100 files a year, which the list handles easily.

    **A file that reached the cloud recently is re-fetched even if imported**
    (within RECHECK_DAYS). Transcription finishes after sync: once one segment
    has been seen the file is in known for good, and a transcript completed
    later would never be looked at. The list only has created_at / duration /
    id / name / serial_number / start_at — no transcription state, no updated
    time — so whether the cloud changed cannot be told, and time is the fallback.

    ("Not transcribed at all" already worked: 0 events → no uid carries this id
    → not in known → fetched again next time. Only "fetched but incomplete"
    needed this.)

    The anchor is created_at, not start_at: by recording time, a recording two
    weeks late is outside the window the moment it arrives — and that is
    exactly the one most likely not to be transcribed yet.

    ## The second tier: a 30-day fingerprint sweep

    All of the above only handles "transcription after sync". It assumes
    transcription happens once — and that was measured false: press re-generate
    in the app and the transcript is swapped (fewer segments, a different text
    hash, diarization re-done), with no version marker in the API.

    So files outside the hot window are still checked, just **once a day** —
    fetching a month of files every round would burn the quota.

    `checked` is file_id -> when its details were last fetched. Without it this
    falls back to the hot window alone — debugging scripts and older tests
    should not have to build a table first.
    """
    hot, sweep = split_fetch(listing, known_ids, now_ms, checked)
    return hot + sweep


def split_fetch(listing: list[dict], known_ids: set, now_ms: int | None = None,
                checked: dict | None = None) -> tuple[list[str], list[str]]:
    """Split the files to fetch into two tiers: hot (fetching) and sweep
    (re-checking).

    Apart because **they fail with different meaning**. Not getting the hot tier
    = today's recordings missing = real missing data, which must propagate so a
    person sees it. Not getting the sweep only means "this old recording went
    unchecked today" — check it tomorrow. Measured: a round of a few hundred
    detail requests ran into a 429 that net.get_json's backoff could not wait
    out; together, one burned quota would also throw away the day's new
    recordings.

    The hot tier comes first, so the quota is spent on fetching first.
    """
    now = now_ms if now_ms is not None else int(dt.datetime.now(dt.UTC)
                                                .timestamp() * 1000)
    fresh = now - RECHECK_DAYS * 86400_000
    sweep_from = now - SWEEP_DAYS * 86400_000
    hot: list[str] = []
    sweep: list[str] = []
    for f in listing:
        fid = f.get("id")
        if not fid:
            continue
        # Compare the normalised id, return the API's own — the detail
        # request's URL needs the original.
        if _norm_id(fid) not in known_ids:
            # The recheck window only widens the "already imported" gate; it must
            # never become a new filter: with delays up to 362 hours, filtering
            # by time loses late recordings for good.
            hot.append(fid)
            continue
        landed = _parse_utc(f.get("created_at")) or _parse_utc(f.get("start_at"))
        # An imported file with no known time is not re-fetched — picking it up
        # every round would become a permanent fixed cost.
        if landed is None:
            continue
        if landed >= fresh:                  # hot: every round
            hot.append(fid)
        elif checked is not None and landed >= sweep_from:
            # The sweep: once a day. Never checked (just upgraded) counts as due.
            last = checked.get(_norm_id(fid))
            if last is None or now - last >= SWEEP_EVERY_MS:
                sweep.append(fid)
    return hot, sweep


def _listing() -> list[dict]:
    """Every page. The list comes newest created_at first (measured), but
    nothing here depends on that order.

    Through authed_get_json rather than building the Bearer header here: each
    request fetches the token afresh, so after a forced refresh the remaining
    pages use the new one at once. 900 detail requests can run past the
    server's revocation time, and a token going stale **midway** is routine,
    not an accident.
    """
    out, page = [], 1
    while page <= MAX_PAGES:
        data = plaud_auth.authed_get_json(
            f"{BASE}/open/third-party/files/?page={page}&page_size={PAGE}")
        batch = data.get("data") or []
        out.extend(batch)
        if len(batch) < PAGE:
            break
        page += 1
    return out


def _known_file_ids(con) -> set:
    """The file_ids already imported.

    Read back from the uid prefix, not from the payload — the uid is
    'plaud:{file_id}:{i}', so no index on the payload is needed.
    """
    rows = con.execute(
        "SELECT DISTINCT uid FROM events WHERE source=?", (SOURCE,))
    ids = set()
    for (uid,) in rows:
        parts = uid.split(":")
        if len(parts) >= 3:
            ids.add(parts[1])
    return ids


def _known_serials(con) -> dict:
    """file_id -> the serial already stored in the database.

    Since 2026-09-15 PLAUD returns serial_number as null — in the listing and in
    the detail alike, with no replacement field (no device_sn, sn or device).
    The API will never give it again, so **the stored copy is the only one**.

    Without this fallback the loss is silent: the RECHECK_DAYS window re-fetches
    the last 7 days of details every round and REWRITE overwrites, so each pass
    over an old recording blanks its serial. Within a week every one is blank,
    not a word of the text has changed, and nothing reports an error.

    It is worth the trouble because the serial is the only way to tell a
    recording made by the desktop software from one made by the wearable. The
    wearable hears the room and the desktop app captures system audio, so their
    echo rates differ and recalibrating the echo threshold needs them apart.
    """
    rows = con.execute(
        "SELECT DISTINCT json_extract(payload, '$.file_id'), "
        "       json_extract(payload, '$.serial') "
        "FROM events WHERE source=?", (SOURCE,))
    return {fid: serial for fid, serial in rows if fid and serial}


def collect(since_ms: int | None = None) -> list[tuple]:
    """Fetch transcripts of new recordings.

    since_ms is **deliberately unused** in this source (see needs_fetch). It
    stays in the signature so cmd_import can call every importer the same way.

    PlaudAuthExpired is not swallowed: an expired authorisation propagates to
    cmd_import's AUTH_EXPIRED branch and stops the whole import (the same policy
    as WHOOP). Network errors propagate too, into each source's own fault
    isolation.
    """
    # Make sure there is a token before touching the database — if it was
    # never authorised, say so plainly instead of opening a connection and
    # reading every uid for nothing.
    plaud_auth.ensure_access_token()

    # Its own connection: cmd_import passes no con into collect, and this
    # source must know which files are already imported to skip most detail
    # requests. WAL + busy_timeout already allow many readers and one writer,
    # so the resident collector does not collide with it.
    con = db.connect()
    try:
        known = _known_file_ids(con)
        serials = _known_serials(con)
        checked, prev = _checked_map(con), _generating_map(con)
    finally:
        con.close()

    rows: list[tuple] = []
    listing = _listing()
    by_id = {f["id"]: f for f in listing if f.get("id")}
    now = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    seen: list[tuple] = []

    hot, sweep = split_fetch(listing, known, checked=checked)
    for fid in hot + sweep:
        try:
            detail = plaud_auth.authed_get_json(
                f"{BASE}/open/third-party/files/{fid}")
        except urllib.error.HTTPError as e:
            # A 429 is acceptable in the **sweep**: that tier re-checks data
            # already stored, so unchecked today means checked tomorrow. Losing
            # the round's new recordings over it would be backwards.
            #
            # The hot tier still raises — that is real missing data, and
            # cmd_import must record a failure that doctor can see. (Hot comes
            # first, so by now it is already fetched.)
            if e.code == 429 and fid in sweep:
                break
            raise
        except urllib.error.URLError:
            # A dropped line that outlasted net's retries is the same: the sweep
            # stops, the hot tier's new recordings are kept; a drop in the hot
            # tier itself still raises.
            if fid in sweep:
                break
            raise
        # The details also have start_at/duration, but the list always does;
        # it is the safer one.
        meta = {**by_id.get(fid, {}), **{k: v for k, v in detail.items()
                                         if k in ("start_at", "duration", "id")
                                         and v is not None}}
        # A non-empty value from the API wins (in case PLAUD ever restores it);
        # only an empty one falls back to the stored copy — the other way round,
        # a restored value could never overwrite the old one.
        if not meta.get("serial_number"):
            meta["serial_number"] = serials.get(_norm_id(fid), "")
        segs = transcript_of(detail)
        gen = is_generating(detail)
        nid = _norm_id(fid)
        # When it was **first seen** empty. Recording this rather than "empty
        # for how long": the latter needs an update every round and breaks when
        # one is missed; the former is a fact however many rounds are missed.
        since = (prev.get(nid) or now) if gen else None
        # Recorded every round, **including the files skipped** — generating is
        # itself what is being watched.
        seen.append((nid, {"checked_ms": now,
                           "fp": fingerprint(segs) if segs else None,
                           "generating_since": since}))
        if gen and now - (since or now) < GENERATING_GRACE_MS:
            # A half-finished transcript stays out: importing it wastes an infer,
            # it has to be imported again once done, and the page is wrong in
            # between — with every step exiting 0.
            #
            # Held only for GENERATING_GRACE_MS. Held for good, "automatic
            # summaries switched off in the app" — which matches **forever** —
            # would silently lose everything.
            continue
        rows.extend(to_events(meta, segs))
        rows.extend(mark_events(meta, marks_of(detail)))

    if seen:
        con = db.connect()
        try:
            _record(con, seen)
        finally:
            con.close()
    return rows


def _checked_map(con) -> dict:
    """file_id -> when its details were last fetched. A database that has no
    plaud_files table yet reads as empty."""
    try:
        return {fid: ms for fid, ms in con.execute(
            "SELECT file_id, checked_ms FROM plaud_files")}
    except sqlite3.OperationalError:
        return {}


def _generating_map(con) -> dict:
    """file_id -> when note_list was first seen empty. Tolerates an old
    database the same way."""
    try:
        return {fid: ms for fid, ms in con.execute(
            "SELECT file_id, generating_since FROM plaud_files "
            "WHERE generating_since IS NOT NULL")}
    except sqlite3.OperationalError:
        return {}


def _record(con, seen: list[tuple]) -> None:
    """Write this round's observations to plaud_files.

    generating_since goes back to NULL once generation finishes — otherwise a
    recording that recovered long ago keeps "empty since months ago" for good.
    """
    con.executemany(
        "INSERT INTO plaud_files (file_id, checked_ms, fp, generating_since) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(file_id) DO UPDATE SET "
        "checked_ms=excluded.checked_ms, fp=excluded.fp, "
        "generating_since=excluded.generating_since",
        [(fid, o["checked_ms"], o["fp"], o["generating_since"]) for fid, o in seen])
    con.commit()


# ── How this source appears downstream ────────────────────────
# See umbra/registry.py for the full protocol.

LANES = [{"key": "talk", "kind": None,
          "origin": ("PLAUD recorder",
                     "transcript pieces; time = recording start + offset")}]

# dur_ms here is how long one utterance took, given by the ASR's segment
# boundaries — nothing like "how long a tab lived". Distrusting it would collapse
# a 56-second remark into an instant on the timeline.
DURATION = "trusted"


def _rec(e) -> str:
    """Short code for a recording — the last four of file_id.

    file_id rather than serial_number: the desktop app's serial is unique per
    recording (it equals start_at in milliseconds), so using it as a device
    identifier is wrong.

    The full string is 32 hex characters, which is noise to both the model and a
    human; four is enough to tell two apart. A missing file_id is recorded as
    '?': it **must** be recorded, or "two recordings overlap here" cannot be
    detected, and then not one tag appears — without an error.
    """
    return (e.get("file_id") or "?")[-4:]


def render(e) -> str | None:
    t = (e.get("text") or "").strip()
    if not t:
        return None
    # Transcripts are the most valuable thing at this layer; not one character is
    # truncated. The prefix is added only when two devices genuinely appear in
    # the same stretch (_multi_rec is computed by digest.render). Adding it
    # unconditionally would change the digest of every historical day and trigger
    # a full re-inference.
    tag = f"[{_rec(e)}] " if e.get("_multi_rec") else ""
    return f"🗣 {tag}{e.get('speaker') or '?'}: {t}"


def classify(e) -> str | None:
    """Talking outranks coding on the ladder but sits below the containers.

    Note it is deliberately **not** treated as "somebody is working this Mac":
    talking happens precisely when someone is away from the computer, and
    counting it as activity would report a contradiction for every slot spent in
    a meeting with the Mac untouched.
    """
    return "talk" if (e.get("text") or "").strip() else None


# ── Window rendering ──────────────────────────────────────────
#
# A window of this source's records, as one block of text for a model to read.
# It lives here because both things it does are this source's business and
# nobody else's: which records carry evidence, and merging the same sentence
# captured twice.
#
# The marker strings are a parameter rather than a constant. Two lanes read
# plaud windows and they keep their own wording on purpose — the rendered text
# is an inference cache key, so one lane rewording its separator must not
# invalidate the other's whole history.

# ── Two recordings of one sentence ────────────────────────────
# With the wearable and Plaud Desktop both running in a meeting, one sentence
# has two transcripts. The cost is not looks: it is half of context_max_lines'
# eight-line budget.
#
# The measure and the threshold were measured — on the first meeting where both
# tracks landed, 2026-08-24 (31.5 minutes; 160 sentences from the wearable +
# 158 from the software = 318).
#
# Containment rather than Jaccard: **the two tracks split sentences
# differently** as a rule. One cuts a sentence in two and the other does not,
# so the longer piece has extra, and Jaccard's union denominator punishes that
# heavily — measured, it missed more than half of the real duplicates.
#
# Containment also has the better reading: it is **how much of the dropped
# sentence survives in the one kept**. 1.0 means the short one lies wholly inside
# the long one and merging loses nothing. So the question is not "is it the same
# sentence" but "how much does merging lose":
#
#     threshold  left (of 318)  merged  content lost
#     0.7        234          84      4.0%
#     0.8        252          66      2.5%
#     0.9        296          22      0.2%   <- now
#     0.95       305          13      0.0%
#
# Compression and safety pull against each other. 0.9 removes only 7% of lines
# — **it does not solve the original problem of duplicates eating half the
# eight-line budget**. That is solved in transcript_excerpt by scaling the line
# budget by the number of recordings (deterministic, no text comparison). This
# only does the nearly lossless part.
DEDUPE_THRESHOLD = 0.9
DEDUPE_MIN_CHARS = 8  # shorter than this (whitespace removed) never merges
# The time gate is in **seconds**. Measured 2026-08-24: the two tracks align
# within 1.4 s (seven identical anchor sentences, median +0.8 s) — both are
# start_at + segment offset. The first version used "the same minute", which
# produced 918 candidate pairs across tracks, very few real; a confirmed false
# merge was 16.4 s apart at containment 0.83 — similarity could not stop it, the
# time gate did. ±5 s leaves 3.5× margin over the measured 1.4.
DEDUPE_NEAR_MS = 5000


def same_utterance(a: str, b: str) -> float:
    """How much two texts look like the same sentence. 0..1.

    **Containment (how much of the shorter one lies in the longer), not
    Jaccard.** The tracks split sentences differently as a rule; one cuts a
    sentence in two and the other does not, so the longer piece has extra, and
    Jaccard's union denominator punishes that — measured, it missed more than half
    of the real duplicates.
    The question is "is the short one wholly covered by the other", one way.

    Same direction and same 3-grams as echo_score; the only difference is that
    echo_score always takes the quote as numerator, and this takes the shorter.

    ⚠️ The measure **is only trustworthy on long enough samples**. A short
    sentence has few grams, and they scatter into any long one for a high score
    (a real false merge: a five-character "uh, I'm on it" against an unrelated long
    turn scored 0.75).
    The length gate is in dedupe_recordings — the measure answers "how alike",
    while "is this sample worth trusting" is another question, and mixing them
    makes both unclear.
    """
    from .. import analyzer
    ga, gb = analyzer.grams(a or ""), analyzer.grams(b or "")
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def _conf() -> dict:
    from .. import config
    return config.load()


def dedupe_threshold() -> float:
    """0 or less turns cross-recording merging off."""
    v = ((_conf().get("sources") or {}).get("plaud") or {}).get(
        "dedupe_threshold",
        (_conf().get("actions") or {}).get("dedupe_threshold"))
    return float(v) if isinstance(v, (int, float)) else DEDUPE_THRESHOLD


def dedupe_recordings(rows: list[dict], quote: str = "") -> tuple:
    """(rows kept, how many merged). rows look like {"m", "text", "file_id", ...}.

    Only rows from **different file_ids**, close in time, and alike as text are
    merged. The same thing twice in one recording is something said twice;
    merging it would rewrite the facts.

    Which one stays: one carrying the quote beats everything (it anchors the
    card; without it the excerpt has no centre and transcript_excerpt falls back
    to the middle); otherwise the longer one, since the shorter was usually cut.
    """
    bar = dedupe_threshold()
    if bar <= 0:
        return list(rows), 0
    q = (quote or "").strip()
    kept: list[dict] = []
    merged = 0
    for r in rows:
        if len(r["text"].replace(" ", "")) < DEDUPE_MIN_CHARS:
            kept.append(r)                    # too short to trust a match
            continue
        for i, k in enumerate(kept):
            if k.get("file_id") == r.get("file_id"):
                continue                      # the same recording: not a repeat, said again
            if len(k["text"].replace(" ", "")) < DEDUPE_MIN_CHARS:
                continue
            if abs(k["ts"] - r["ts"]) > DEDUPE_NEAR_MS:
                continue
            if same_utterance(k["text"], r["text"]) < bar:
                continue
            merged += 1
            k_has = bool(q) and q in k["text"]
            r_has = bool(q) and q in r["text"]
            if r_has and not k_has:
                kept[i] = r
            elif not k_has and len(r["text"]) > len(k["text"]):
                kept[i] = r
            break
        else:
            kept.append(r)
    return kept, merged


def render_window(events, a: int, b: int, lo_ms: int, ctx: int = 0,
                  marks=None, min_speakers: int = 0) -> str:
    """This source's records for [a, b), with ctx minutes either side.

    **An empty target returns an empty string.** With no speech in the window
    there is nothing to judge, so calling a model is waste — and it will not
    decline, it will reach into the context and answer about that instead.

    min_speakers drops windows with fewer than that many distinct voices. One
    person dictating to an agent is not addressing anyone, and measured, those
    windows yield nothing but travel plans. Counting takes the maximum within
    any single recording rather than the union across recordings: two tracks
    running at once would otherwise count one person as two, while counting
    labels alone would count a two-person meeting as one.
    """
    target, after_mark = marks or (TARGET_MARK, AFTER_MARK)
    rows = []
    for e in events:
        if e.get("source") != "plaud":
            continue
        text = (e.get("text") or "").strip()
        if not text:
            continue                    # a segment with no words is no evidence
        m = (e["ts"] - lo_ms) // 60000
        if m < a - ctx or m >= b + ctx:
            continue
        rows.append({"ts": e["ts"], "m": m, "text": text,
                     "speaker": e.get("speaker") or "?",
                     "file_id": e.get("file_id")})

    if min_speakers:
        per = {}
        for r in rows:
            if a <= r["m"] < b:
                per.setdefault(r["file_id"], set()).add(r["speaker"])
        if max((len(v) for v in per.values()), default=0) < min_speakers:
            return ""

    # The same sentence caught by two devices is fed once. Otherwise it is
    # extracted twice, and the dedupe key downstream hangs on the quote -- two
    # ASR passes word it differently, so that key does not catch it. Observed
    # 2026-08-24: one item arrived twice and needed dismissing twice.
    # A single-track window is unchanged byte for byte, which matters because
    # this text is a cache key.
    rows, _ = dedupe_recordings(rows)

    from .. import render as R
    before, inside, after = [], [], []
    for r in rows:
        row = f"{R.hm(r['m'])} [{r['speaker']}] {r['text']}"
        m = r["m"]
        (inside if a <= m < b else (before if m < a else after)).append((m, row))

    if not inside:
        return ""

    def fmt(rs):
        return [t for _, t in sorted(rs, key=lambda x: x[0])]

    out = fmt(before) + [target] + fmt(inside)
    if after:
        out += [after_mark] + fmt(after)
    return "\n".join(out)


TARGET_MARK = "───── the stretch you are judging starts here ─────"
AFTER_MARK = "───── context after that stretch ─────"
