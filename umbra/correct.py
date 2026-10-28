"""Your own correction: "that hour I was actually doing something else".

## Why it exists

An hour reading on a separate e-reader over dinner leaves no record anywhere —
not in app usage, not in the browser, not in the reading app, which keeps only
highlights. With nothing to read, the model wrote "not sure" for half of it and
invented an evening nap for the other half, at high confidence.

That is not the model misreading. It is a **sensor blind spot**: it cannot be
filled from the data, but a person can fill it — provided there is somewhere to
say so, and what was said counts, stays, and is fed back.

## Three decisions

**One: a correction is an event, source "me". No new table.**

It is not a guess but something you recorded, the same in kind as any other
source; only the recorder is you. That buys a whole pipeline for free:

    db.load_range brings it along
      → the digest writes it into the windows it covers (render below)
      → **the digest changed, so infer's cache key changed**
      → the next round re-asks only those windows

Not one line of scheduling, because infer already keys its cache on the digest.

When you said it (`said_at`) and which minutes it covers (`ts`) are stored
apart. Testimony after the fact is not a recording at the time, and once the two
are mixed there is no telling "measured then" from "remembered three days later".

**Two: a and b come from ts + dur_ms only.** The digest computes minutes as
`(ts - lo_ms) // 60000`. A second copy of a/b in the payload drifts from it
sooner or later — on a day the clocks change, by an hour — and the symptom is
the page saying 18:00 while the model read 19:00, with no error anywhere.

**Three: inferences and day_events are not touched.** They are the model's own
words, and every comparison between models rests on them. A correction is a layer
on top (apply is a pure function): the page and the totals use the result, what
the model said stays as it was, and the detail panel shows both.

## What you said wins, always

If the model, re-asked, still disagrees (disagrees), the page still shows yours,
with a mark. The mark is the valuable part: it points at a blind spot that will
come back next week at the same dinner with the same e-reader.

## A source nobody imports

It renders like a source (render, classify, label) and is registered as one by
the core, the way live and idle are; it has no collect(). Corrections are written
by you — on the page or with `umbra correct` — never pulled.
"""

import time

from . import analyze, db, taxonomy

SOURCE = "me"
KIND = "correct"


def _norm(a, b, cat: str, n_min: int = 1440):
    """Check and normalise. **Anything invalid raises** — clamping it quietly
    would store a correction you never made, drawn exactly like one you did."""
    try:
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        raise ValueError(f"not a time: {a}–{b}") from None
    if not (0 <= a < b <= n_min):
        raise ValueError(f"span out of the day or reversed: {a}–{b} "
                         f"(needs 0 ≤ start < end ≤ {n_min})")
    key = taxonomy.key_of(cat) if cat else None
    if not key:
        # Required, and recognised. A category nobody knows draws as a grey band
        # for ever — which reads exactly like the model saying it was unsure.
        raise ValueError(f"not a category: {cat!r}")
    return a, b, key


def _m(t: str) -> int:
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_span(s: str) -> tuple:
    """'18:00-19:00' -> (1080, 1140). ValueError when it does not read.

    Here rather than in the CLI: the page and the command line take the same
    notation, and two parsers drift — the symptom being the hour changed on the
    page and the hour changed in a terminal one cell apart."""
    try:
        a, b = str(s).replace("–", "-").replace("—", "-").split("-")
        return _m(a.strip()), _m(b.strip())
    except (ValueError, AttributeError, IndexError):
        raise ValueError(f"cannot read the span {s!r} (write it as 18:00-19:00)") from None


def save(con, day: str, a, b, cat: str, text: str = "",
         said_at: int | None = None) -> str:
    """Write one correction; returns its uid.

    said_at is in the uid, so **changing your mind twice is two rows**, neither
    overwritten. The events table is append-only already, and it shows how often
    you changed it; which one counts is effective()'s call, at read time."""
    lo, hi = analyze.day_bounds(day, con)
    a, b, key = _norm(a, b, cat, (hi - lo + 1) // 60000)
    said_at = int(time.time() * 1000) if said_at is None else int(said_at)
    uid = f"{SOURCE}:{day}:{a}-{b}:{said_at}"
    db.insert(con, [(uid, lo + a * 60000, (b - a) * 60000, SOURCE, KIND,
                     {"cat": key, "text": (text or "").strip(), "said_at": said_at})])
    return uid


def withdraw(con, day: str, said_at: int) -> bool:
    """Undo: take back the correction said at said_at on this day. True when
    there was one.

    Marked, not deleted (withdrawn_at): the record of what you said and took
    back stays, as every change of mind does. But a withdrawn correction counts
    nowhere — load() skips it and render() writes nothing for it, so the digest
    is again what it was before, and the model reads the stretch as if it had
    never been said."""
    cur = con.execute(
        "UPDATE events SET payload=json_set(payload, '$.withdrawn_at', ?) "
        "WHERE source=? AND kind=? AND uid LIKE ? AND uid LIKE ? "
        "AND json_extract(payload, '$.withdrawn_at') IS NULL",
        (int(time.time() * 1000), SOURCE, KIND, f"{SOURCE}:{day}:%", f"%:{int(said_at)}"))
    con.commit()
    return cur.rowcount > 0


def withdrawn(con, lo_ms: int, hi_ms: int) -> int:
    """How many corrections in [lo, hi] were taken back — for the page cache's
    fingerprint. Undo marks a row instead of adding one, so a fingerprint made
    of row counts and ids would miss it and keep serving the correction."""
    return con.execute(
        "SELECT COUNT(*) FROM events WHERE source=? AND kind=? "
        "AND json_extract(payload, '$.withdrawn_at') IS NOT NULL "
        "AND ts <= ? AND ts + COALESCE(dur_ms, 0) >= ?",
        (SOURCE, KIND, hi_ms, lo_ms)).fetchone()[0]


def load(con, day: str) -> list:
    """Every correction on this day, in order. None is an empty list, not an
    error. Through db.load_range rather than SQL of its own: which records belong
    to a day (by overlap, not by start) is decided in one place."""
    lo, hi = analyze.day_bounds(day, con)
    n_min = (hi - lo + 1) // 60000
    out = []
    for e in db.load_range(con, lo, hi):
        if e.get("source") != SOURCE or e.get("kind") != KIND or e.get("withdrawn_at"):
            continue
        a = (e["ts"] - lo) // 60000
        b = a + (e.get("dur_ms") or 0) // 60000
        out.append({"a": max(0, a), "b": min(n_min, b),
                    "cat": taxonomy.norm(e.get("cat") or "unknown"),
                    "text": e.get("text") or "", "said_at": e.get("said_at") or 0})
    out.sort(key=lambda c: (c["a"], c["said_at"]))
    return out


def days_said_since(con, since_ms: int) -> list[str]:
    """The days corrections said at or after since_ms are about.

    For the round: it re-judges recent days and those an import wrote to, and a
    correction is neither — a day corrected last week would never be re-read.
    The day is read off the uid save() wrote, not recomputed from ts: which day
    a minute belongs to is a timezone question already answered there."""
    rows = con.execute(
        "SELECT uid FROM events WHERE source=? AND kind=? "
        "AND (CAST(json_extract(payload, '$.said_at') AS INTEGER) >= ? "
        "     OR CAST(json_extract(payload, '$.withdrawn_at') AS INTEGER) >= ?)",
        (SOURCE, KIND, int(since_ms), int(since_ms)))
    return sorted({uid.split(":")[1] for (uid,) in rows})


def effective(corrections) -> list:
    """Corrections that may overlap -> each minute belongs to one (**the later
    one wins**).

    By minute rather than by whole span: "18:00–19:00 reading", then "actually
    a call from 18:40". Replacing whole spans would drop the first correction,
    when only its last twenty minutes changed."""
    if not corrections:
        return []
    by_min = {}
    for c in sorted(corrections, key=lambda c: (c.get("said_at") or 0, c["a"])):
        for m in range(c["a"], c["b"]):
            by_min[m] = c
    out = []
    for m in sorted(by_min):
        c = by_min[m]
        if out and out[-1]["b"] == m and out[-1]["_of"] is c:
            out[-1]["b"] = m + 1
        else:
            out.append({**c, "a": m, "b": m + 1, "_of": c})
    for o in out:
        o.pop("_of")
    return out


def _slice(e: dict, a: int, b: int) -> dict:
    return {**e, "a": a, "b": b, "start": hm(a), "end": hm(b)}


def _mine(c: dict, evs) -> dict:
    """One correction -> its block on the page. With no text (only the category
    changed) the title stays the model's: you said the category was wrong, not
    the title."""
    covered = [e for e in evs if e["a"] < c["b"] and e["b"] > c["a"]]
    title = c.get("text") or ""
    if not title:
        title = covered[0]["title"] if covered else taxonomy.label(c["cat"], "en")
    return {"a": c["a"], "b": c["b"], "cat": c["cat"], "title": title,
            "start": hm(c["a"]), "end": hm(c["b"]),
            "mine": True, "said_at": c.get("said_at") or 0,
            # What it covers does not just vanish: the detail panel shows "what
            # the model said" beside it.
            "was": [{"a": e["a"], "b": e["b"], "cat": e["cat"], "title": e["title"],
                     "start": e["start"], "end": e["end"],
                     "detail": e.get("detail", "")} for e in covered]}


def apply(evs, corrections) -> list:
    """The model's calendar events + your corrections -> what the page draws.

    **Pure: writes nothing, changes nothing it is given.** The model's events
    are the record. A correction cuts its span out of them and keeps both ends —
    where the truth starts and where the model cut rarely line up — and an event
    cut away entirely is dropped."""
    eff = effective(corrections)
    if not eff:
        return evs
    out = []
    for e in evs:
        cuts = [c for c in eff if c["a"] < e["b"] and c["b"] > e["a"]]
        if not cuts:
            out.append(e)
            continue
        m = e["a"]
        for c in sorted(cuts, key=lambda c: c["a"]):
            if c["a"] > m:
                out.append(_slice(e, m, c["a"]))
            m = max(m, c["b"])
        if m < e["b"]:
            out.append(_slice(e, m, e["b"]))
    out += [_mine(c, evs) for c in eff]
    out.sort(key=lambda e: (e["a"], e["b"]))
    return out


def _ab(r) -> tuple:
    """A span's start and end minute, from either shape: inference rows carry
    start/end ("18:00"), the page's carry a/b (1080). Reading only one would
    give the command line and the page different answers about the same hour."""
    if "a" in r and "b" in r:
        return int(r["a"]), int(r["b"])
    return _m(r["start"]), _m(r["end"])


MARK = "your own correction"


def read_by_model(c: dict, digests) -> bool | None:
    """Has the model read this correction yet?

        True    every window it covers was asked with it in the text
        False   at least one was asked without it — the judgement there is
                the one made before you said it
        None    no digest is kept for those windows; there is no telling

    Told apart from agreeing. Right after you say it, "the model still says
    Social" would be false — it has not been asked since — and reads as the
    model arguing with you. The digest each window was asked with is kept, so
    the evidence is there."""
    covering = [r for r in digests if r["a"] < c["b"] and r["b"] > c["a"]]
    if not covering:
        return None
    needle = (c.get("text") or "").strip()
    return all(MARK in r["text"] and needle in r["text"] for r in covering)


def disagrees(c: dict, spans):
    """Whether the model, re-asked, agrees with this correction.

        None              it said nothing about this span — **not** disagreement
        False             it agrees
        {..., soft: True} the same thing, filed the other way round
        {...}             it disagrees, with the cat / title it holds to

    **Against the first-pass spans, not the calendar.** The first pass read the
    records and the correction, and has `also`: "mainly reading, eating too" can
    only be said there. The calendar has to choose one category per event; held
    against it, a model that agreed completely was flagged as disagreeing.

    **soft**: "the order is reversed" is not "it insists you were asleep". Flagged
    the same, the marks become noise, and the real one — the blind spot that
    will come back — drowns with them.

    **unknown does not vote.** "Not sure" is the model abstaining, not a claim;
    counted, half an hour unsure and half an hour asleep tie, and the tie goes to
    "unknown" — its one honest answer reported as its position.

    The category covering the most minutes decides, ties by name so the same data
    gives the same answer twice.
    """
    mins, hit = {}, []
    for r in spans:
        a, b = _ab(r)
        lo, hi = max(a, c["a"]), min(b, c["b"])
        if hi <= lo:
            continue
        hit.append(r)
        if r.get("cat") == "unknown":
            continue
        mins[r["cat"]] = mins.get(r["cat"], 0) + (hi - lo)
    if not mins:
        return None
    top = min(mins, key=lambda k: (-mins[k], k))
    if top == c["cat"]:
        return False
    best = max((r for r in hit if r.get("cat") == top),
               key=lambda r: min(_ab(r)[1], c["b"]) - max(_ab(r)[0], c["a"]))
    # The model writes display names in also (Reading / 读书); the store keeps
    # keys. Without normalising, this never fires under one of the languages.
    soft = any(taxonomy.norm(x) == c["cat"]
               for r in hit for x in (r.get("also") or []))
    return {"cat": top, "soft": soft,
            "title": best.get("act") or best.get("activity") or "",
            "detail": best.get("detail", "")}


# ── As a source (registry._builtin) ────────────────────────────

def render(e) -> str | None:
    """The digest line. It states its own span: the digest prefixes each line
    with the minute it falls in, and a correction covers a stretch — with the
    prefix alone, the 18:30 window would read it as "at 18:30" and judge the
    first half hour as before."""
    if e.get("kind") != KIND or e.get("withdrawn_at"):
        return None
    a = e["_min"]
    b = a + (e.get("dur_ms") or 0) // 60000
    from . import infer
    cat = taxonomy.label(e.get("cat") or "unknown", infer.lang())
    t = (e.get("text") or "").strip()
    return (f"✎ {MARK}, this is what happened {hm(a)}–{hm(b)}: {cat}"
            + (f" — {t}" if t else ""))


def classify(e) -> str | None:
    """The category you gave, for the offline rule layer."""
    return taxonomy.norm(e.get("cat") or "unknown") if e.get("kind") == KIND else None


def label(cat: str, e) -> str | None:
    """Your words, when you wrote any."""
    return (e.get("text") or "").strip() or None
