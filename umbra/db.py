"""Umbra's only storage layer.

Design rule: store point events only, append-only, never UPDATE. Every
"interval" (09:12-09:47 writing code) is derived at analysis time and never
persisted. That way changing the segmentation algorithm needs no re-collection
and no data migration.
"""

import json
import os
import sqlite3

from . import goals, inference, paths, tz

DB_DIR = paths.data_dir()
DB_PATH = os.path.join(DB_DIR, "events.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    uid     TEXT PRIMARY KEY,   -- source + natural key; makes re-import idempotent
    ts      INTEGER NOT NULL,   -- UTC epoch milliseconds
    dur_ms  INTEGER,            -- duration the source itself reports
                                -- (Chrome visit_duration / knowledgeC session)
    source  TEXT NOT NULL,      -- chrome | claude | zsh | git | app
    kind    TEXT NOT NULL,      -- visit | prompt | command | commit | focus
    payload TEXT NOT NULL       -- JSON
);
CREATE INDEX IF NOT EXISTS idx_ts     ON events(ts);
CREATE INDEX IF NOT EXISTS idx_src_ts ON events(source, ts);

-- The outcome of each source's last import *attempt*.
-- ⚠️ This has to be kept separate from "the timestamp of the newest event":
--    git having no new commits for two days is normal, and in the events table
--    it looks exactly like the importer not having run for two days — but one
--    needs no action and the other is a fault. Reading only event times
--    misreports the first as stale and misses the second entirely.
--
--    Three different quantities; none of them substitutes for another:
--      last_run  last *attempt*        old = the schedule is not running
--      last_ok   last *success*        old = no new data is arriving
--      fails     consecutive failures  accumulating = stop treating it as a blip
--
--    With only last_run, one manual run that fails on permissions overwrites
--    the record of the schedule having succeeded (observed: the four sources
--    needing Full Disk Access), and several broken rounds in a row are
--    invisible (observed: WHOOP authorisation expiring, unnoticed for two).
CREATE TABLE IF NOT EXISTS imports (
    source   TEXT PRIMARY KEY,
    last_run INTEGER NOT NULL,   -- UTC epoch milliseconds
    ok       INTEGER NOT NULL,
    added    INTEGER,
    error    TEXT,
    last_ok  INTEGER,
    fails    INTEGER NOT NULL DEFAULT 0
);
"""

# viz's per-day cache. **Derived data**: dropping the whole table at any moment
# loses nothing, it simply regenerates.
# Defined here rather than in viz.py because viz imports db, and the reverse
# would be a circular import.
SCHEMA += """
CREATE TABLE IF NOT EXISTS viz_cache (
    day     TEXT PRIMARY KEY,
    fp      TEXT NOT NULL,      -- input fingerprint (events + inference tables
                                -- + code + whether raw is embedded)
    payload TEXT NOT NULL       -- this day's JSON, used verbatim on a hit
);
"""

# Progress of the import that is running now, or the last one. The page reads
# it; the terminal output it mirrors cannot be read by anything.
# Only the latest run is kept: this is a status display, not a history —
# `imports` above is the history.
SCHEMA += """
CREATE TABLE IF NOT EXISTS import_progress (
    run     INTEGER NOT NULL,   -- UTC epoch ms the run started; identifies it
    pos     INTEGER NOT NULL,   -- order sources are read in
    source  TEXT NOT NULL,
    state   TEXT NOT NULL,      -- next | reading | done | failed | aborted
    rows    INTEGER,            -- rows read, once done
    error   TEXT,
    updated INTEGER NOT NULL,
    PRIMARY KEY (run, source)
);
"""

# Which timezone you were in (tz.py). **Not a cache** — dropping it loses no raw
# event, but it loses which offset applies to which stretch of history, and
# every cross-timezone day changes shape again.
SCHEMA += tz.SCHEMA

# PLAUD files as observed across rounds, beyond any one event. Transcripts are
# replaced in place and the API has no status or updated_at, so "was this file
# re-transcribed" and "has it been generating for long" can only be answered by
# remembering what the last fetch saw (importers/plaud.py).
SCHEMA += """
CREATE TABLE IF NOT EXISTS plaud_files (
    file_id          TEXT PRIMARY KEY,   -- normalised (no of_ prefix)
    checked_ms       INTEGER NOT NULL,   -- details last fetched; throttles the sweep
    fp               TEXT,               -- transcript fingerprint; changed = re-transcribed
    -- When note_list was first seen empty; NULL once it fills in. "Since when"
    -- rather than "for how long": the latter needs an update every round and
    -- breaks when one is missed, the former survives any number of missed rounds.
    generating_since INTEGER
);
"""

# The inference tables are defined in the module that uses them, but they are
# **concatenated into the same SCHEMA constant**. Hanging them off connect()
# alone means a test that runs executescript(db.SCHEMA) builds a database
# missing a table — so unit tests pass and only the real thing breaks. That
# already happened once (migrate was tested in isolation and connect forgot to
# call it). There can be exactly one source for the schema.
SCHEMA += inference.SCHEMA
SCHEMA += goals.SCHEMA

# Generic Analyzer output and run markers (analyzer.py).
from . import analyzer as _analyzer      # noqa: E402
SCHEMA += _analyzer.SCHEMA

# Sink dispatch state (sink.py). Separate from analyzer output because it has
# a lifecycle: written once per attempt, updated when an answer arrives.
from . import sink as _sink              # noqa: E402
SCHEMA += _sink.SCHEMA

# ── Frozen history ────────────────────────────────────────────
#
# These eight tables belong to the three lanes that predate the Analyzer and
# Sink interfaces. Their definitions used to live in umbra/actions.py and
# umbra/claims.py, which db.py imported mid-load to assemble the one SCHEMA
# constant. That worked, but it meant those two modules could never be deleted
# without breaking db.connect() for every user on every command — and deleting
# them is exactly what moving the lanes into plugins does.
#
# They are frozen. New rows go to analyzer_rows and sink_dispatch; nothing is
# added here again. They stay because they hold results that cost money to
# produce — a verification report ran to a few dollars a day — and because reads
# union the two shapes, which needs the tables to exist even on a fresh install
# that has no legacy rows in them.
#
# Moving them here also removes the deferred-import fragility this file's own
# comment describes above. That was the price of keeping one SCHEMA; it is no
# longer being paid.

SCHEMA += """
-- Voice-extracted task proposals.
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,
    t           TEXT,               -- HH:MM, when it was said
    task        TEXT NOT NULL,      -- the task, in one sentence
    quote       TEXT NOT NULL,      -- the words themselves, for checking
    model       TEXT,
    status      TEXT NOT NULL DEFAULT 'proposed',
                -- proposed|dispatched|approved|dismissed
    result      TEXT,               -- the sink's receipt
    created_ms  INTEGER,
    type        TEXT,               -- question|errand|followup|idea, or unknown
    win_a       INTEGER,            -- the window it came from
    dispatch_id TEXT,               -- the destination's id for it
    verdict     TEXT,               -- ran|not_a_task|already_done
    verdict_note TEXT,              -- what you wrote when you tapped
    confidence  TEXT,               -- high|medium|low|unknown (the model's own)
    domain      TEXT,               -- work|personal|unknown (the model's)
    marked      INTEGER NOT NULL DEFAULT 0,  -- a device button was pressed nearby
    speakers    INTEGER,            -- distinct speakers in the same window
    echo_score  REAL,               -- overlap with dictation/clipboard at the time
    echo_text   TEXT                -- the matching text: why this counts as an echo
);
-- The dedupe key. It is what stops a dismissed proposal coming back on the next
-- scan and asking to be dismissed again.
-- On quote rather than task: task is the model's rewording and differs every
-- round, while quote is the transcript verbatim and is stable. An idempotency
-- key belongs on the deterministic side.
CREATE UNIQUE INDEX IF NOT EXISTS idx_prop_dedup ON proposals(day, quote);

-- Per-window input cache: identical text and a previous success means no call.
CREATE TABLE IF NOT EXISTS task_digests (
    day   TEXT NOT NULL,
    model TEXT NOT NULL,
    a_min INTEGER NOT NULL,
    n_seg INTEGER NOT NULL,     -- speech segments in the window
    ok    INTEGER NOT NULL DEFAULT 0,   -- has this window ever succeeded
    text  TEXT NOT NULL,        -- what was actually sent
    PRIMARY KEY (day, model, a_min)
);

-- Claims said out loud, and what verification made of them.
CREATE TABLE IF NOT EXISTS claims (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT NOT NULL,
    t          TEXT,                -- HH:MM
    win_a      INTEGER,             -- the window it came from
    speaker    TEXT,                -- the label within one recording
    claim      TEXT NOT NULL,       -- a claim that stands on its own
    quote      TEXT NOT NULL,       -- the words themselves, for checking
    kind       TEXT,                -- number|entity|rule|mechanism|comparison|other
    verdict    TEXT,                -- the model's first pass, from its own memory
    absolute   INTEGER NOT NULL DEFAULT 0,   -- has a threshold, date, exception or price
    why        TEXT,                -- the reason for that first pass; a lead, not a finding
    model      TEXT,
    status     TEXT NOT NULL DEFAULT 'new',  -- new|checking|checked|skipped
    check_task TEXT,                -- the destination's id for the verification
    report     TEXT,                -- the verification verbatim, with its sources
    card       TEXT,                -- the report compressed into what gets read
    attempt    INTEGER NOT NULL DEFAULT 0,   -- in the idempotency key, so a resend is not a duplicate
    created_ms INTEGER
);
-- Same reasoning as proposals: dedupe on the transcript, not on the rewording.
CREATE UNIQUE INDEX IF NOT EXISTS idx_claim_dedup ON claims(day, quote);

-- Whether a day's digest card has gone out. Its own table rather than a column
-- on claims, because it is one fact per day and hanging it on every claim stores
-- the same fact N times.
CREATE TABLE IF NOT EXISTS claim_cards (
    day        TEXT PRIMARY KEY,
    task       TEXT NOT NULL,
    created_ms INTEGER
);

-- Per-claim delivery. claim_cards can say a card went out that day but not
-- whether a claim arriving later made it into one — and recordings reach the
-- cloud up to a fortnight late, so delivery has to be tracked per claim or the
-- first card of a day seals it permanently.
CREATE TABLE IF NOT EXISTS claim_card_items (
    claim_id   INTEGER PRIMARY KEY,
    task       TEXT NOT NULL,
    created_ms INTEGER NOT NULL
);

-- "I have looked at this one", from the page's To check tab. Its own table
-- rather than a claim status: status is the lookup's lifecycle (queued, sent,
-- checked), and having read a claim is not a step in it. One table also
-- covers the claims still in the old store and those in analyzer_items —
-- their ids never collide.
CREATE TABLE IF NOT EXISTS claim_seen (
    claim_id INTEGER PRIMARY KEY,
    at_ms    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_digests (
    day   TEXT NOT NULL,
    model TEXT NOT NULL,
    a_min INTEGER NOT NULL,
    n_seg INTEGER NOT NULL,
    ok    INTEGER NOT NULL DEFAULT 0,
    text  TEXT NOT NULL,
    PRIMARY KEY (day, model, a_min)
);
"""

# Columns added over time. CREATE TABLE IF NOT EXISTS does **nothing** to a
# table that already exists, so new code meeting an old database raises
# OperationalError outright — and that database is the user's only copy.
_ADDED_COLUMNS = [
    # Backfill old rows with 0 rather than leaving NULL: NULL + 1 is still
    # NULL, so the consecutive-failure count would never increment, silently.
    ("imports", "fails", "INTEGER NOT NULL DEFAULT 0"),
    ("imports", "last_ok", "INTEGER"),
    # Voice dispatch v2 (2026-08-21). Proposals in older databases have none of
    # these and are left NULL: "we do not know which kind this was" and "this
    # is the unknown kind" are different facts, so no fake value is written.
    ("proposals", "type", "TEXT"),
    ("proposals", "win_a", "INTEGER"),
    ("proposals", "dispatch_id", "TEXT"),
    ("proposals", "verdict", "TEXT"),
    ("proposals", "verdict_note", "TEXT"),
    ("claims", "card", "TEXT"),
    # Backfilled with 0: how many times this day has been dispatched, which
    # goes into the idempotency key. NULL would make max() raise, and here
    # "never dispatched" and "unknown how many times" are the same thing.
    ("claims", "attempt", "INTEGER NOT NULL DEFAULT 0"),
    ("proposals", "confidence", "TEXT"),
    ("proposals", "domain", "TEXT"),
    ("proposals", "marked", "INTEGER NOT NULL DEFAULT 0"),
    ("proposals", "speakers", "INTEGER"),
    ("proposals", "echo_score", "REAL"),
    ("proposals", "echo_text", "TEXT"),
    # Priority targets (decided 2026-09-28): how much of a priority
    # the week is aiming for. NULL = no target, which is a real choice —
    # "work on this" without a number — not a missing value to fill in.
    ("goals", "target", "REAL"),
    ("goals", "unit", "TEXT"),
]


def progress_start(con: sqlite3.Connection, names) -> int:
    """Open a new import run with every source queued; drop older runs."""
    import time
    run = int(time.time() * 1000)
    with con:
        con.execute("DELETE FROM import_progress")
        con.executemany(
            "INSERT INTO import_progress (run, pos, source, state, updated) "
            "VALUES (?,?,?,?,?)",
            [(run, i, n, "next", run) for i, n in enumerate(names)])
    return run


def progress_set(con: sqlite3.Connection, run: int, source: str, state: str,
                 rows: int | None = None, error: str | None = None) -> None:
    import time
    with con:
        con.execute(
            "UPDATE import_progress SET state=?, rows=COALESCE(?, rows), "
            "error=?, updated=? WHERE run=? AND source=?",
            (state, rows, (error or "")[:300] or None, int(time.time() * 1000),
             run, source))


def progress_abort(con: sqlite3.Connection, run: int) -> None:
    """Whatever had not started will not: say so rather than leave it queued
    forever, which the page would show as an import that never finishes."""
    import time
    with con:
        con.execute("UPDATE import_progress SET state='aborted', updated=? "
                    "WHERE run=? AND state IN ('next', 'reading')",
                    (int(time.time() * 1000), run))


def progress_latest(con: sqlite3.Connection) -> dict | None:
    rows = con.execute(
        "SELECT run, source, state, rows, error, updated FROM import_progress "
        "ORDER BY pos").fetchall()
    if not rows:
        return None
    srcs = [{"source": s, "state": st, "rows": n, "error": e}
            for _, s, st, n, e, _ in rows]
    running = any(x["state"] in ("next", "reading") for x in srcs)
    return {"run": rows[0][0], "running": running,
            "updated": max(r[5] for r in rows), "sources": srcs}


def migrate(con: sqlite3.Connection) -> None:
    """Bring an old database up to the current schema. connect() calls this
    every time, so it must be safe to repeat.

    Table creation happens here too, not in connect() — if "bring up to the
    current schema" only added columns, anything calling migrate() directly
    (tests, scripts) would get a half-migrated database that looks entirely
    normal until some new table is written for the first time. And the missing
    table is always the newest one, which is to say the least exercised one.
    """
    con.executescript(SCHEMA)
    for table, col, decl in _ADDED_COLUMNS:
        cols = {c[1] for c in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Backfill: for an old row with ok=1, its last_run **is** the moment it last
    # succeeded. Without this, migrating a healthy database produces a screen of
    # "never succeeded" (measured: all 13 sources) — and once a diagnostic tool
    # starts crying wolf people stop reading it, which is worse than not having
    # the field at all.
    #
    # Rows with ok=0 are not guessed at: we genuinely do not know when they last
    # succeeded, and substituting last_run would display "has been failing all
    # along" as "succeeded just now". What cannot be recovered stays empty.
    con.execute("UPDATE imports SET last_ok = last_run "
                "WHERE ok = 1 AND last_ok IS NULL")
    con.commit()
    # Order matters: the timezone history first, so tz.bounds() is right, and
    # only then the two steps that depend on it.
    _seed_tz(con)
    _add_day_lo(con)
    _backfill_day_lo(con)
    items_after_legacy(con)


#: The inference tables gain day_lo / day_n, and they belong in the primary key.
#: SQLite cannot change a primary key, so each table is rebuilt.
_DAY_LO_TABLES = ("inferences", "day_events", "digests", "infer_failures")


def _add_day_lo(con: sqlite3.Connection) -> None:
    """Add day_lo / day_n to the inference tables and put them in the key.

    Why a rebuild is unavoidable: a_min is "minute of the day", and after a
    timezone change the old cut's a_min=60 and the new cut's a_min=60 on the same
    day are different moments on one key. Adding the columns with ALTER alone
    would let INSERT OR REPLACE overwrite old rows one by one — exactly the
    irreversible hole this closes.

    The rebuild **moves and computes nothing**: old rows are copied over with
    day_lo 0, filled in by _backfill_day_lo. If the copy fails it raises — a
    half-migrated database is far more dangerous than an unmigrated one.
    """
    for table in _DAY_LO_TABLES:
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({table})")]
        if not cols or "day_lo" in cols:
            continue
        cl = ", ".join(cols)
        with con:
            con.execute(f"ALTER TABLE {table} RENAME TO _{table}_old")
            con.executescript(inference.SCHEMA)   # recreates only the renamed one
            con.execute(f"INSERT INTO {table} ({cl}, day_lo, day_n) "
                        f"SELECT {cl}, 0, 1440 FROM _{table}_old")
            con.execute(f"DROP TABLE _{table}_old")


def _backfill_day_lo(con: sqlite3.Connection) -> None:
    """Fill old rows' day_lo = 0 with the cut **they were made under**.

    0 is the sentinel (its real meaning, 1970-01-01, never occurs here). It is
    filled from tz.bounds(), which _seed_tz has just made agree with the old
    analyze.day_bounds() day by day — so old rows still read back and no day
    goes blank on the page.
    """
    for table in _DAY_LO_TABLES:
        days = [r[0] for r in con.execute(
            f"SELECT DISTINCT day FROM {table} WHERE day_lo = 0")]
        for day in days:
            lo, hi = tz.bounds(con, day)
            con.execute(f"UPDATE {table} SET day_lo=?, day_n=? "
                        f"WHERE day=? AND day_lo=0",
                        (lo, (hi - lo + 1) // 60_000, day))
    con.commit()


def _seed_tz(con: sqlite3.Connection) -> None:
    """Backfill tz_marks from the machine's daylight-saving history, over the
    span the database already holds.

    It only really runs while tz_marks is empty (the gate is in tz.seed). An
    empty database records the current offset too — "which timezone right now"
    is the first useful fact. Backfilled this way, an existing database cuts
    every day exactly as before (tests/test_tz.py).
    """
    import time as _time
    row = con.execute("SELECT MIN(ts) FROM events").fetchone()
    now = int(_time.time() * 1000)
    tz.seed(con, int(row[0]) if row and row[0] else now - 30 * tz.DAY_MS, now)


def items_after_legacy(con: sqlite3.Connection) -> None:
    """Start new finding ids above every id the voice lanes' own tables used.

    A task or claim is named by its id everywhere a person meets it — `umbra
    tasks approve 37`, the page, and the idempotency key a destination dedupes
    on (umbra:<day>:<id>). Those two tables are frozen and the new store takes
    over, so for as long as both are read an id has to mean one finding. Raising
    the counter once is enough: the frozen tables never gain a row, so nothing
    can catch up. Safe to repeat; it only ever raises.
    """
    hi = max([r[0] or 0 for r in con.execute(
        "SELECT seq FROM sqlite_sequence WHERE name IN ('proposals', 'claims')")]
        + [con.execute("SELECT COALESCE(MAX(id), 0) FROM proposals").fetchone()[0],
           con.execute("SELECT COALESCE(MAX(id), 0) FROM claims").fetchone()[0]])
    row = con.execute("SELECT seq FROM sqlite_sequence "
                      "WHERE name='analyzer_items'").fetchone()
    if row is None:
        con.execute("INSERT INTO sqlite_sequence (name, seq) "
                    "VALUES ('analyzer_items', ?)", (hi,))
    elif (row[0] or 0) < hi:
        con.execute("UPDATE sqlite_sequence SET seq=? "
                    "WHERE name='analyzer_items'", (hi,))
    con.commit()


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    # WAL: readers and writers do not block each other. Required while the
    # resident collector is writing.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    # WAL allows many readers and one writer, but **two writing processes**
    # still exclude each other — which is exactly what running an import while
    # the collector is resident looks like. Without busy_timeout that raises
    # "database is locked" immediately.
    #
    # 60 seconds is not arbitrary: 5 seconds was measurably not enough on
    # 2026-08-22, and collector.err.log has the
    # `sqlite3.OperationalError: database is locked` to prove it. One plaud
    # round executemany's 12,702 rows, and since the voice lane was split out
    # that happens every hour — colliding with the resident collector is the
    # normal case, not an accident. Waiting a minute beats losing a round.
    con.execute("PRAGMA busy_timeout=60000")
    migrate(con)                 # create tables + add columns, both inside
    return con


def insert(con: sqlite3.Connection, rows, replace: bool = False) -> int:
    """Bulk insert. A uid conflict is ignored — that is where idempotency comes
    from, and why importers can be re-run freely.

    replace=True overwrites instead, for `--full`. The reason: a 1:1 source's
    uid is a natural key handed over by the source itself (a Chrome visit id, a
    git sha) and never changes, so **changing an importer's payload shape
    updates not one of the existing tens of thousands of rows**. Hit on
    2026-08-16 adding a synced field to chrome: brand new code, all tests green,
    database untouched.

    `--full` means "read it again from the source", so the source's payload
    should win. The incremental path keeps ignoring conflicts: tens of thousands
    of rows a day, and comparing each one is wasted work.
    """
    verb = "REPLACE" if replace else "IGNORE"
    cur = con.executemany(
        f"INSERT OR {verb} INTO events (uid, ts, dur_ms, source, kind, payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(uid, ts, dur, src, kind, json.dumps(p, ensure_ascii=False))
         for uid, ts, dur, src, kind, p in rows],
    )
    con.commit()
    return cur.rowcount


def changed_rows(con: sqlite3.Connection, rows) -> dict:
    """Compare a 1:1 source's re-pulled batch against what is stored, without
    writing anything.

    A REWRITE source overwrites the last few days verbatim, and SQLite's
    rowcount counts a REPLACE with identical content as a row; that number can
    honestly be called written, never added. This returns the real
    new / changed / unchanged counts, plus the rows that should trigger
    downstream recomputation.

    uids are queried in chunks: SQLite caps bound parameters, and one Plaud
    round measurably re-pulls 13k rows.
    """
    rows = list(rows)
    encoded = {
        uid: (ts, dur, src, kind, json.dumps(payload, ensure_ascii=False))
        for uid, ts, dur, src, kind, payload in rows
    }
    existing = {}
    uids = list(encoded)
    for i in range(0, len(uids), 400):
        batch = uids[i:i + 400]
        marks = ",".join("?" for _ in batch)
        existing.update({r[0]: r[1:] for r in con.execute(
            "SELECT uid,ts,dur_ms,source,kind,payload FROM events "
            f"WHERE uid IN ({marks})", batch)})

    new = changed = unchanged = 0
    dirty = []
    for row in rows:
        uid = row[0]
        old = existing.get(uid)
        if old is None:
            new += 1
            dirty.append(row)
        elif old != encoded[uid]:
            changed += 1
            dirty.append(row)
        else:
            unchanged += 1
    return {"new": new, "changed": changed, "unchanged": unchanged,
            "rows": dirty}


def new_rows(con: sqlite3.Connection, rows) -> list:
    """The rows in this batch the database does not hold yet (by uid). Writes
    nothing.

    For the INSERT OR IGNORE path, to know which days this round really
    touched. The rows pulled cannot be used: chrome hands its whole history over
    again every round, and counted that way every day of the last half year
    would be "touched" and sent to be judged again.
    """
    rows = list(rows)
    uids = [r[0] for r in rows]
    have = set()
    for i in range(0, len(uids), 400):          # SQLite caps bound parameters
        batch = uids[i:i + 400]
        marks = ",".join("?" for _ in batch)
        have.update(r[0] for r in con.execute(
            f"SELECT uid FROM events WHERE uid IN ({marks})", batch))
    return [r for r in rows if r[0] not in have]


def _like_prefix(s: str) -> str:
    """A literal turned into a safe LIKE prefix pattern.

    file_ids are hex and hold no wildcard, but correctness should not rest on
    the data happening to be clean — if the uid's makeup ever changes, silently
    matching the wrong rows is much harder to trace than an error.
    """
    for ch in ("\\", "%", "_"):
        s = s.replace(ch, "\\" + ch)
    return s + ":%"


def replace_groups(con: sqlite3.Connection, rows) -> dict:
    """Replace each uid group **whole**: the group's old rows go, the new ones
    are written.

    For plaud, whose uid is `plaud:{file_id}:{n}` — and n is the segment's
    **position** in the transcript array, not its identity. Re-generate in the
    app and the new version may merge segments and shift the whole array.

    `insert(replace=True)` is wrong here, and subtly: REPLACE touches only the
    uids this batch brings. When the new version is shorter, the old tail's uids
    are not in the batch at all, nothing touches them, and they stay forever.
    And they are **not garbage but duplicates**: the same sentence stored again
    under a shifted index, the old one still there, at the same moment. Dedupe
    cannot remove them (different uids), COUNT(*) doubles, and every row looks
    fine on its own — no "is this row right" check can see it.

    A group is the uid minus its last segment:

        plaud:{file_id}:{i}       → plaud:{file_id}        transcript
        plaud:mark:{file_id}:{i}  → plaud:mark:{file_id}   hardware marks

    **Only groups present in the new batch are replaced.** Marks hold only a
    placeholder until transcription finishes, so marks_of returning [] is a
    normal state every round; treating "not fetched this round" as "gone" would
    let one network blip erase the highest-signal rows in the store. An empty
    batch likewise deletes nothing — replace_range's discipline.

    Returns what changed_rows returns, plus `deleted`. `rows` **includes the
    deleted rows**: a deletion is a change, and a day that lost sentences without
    triggering a recompute would leave the page on the version with duplicates,
    with no error.

    Only new and changed rows are written. Re-writing unchanged ones with
    REPLACE deletes and re-inserts them with new rowids — the content is the
    same but thousands of pages are rewritten every round, which anything copying
    the database page by page then moves for nothing.
    """
    rows = list(rows)
    out = {"new": 0, "changed": 0, "unchanged": 0, "deleted": 0, "rows": []}
    if not rows:
        return out

    encoded = {
        uid: (ts, dur, src, kind, json.dumps(payload, ensure_ascii=False))
        for uid, ts, dur, src, kind, payload in rows
    }
    existing = {}
    for group in {uid.rsplit(":", 1)[0] for uid in encoded}:
        existing.update({r[0]: r[1:] for r in con.execute(
            "SELECT uid,ts,dur_ms,source,kind,payload FROM events "
            "WHERE uid LIKE ? ESCAPE '\\'", (_like_prefix(group),))})

    dirty = []
    for row in rows:
        old = existing.get(row[0])
        if old is None:
            out["new"] += 1
            dirty.append(row)
        elif old != encoded[row[0]]:
            out["changed"] += 1
            dirty.append(row)
        else:
            out["unchanged"] += 1
    write = list(dirty)

    gone = [uid for uid in existing if uid not in encoded]
    for uid in gone:
        ts, dur, src, kind, payload = existing[uid]
        dirty.append((uid, ts, dur, src, kind, json.loads(payload)))

    with con:                                   # delete and write in one transaction
        for i in range(0, len(gone), 400):      # SQLite caps bound parameters
            batch = gone[i:i + 400]
            con.execute("DELETE FROM events WHERE uid IN "
                        f"({','.join('?' for _ in batch)})", batch)
        con.executemany(
            "INSERT OR REPLACE INTO events "
            "(uid, ts, dur_ms, source, kind, payload) VALUES (?, ?, ?, ?, ?, ?)",
            [(uid, ts, dur, src, kind, encoded[uid][4])
             for uid, ts, dur, src, kind, _p in write],
        )
    out["deleted"] = len(gone)
    out["rows"] = dirty
    return out


def replace_range(con: sqlite3.Connection, source: str,
                  lo_ms: int, hi_ms: int, rows) -> int:
    """Recompute a **segmented source's** rows over a stretch of time: delete
    the old ones, then write the new.

    For location / media / iphone / intent / weread — they aggregate raw records
    into intervals (GPS points into stays, highlights into reading sessions), so
    changing the algorithm changes the rows produced from the same raw data.
    INSERT OR IGNORE is wrong here:

      · uid unchanged but content changed → the whole row is ignored, and the
        fix can never reach the database
      · rows the new algorithm no longer produces → left behind as orphans

    Observed on 2026-08-16: after merging the fix for "round trips that end
    where they started", the database still held the old 154-minute surf session
    and the bogus "surf → surf" trip was still there.

    **An empty batch deletes nothing.** When collection fails, or that stretch
    genuinely has no data, delete-then-write silently wipes existing history —
    one network blip is enough.
    """
    rows = list(rows)
    if not rows:
        return 0
    with con:                                   # delete and write in one transaction
        con.execute("DELETE FROM events WHERE source=? AND ts>=? AND ts<=?",
                    (source, lo_ms, hi_ms))
        con.executemany(
            "INSERT OR REPLACE INTO events "
            "(uid, ts, dur_ms, source, kind, payload) VALUES (?, ?, ?, ?, ?, ?)",
            [(uid, ts, dur, src, kind, json.dumps(p, ensure_ascii=False))
             for uid, ts, dur, src, kind, p in rows],
        )
    return len(rows)


# How far back an incremental import reaches.
#
# It cannot be 0: sources **backfill and revise** records for periods they have
# already reported. A WHOOP recovery score cannot be computed until the sleep
# ends (it lands first as PENDING_SCORE and gets a score hours later), and
# Butler writes a task's status and duration only once the task finishes.
# Pulling strictly "since last time" loses those permanently, with no error —
# the database is simply, quietly, missing a piece.
#
# Three days is a judgement call: enough to cover both kinds of backfill,
# without degenerating into a full pull every time.
OVERLAP_MS = 3 * 86_400_000


def since_for(con: sqlite3.Connection, source: str,
              overlap_ms: int = OVERLAP_MS) -> int | None:
    """Where this source should start pulling from. None means no baseline, so
    pull everything."""
    row = con.execute("SELECT max(ts) FROM events WHERE source=?",
                      (source,)).fetchone()
    if not row or row[0] is None:
        return None
    return max(0, row[0] - overlap_ms)


def load_range(con: sqlite3.Connection, lo_ms: int, hi_ms: int) -> list[dict]:
    """Every event that **overlaps** this window, in time order. The analysis
    layer's only read interface.

    The test is overlap, not "does the start fall inside the window". The
    latter loses events that cross midnight entirely: a 762-minute stay
    beginning 18:37 last night covers 00:00-07:20 today, but its start is
    yesterday — so it never appears when computing today's coverage, and 440
    minutes vanish.

    coverage() already handles this: span() clamps events with a negative m0
    back into the day, and mark() rejects anything out of bounds. The only
    missing piece was ever this query.

    Events with a NULL or 0 duration are treated as having no extent — they
    appear only at their own instant, rather than being taken to continue
    indefinitely because they have no end time.
    """
    rows = con.execute(
        "SELECT ts, dur_ms, source, kind, payload FROM events "
        "WHERE ts <= ? AND ts + COALESCE(dur_ms, 0) >= ? ORDER BY ts",
        (hi_ms, lo_ms),
    ).fetchall()
    return [
        {"ts": ts, "dur_ms": dur, "source": src, "kind": kind, **json.loads(p)}
        for ts, dur, src, kind, p in rows
    ]


# How many consecutive failures before this counts as a fault. One may be no
# more than a network blip, or a manual run without Full Disk Access; two
# scheduled rounds failing in a row is genuinely broken (which is exactly what
# the WHOOP authorisation expiry was, and nothing anywhere showed it).
#
# It lives here rather than in the CLI because both consumers of freshness — the
# CLI and the viz page — need it, and keeping a copy in each has already played
# out once, on 2026-08-20: the front end had written it as "alarm on a single
# failure", and four healthy sources all showed ⚠.
FAIL_STREAK = 2


def record_import(con: sqlite3.Connection, source: str, ok: bool,
                  added: int = 0, error: str | None = None,
                  at: int | None = None) -> None:
    """at: when the run happened (epoch ms); now unless given — only the
    public demo (devseed.site) gives it, so its imports sit at one moment."""
    import time
    now = int(time.time() * 1000) if at is None else at
    con.execute(
        "INSERT INTO imports (source, last_run, ok, added, error, last_ok, fails) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(source) DO UPDATE SET "
        "last_run=excluded.last_run, ok=excluded.ok, added=excluded.added, "
        "error=excluded.error, "
        # On failure, keep the previous success timestamp — one manual run
        # without permissions must not erase the fact that the schedule
        # succeeded yesterday.
        "last_ok=CASE WHEN excluded.ok THEN excluded.last_ok "
        "             ELSE imports.last_ok END, "
        # Reset on success, accumulate on failure. One failure may be a network
        # blip; three in a row is real. No COALESCE needed: the fails column is
        # NOT NULL DEFAULT 0, and migrated rows are 0 too. Two defences covering
        # each other looks prudent, but it means removing either one is
        # untestable.
        "fails=CASE WHEN excluded.ok THEN 0 ELSE imports.fails + 1 END",
        (source, now, 1 if ok else 0, added, error,
         now if ok else None, 0 if ok else 1),
    )
    con.commit()


def freshness(con: sqlite3.Connection) -> list[dict]:
    """Each source's freshness.

    Only the collector (live/idle) writes continuously; the other sources update
    only when an import runs. Not surfacing this makes staleness read as fact —
    on the page, "did not go online that day" and "that day's browsing history
    has not been imported yet" look identical.

    Returns two different times; do not conflate them:
      last_event  the timestamp of the newest data itself (may be very old, and
                  entirely normal)
      last_run    the last import attempt (old means a real fault)
    """
    import time
    now = time.time() * 1000
    events = {s: (n, ts) for s, n, ts in con.execute(
        "SELECT source, count(*), max(ts) FROM events GROUP BY source")}
    runs = {s: row for s, *row in con.execute(
        "SELECT source, last_run, ok, error, last_ok, COALESCE(fails, 0) "
        "FROM imports")}

    out = []
    for src in sorted(set(events) | set(runs)):
        n, last_event = events.get(src, (0, None))
        last_run, ok, err, last_ok, fails = runs.get(
            src, (None, None, None, None, 0))
        out.append({
            "source": src, "count": n, "last_event": last_event,
            "event_age_min": (now - last_event) / 60000 if last_event else None,
            "last_run": last_run,
            "run_age_min": (now - last_run) / 60000 if last_run else None,
            "ok": None if ok is None else bool(ok),
            "error": err,
            # Last *successful* import. Kept apart from last_run: this one going
            # stale means no new data is arriving, that one going stale means
            # the schedule itself is not running.
            "last_ok": last_ok,
            "ok_age_min": (now - last_ok) / 60000 if last_ok else None,
            "fails": fails,
            # The collector writes continuously and has no "import" action, so
            # its event time stands in for one.
            "live": src in ("live", "idle"),
        })
    return out


def stats(con: sqlite3.Connection) -> list[tuple]:
    return con.execute(
        "SELECT source, count(*), "
        "       date(min(ts)/1000, 'unixepoch', 'localtime'), "
        "       date(max(ts)/1000, 'unixepoch', 'localtime') "
        "FROM events GROUP BY source ORDER BY 2 DESC"
    ).fetchall()
