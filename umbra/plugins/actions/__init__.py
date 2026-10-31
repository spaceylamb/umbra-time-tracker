"""Tasks heard in what you said: a recording → proposals → a destination → your verdict.

    scan      one day's Plaud transcript, judged **in 30-minute windows** →
              proposals, kept in the core's findings store
    dispatch  proposals → the destination you chose, held for a person
    you       ▶ run it / ✕ not a task / ✓ already done   ← the gate is there
    verdicts  what you tapped comes back on a later round and is recorded

Not only "remind me to X". **Four kinds**: a question not yet worked out
(question), something to do (errand), a promise to someone else (followup), an
idea not yet decided (idea). The kind exists only on Umbra's side — a destination
need not care, and all four arrive as one kind of item.

**The gate is at the destination, not here.** The reference sink sends each
proposal held for review (Butler's `proposed` status, which its daemon never
claims), so nothing starts until you act on it. Umbra adds no second gate of its
own — two gates only make you say yes to one thing twice. A proposal must carry
the words and the minute they were said: a proposal with no quote cannot be
checked, the same discipline as "on what grounds" in the inference chain.

## Why it does not ride on inference

Inference already slices a day into windows, renders them and caches by the
rendered text, so asking "any tasks in here?" in the same call looks free. It
is not, for three reasons:

  1. Inference's cache is keyed by model. Sharing it would tie task freshness to
     whichever model inference happened to use; switch models and every task
     answer is thrown away.
  2. The two questions fail differently. One combined prompt means rewording
     inference **quietly changes task recall**, with nothing to report it — the
     pair this project guards against most: "the model said nothing" versus "we
     never asked".
  3. Measured on one day: every source rendered line by line came to 235,173
     characters, speech only 52%. The rest — browser titles, window titles,
     foreground apps — says nothing about what someone said they need to do, and
     feeding it in doubles the cost.

So this lane reads speech only, keeps its own findings and cache, and runs as
its own step in each round with its own fault isolation. What it shares with
inference is the 30-minute grid: window numbers have to line up, or a person
cannot cross-check one against the other.

## Not real-time

The floor is Plaud's upload delay (median 40 hours, measured). This catches
"things to deal with later", not "right now".
"""

import json
import os
import subprocess

from ... import analyzer, config
from .prompts import AFTER_MARK, DUP_PROMPT_EN, DUP_PROMPT_ZH, MARK_BLOCK_EN, MARK_BLOCK_ZH, PROMPT_EN, PROMPT_ZH, QUOTE_MARK_EN, QUOTE_MARK_ZH, TARGET_MARK, _CARD_TEXT, _card, _prompt, quote_mark, render_marks  # noqa: F401  (re-exported)
from .echo import DEDUPE_MIN_CHARS, DEDUPE_NEAR_MS, DEDUPE_THRESHOLD, ECHO_GRAM, ECHO_MIN_CHARS, ECHO_THRESHOLD, ECHO_WINDOW_MIN, _grams, echo_match, echo_pool, echo_score, same_utterance  # noqa: F401  (re-exported)
from .judge import CONF_ORDER, DOMAINS, MARK_NEAR_MIN, TYPES, _minute_of, _norm_conf, _norm_domain, _norm_type, _owns_quote, _speakers_in, marks_in  # noqa: F401  (re-exported)
from .store import PLUGIN, _LEGACY_COLS, _write, record, records  # noqa: F401  (re-exported)
from .verdicts import VERDICTS, _KEY, close, echoes, negatives, pending, queued, record_verdicts, reopen, set_status  # noqa: F401  (re-exported)

# Only config can be imported at module level: this module is loaded through
# the plugin registry, which can happen while db / analyze / digest are still
# initialising, and importing them at the top would be a circular import.

# What the core reads off this plugin (see docs/writing-a-plugin.md).
TITLE = "To-dos from recordings"
WHAT = "Finds things you said you'd do in your recordings."
ORDER = 2
SENDS = True                  # off until a sink is named and sending switched on
SEND_WHAT = "each to-do"
LEGACY_SECTION = True         # an [actions] section predates plugins
SAID_KIND = "task"            # what the drawer's ✓ / ✕ act on (said_mark)

WORKERS = analyzer.WORKERS   # one number, owned by analyzer.py
CONTEXT_MIN = 30          # minutes of speech shown either side of the window


# ── Context on the card ───────────────────────────────────────
# Given one sentence, the reader cannot recognise what they were talking about —
# worse, cannot tell "I did say that" from "the transcriber misheard".
#
# Excerpt radius, measured on 79 real proposals:
#   ±5 min   median 1,320 chars / max 4,038
#   ±10      median 2,494 / max 9,165       <- default
#   ±15      median 3,275 / max 13,649      too long on a phone
CONTEXT_WIN_MIN = 10
# Lines kept either side. **Lines are the unit a person reads** — ten minutes
# alone may be three sentences, ten minutes with four people setting up a tent
# forty sentences and 2,500 characters (measured). Cut by time alone, the one
# sentence is buried in the middle, which is no context at all. The time window
# becomes an outer guard: a sentence after half an hour of silence is not
# context.
CONTEXT_MAX_LINES = 8


# ── One task, said twice ──────────────────────────────────────
# A thing that matters comes up more than once — "I need to book the car in",
# and forty minutes later "remind me, the car service". Each window owns its
# own quote, so each finds it, and the drawer listed it twice.
#
# Text similarity cannot tell these apart, measured on titles: a restatement
# scored 0.43–0.48 while "send Maya the numbers" / "send Sam the numbers" — two
# tasks — scored 0.83. So the model judges, once per day and only when the
# round found something new: one short call over the day's titles, not one per
# window. The later one is held as `duplicate`, shown faded, and Undo brings
# it back — a wrong merge is visible and reversible, not lost.


def _conf() -> dict:
    return config.load()


# ── Window rendering (the old lane's) ─────────────────────────

def render(events, a: int, b: int, lo_ms: int, ctx: int = CONTEXT_MIN) -> str:
    """The speech transcript of window [a, b), with ctx minutes of context either
    side — the old lane's own rendering.

    Kept because its output is what the old cache (task_digests) recorded, and
    the cache bridge and its test compare against it byte for byte. The lane
    itself renders through render_window below.

    **No speech in the target stretch returns an empty string** — there is
    nothing to judge, a call would be waste, and the model would dutifully
    extract something from the context.
    """
    from ... import render as R
    rows = []
    for e in events:
        if e.get("source") != "plaud":
            continue
        text = (e.get("text") or "").strip()
        if not text:
            continue                    # a segment with no text is no evidence
        m = (e["ts"] - lo_ms) // 60000
        if m < a - ctx or m >= b + ctx:
            continue
        rows.append({"ts": e["ts"], "m": m, "text": text,
                     "speaker": e.get("speaker") or "?",
                     "file_id": e.get("file_id")})

    # One sentence captured by two tracks is fed once — otherwise it becomes two
    # proposals, and since the dedupe key is the quote and two transcriptions
    # differ, the key cannot stop it. It happened: one sentence about contacting
    # two people became two proposals, each needing a tap. A single-track window
    # is unchanged to the byte — the cache key is this text.
    rows, _ = dedupe_recordings(rows)

    before, inside, after = [], [], []
    for r in rows:
        row = f"{R.hm(r['m'])} [{r['speaker']}] {r['text']}"
        m = r["m"]
        (inside if a <= m < b else (before if m < a else after)).append((m, row))

    if not inside:
        return ""

    def fmt(rows):
        return [r for _, r in sorted(rows, key=lambda x: x[0])]

    out = fmt(before) + [TARGET_MARK] + fmt(inside)
    if after:
        out += [AFTER_MARK] + fmt(after)
    return "\n".join(out)


# ── The old window cache ──────────────────────────────────────

# This lane's own cache table. The mechanism is analyzer.load_digests /
# save_digests; only the table name is ours.
DIGEST_TABLE = "task_digests"


def load_digests(con, day: str, model: str) -> dict:
    from ... import analyzer
    return analyzer.load_digests(con, DIGEST_TABLE, day, model)


def _above_bar(conf: str) -> bool:
    """Good enough to send you? The bar is [actions] min_confidence in config;
    none by default.

    **unknown always passes.** A model that gives no confidence is not
    unconfident — switch to one that won't output the field, and treating silence
    as a low score would make a whole day's proposals vanish without an error.
    It is the same pair to keep apart as "no data" and "not imported".
    """
    bar = ((_conf().get("actions") or {}).get("min_confidence") or "").strip().lower()
    if bar not in CONF_ORDER or conf == "unknown":
        return True
    return CONF_ORDER.index(conf) >= CONF_ORDER.index(bar)


def echo_threshold() -> float:
    """0 or less turns the echo filter off."""
    v = (_conf().get("actions") or {}).get("echo_threshold")
    return float(v) if isinstance(v, (int, float)) else ECHO_THRESHOLD


def dedupe_threshold() -> float:
    """0 or less turns cross-recording merging off."""
    v = (_conf().get("actions") or {}).get("dedupe_threshold")
    return float(v) if isinstance(v, (int, float)) else DEDUPE_THRESHOLD


def dedupe_recordings(rows: list[dict], quote: str = "") -> tuple:
    """(rows kept, how many merged). rows look like {"m", "text", "file_id", ...}.

    Only rows from **different file_ids**, close in time, and alike as text are
    merged. The same thing twice in one recording is something said twice;
    merging it would rewrite the facts.

    Which one stays: the one carrying the quote beats everything (it anchors the
    card; without it the excerpt has no centre and transcript_excerpt falls back
    to the middle); otherwise the longer, since the shorter was usually cut.
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
                continue                      # the same recording: said again, not a repeat
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


def _self():
    import sys
    return sys.modules[__name__]


# ── Scanning a day ────────────────────────────────────────────

def _bridge_cache(con, day: str, model: str) -> int:
    """Carry this lane's old cache over to the core's, window by window.

    The old cache (task_digests) recorded which exact window texts had been
    answered. Without carrying it over, the first round after the move would
    ask every window of the recent days again — paying to get the same findings,
    worded a little differently, which the old table's quotes would not catch.
    Where the old cache answered exactly the text the window renders to now, the
    window is recorded as answered. Anything that differs is asked, as it would
    have been.
    """
    old = load_digests(con, day, model)
    if not old:
        return 0
    n = 0
    for w in analyzer.windows(con, _self(), day, model):
        prev = old.get(w["a"])
        if not prev or prev[0] != w["text"] or not prev[1]:
            continue
        if analyzer.cached(con, PLUGIN, day, model, w["a"], w["digest"]):
            continue
        if con.execute("SELECT 1 FROM analyzer_runs WHERE plugin=? AND day=? "
                       "AND model=? AND win_a=?",
                       (PLUGIN, day, model, w["a"])).fetchone():
            continue          # the core has its own answer for this window
        analyzer.mark_ran(con, PLUGIN, day, model, w["a"], w["digest"])
        n += 1
    return n


def mark_duplicates(con, day: str, ids, call=None, model: str | None = None,
                    log=None) -> int:
    """Hold back this round's new proposals that restate one already found
    that day. Only new ones move — what was listed before stays where it is —
    and nothing moves when the model cannot be asked: a failed check costs a
    duplicate in the list, never a task. Returns how many were held."""
    from ... import analyzer as AN, infer, providers
    log = log or (lambda *_: None)
    new = set(ids or [])
    rows = [r for r in records(con, day=day)
            if r["status"] not in ("echo", "duplicate")]
    if not any(r["id"] in new and r["status"] == "proposed" for r in rows) \
            or len(rows) < 2:
        return 0
    rows.sort(key=lambda r: (_minute_of(r["t"], r["win_a"] or 0), r["id"]))
    items = "\n".join(f"{r['id']} {r['t'] or '--:--'}"
                      f"{' NEW' if r['id'] in new else ''}  {r['task']}"
                      for r in rows)
    tpl = DUP_PROMPT_ZH if infer.lang() == "zh" else DUP_PROMPT_EN
    try:
        call = call or providers.resolve(model or infer.MODEL)
        pairs = AN.parse_json(call(tpl.format(items=items)))
    except Exception as e:                              # noqa: BLE001
        log(f"  ⚠ actions {day}: could not check for repeats: {e}")
        return 0
    order = {r["id"]: i for i, r in enumerate(rows)}
    held = 0
    for p in pairs if isinstance(pairs, list) else []:
        try:
            rep, first = int(p[0]), int(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        # Trust the rule, not the reply: a repeat is new, the original is
        # listed, and earlier.
        if rep not in new or first not in order or rep not in order \
                or order[first] >= order[rep]:
            continue
        if _write(con, rep, "duplicate", expect="proposed", dup_of=first):
            held += 1
    return held


def scan(con, day: str, model: str | None = None, call=None, log=None,
         fresh: bool = False) -> dict:
    """Scan one day's recordings. Returns {"fresh", "windows", "fresh_calls",
    "reused", "failed", "inserted", "echo_filtered", "domain_filtered"} — the
    same shape this lane always returned, so the round's log line is unchanged.

    Idempotent: a proposal is identified by its quote within the day, and one
    you dismissed does not come back. Incremental: a window whose text is
    byte-identical to last round is not asked again. Both are the core's now
    (KEY, and the run cache); this is the lane's view of the result.
    """
    from ... import db, infer
    model = model or infer.MODEL
    db.items_after_legacy(con)
    if not fresh:
        _bridge_cache(con, day, model)
    r = analyzer.run(con, PLUGIN, _self(), day, model, call=call, log=log,
                     fresh=fresh)
    dupes = mark_duplicates(con, day, r.get("ids"), call=call, model=model,
                            log=log)
    new = [x for x in records(con, day=day) if x["src"] == "item"
           and x["id"] in set(r.get("ids") or [])]
    skip = {str(d).strip().lower() for d in
            ((_conf().get("actions") or {}).get("skip_domains") or [])}
    fresh_rows = [x for x in new if x["status"] not in ("echo", "duplicate")]
    return {"fresh": fresh_rows, "windows": r["called"], "duplicates": dupes,
            "fresh_calls": r["called"] - r["failed"], "reused": r["reused"],
            "failed": r["failed"], "inserted": len(new),
            "echo_filtered": len(new) - len(fresh_rows) - dupes,
            "domain_filtered": sum(1 for x in fresh_rows if not x["marked"]
                                   and (x["domain"] or "unknown") in skip)}


# ── Sending ───────────────────────────────────────────────────

def span_context(con, day: str, minute: int) -> dict | None:
    """"What was going on then" — **reusing the spans inference already computed,
    with no new model call**.

    The day's activity is already in the inferences table (activity, who with,
    category). Asking a model again would buy a second answer that may
    contradict the timeline — and two chains describing one stretch differently
    is exactly the "both look right, which do I believe" this project cannot
    accept.

    Nothing returns None, not an empty string: "inference has not run on that
    day" and "that half hour really was empty" are different, and the card must
    be able to say which.
    """
    from ... import infer, tz
    # Only the day's current cut: after a timezone change an old row at the
    # same minute of the day is a different moment.
    lo, hi = tz.bounds(con, day)
    rows = list(con.execute(
        "SELECT a_min, b_min, cat, payload, model FROM inferences "
        "WHERE day=? AND day_lo=? AND day_n=? AND a_min<=? AND b_min>? "
        "ORDER BY a_min", (day, lo, (hi - lo + 1) // 60_000, minute, minute)))
    if not rows:
        return None
    # A day may have results from several models. Prefer the configured one,
    # so the card and the timeline on the page are the same judgement.
    pick = next((r for r in rows if r[4] == infer.MODEL), rows[0])
    a, b, cat, payload, _ = pick
    try:
        d = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    return {"a": a, "b": b, "cat": cat,
            "activity": d.get("activity") or "",
            "with": d.get("with") or ""}


def transcript_excerpt(con, day: str, minute: int, quote: str,
                       win: int | None = None,
                       max_lines: int | None = None) -> str:
    """The transcript win minutes either side, with the sentence marked.

    **Computed at send time, never stored.** The events are already in the
    database; a second copy would be two truths for one piece of data, and
    changing the window would need a rescan.

    With speakers: whether something was said to a person or to oneself shows
    only in that column.
    """
    from ... import analyze, render as R
    spec = _conf().get("actions") or {}
    if win is None:
        v = spec.get("context_win_min")
        win = v if isinstance(v, int) and v > 0 else CONTEXT_WIN_MIN
    if max_lines is None:
        v = spec.get("context_max_lines")
        max_lines = v if isinstance(v, int) and v > 0 else CONTEXT_MAX_LINES
    cap = max_lines
    lo, _ = analyze.day_bounds(day, con)
    rows = []
    for ts, payload in con.execute(
            "SELECT ts, payload FROM events WHERE source='plaud' "
            "AND ts>=? AND ts<? ORDER BY ts",
            (lo + (minute - win) * 60000, lo + (minute + win) * 60000)):
        try:
            d = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        text = (d.get("text") or "").strip()
        if not text:
            continue
        rows.append({"ts": ts, "m": (ts - lo) // 60000, "text": text,
                     "speaker": d.get("speaker") or "?",
                     "file_id": d.get("file_id")})

    # Merge one sentence captured by two recordings first — otherwise half the
    # max_lines budget is spent on repeats. **Before** trimming: trimming first
    # would leave half the room empty.
    rows, merged = dedupe_recordings(rows, quote)

    # A stretch captured by N recordings needs N times the lines to show as
    # much distinct content. Deduplication cannot make that up (0.9 removes only
    # 7%), but this step is deterministic and compares no text. It keeps what
    # "lines are the unit a person reads" means: eight lines are eight different
    # things said, not eight rows.
    cap = cap * max(1, len({r["file_id"] for r in rows}))

    out, hit = [], None
    for r in rows:
        line = f"{R.hm(r['m'])} [{r['speaker']}] {r['text']}"
        # Find "this one" among dozens of lines, or the excerpt is just a wall
        if hit is None and quote and quote.strip() and quote.strip() in r["text"]:
            line += quote_mark()
            hit = len(out)
        out.append(line)

    if not out:
        return ""
    # Trim to the budget around that sentence. If it cannot be found (the
    # transcript and the quote disagree), take the middle — better than the top.
    center = hit if hit is not None else len(out) // 2
    a, b = max(0, center - cap), min(len(out), center + cap + 1)
    kept = out[a:b]
    # What was left out **is said**: a silent cut reads as "that was all that
    # was said".
    if a > 0:
        kept.insert(0, _card("before").format(a))
    if b < len(out):
        kept.append(_card("after").format(len(out) - b))
    # What was merged **is said** too — the same rule.
    if merged:
        kept.append(_card("merged").format(merged))
    return "\n".join(kept)


def item_prompt(con, day: str, t: str | None, task: str, quote: str) -> str:
    """The text of the item sent to the destination.

    **The words go with it** — the agent must have what you said, not only the
    model's rewording; if the rewording is off, an agent that sees the words can
    correct it.

    Two blocks of context follow, because with one sentence you cannot recognise
    what you were talking about, let alone tell "I did say that" from "the
    transcriber misheard".

    Disambiguation ("that one", "him") is left to the destination: an agent
    there may have your email, calendar and chat; Umbra has only speech.
    Whoever has the information does the guessing.
    """
    when = f"{day} {t}" if t else day
    head = _card("origin")
    parts = [task, "", head + "─" * (50 - len(head)), when]

    minute = _minute_of(t, -1) if t else -1
    ctx = span_context(con, day, minute) if minute >= 0 else None
    if ctx:
        who = _card("with").format(ctx["with"]) if ctx["with"] else ""
        parts.append(_card("doing").format(ctx["activity"], who)
                     + f"   [{_hm_span(ctx['a'], ctx['b'])} · {ctx['cat']}]")
    else:
        # A missing line must not read as "nothing was happening then"
        parts.append(_card("no_infer"))
    parts += ["", _card("said").format(quote)]

    excerpt = transcript_excerpt(con, day, minute, quote) if minute >= 0 else ""
    if excerpt:
        head = _card("around")
        parts += ["", head + "─" * (50 - len(head)), _card("around_note"), "",
                  excerpt]

    parts += ["", "─" * 50, _card("ask")]
    return "\n".join(parts)


def _hm_span(a: int, b: int) -> str:
    return f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}"


# At most this many sent per round. **Capped by default**, because the backlog
# the moment sending is switched on is where this lane hurts most: measured, a
# real database held 32 old proposals over 44 days of speech. Dozens of cards
# landing at once means nobody reads that inbox again.
MAX_PER_RUN = 5


def dispatchable(con, today: str | None = None) -> list[tuple]:
    """The proposals that qualify to go out this round, oldest first.

    Three filters, each with one job:
      status='proposed'   not sent yet
      min_confidence      ones the model itself is unsure of (none by default)
      dispatch_days       too old to revive (none by default) — something said
                          three weeks ago and left alone is usually dead, and
                          turning it into a card to deal with is noise
    Then cut to max_per_run. **What is cut stays queued** for the next round:
    silently dropping something you said for good is far worse than sending it
    three days late.
    """
    import datetime as _dt
    spec = _conf().get("actions") or {}
    # Marked ones first: when the cap cuts, one a person pressed a button for
    # must not be pushed out by one the model guessed.
    rows = [(r["id"], r["day"], r["task"], r["quote"], r["confidence"], r["t"],
             r["domain"], r["speakers"], r["marked"])
            for r in sorted(records(con, status="proposed"),
                            key=lambda r: (-(r["marked"] or 0), r["day"],
                                           r["t"] or "", r["id"]))]
    # "No reminders for work." unknown always passes — the model saying
    # nothing is not the model saying work, and treating silence as a match
    # would swallow a whole day after a model switch.
    # Marked proposals skip all three gates below. Each of them guesses one thing
    # (sure enough? work? talking to oneself?), and a mark is the person's own
    # press — nobody presses a button for something they don't want.
    skip = {str(d).strip().lower() for d in (spec.get("skip_domains") or [])}
    if skip:
        rows = [r for r in rows if r[8] or (r[6] or "unknown") not in skip]

    # Proposals with only one person talking are probably said to an AI
    # (measured echo rate: 68% with one speaker, 34% with three). **Off by
    # default** — the echo filter already catches most, this gate catches the
    # rest, and what it wrongly kills is "remembering something real while
    # alone", which most deserves keeping. An unknown count (old proposals,
    # NULL) is not blocked: reading NULL as 1 would silently swallow everything
    # from before the field existed.
    ms = spec.get("min_speakers")
    if isinstance(ms, int) and ms > 0:
        rows = [r for r in rows if r[8] or r[7] is None or r[7] >= ms]

    days = spec.get("dispatch_days")
    if isinstance(days, int) and days > 0:
        today = today or str(_dt.date.today())
        floor = str(_dt.date.fromisoformat(today) - _dt.timedelta(days=days - 1))
        rows = [r for r in rows if r[1] >= floor]

    rows = [r[:6] for r in rows if r[8] or _above_bar(r[4] or "unknown")]
    cap = spec.get("max_per_run")
    cap = cap if isinstance(cap, int) and cap > 0 else MAX_PER_RUN
    return rows[:cap]


def _item(con, pid: int, day: str, t: str | None, task: str,
          quote: str) -> dict:
    """The item this lane sends. Shared with preview, so that what you are shown
    before sending is what is sent — a preview built separately drifts, and the
    drift is invisible until something goes out that nobody reviewed."""
    return {"title": task[:80],
            "body": item_prompt(con, day, t, task, quote),
            "key": f"umbra:{day}:{pid}",
            "visible": True,
            # Held for a person, never started on its own. A proposal is
            # something to decide about, not something to run.
            "interactive": True}


def preview(con, today: str | None = None) -> list[dict]:
    """**Exactly** what the next dispatch would send. Nothing is sent.

    Deliberately does not check for credentials — if "look before sending"
    required credentials first, it would come one step too late.
    """
    return [{"id": r[0], "day": r[1], "t": r[5], "task": r[2],
             "quote": r[3], "confidence": r[4],
             "body": _item(con, r[0], r[1], r[5], r[2], r[3])}
            for r in dispatchable(con, today=today)]


def _sink_name() -> str | None:
    """Which destination this lane sends to, or None when sending is off.

    Both halves of the gate live in sink.enabled_for. The old
    [actions] dispatch = "butler" key is still honoured so an upgrade does not
    silently stop sending.
    """
    from ... import sink as S
    name = S.enabled_for("actions", _conf())
    if name:
        return name
    legacy = ((_conf().get("actions") or {}).get("dispatch") or "").strip()
    return legacy or None


def dispatch_enabled() -> bool:
    """Is sending switched on?

    Asks the same question dispatch() acts on. It used to check only the old
    `[actions] dispatch = "butler"` key, so a sink configured the documented way
    — [plugins.actions] sink + send — was refused at the gate while dispatch()
    itself would have sent: the scheduled round never got as far as asking it.

    Extraction is local, free and has no side effects, so it runs whenever the
    lane is enabled. Sending is not: it creates around twenty cards somewhere
    else, and installing something that immediately starts writing into an
    inbox is a decision made before anyone has seen a single proposal.
    """
    return _sink_name() is not None


def dispatch(con, log=print, today: str | None = None) -> int:
    """Send the proposals that have not gone out yet. Returns how many went.

    No project or routing hint goes with them. The destination knows which
    projects exist and Umbra does not, so the decision is made on the side that
    has the information; anything it cannot place lands in its own catch-all.

    Held-back and truncated proposals **stay in the database** with their status
    untouched. Dropping them would make "it found this and chose not to send it"
    and "it found nothing" the same thing, and those two are fixed in completely
    different places. `umbra tasks list` still shows them.
    """
    from ... import sink as S
    name = _sink_name()
    if not name:
        return 0
    send = S.resolve(name)["send"]
    sent = 0
    for pid, day, task, quote, _conf_v, t in dispatchable(con, today=today):
        try:
            tid = send(_item(con, pid, day, t, task, quote))
        except S.Duplicate as d:
            # This lane keys on the proposal id, so a duplicate means the
            # destination already has it. Recording that is the whole point of
            # the key — otherwise every round resends everything.
            tid = d.receipt
        except S.Fatal:
            # The destination is misconfigured and every remaining item would
            # land the same way — each one running unattended. Stop rather than
            # lose a day's worth one at a time.
            con.commit()
            raise
        except Exception as e:                            # noqa: BLE001
            # **Status does not move on failure.** Marking it dispatched would
            # say it was sent when it was not.
            log(f"  ✗ [{pid}] dispatch failed; status stays proposed: {e}")
            continue
        _write(con, pid, "dispatched", dispatch_id=tid)
        S.record(con, PLUGIN, str(pid), name, S.SENT, external_id=tid)
        sent += 1
    con.commit()
    return sent


def pull_verdicts(con, log=print) -> int:
    """Collect what you tapped. Returns how many verdicts landed.

    In one request rather than one per proposal. The question is "what did
    somebody act on", not "what happened to task X" asked N times, and this lane
    is in no hurry. Receipts the destination names that we never issued are
    skipped, so a destination that reports everything it knows is still correct.
    """
    from ... import sink as S
    name = _sink_name()
    if not name:
        return 0
    fn = S.resolve(name).get("feedback")
    if not callable(fn):
        # A destination that reports nothing back is a legitimate one to send to;
        # it just means the taps happen somewhere Umbra cannot see.
        return 0
    try:
        items = [e.get("result") for e in fn() or []]
    except Exception as e:                                # noqa: BLE001
        log(f"  ✗ could not collect verdicts: {e}")
        return 0
    return record_verdicts(con, [i for i in items if i])


def make_sink():
    """The executor configured in [actions] — the old outlet for people without a
    destination service, used by `umbra tasks approve`.

    command: a shell command, with the task in UMBRA_TASK / UMBRA_QUOTE
    environment variables — **never interpolated into the string**, which would
    hand model output straight to a shell.
    webhook: POST JSON to a URL.
    """
    spec = _conf().get("actions") or {}
    kind = spec.get("sink")
    if kind == "command":
        cmd = spec["command"]

        def run(task: dict) -> str:
            env = {**os.environ, "UMBRA_TASK": task["task"],
                   "UMBRA_QUOTE": task.get("quote", ""),
                   "UMBRA_DAY": task.get("day", ""),
                   "UMBRA_TIME": task.get("t") or ""}
            r = subprocess.run(["/bin/sh", "-c", cmd], env=env,
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"sink command exited {r.returncode}: "
                                   f"{r.stderr.strip()[:200]}")
            return r.stdout.strip()[:400]
        return run
    if kind == "webhook":
        url = spec["url"]

        def post(task: dict) -> str:
            from ... import net
            net.post_json(url, task)
            return f"POST {url}"
        return post
    raise RuntimeError(
        "no sink configured. Add to ~/.config/umbra/config.toml:\n"
        "  [actions]\n"
        '  sink = "command"\n'
        '  command = "echo \\"$UMBRA_TASK\\" >> ~/todo.md"   # or sink = "webhook" + url')


def approve(con, pid: int, sink=None) -> bool:
    """Execute through the local outlet (make_sink).

    A failed execution **leaves the status alone** — marking it approved would
    say it went when it did not.
    """
    rec = record(con, pid)
    if not rec or rec["status"] != "proposed":
        return False
    task = {"day": rec["day"], "t": rec["t"], "task": rec["task"],
            "quote": rec["quote"]}
    sink = sink or make_sink()
    try:
        result = sink(task)
    except Exception as e:                              # noqa: BLE001
        print(f"  ✗ execution failed; status stays proposed: {e}")
        return False
    set_status(con, pid, "approved", str(result))
    return True


# ── The Analyzer declaration ──────────────────────────────────
#
# What this lane reads, how it slices a day, what it asks, and how to read the
# answer. The rest belongs to the core.

INPUT = "events:plaud"
WINDOW = 30
# A proposal is identified by the words themselves, within a day — the same key
# the old table deduplicated on. Rewordings of one task share a quote; two
# different tasks almost never do.
KEY = "quote"
# CONTEXT_MIN is 30, defined above. Thirty minutes either side, because a task
# revealed in passing usually needs the conversation around it to be a task at
# all — "we should do that" means nothing without what came before.


def PROMPT() -> str:
    """A callable: the wording is chosen from config at run time, and a
    module-level string would pin whichever language loaded first."""
    return _prompt()


def render_window(events, a, b, lo_ms, ctx=CONTEXT_MIN):
    """The source's own rendering, with this lane's marker wording.

    The markers are byte-identical to what this lane has always sent, because
    the rendered text is the inference cache key — rewording them would re-run
    every cached day at real cost, and this change is not worth that.
    """
    from ...importers import plaud
    return plaud.render_window(events, a, b, lo_ms, ctx=ctx,
                               marks=(TARGET_MARK, AFTER_MARK))


def params(con, day: str, window: dict) -> dict:
    """What no window contains: everything nearby that could make a proposal an
    echo.

    He dictates tasks to an agent. At the moment he says one out loud the work
    is already under way, so extracting it and pushing it back as a to-do turns
    the day's finished work into a queue of cards — measured on 8-17, 31 of 32
    proposals were that.

    It belongs in params rather than being loaded inside parse because it is an
    input to the judgement, and the cache key has to cover it: a prompt arriving
    later can turn something that was a real task into an echo, and a cache that
    did not notice would keep the stale answer.
    """
    from ... import analyze, db
    lo, hi = analyze.day_bounds(day, con)
    span = ECHO_WINDOW_MIN * 60000
    return {
        "echoes": echo_pool(db.load_range(con, lo - span, hi + span), lo),
        # The device buttons pressed during *this* half hour. The day's full set
        # would be wrong in the prompt: a mark means "look harder right here",
        # and pointing it at the wrong stretch is worse than not having it.
        "marks": render_marks(marks_in(window["events"], window["a"],
                                       window["b"], window["lo_ms"])),
        # Quotes the old table already holds for this day. The new store's key
        # cannot see them, so without this the first rounds after the move
        # would find each one again and send it a second time. The old table
        # never changes, so this never moves the cache.
        "_known": sorted({q for (q,) in con.execute(
            "SELECT quote FROM proposals WHERE day=?", (day,))}),
        # Read at judging time, as it always was: changing the threshold later
        # does not re-classify what was already found.
        "_echo_bar": echo_threshold(),
    }


def parse(items, window):
    """The model's array -> proposals, with this lane's own rejections.

    Kept plugin-local rather than pushed into the core because the rules differ
    per lane: quote ownership here runs at thirty minutes of context, where
    "not found in this window" does not mean what it means at zero.

    Each proposal is stored with how it was judged: whether a device button was
    pressed near it, how many people were talking around it, and whether it
    echoes something dictated to an agent. An echo arrives already held back —
    recorded, not dropped, because "it found this and judged it an echo" and "it
    found nothing" need different fixes, and only one of them is a bug.
    """
    p = window.get("params") or {}
    known = set(p.get("_known") or [])
    bar = p.get("_echo_bar", ECHO_THRESHOLD)
    events = window.get("events") or []
    lo = window.get("lo_ms") or 0
    speech = [e for e in events if e.get("kind") != "mark"]
    marks = marks_in(events, window["a"], window["b"], lo)
    out = []
    for x in items:
        if not isinstance(x, dict) or not x.get("task") or not x.get("quote"):
            continue                # nothing to check the proposal against
        if x["quote"] in known:
            continue                # already found, before the move
        if not _owns_quote(window["text"], x["quote"]):
            continue                # belongs to a neighbouring window
        at = _minute_of(x.get("time"), window["a"])
        # A mark is "this moment", not "this half hour".
        near = any(abs(m["_min"] - at) <= MARK_NEAR_MIN for m in marks)
        # Five minutes either side: who was talking around a sentence says
        # whether he was speaking to a person or to himself. The whole window
        # would let a conversation twenty minutes earlier decide it.
        nsp = _speakers_in(speech, lo, at - 5, at + 5)
        # Marked means not an echo: he pressed the button while speaking to
        # someone, which is emphasis. The echo filter exists for dictation to an
        # agent, and nobody presses a hardware button while dictating.
        score, hit = (echo_match(x["quote"], p.get("echoes") or [], at=at)
                      if bar > 0 and not near else (0.0, None))
        out.append({
            "a": window["a"], "b": window["b"],
            "t": x.get("time"), "task": x["task"], "quote": x["quote"],
            "type": _norm_type(x.get("type")),
            "confidence": _norm_conf(x.get("confidence")),
            "domain": _norm_domain(x.get("domain")),
            "marked": bool(near), "speakers": nsp,
            "echo_score": round(score, 3), "echo_text": (hit or "")[:500] or None,
            "state": "echo" if score >= bar > 0 else "proposed",
        })
    return out


def sync(con, days, log=print):
    """One scheduled round: extract the days handed over, then send and collect.

    Sending gets its own try. Extraction cost money and its results are already
    stored, and one 503 from the destination should not make the whole round
    count as never having run; the next round sends whatever has not gone out.
    """
    for day in days:
        r = scan(con, day)
        waiting = len(r["fresh"]) - r.get("domain_filtered", 0)
        log(f"  actions {day}: reused {r['reused']} / called "
            f"{r['fresh_calls']} windows -> extracted "
            f"{r.get('inserted', len(r['fresh']))}"
            f" / echoes {r.get('echo_filtered', 0)} / said again "
            f"{r.get('duplicates', 0)} / domain-filtered "
            f"{r.get('domain_filtered', 0)} / awaiting dispatch {waiting}")
    if not dispatch_enabled():
        return
    try:
        n = dispatch(con, log=log)
        if n:
            log(f"  actions: dispatched {n}")
        # Send before pulling: nothing sent this round has been acted on yet, so
        # what comes back belongs to earlier rounds. The other order is not
        # wrong, merely a wasted trip.
        v = pull_verdicts(con, log=log)
        if v:
            log(f"  actions: {v} verdicts pulled back")
    except Exception as e:                    # noqa: BLE001
        log(f"  ✗ actions dispatch/verdicts: {e}")


# ── The drawer (api.said / api.said_mark) ─────────────────────

def said(con, date: str, conf: dict) -> dict:
    """This lane's part of the day's drawer: the tasks heard that day, and how
    many were held back as echoes. Echoes — things said while dictating to an
    agent — are counted, not listed: saying them was already doing them. They
    are kept so "found but held back" stays distinguishable from "never found",
    which is why the count is sent at all. `sending` is whether anything here
    has left the machine."""
    from ... import sink
    tasks, held = [], 0
    for r in records(con, day=date):
        if r["status"] == "echo":
            held += 1
            continue
        t, w = r["t"], r["win_a"]
        w = w if w is not None else (analyzer.hm_min(t, 0) // 30) * 30
        tasks.append({"id": r["id"], "t": t or "", "at": analyzer.hm_min(t, w),
                      "win": [w, w + 30], "task": r["task"], "quote": r["quote"],
                      "type": r["type"] or "unknown",
                      "domain": r["domain"] or "unknown",
                      "confidence": r["confidence"] or "unknown",
                      "status": r["status"], "verdict": r["verdict"],
                      "marked": bool(r["marked"]),
                      "speakers": r["speakers"]})
    return {"tasks": tasks, "held": held,
            "sending": sink.enabled_for(PLUGIN, conf) is not None}


def said_mark(con, item_id: int, body: dict) -> dict:
    """The page's ✓ and ✕: {"verdict": "already_done" | "not_a_task"}, or null
    to reopen. Without them the drawer could only be read: a sink that only
    receives never sends a verdict back, so the only way to close a task was
    `umbra tasks dismiss` — which the drawer then did not show."""
    if record(con, item_id) is None:
        raise LookupError(f"no task {item_id}")
    verdict = body.get("verdict")
    ok = reopen(con, item_id) if verdict is None else close(con, item_id, verdict)
    return {"ok": ok}


# ── The command line (umbra tasks) ──────────────────────────

CLI_NAME = "tasks"
CLI_HELP = "tasks heard in what you said → a destination you choose → your verdicts"


def cli_args(p) -> None:
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "scan", "preview", "dispatch", "verdicts",
                            "approve", "dismiss", "echoes", "negatives"])
    p.add_argument("id", nargs="?", help="a date for scan; a proposal id for approve/dismiss")
    pv = p.add_mutually_exclusive_group()
    pv.add_argument("--done", dest="verdict", action="store_const", const="already_done",
                    help="dismiss: it was a real task, already dealt with")
    pv.add_argument("--not-a-task", dest="verdict", action="store_const", const="not_a_task",
                    help="dismiss: extraction was wrong (counts in negatives)")


def cli(args):
    """Tasks heard in what you said: scan → preview → dispatch → your verdicts.

    Nothing leaves this machine unless sending is switched on and you run
    dispatch (or a round does). The destination holds what arrives for review,
    so a proposal never starts work on its own.
    """
    from ... import db
    con = db.connect()
    if args.action == "scan":
        import datetime as _dt
        day = args.id or str(_dt.date.today())
        r = scan(con, day, log=print)
        print(f"  {day}: {len(r['fresh'])} new proposals "
              f"(called {r['fresh_calls']} / reused {r['reused']} windows"
              + (f", {r['failed']} failed" if r["failed"] else "") + ")")
    elif args.action == "preview":
        rows = preview(con)
        if not rows:
            print("  the next round would send nothing.")
        for r in rows:
            print(f"  [{r['id']}] {r['day']} {r['t'] or '--:--'} "
                  f"({r['confidence'] or '?'})  {r['task']}")
            print(f"        said: {r['quote']}")
        print(f"\n  {len(rows)} this round, {queued(con)} queued in all."
              f"\n  Nothing has been sent. To send: umbra tasks dispatch")
    elif args.action == "dispatch":
        print(f"  dispatched {dispatch(con)}")
    elif args.action == "verdicts":
        print(f"  pulled back {pull_verdicts(con)} verdicts")
    elif args.action == "approve":
        ok = approve(con, int(args.id))
        print("  ✓ executed" if ok else "  ✗ not executed (bad id / not pending / sink failed)")
    elif args.action == "dismiss":
        # The same two verdicts as the drawer, and one of them has to be
        # named: "dismissed" alone cannot say whether extraction was wrong,
        # and only "not a task" may count against it (negatives).
        verdict = args.verdict
        if verdict is None:
            raise SystemExit("  say which: umbra tasks dismiss <id> --done | --not-a-task")
        if close(con, int(args.id), verdict):
            print(f"  closed: {'not a task' if verdict == 'not_a_task' else 'done'}")
        else:
            print("  ✗ not closed (bad id, or already closed)")
    elif args.action == "echoes":
        rows = echoes(con)
        if not rows:
            print("  nothing was held back as an echo.")
        for r in rows:
            print(f"  {r['day']} {r['t'] or '--:--'} ({r['score']:.0%})  {r['task']}")
            print(f"        recorded: {r['quote'][:70]}")
            print(f"        matched:  {r['match'][:70]}")
    elif args.action == "negatives":
        neg = negatives(con)
        if not neg:
            print("  no negatives yet — nothing has been marked \"not a task\".")
        for n in neg:
            print(f"  {n['day']} {n['t'] or '--:--'} [{n['type'] or '?'}] {n['task']}")
            print(f"        quote: {n['quote']}")
            if n["note"]:
                print(f"        you said: {n['note']}")
    else:
        rows = pending(con)
        if not rows:
            print("  no pending proposals. Scan a day: umbra tasks scan 2026-08-19")
        for pid, day, t, task, quote, typ, status in rows:
            mark = "sent" if status == "dispatched" else "local"
            print(f"  [{pid}] {day} {t or '--:--'} [{typ or '?'}] {task}  ({mark})")
            print(f"        quote: {quote}")
