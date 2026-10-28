"""The machinery every Analyzer needs, owned once.

An Analyzer reads a layer of the timeline, calls a model over windows of a day,
and writes derived rows. Umbra does this three times — goal attribution, spoken
claims, voice-extracted tasks — and each lane had built the same scaffolding for
itself.

The duplication was not only wasteful, it was reaching across module boundaries
to do it. `claims` called `actions._grams`; both `claims` and `actions` called
`infer._json`. Two of the three private names in that list belong to modules
their callers are documented as independent of.

What lives here is what every Analyzer needs and no Analyzer should write:

    parse_json      pull the model's array out of whatever it wrapped it in
    grams           character n-grams, for overlap scoring
    load_digests    the per-window input cache
    save_digests
    run_windows     bounded concurrency over the windows that need a call

What does **not** live here is anything a lane decides for itself: its prompt,
its parser, which windows are worth a call, and what a returned row means. Those
differ per lane and moving them here would recreate the coupling this module
removes.

## What is deliberately still shared, and what is not

The lanes keep separate model calls, separate cache tables, and separate
prompts. `umbra/actions.py`'s docstring argues why at length: one call answering
two questions makes both worse, and a later edit to one lane's wording silently
changes the other lane's recall with nothing to report it. Sharing this module
does not weaken that. An interface is not an implementation, and two lanes that
both call `run_windows` still make their own calls against their own caches.

Quote ownership stays with each lane, and that is not an oversight. Claims runs
at zero context, where "this quote is nowhere in the window" means the claim
belongs to a neighbour; Actions runs at thirty minutes of context, where the
same rule would drag in every neighbouring window's output.
"""

import concurrent.futures
import hashlib
import json
import re
import time

# Concurrent windows. Higher hits rate limits, and backing off from a rate limit
# is slower than running serially. The same number infer.py uses, for the same
# reason and against the same providers.
WORKERS = 4


def parse_json(txt: str):
    """Dig the model's array out of its reply.

    It wraps things in ```json fences, prefixes them with "Sure, here you go:",
    and occasionally nests the array inside an object.

    If nothing can be dug out, raise. **Never return an empty list.** That would
    make "the model said nothing happened in this window" and "we failed to parse
    the model" indistinguishable — and the caller stores the first as a result
    while the second needs a retry.
    """
    t = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", t, re.S)
        if not m:
            raise ValueError(f"the model returned no JSON: {txt[:120]}")
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"the model returned broken JSON: {txt[:120]}") from e
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list):
                return v
        raise ValueError(
            f"the model returned an object with no array in it: {txt[:120]}")
    if not isinstance(d, list):
        raise ValueError(f"the model did not return an array: {txt[:120]}")
    return d


def grams(s: str, n: int = 3) -> set:
    """Character n-grams with whitespace removed.

    Whitespace goes because the two texts being compared are usually a
    transcript and something typed, and where the spaces fall is exactly what
    differs between them.
    """
    s = "".join(c for c in s if not c.isspace())
    return {s[i:i + n] for i in range(len(s) - n + 1)}


# ── The per-window input cache ────────────────────────────────
#
# The table name is a parameter because each lane owns its own cache. Two lanes
# sharing one cache table would make a re-run of either invalidate the other's
# work, and would tie one lane's freshness to which model the other last used.


def load_digests(con, table: str, day: str, model: str) -> dict:
    """{minute: (text, ok)} from a voice lane's old cache table, for one day.

    Read only. Both lanes now cache through analyzer_runs like every other
    Analyzer; their old tables are consulted once per window, to carry over
    what they had already answered (see each lane's _bridge_cache)."""
    return {a: (text, ok) for a, text, ok in con.execute(
        f"SELECT a_min, text, ok FROM {table} WHERE day=? AND model=?",
        (day, model))}


def run_windows(call, jobs, prompt_of, log=None, label=None):
    """Call the model for each job window, bounded at WORKERS.

    Returns (results, failed) where results is [(job, parsed_rows), ...] for the
    windows that answered and parsed, and failed counts the rest.

    A window that raises is counted and skipped, never allowed to take the batch
    down with it: the other windows of that day cost money too, and a day that
    loses every window because one reply was malformed is the expensive failure.

    The job dict is marked `ok` on success and left alone on failure. Callers
    write the whole batch back afterwards, so a failed window keeps `ok = 0` and
    is retried next round — caching a failure as a success means that stretch of
    the day is never looked at again.
    """
    log = log or (lambda *_: None)
    label = label or (lambda r: str(r.get("a")))
    got, failed = [], 0
    if not jobs:
        return got, failed
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(call, prompt_of(r)): r for r in jobs}
        for f in concurrent.futures.as_completed(futs):
            r = futs[f]
            try:
                items = parse_json(f.result())
            except Exception as e:                        # noqa: BLE001
                failed += 1
                log(f"  ✗ {label(r)} {e}")
                continue
            r["ok"] = True
            got.append((r, items))
            log(f"  ✓ {label(r)} {len(items)}")
    return got, failed


# ── Storage ───────────────────────────────────────────────────
#
# One table for every Analyzer's output, and a second for what ran. Two tables
# rather than one because they answer different questions and a single table
# cannot answer the second: a window that ran and produced nothing leaves no
# rows, which is indistinguishable from a window that was never called. That
# difference decides whether the next round pays for the call again.
#
# Sink dispatch state lives in neither of these. Analyzer output is written once;
# a dispatch has a lifecycle and is updated repeatedly. The existing `claims`
# table mixes the two — day, speaker, claim, quote, kind, verdict on one side and
# status, check_task, report, card, attempt on the other — and that mixture is
# what makes it impossible to generalize as it stands. U4 owns the second table.

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyzer_rows (
    plugin   TEXT NOT NULL,
    day      TEXT NOT NULL,
    model    TEXT NOT NULL,
    -- The window this row came from. Deletion is scoped by it, so re-running one
    -- window replaces exactly its own output and leaves the rest of the day alone.
    win_a    INTEGER NOT NULL,
    -- What this row is *about*, which is not the same as the window it came from.
    -- A goal attribution names the span it credits; a claim names the minute it
    -- was said. Both are inside the window and neither equals it.
    a_min    INTEGER NOT NULL,
    b_min    INTEGER NOT NULL,
    -- One window can yield several rows, and one span can be credited to more
    -- than one goal, so a_min alone is not unique.
    seq      INTEGER NOT NULL,
    payload  TEXT NOT NULL,
    PRIMARY KEY (plugin, day, model, win_a, seq)
);
CREATE INDEX IF NOT EXISTS idx_analyzer_day ON analyzer_rows(plugin, day);

-- What ran, and what it read. `digest` is the cache key: an unchanged value
-- means the model would be answering the same question again, so it is not
-- asked. It covers everything that would change the answer, which is the
-- rendered window plus whatever the Analyzer declared through params -- goal
-- attribution depends on three sentences that appear in no window, and a cache
-- keyed on the window alone would keep serving numbers computed against goal
-- text the user has since rewritten.
CREATE TABLE IF NOT EXISTS analyzer_runs (
    plugin   TEXT NOT NULL,
    day      TEXT NOT NULL,
    model    TEXT NOT NULL,
    win_a    INTEGER NOT NULL,
    digest   TEXT NOT NULL,
    at_ms    INTEGER NOT NULL,
    PRIMARY KEY (plugin, day, model, win_a)
);

-- Findings: what an Analyzer that declares KEY produces.
--
-- analyzer_rows holds a judgement *of* a window, and re-judging replaces it --
-- goal attribution is the current answer and nothing else. A finding is a
-- different thing: something said, a task heard, a claim made. Once found it
-- has an identity, and things happen to it -- it is sent, a person dismisses
-- it, an answer comes back. A window judged again after a late recording must
-- not delete a finding that has already gone out, or re-send it under a
-- rewording. So findings accumulate, keyed by what the plugin says identifies
-- one within a day (the transcript quote, for both voice lanes), and are never
-- replaced. A dismissed finding the model produces again stays dismissed.
--
-- payload is what the Analyzer found and is written once. state and meta are
-- the plugin's own bookkeeping about the finding afterwards and are updated.
-- What was *sent* stays in sink_dispatch: a send has a destination, a receipt
-- and attempts, which belong to the sink and not to the finding.
CREATE TABLE IF NOT EXISTS analyzer_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin   TEXT NOT NULL,
    day      TEXT NOT NULL,
    key      TEXT NOT NULL,
    model    TEXT NOT NULL,
    win_a    INTEGER NOT NULL,
    a_min    INTEGER NOT NULL,
    b_min    INTEGER NOT NULL,
    payload  TEXT NOT NULL,
    state    TEXT NOT NULL DEFAULT 'new',
    meta     TEXT NOT NULL DEFAULT '{}',
    at_ms    INTEGER NOT NULL,
    updated_ms INTEGER,
    UNIQUE (plugin, day, key)
);
CREATE INDEX IF NOT EXISTS idx_items_state ON analyzer_items(plugin, state);
"""


def digest_of(text: str, params=None) -> str:
    """The cache key for one window.

    params is folded in with a separator that cannot occur in JSON output, so
    that no rearrangement of window text and parameters can produce a collision.
    Sorted keys because a dict's iteration order is not part of the input.
    """
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8"))
    if params:
        h.update(b"\x00params\x00")
        h.update(json.dumps(params, sort_keys=True,
                            ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


def cached(con, plugin: str, day: str, model: str, win_a: int,
           digest: str) -> bool:
    """Has this exact window, with this exact input, already been answered?"""
    row = con.execute(
        "SELECT digest FROM analyzer_runs "
        "WHERE plugin=? AND day=? AND model=? AND win_a=?",
        (plugin, day, model, win_a)).fetchone()
    return bool(row) and row[0] == digest


def save_rows(con, plugin: str, day: str, model: str, win_a: int,
              digest: str, rows, now_ms: int | None = None) -> int:
    """Store one window's output, replacing whatever that window held before.

    **An empty batch still writes the run marker.** A window where the model
    genuinely found nothing is an answer, and recording it is what stops the next
    round paying to ask again. This is the opposite of the empty-batch rule on
    db.replace_range and inference.save, and the difference is real: there, an
    empty batch means the collection failed and deleting would destroy data;
    here, the model answered and the answer was nothing.
    """
    rows = list(rows)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    with con:
        con.execute(
            "DELETE FROM analyzer_rows "
            "WHERE plugin=? AND day=? AND model=? AND win_a=?",
            (plugin, day, model, win_a))
        con.executemany(
            "INSERT INTO analyzer_rows "
            "(plugin, day, model, win_a, a_min, b_min, seq, payload) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(plugin, day, model, win_a,
              int(r.get("a", win_a)), int(r.get("b", win_a)), i,
              json.dumps({k: v for k, v in r.items() if k not in ("a", "b")},
                         ensure_ascii=False))
             for i, r in enumerate(rows)])
        con.execute(
            "INSERT OR REPLACE INTO analyzer_runs "
            "(plugin, day, model, win_a, digest, at_ms) VALUES (?,?,?,?,?,?)",
            (plugin, day, model, win_a, digest, now))
    return len(rows)


def load_rows(con, plugin: str, day: str, model: str | None = None) -> list[dict]:
    """One plugin's output for one day, in window order then row order."""
    q = ("SELECT win_a, a_min, b_min, payload FROM analyzer_rows "
         "WHERE plugin=? AND day=?")
    args = [plugin, day]
    if model is not None:
        q += " AND model=?"
        args.append(model)
    q += " ORDER BY win_a, seq"
    return [{"win_a": w, "a": a, "b": b, **json.loads(p)}
            for w, a, b, p in con.execute(q, args)]


def ran(con, plugin: str, day: str, model: str) -> set:
    """Which windows have been answered, including those that answered nothing.

    Callers use this to tell an empty day from an unvisited one. Without it,
    every day that genuinely contained nothing is recomputed every round and the
    difference never shows.
    """
    return {r[0] for r in con.execute(
        "SELECT win_a FROM analyzer_runs WHERE plugin=? AND day=? AND model=?",
        (plugin, day, model))}


def save_items(con, plugin: str, day: str, model: str, win_a: int,
               digest: str, rows, key: str, now_ms: int | None = None) -> list:
    """Keep one window's findings. Returns the ids of the ones that are new.

    Nothing is deleted and nothing already kept is touched: a finding seen
    before -- same key, same day -- is left exactly as it was, state included.
    That is the whole difference from save_rows, and the reason it exists.

    A row with no key is dropped. A finding that cannot be told apart from the
    next one cannot be followed through being sent and answered, and keeping
    it anyway would mean finding it again, as new, every time.

    A row may carry `state` -- found already held back, for instance. It is
    the item's starting state, not part of what was found.

    The run marker is written as save_rows writes it, empty batch included.
    """
    now = int(time.time() * 1000) if now_ms is None else now_ms
    ids = []
    with con:
        for r in rows:
            r = dict(r)
            k = r.get(key)
            if k is None or not str(k).strip():
                continue
            state = r.pop("state", None) or "new"
            a, b = int(r.pop("a", win_a)), int(r.pop("b", win_a))
            cur = con.execute(
                "INSERT OR IGNORE INTO analyzer_items "
                "(plugin, day, key, model, win_a, a_min, b_min, payload, state, "
                " at_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (plugin, day, str(k), model, win_a, a, b,
                 json.dumps(r, ensure_ascii=False), state, now))
            if cur.rowcount:
                ids.append(cur.lastrowid)
        con.execute(
            "INSERT OR REPLACE INTO analyzer_runs "
            "(plugin, day, model, win_a, digest, at_ms) VALUES (?,?,?,?,?,?)",
            (plugin, day, model, win_a, digest, now))
    return ids


def mark_ran(con, plugin: str, day: str, model: str, win_a: int,
             digest: str, now_ms: int | None = None) -> None:
    """Record that a window has been answered, without storing anything.

    For a plugin moving its cache from a table of its own: where its old cache
    says this exact input was already answered, asking again would only pay to
    get the same findings, worded slightly differently, and keep both.
    """
    now = int(time.time() * 1000) if now_ms is None else now_ms
    with con:
        con.execute(
            "INSERT OR REPLACE INTO analyzer_runs "
            "(plugin, day, model, win_a, digest, at_ms) VALUES (?,?,?,?,?,?)",
            (plugin, day, model, win_a, digest, now))


_ITEM_COLS = ("id, day, key, model, win_a, a_min, b_min, payload, state, meta, "
              "at_ms, updated_ms")


def _item(row) -> dict:
    (i, day, key, model, win_a, a, b, payload, state, meta, at_ms,
     updated_ms) = row
    return {**json.loads(payload), "id": i, "day": day, "key": key,
            "model": model, "win_a": win_a, "a": a, "b": b, "state": state,
            "meta": json.loads(meta or "{}"), "at_ms": at_ms,
            "updated_ms": updated_ms}


def load_items(con, plugin: str, day: str | None = None,
               state=None) -> list[dict]:
    """One plugin's findings, oldest day first, in the order they were said.

    state may be one state or several. The fields Umbra owns -- id, day, key,
    state, meta and the rest -- are written over any payload field of the same
    name, so a plugin cannot shadow its own bookkeeping by accident.
    """
    q, args = f"SELECT {_ITEM_COLS} FROM analyzer_items WHERE plugin=?", [plugin]
    if day is not None:
        q += " AND day=?"
        args.append(day)
    if state is not None:
        states = [state] if isinstance(state, str) else list(state)
        q += f" AND state IN ({','.join('?' * len(states))})"
        args += states
    q += " ORDER BY day, a_min, id"
    return [_item(r) for r in con.execute(q, args)]


def get_item(con, plugin: str, item_id: int) -> dict | None:
    row = con.execute(f"SELECT {_ITEM_COLS} FROM analyzer_items "
                      "WHERE plugin=? AND id=?", (plugin, item_id)).fetchone()
    return _item(row) if row else None


def update_item(con, plugin: str, item_id: int, state: str | None = None,
                meta: dict | None = None, expect=None,
                now_ms: int | None = None) -> bool:
    """Move one finding along. Returns whether it moved.

    meta is merged into what is there, so two steps recording different things
    do not erase each other. expect is the state (or states) the finding must be
    in for this to apply -- a dismissal arriving after the item was sent should
    not be undone by a late "sent" -- and a refused move returns False rather
    than raising: it is a race, not an error.

    The payload is never touched. What was found stays what was found.
    """
    now = int(time.time() * 1000) if now_ms is None else now_ms
    cur = get_item(con, plugin, item_id)
    if cur is None:
        return False
    if expect is not None:
        allowed = [expect] if isinstance(expect, str) else list(expect)
        if cur["state"] not in allowed:
            return False
    new_meta = {**cur["meta"], **(meta or {})}
    with con:
        con.execute(
            "UPDATE analyzer_items SET state=?, meta=?, updated_ms=? "
            "WHERE plugin=? AND id=?",
            (state or cur["state"], json.dumps(new_meta, ensure_ascii=False),
             now, plugin, item_id))
    return True


def drop(con, plugin: str) -> None:
    """Remove everything one plugin has stored. Uninstalling is this, not a
    hunt through the filesystem for its files."""
    with con:
        con.execute("DELETE FROM analyzer_rows WHERE plugin=?", (plugin,))
        con.execute("DELETE FROM analyzer_runs WHERE plugin=?", (plugin,))
        con.execute("DELETE FROM analyzer_items WHERE plugin=?", (plugin,))


def ensure_tables(con, decl: str | None) -> None:
    """Create a plugin's own tables, for the plugin that genuinely needs them.

    The escape hatch from the generic store. It is a declaration rather than a
    plugin running DDL at import time, because the point is that "this plugin
    owns a table" is visible to someone reading the plugin — a plugin that just
    executes CREATE TABLE owns one invisibly.

    The core never writes to a table declared this way. Whatever a plugin takes
    into its own hands, it keeps in its own hands.
    """
    if decl:
        con.executescript(decl)
        con.commit()


# ── The Analyzer interface ────────────────────────────────────
#
# A module becomes an Analyzer by declaring what it reads, how it slices a day,
# what it asks, and how to read the answer:
#
#     INPUT   = "events:plaud" | "spans" | "day_events"
#     WINDOW  = 30 | "day"
#     PROMPT  = "...{text}..."
#     parse(reply)          -> list of row dicts
#     params(con, day, window) -> dict | None   optional
#     render_window(...)    -> str              optional
#
# Everything else — windowing, rendering, the cache, concurrency, the model call,
# storage, incrementality — is the core's. A plugin author writes a prompt and a
# parser, and gets "unchanged input costs nothing" without knowing it exists.

INPUT_LAYERS = ("spans", "day_events")


class BadDeclaration(ValueError):
    """An Analyzer declared something the core cannot resolve.

    Raised rather than skipped. A plugin with a typo in its INPUT is trying to
    participate and failing, which is a different fact from a module that never
    declared itself an Analyzer at all — and only one of them wants telling.
    """


def resolve_input(spec) -> tuple[str, str | None]:
    """"events:plaud" -> ("events", "plaud"); "spans" -> ("spans", None)."""
    if not isinstance(spec, str) or not spec.strip():
        raise BadDeclaration(f"INPUT must be a string, got {spec!r}")
    spec = spec.strip()
    if spec in INPUT_LAYERS:
        return spec, None
    kind, _, arg = spec.partition(":")
    if kind == "events" and arg:
        return "events", arg
    raise BadDeclaration(
        f"unknown INPUT {spec!r}; expected one of "
        f"{', '.join(INPUT_LAYERS)} or 'events:<source>'")


def resolve_window(spec, n_min: int = 1440) -> list[tuple[int, int]]:
    """Slice a day. An integer is minutes; "day" is one window over the whole day.

    A day is the largest slice on purpose. Anything wider would make one part of
    a period invalidate another's cache — new data on Friday would change a
    week-wide key and re-judge Monday, so a number read on Wednesday quietly
    becomes a different number on Friday with nothing having changed.

    n_min is the length of the day being judged — a daylight-saving or travel
    day is not 1440 minutes, and windows tile the day that was lived. The
    declaration is still checked against an ordinary day: windows that straddle
    midnight on ordinary days are a mistake whatever today is. On a day that is
    not a multiple of the window, the last one is shorter.
    """
    if spec == "day":
        return [(0, n_min)]
    if isinstance(spec, bool) or not isinstance(spec, int) or spec <= 0:
        raise BadDeclaration(
            f"WINDOW must be a positive number of minutes or 'day', got {spec!r}")
    if 1440 % spec:
        raise BadDeclaration(
            f"WINDOW of {spec} minutes does not divide a day evenly; "
            "a partial trailing window would be judged against less input "
            "than every other window of the day")
    return [(a, min(a + spec, n_min)) for a in range(0, n_min, spec)]


TARGET_MARK = "───── the stretch you are judging starts here ─────"
AFTER_MARK = "───── context after that stretch ─────"


#: The pseudo-source _gather tags the two inferred layers with. They have no
#: source behind them -- they are Umbra's own output -- so nothing in the
#: registry can render them.
INFERRED = "_inferred"


def _inferred_line(e) -> str:
    """One inferred span or calendar event, as the model reads it.

    Without this, a plugin declaring INPUT = "spans" gets an empty string for
    every window of every day: the default rendering asks the registry for the
    event's source, `_inferred` is not a source, and every line is dropped. The
    run reports `empty 48` where `called 48` belonged, writes nothing, and raises
    nothing -- the failure this codebase keeps finding, in the one layer the
    interface advertises and no bundled plugin happened to read.

    The prefix is already the start time, so this carries the end, the category
    and what was happening. `activity` is a span's wording and `title` is a
    calendar event's; a layer that has neither is not one of these two.
    """
    what = e.get("activity") or e.get("title") or ""
    also = e.get("also") or []
    parts = [p for p in (f"until {e['end']}" if e.get("end") else "",
                         e.get("cat") or "", what) if p]
    line = " · ".join(parts)
    return line + ("; also: " + ", ".join(also) if also else "")


def render_window(events, a: int, b: int, lo_ms: int, ctx: int = 0) -> str:
    """The default window rendering: each event through its own source.

    The per-event line belongs to the source, which is the only code that knows
    the shape of its own records — that is already how the digest for inference
    is built, and this reuses it rather than growing a second opinion about how a
    chrome visit reads.

    What the core owns is the assembly: which events fall in range, their order,
    and the separator that keeps the target stretch distinguishable from its
    context. Without that separator a model asked about one half hour answers
    about the whole window, every time.

    An Analyzer that needs something else — a source-specific dedupe, a different
    marker vocabulary — declares its own `render_window` and this is not used.
    Returning "" for a window with nothing in the target is deliberate: asking a
    model to judge a stretch it has no records for is paying it to guess.
    """
    from . import registry
    before, inside, after = [], [], []
    for e in events:
        m = (e["ts"] - lo_ms) // 60000
        if m < a - ctx or m >= b + ctx:
            continue
        fn = (_inferred_line if e.get("source") == INFERRED
              else registry.hook(e.get("source"), "render"))
        if fn is None:
            continue
        txt = fn({**e, "_min": m})
        if not txt:
            continue
        (inside if a <= m < b else (before if m < a else after)).append((m, txt))
    if not inside:
        return ""

    def fmt(rows):
        return [f"{R_HM(m)} {t}" for m, t in sorted(rows, key=lambda x: x[0])]

    out = fmt(before) + [TARGET_MARK] + fmt(inside)
    if after:
        out += [AFTER_MARK] + fmt(after)
    return "\n".join(out)


def R_HM(m):
    from . import render
    return render.hm(m)


def _gather(con, kind: str, arg: str | None, day: str, model: str,
            pad_min: int = 0):
    """The day's input for one Analyzer, as (events, lo_ms).

    Each layer is read through its existing owner rather than a query written
    here: db.load_range for events, inference.load for spans, load_events for
    calendar events. A second query shape would drift from the first.

    Raw events are read pad_min either side of the day, so that context reaches
    across midnight. Without it the first and last windows of every day are
    rendered with half their context missing: a conversation that started at
    23:50 arrives at 00:05 with no beginning. The lanes that read speech always
    loaded the padding themselves; the core now does it for every plugin.
    """
    from . import analyze, db, inference as INF
    lo, hi = analyze.day_bounds(day, con)
    if kind == "events":
        pad = max(int(pad_min), 0) * 60000
        return [e for e in db.load_range(con, lo - pad, hi + pad)
                if e.get("source") == arg], lo
    if kind == "spans":
        rows = INF.load(con, day).get(model) or []
    else:
        rows = INF.load_events(con, day).get(model) or []
    # Spans and calendar events carry minutes, not timestamps. Give them a ts so
    # one windowing rule serves all three layers.
    return [{**r, "ts": lo + INF.minute(r["start"]) * 60000, "source": INFERRED}
            for r in rows], lo


def _today() -> str:
    """The wall-clock date, for a prompt that needs to tell the model its own
    knowledge has a cutoff.

    Supplied by the core and **deliberately outside the cache key**. It changes
    every day, so folding it in would re-run every window of every day, forever,
    for an input that is ambient rather than evidence. An answer made on a given
    day is the answer for that day — the same reasoning that makes goal
    attribution stored rather than cached, because the model is an input nobody
    controls and silently recomputing history rewrites it.
    """
    import datetime as _dt
    return str(_dt.date.today())


def _day_minutes(con, day: str) -> int:
    from . import analyze
    lo, hi = analyze.day_bounds(day, con)
    return (hi - lo + 1) // 60000


def windows(con, mod, day: str, model: str) -> list[dict]:
    """Every window of one day that has a question in it.

    Separated from `run` so that a reader can ask whether an answer is current
    without being able to spend money finding out: a page drawing last week's
    hours needs to know whether they are stale, and it must not be able to
    trigger a re-judgement by looking.

    Each entry is {"a", "b", "text", "params", "digest"}. Windows the model will
    not be asked about -- nothing in them, or nothing to ask -- are absent
    rather than present and empty, because "no question" and "a question with no
    input" are the same fact here and neither is an answer.
    """
    kind, arg = resolve_input(getattr(mod, "INPUT"))
    ctx = int(getattr(mod, "CONTEXT_MIN", 0))
    events, lo = _gather(con, kind, arg, day, model, pad_min=ctx)
    pfn = getattr(mod, "params", None)
    render = getattr(mod, "render_window", None) or render_window

    out = []
    for a, b in resolve_window(getattr(mod, "WINDOW", 30), _day_minutes(con, day)):
        text = render(events, a, b, lo, ctx=ctx)
        if not text.strip():
            continue
        # params is asked per window, not per day. A day-shaped analyzer ignores
        # the window argument and answers once because it has one window; a
        # window-shaped one needs the difference — the tasks lane's prompt
        # carries the device buttons pressed during *this* half hour, and the
        # day's full set would be wrong in it.
        p = pfn(con, day, {"a": a, "b": b, "events": events, "lo_ms": lo}) \
            if callable(pfn) else None
        # A declared params hook returning None says there is no question here.
        # Found by moving goal attribution onto this interface: a day whose week
        # has no goals has plenty of input and nothing to ask about it, and
        # asking which of nothing a stretch advanced is paying for an empty
        # answer. `{}` remains the way to say "no parameters this window" — the
        # two are different, and only a declared hook can say the first.
        if callable(pfn) and p is None:
            continue
        out.append({"a": a, "b": b, "text": text, "params": p,
                    "digest": digest_of(text, p),
                    # Not part of the key: everything that matters in them is
                    # already in the rendered text or in params.
                    "events": events, "lo_ms": lo})
    return out


def current(con, name: str, mod, day: str, model: str) -> bool:
    """Has this analyzer already answered this day, against what is true now?

    The question a view asks before drawing a stored number. It covers both ways
    an answer goes stale -- new data in the window, and a changed parameter such
    as a rewritten goal -- because both move the same digest.

    A day with nothing to ask about is current. It has no outstanding question,
    and reporting it as pending would leave a warning on screen that no amount of
    running could ever clear.
    """
    return all(cached(con, name, day, model, w["a"], w["digest"])
               for w in windows(con, mod, day, model))


def run(con, name: str, mod, day: str, model: str, call=None, log=None,
        fresh: bool = False) -> dict:
    """Run one Analyzer over one day. Returns counts, never raises for one window.

    The shape of the return matches infer.run_day and the two voice lanes so the
    ops layer can report every analysis the same way.
    """
    from . import providers
    log = log or (lambda *_: None)
    total = len(resolve_window(getattr(mod, "WINDOW", 30), _day_minutes(con, day)))
    asked = windows(con, mod, day, model)

    jobs, reused = [], 0
    for w in asked:
        if not fresh and cached(con, name, day, model, w["a"], w["digest"]):
            reused += 1
            continue
        jobs.append({**w, "ok": False})
    skipped = total - len(asked)

    # PROMPT may be a callable. Found by moving a real lane onto this
    # interface: claims chooses between a Chinese and an English prompt from
    # config at run time, and a module-level string is fixed at import — it
    # would pin whichever language happened to be set the first time the process
    # loaded, which is not a failure anything would report.
    tpl = getattr(mod, "PROMPT")
    if callable(tpl):
        tpl = tpl()
    # Resolved only when there is something to ask: a missing key is an
    # error worth raising once, but a round where every window is cached
    # asks nothing and has nothing to report.
    got, failed = run_windows(
        call or (providers.resolve(model) if jobs else None), jobs,
        lambda r: tpl.format(day=day, text=r["text"], a=r["a"], b=r["b"],
                             today=_today(), **(r["params"] or {})),
        log=log, label=lambda r: R_HM(r["a"]))

    written, ids = 0, []
    fn = getattr(mod, "parse", None)
    key = getattr(mod, "KEY", None)
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise BadDeclaration(f"KEY must name a field of the rows, got {key!r}")
    for r, items in got:
        # parse sees the window it is parsing, params included. Discovered by
        # moving real lanes onto this interface: claims has to reject a quote
        # belonging to a neighbouring window, and tasks has to drop a proposal
        # that echoes something already dictated nearby. Neither can tell from
        # the model's array alone. A parser blind to the question it is
        # answering can only reshape, never judge.
        #
        # It also sees the window's raw events. The tasks lane needs who was
        # talking in the minutes around a sentence -- talking to a person or to
        # an agent -- and the minute a device button was pressed, neither of
        # which the model returns or the rendered text carries in usable form.
        window = {"a": r["a"], "b": r["b"], "text": r["text"],
                  "params": r["params"], "events": r["events"],
                  "lo_ms": r["lo_ms"]}
        rows = fn(items, window) if callable(fn) else items
        if key:
            new = save_items(con, name, day, model, r["a"], r["digest"], rows,
                             key)
            ids += new
            written += len(new)
        else:
            written += save_rows(con, name, day, model, r["a"], r["digest"],
                                 rows)
    out = {"windows": total, "called": len(jobs), "reused": reused,
           "empty": skipped, "failed": failed, "rows": written}
    if key:
        out["ids"] = ids          # the findings that are new this run
    return out


# ── A round ───────────────────────────────────────────────────

COUNTS = ("windows", "called", "reused", "empty", "failed", "rows")


def hm_min(t, fallback: int) -> int:
    """"HH:MM" -> minutes into the day; fallback when it does not read."""
    try:
        h, m = str(t).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return fallback


def model_for(name: str, conf: dict | None = None) -> str:
    """The model a plugin runs on: its own `[plugins.<name>] model`, else the
    one inference uses. The model is in every cache key, so changing it re-asks
    the windows it covers."""
    from . import config, infer
    conf = config.load() if conf is None else conf
    return (((conf.get("plugins") or {}).get(name)) or {}).get("model") or infer.MODEL


def run_days(con, name: str, mod, days, model: str | None = None,
             log=None) -> dict:
    """Run one Analyzer over several days. Returns the summed counts.

    One day raising does not cost the others, and is not silent: a day that
    was never judged reads, later, exactly like a day where nothing was found.
    The day is named in `errors` and the counts say how many there were.
    """
    model = model or model_for(name)
    log = log or (lambda *_: None)
    out = {k: 0 for k in COUNTS}
    out["errors"] = {}
    for day in days:
        try:
            r = run(con, name, mod, day, model, log=log)
        except Exception as e:                            # noqa: BLE001
            out["errors"][day] = f"{type(e).__name__}: {e}"
            continue
        for k in COUNTS:
            out[k] += r.get(k, 0)
    return out


def sync_plugin(con, name: str, mod, days, log=print) -> dict | None:
    """What a scheduled round (`umbra sync`, `umbra voice`) does for one plugin.

    A plugin may declare `sync(con, days, log)` when its round is more than
    judging the days it is handed — sending what it found and collecting what
    came back, or judging a range of its own choosing. Otherwise the round is
    run_days over the days the scheduler handed it.

    The hook is `sync` rather than `round` because a module-level `round`
    shadows the builtin for the whole plugin, and the first `round(x, 3)`
    anywhere in it then fails at run time — in a lane that computes scores,
    that was not hypothetical.
    """
    fn = getattr(mod, "sync", None)
    if callable(fn):
        return fn(con, days, log)
    r = run_days(con, name, mod, days, log=log)
    log(f"  {name}: {len(days)} days · asked {r['called']} · reused "
        f"{r['reused']} · rows {r['rows']}"
        + (f" · {r['failed']} calls failed" if r["failed"] else "")
        + (f" · {len(r['errors'])} days failed" if r["errors"] else ""))
    for day, err in r["errors"].items():
        log(f"  ⚠ {name} {day}: {err}")
    return r
