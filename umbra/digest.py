"""Compress every source inside one time window into a block of text — **this
is what the model is given**.

The first link in the three-stage chain, and the deterministic one:

    digest   all sources in a window -> compressed text   <- this file
    label    text -> activity spans                       the model
    expand   spans -> 288 slots + validation              deterministic
                                                          (inference.py)

This used to be lab/digest.py, run once and left in a scratch directory, which
was cleared on 2026-08-18. Moved into the repo along with every trap it hit.

The compression ratio is the crux: 8-17 had 6,053 raw records and 200k
characters in a day, compressed into 48 blocks of a few thousand characters
each. Uncompressed, a single window exceeds the context; over-compressed, the
model has nothing to go on. Which is why **how much was compressed away has to
be visible** — each block is stored with its n_rec alongside in the digests
table (see inference.save_digests).

## The mistake this layer invites: silent fabrication

No error, nothing missing — just **a record invented that never existed**, and
the model believes it:

    WHOOP recovery  read as a workout    -> a "workout" span out of nowhere
    weread daily    read as an instant   -> "reading" at 09:00 (that figure is
                                            a whole day's total)
    location gap    read as a place      -> "at ?" (a gap means precisely that
                                            we do not know where)

All the same shape: the source has a kind distinguishing "something that
happened at a moment" from "a daily total", and the code used a binary else to
handle four kinds. **A record with no position in time should simply not
appear.**

## Speakers are not mapped

An earlier version had a hard rule rewriting Speaker 2 as "me" (52 times across
two days, without exception). But segment.py had already stated that Speaker
1/2 are numbered *within a single recording* and do not carry across files —
three files happening to agree does not make the fourth agree. The error lands
in the `with` column, which is exactly the field separating "a Zoom work
meeting" from "talking to family". Better to let the model judge from content.
"""

from . import registry, render as _R

WINDOW_MIN = 30
CONTEXT_MIN = 60          # how much to show either side of the target stretch

# The target stretch and its context must be separable: the model is asked to
# judge only that half hour, and without a separator it folds activity from the
# context into its answer.
TARGET_MARK = "───── the stretch you are judging starts here ─────"
AFTER_MARK = "───── context after that stretch ─────"


def windows(win: int = WINDOW_MIN, n_min: int = 1440):
    """A day cut into fixed slots. No overlaps, no gaps.

    n_min is how many minutes **this day** has, not the constant 1440: the two
    daylight-saving days are 1380 / 1500, a day crossing timezones can be 600 or
    2340. With 1440 hard-coded a short day gets empty windows (model calls for
    nothing) and a long day's end is never read.

    The last slot is clamped to n_min: a day's length need not be a multiple of
    win."""
    return [(a, min(a + win, n_min)) for a in range(0, n_min, win)]


_hm = _R.hm




def _overlap(spans) -> bool:
    """Do any two of these intervals overlap? Intervals are (start, end) ms."""
    xs = sorted(spans)
    return any(nxt[0] < cur[1] for cur, nxt in zip(xs, xs[1:]))


def line(e) -> str | None:
    """One event -> one line of text. **Returns None when it has no position
    in time.**

    The source owns this: each module declares its own render(), and this is the
    dispatch. It used to be an if/elif chain over every built-in source name,
    which meant a source not written into the chain contributed nothing to the
    digest — no error, no dropped counter, and its rows still visible in the
    page's drill-down, so it looked present while being invisible to the model.

    A source with no render() still returns None, which is the same answer the
    chain gave an unknown name. The difference is that it is now a source's own
    omission rather than a list somebody forgot to edit.
    """
    fn = registry.hook(e.get("source"), "render")
    return fn(e) if fn is not None else None


_span = _R.span


def render(events, a: int, b: int, ctx: int = CONTEXT_MIN, lo_ms: int = 0) -> str:
    """The digest for [a, b), with ctx minutes of context either side.

    **Filtered by interval overlap, not by start time.** Filtering by start
    time makes a 7-hour WHOOP sleep appear only in the one window it begins in,
    invisible to the following 13 — measured, sleep dropped from 390 minutes to
    90. db.load_range had made the same mistake.

    An empty window returns an empty string. Empty is not an error: when nothing
    genuinely happened in that half hour it should be empty, and the digests
    table stores that emptiness verbatim (see save_digests).
    """
    lo, hi = a - ctx, b + ctx

    # First check whether two recordings **overlap in time** here. Only then do
    # speech lines get a source tag — when a meeting is captured by both the
    # worn device and the desktop app, your own words appear twice (two ASR
    # passes, with slightly different wording), and untagged the model reads it
    # as having been said twice.
    #
    # Counting distinct file_ids is not enough: the worn device records in
    # 5-hour segments, and the seam between two of them falls inside one render
    # range; so do two back-to-back meetings. Those cases contain no duplication
    # at all, and tagging them would mislead. Overlap is what matters.
    #
    # With no overlap, not one byte is added: a changed digest means re-inference.
    spans: dict = {}
    for e in events:
        if e.get("source") != "plaud" or not (e.get("text") or "").strip():
            continue
        s0 = (e["ts"] - lo_ms) // 60000
        if s0 >= hi or s0 + (e.get("dur_ms") or 0) // 60000 < lo:
            continue
        k = e.get("file_id") or "?"
        t0, t1 = e["ts"], e["ts"] + (e.get("dur_ms") or 0)
        p0, p1 = spans.get(k, (t0, t1))
        spans[k] = (min(p0, t0), max(p1, t1))
    multi = _overlap(spans.values())

    before, inside, after, ongoing = [], [], [], []
    for e in events:
        s0 = (e["ts"] - lo_ms) // 60000
        s1 = s0 + (e.get("dur_ms") or 0) // 60000
        if s0 >= hi or s1 < lo:
            continue
        txt = line({**e, "_min": s0, "_multi_rec": multi})
        if not txt:
            continue
        if s0 >= b:
            after.append((s0, txt))
        elif s0 >= a:
            inside.append((s0, txt))
        else:
            before.append((s0, txt))
            # A record that started before the target but **has not ended**
            # needs a line inside the target too. Otherwise a sleep covering the
            # whole window appears only in the context, the target has not one
            # line, and it looks like "nothing happened in this half hour" —
            # measured on 8-18, 00:00-06:00 produced no spans at all for exactly
            # this reason, while the person lay asleep.
            if s1 > a:
                # Records that already state their start and end (sleep,
                # workout, location) should not repeat it.
                tail = (" (still ongoing)" if _hm(s0) in txt
                        else f" (since {_hm(s0)}, still ongoing)")
                ongoing.append(f"{_hm(a)} {txt}{tail}")

    def fmt(rows, seen):
        out = []
        for m, txt in sorted(rows, key=lambda r: r[0]):
            if txt in seen:      # deduped across the whole window, not just
                                 # adjacent lines — see the module docstring
                continue
            seen.add(txt)
            out.append(f"{_hm(m)} {txt}")
        return out

    seen: set = set()
    out = fmt(before, seen)
    body = ongoing + fmt(inside, seen)
    if body or out or after:
        out.append(TARGET_MARK)
    out += body
    tail = fmt(after, seen)
    if tail:
        out.append(AFTER_MARK)
        out += tail
    return "\n".join(out) if (body or [x for x in out if x not in
                                        (TARGET_MARK, AFTER_MARK)]) else ""
