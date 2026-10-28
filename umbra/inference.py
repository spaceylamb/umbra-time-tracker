"""The model's guess at what a stretch of time was. **A guess, not an
observation.**

From an experiment on 2026-08-17: compress **every source** covering a stretch
(voice transcripts, AI conversations, browsing, location, WHOOP, clipboard...)
into a block of text, ask the model what that half hour was, then cut the answer
into 5-minute slots.

Considerably more accurate than blocks.py's priority ladder. The hardest number:
17 slots where the voice transcript lines up sentence-for-sentence with a butler
prompt (moments where we **know** he was dictating to an AI),

    rule-based       0/17
    gemini-3.7-flash 13/17    gpt-5.6-sol 11/17    claude-opus-5 10/17

The rule-based version is wrong for a defensible reason — talk outranks code on
the ladder, so any speech at all is judged "talking". It looks at **which source
fired**, never at **what was said**, and what was said happened to be "merge
this branch into main". One source means opposite things in different contexts,
and a table of constant priorities cannot express that. In the other direction,
a 22:00 video call with grandparents is a case where talk should win.

The chain has three stages, and **only the middle one is the model**:

    digest   all sources in a window -> compressed text   deterministic
    label    text -> activity spans                       <- the model
    expand   spans -> 288 slots + validation              deterministic
                                                          (this file)

The split is not fastidiousness; it comes from three hard constraints. It has to
be able to run on a local open-weights model eventually (they are weaker, so
give them as little as possible). Having the model emit 288 rows is pure token
waste, and it cuts sloppily at slot boundaries. And expansion and validation must
be reproducible, or even "compare today with yesterday" becomes impossible.
"""

import collections
import json

from . import taxonomy, tz
import sqlite3

SLICE_MIN = 5
#: A whole ordinary day's slot count. **Only a default** — a real day can be as
#: short as 600 minutes or as long as 2340 (see tz.day_minutes).
BLOCKS = 1440 // SLICE_MIN


def n_blocks(n_min: int) -> int:
    """How many slots this day has. Rounded up: a tail shorter than a slot did
    happen too."""
    return -(-int(n_min) // SLICE_MIN)


def minute(t) -> int:
    """Accepts either "09:03" or 543.

    The pass that produces digests computes windows in minutes; forcing it to
    format to HH:MM and parse back would only add one more chance for a
    round-trip conversion to go wrong.
    """
    if isinstance(t, (int, float)):
        return int(t)
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


_min = minute        # the private name four modules already reach for


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def expand(spans, report: bool = False, n_min: int = 1440):
    """Activity spans -> 288 slots. Uncovered slots are None.

    Uncovered slots are not filled in. Extending a neighbouring span into them
    would be us inventing on the model's behalf — leaving them empty is what
    makes "there is no answer here yet" visible.

    Out-of-range spans are clamped into the day, **but reported**. A model
    emitting 25:00 has made an arithmetic error, and correcting it silently
    means never finding out. "Something went wrong and it does not show" is the
    one thing this project refuses above all.
    """
    out = [None] * n_blocks(n_min)
    bad = []
    for s in spans:
        try:
            a, b = _min(s["start"]), _min(s["end"])
        except (KeyError, ValueError, AttributeError):
            bad.append(f"malformed time: {s.get('start')}-{s.get('end')}")
            continue
        if not (0 <= a < b <= n_min):
            bad.append(f"out of range: {s.get('start')}-{s.get('end')}")
            a, b = max(0, min(a, n_min - 1)), max(1, min(b, n_min))
            if a >= b:
                continue
        # Shorter than one slot still occupies one: 22:03-22:04 did happen.
        for i in range(a // SLICE_MIN, min(len(out), -(-b // SLICE_MIN))):
            if out[i] is None:            # first writer wins; no overwriting
                out[i] = s
    return (out, bad) if report else out


def agreement(per_model):
    """How many models said the same thing about each slot.

    **The denominator is the number of models, not the number that spoke.** One
    saying "coding" while another simply did not cover that slot is not 1/1
    agreement — scoring it that way makes "only one model spoke" look exactly as
    well-supported as "all three agree".

    ⚠️ Models only; never mix the rule-based version in. Doing so merges two
    different questions into one number: "do the AIs agree" (is the evidence
    strong) and "do the AIs match the rules" (how far apart are the two
    methods). Given a 3/4 you could not tell which you were looking at.
    """
    tot = len(per_model)
    out = []
    # One day's expansions all have that day's length.
    for i in range(len(per_model[0]) if per_model else BLOCKS):
        cats = [(c[i] or {}).get("cat") for c in per_model]
        named = [c for c in cats if c]
        same, n = (collections.Counter(named).most_common(1)[0]
                   if named else (None, 0))
        out.append({"n": n, "tot": tot, "same": same})
    return out


SCHEMA = """
-- Activity spans guessed by a model. **Deliberately separate from events.**
-- Every row in events should be something a source actually recorded; mixing
-- guesses in makes "this was measured" and "this was guessed" permanently
-- indistinguishable. Carrying the model name means re-running with a different
-- model or a changed prompt does not contaminate the original data, and lets
-- several be compared side by side.
CREATE TABLE IF NOT EXISTS inferences (
    day     TEXT NOT NULL,
    model   TEXT NOT NULL,
    a_min   INTEGER NOT NULL,      -- minute of the day
    b_min   INTEGER NOT NULL,
    cat     TEXT NOT NULL,
    payload TEXT NOT NULL,         -- activity / confidence / with / evidence
    -- day_lo is the day's start (UTC ms). **It goes into the key, and must.**
    --
    -- a_min means "minute of the day" — it looks like a plain number but hides
    -- a timezone. After a flight the same day string points at a different
    -- stretch of real time, and the old cut's a_min=60 and the new cut's a_min=60
    -- are different moments on one key: INSERT OR REPLACE would overwrite the
    -- old row by row. With it every row describes itself: the moment is
    -- day_lo + a_min * 60000.
    --
    -- day_n (minutes in the day) must go in too: a plane leaving **in the
    -- daytime** leaves the start where it was and changes only the length
    -- (1440 → 600). Keyed on day_lo alone, exactly that day would be erased.
    day_lo  INTEGER NOT NULL DEFAULT 0,
    day_n   INTEGER NOT NULL DEFAULT 1440,
    PRIMARY KEY (day, model, day_lo, day_n, a_min)
);

-- Second pass: **calendar events** composed from a day's activity spans.
-- Stored apart from inferences because the two granularities are entirely
-- different (spans 1-30 minutes, events 18-390 minutes). One table would make
-- "the model's judgement of this half hour" and "what happened this day"
-- impossible to tell apart.
CREATE TABLE IF NOT EXISTS day_events (
    day     TEXT NOT NULL,
    model   TEXT NOT NULL,
    a_min   INTEGER NOT NULL,
    b_min   INTEGER NOT NULL,
    cat     TEXT NOT NULL,
    title   TEXT NOT NULL,
    payload TEXT NOT NULL,      -- detail / from (which spans it was composed from)
    day_lo  INTEGER NOT NULL DEFAULT 0,      -- see inferences.day_lo
    day_n   INTEGER NOT NULL DEFAULT 1440,   -- see inferences.day_n
    PRIMARY KEY (day, model, day_lo, day_n, a_min)
);

-- **The exact text the model was given.** The first two tables are the model's
-- output; this one is its input.
--
-- The gap found on 2026-08-18 while chasing "why was this judged a Zoom work
-- meeting": all that could be retrieved was the model's output and the 1,427 raw
-- records in the window — this middle layer was used and thrown away. So "what
-- made it decide that" could only be reconstructed backwards, and that cannot
-- answer "Speaker 2 was rewritten as 'me' in the evidence — did digest do that,
-- or did the model?".
--
-- n_rec and text have to be stored together: with only text, the page can still
-- only say "there were 1,427 records in the window", when the model never saw
-- those 1,427 — it saw the compressed result. Side by side, how much was
-- compressed away becomes visible.
CREATE TABLE IF NOT EXISTS digests (
    day     TEXT NOT NULL,
    model   TEXT NOT NULL,
    a_min   INTEGER NOT NULL,      -- the target stretch
    b_min   INTEGER NOT NULL,
    w_a     INTEGER NOT NULL,      -- the window actually shown (target plus an
                                   -- hour of context either side)
    w_b     INTEGER NOT NULL,
    n_rec   INTEGER NOT NULL,      -- raw records in the window **before**
                                   -- compression
    text    TEXT NOT NULL,         -- what was actually sent, **after**
    day_lo  INTEGER NOT NULL DEFAULT 0,      -- see inferences.day_lo
    day_n   INTEGER NOT NULL DEFAULT 1440,   -- see inferences.day_n
    PRIMARY KEY (day, model, day_lo, day_n, a_min)
);

-- **Windows whose call failed.** Without this, a failed half hour has no spans
-- and looks exactly like one the model looked at and found nothing to say
-- about — the page would present an outage as a finding. Written by every run
-- of a day (delete then write: the run knows the full set), so a window that
-- succeeds on retry stops being listed.
CREATE TABLE IF NOT EXISTS infer_failures (
    day     TEXT NOT NULL,
    model   TEXT NOT NULL,
    a_min   INTEGER NOT NULL,
    b_min   INTEGER NOT NULL,
    at      INTEGER NOT NULL,      -- UTC epoch ms of the failed call
    kind    TEXT NOT NULL,         -- auth | call
    reason  TEXT NOT NULL,         -- the error, trimmed; never a key
    day_lo  INTEGER NOT NULL DEFAULT 0,      -- see inferences.day_lo
    day_n   INTEGER NOT NULL DEFAULT 1440,   -- see inferences.day_n
    PRIMARY KEY (day, model, day_lo, day_n, a_min)
);
"""

_KEEP = ("activity", "confidence", "with", "also", "evidence")

# An HTTP 401/403, or the provider saying there is no key: retrying will not
# help, a person has to act. Everything else (timeouts, 5xx, a reply that did
# not parse) is "call" and is simply retried next run.
_AUTH = ("401", "403", "unauthorized", "forbidden", "api key", "api_key",
         "is not set", "permission denied")


def failure_kind(err: str) -> str:
    e = err.lower()
    return "auth" if any(k in e for k in _AUTH) else "call"


def _key(con: sqlite3.Connection, day: str) -> tuple[int, int]:
    """This day's cut right now: (start, minutes in the day).

    Writes and reads both go through it, so the two never compute their own —
    when they do, the symptom is "this day is suddenly empty on the page" with
    not one row missing from the store.
    """
    lo, hi = tz.bounds(con, day)
    return lo, (hi - lo + 1) // 60_000


def save_failures(con: sqlite3.Connection, day: str, model: str, rows) -> None:
    """Replace one model's failed windows for one day with this run's set.

    Unlike save / save_events, **an empty batch does delete**: the run that
    calls this has tried every window it needed to, so an empty set means
    none failed this time, and last round's failures are no longer true.
    rows: [{"a": min, "b": min, "at": ms, "reason": str}]
    """
    lo, dn = _key(con, day)
    with con:
        con.execute("DELETE FROM infer_failures WHERE day=? AND model=? "
                    "AND day_lo=? AND day_n=?", (day, model, lo, dn))
        con.executemany(
            "INSERT OR REPLACE INTO infer_failures "
            "(day, model, a_min, b_min, at, kind, reason, day_lo, day_n) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(day, model, r["a"], r["b"], r["at"], failure_kind(r["reason"]),
              r["reason"][:300], lo, dn) for r in rows])


def load_failures(con: sqlite3.Connection, day: str) -> dict:
    """{model: [{a, b, at, kind, reason}, ...]} in time order."""
    out: dict = {}
    for model, a, b, at, kind, reason in con.execute(
            "SELECT model, a_min, b_min, at, kind, reason FROM infer_failures "
            "WHERE day=? AND day_lo=? AND day_n=? ORDER BY model, a_min",
            (day, *_key(con, day))):
        out.setdefault(model, []).append(
            {"a": a, "b": b, "at": at, "kind": kind, "reason": reason})
    return out


def save(con: sqlite3.Connection, day: str, model: str, spans) -> int:
    """Write all of one model's guesses for one day: delete, then write.

    **An empty batch deletes nothing** — when the call failed, or the model said
    nothing about that stretch, delete-then-write would silently wipe the
    previous result. One network blip is enough. Same reasoning as
    db.replace_range.
    """
    rows = []
    for s in spans:
        try:
            a, b = _min(s["start"]), _min(s["end"])
        except (KeyError, ValueError, AttributeError):
            continue
        rows.append((day, model, a, b, taxonomy.norm(s.get("cat", "unknown")),
                     json.dumps({k: s[k] for k in _KEEP if k in s},
                                ensure_ascii=False)))
    if not rows:
        return 0
    lo, dn = _key(con, day)
    rows = [r + (lo, dn) for r in rows]
    with con:
        # **The DELETE must carry the cut.** Without it, re-inferring the last
        # two days after a flight erases those days' rows under the old cut —
        # rows that cost model calls and only more calls can bring back.
        con.execute("DELETE FROM inferences WHERE day=? AND model=? "
                    "AND day_lo=? AND day_n=?", (day, model, lo, dn))
        con.executemany("INSERT OR REPLACE INTO inferences "
                        "(day, model, a_min, b_min, cat, payload, day_lo, day_n) "
                        "VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def load(con: sqlite3.Connection, day: str) -> dict:
    """{model: [span, ...]}, in time order. No guesses means an empty dict."""
    out: dict = {}
    for model, a, b, cat, payload in con.execute(
            "SELECT model, a_min, b_min, cat, payload FROM inferences "
            "WHERE day=? AND day_lo=? AND day_n=? ORDER BY model, a_min",
            (day, *_key(con, day))):
        out.setdefault(model, []).append(
            {"start": f"{a // 60:02d}:{a % 60:02d}",
             "end": f"{b // 60:02d}:{b % 60:02d}",
             # Older rows hold display names (keys have been stored since
             # 2026-08-19): normalise on read rather than migrating data.
             "cat": taxonomy.norm(cat), **json.loads(payload)})
    return out


# ── Second pass: calendar events ──────────────────────────────
#
# The first pass caps spans at 30 minutes, and that ceiling is **imposed by the
# window**, not by judgement — the model can already see an hour either side; the
# prompt merely asks it to speak about the target 30 minutes only. Enlarging the
# window just raises the ceiling: a five-hour evening gathering still gets cut up.
#
# The second pass needs no window. Its input is the first pass's output, already
# two orders of magnitude smaller:
#     raw data 207,000 characters  ->  first pass ~10,000 (measured prompt 4,190)
# It sees a whole day at once, so there is no ceiling. Measured on 8-16 it
# composed 17 events, the longest 390 minutes (a full night's sleep spanning 13
# windows).

_EV_KEEP = ("detail", "from")


def save_events(con: sqlite3.Connection, day: str, model: str, events) -> int:
    """Write the events one model composed for one day. Delete then write;
    **an empty batch deletes nothing**."""
    rows = []
    for e in events:
        try:
            a, b = _min(e["start"]), _min(e["end"])
        except (KeyError, ValueError, AttributeError):
            continue
        rows.append((day, model, a, b, taxonomy.norm(e.get("cat", "unknown")),
                     e.get("title", ""),
                     json.dumps({k: e[k] for k in _EV_KEEP if k in e},
                                ensure_ascii=False)))
    if not rows:
        return 0
    lo, dn = _key(con, day)
    rows = [r + (lo, dn) for r in rows]
    with con:
        con.execute("DELETE FROM day_events WHERE day=? AND model=? "
                    "AND day_lo=? AND day_n=?", (day, model, lo, dn))
        con.executemany("INSERT OR REPLACE INTO day_events "
                        "(day, model, a_min, b_min, cat, title, payload, day_lo, day_n) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def load_events(con: sqlite3.Connection, day: str) -> dict:
    """{model: [event, ...]}, in time order. a/b are minutes of the day, used
    for drawing the bar."""
    out: dict = {}
    for model, a, b, cat, title, payload in con.execute(
            "SELECT model, a_min, b_min, cat, title, payload FROM day_events "
            "WHERE day=? AND day_lo=? AND day_n=? ORDER BY model, a_min",
            (day, *_key(con, day))):
        out.setdefault(model, []).append(
            {"a": a, "b": b, "cat": taxonomy.norm(cat), "title": title,
             "start": f"{a // 60:02d}:{a % 60:02d}",
             "end": f"{b // 60:02d}:{b % 60:02d}",
             **json.loads(payload)})
    return out


# ── The input side: the text given to the model ───────────────

def _drow(day, model, d):
    a, b = _min(d["start"]), _min(d["end"])
    w = d.get("win") or (a, b)
    txt = d.get("text") or ""
    return (day, model, a, b, _min(w[0]), _min(w[1]),
            int(d.get("n") or 0), txt)


def save_digests(con: sqlite3.Connection, day: str, model: str, items) -> int:
    """Write one model's digest for each window of one day. Delete then write;
    **an empty batch deletes nothing**.

    Same rule as save / save_events: when the call failed, or that run produced
    nothing, delete-then-write would silently wipe the previous result, and one
    network blip is enough.

    Empty text is stored as-is — empty and "never stored" are different facts.
    The first means "nothing did happen in that half hour", the second means "we
    have no record". The interface must be able to tell them apart.
    """
    if not items:
        return 0
    lo, dn = _key(con, day)
    rows = [_drow(day, model, d) + (lo, dn) for d in items]
    with con:
        con.execute("DELETE FROM digests WHERE day=? AND model=? "
                    "AND day_lo=? AND day_n=?", (day, model, lo, dn))
        con.executemany("INSERT OR REPLACE INTO digests "
                        "(day, model, a_min, b_min, w_a, w_b, n_rec, text, day_lo, day_n) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def _dfmt(a, b, wa, wb, n, txt):
    # chars is computed, not stored: it is simply len(text), and a stored copy
    # would only get out of step one day.
    return {"a": a, "b": b, "start": _hm(a), "end": _hm(b),
            "wstart": _hm(wa), "wend": _hm(wb),
            "n": n, "chars": len(txt), "text": txt}


def load_digests(con: sqlite3.Connection, day: str) -> dict:
    """{model: [digest, ...]}, in time order. Nothing means an empty dict."""
    out: dict = {}
    for model, a, b, wa, wb, n, txt in con.execute(
            "SELECT model, a_min, b_min, w_a, w_b, n_rec, text FROM digests "
            "WHERE day=? AND day_lo=? AND day_n=? ORDER BY model, a_min",
            (day, *_key(con, day))):
        out.setdefault(model, []).append(_dfmt(a, b, wa, wb, n, txt))
    return out


def overlap_idx(rows, a: int, b: int):
    """Which rows [a, b) covers — returns indices.

    **By overlap, not by containment.** Windows are fixed half-hour slots;
    calendar events are not (09:03-09:30, 12:32-15:52). Under containment any
    event crossing a slot boundary displays as "no digest", which looks exactly
    like none having been stored.

    Endpoints do not count as overlap: an event ending at 570 should not pick up
    the window starting at 570, or every event gains half an hour and looks like
    the model saw one slot more than it did.

    This rule is **written in exactly one place**: viz needs it to compute
    indices when building the page, and digests_for needs it when querying. A
    copy in each would silently drift the moment one was changed and the other
    forgotten.
    """
    return [i for i, r in enumerate(rows) if r["a"] < b and r["b"] > a]


def digests_for(con: sqlite3.Connection, day: str, model: str, a: int, b: int):
    """The digests of the windows [a, b) spans."""
    rows = load_digests(con, day).get(model, [])
    return [rows[i] for i in overlap_idx(rows, a, b)]
