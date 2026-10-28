"""Reconstruct a day's timeline from its events.

Two steps, both deterministic and both offline:

    build()     events -> rule-based segments (collapse + segment)
    coverage()  events -> per-minute evidence tiers, the honest self-audit

**There is no model call in this module.** There used to be: a `summarize()`
that built its own prompt, hardcoded its own Gemini endpoint and its own model
name, and defined its own category list — which had drifted into a third
taxonomy (coding / research / communication / admin / entertainment / other),
overlapping but not matching
either taxonomy.py or the infer prompts. Exactly the "same table written twice"
failure this codebase documents everywhere else, and it survived because only
one legacy command reached it.

The model pass now lives in exactly one place: infer.py, routed through
providers.resolve. `umbra show` prints the rule-based segments; `umbra infer`
runs the model.
"""

import datetime as dt
import re

from . import db, segment, tz


def day_bounds(day: str, con=None) -> tuple[int, int]:
    """This day's [start, end].

    Given a connection it goes through tz.bounds — cut along the real time
    axis, days joined end to end, a travel day longer or shorter as it was.
    Without one it falls back to the machine-timezone algorithm; on a database
    with no trips recorded the two agree day by day (tests/test_tz.py).
    """
    if con is not None:
        return tz.bounds(con, day)
    return tz.naive_bounds(day)


def _hm(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M")


def describe(seg: dict, max_items: int = 6) -> list[str]:
    """The most representative few events in a segment.

    Claude prompts and speech come first — those two are the only categories of
    "words the person said themselves", and their signal-to-noise ratio beats
    page titles and app names by an order of magnitude. Without promoting them,
    a segment holding a few dozen chrome visits fills max_items with those
    first, squeezing out the one line that actually says what was going on.
    """
    HIGH = ("claude", "butler", "plaud")
    prompts = [e for e in seg["events"] if e["source"] in HIGH]
    others = [e for e in seg["events"] if e["source"] not in HIGH]
    lines, seen = [], set()
    for e in prompts + others:
        if e["source"] == "claude":
            txt = f"💬 [{e.get('project')}] {e.get('text', '')[:110]}"
        elif e["source"] == "butler":
            # Outcome and cost are Butler-only; the local files have neither
            tail = ""
            if e["kind"] == "task":
                bits = [b for b in (e.get("status"),
                                    f"${e['cost_usd']:.2f}" if e.get("cost_usd") else None)
                        if b]
                tail = f"  ({' · '.join(bits)})" if bits else ""
            txt = f"🤖 [{e.get('provider', '?')}] {e.get('text', '')[:100]}{tail}"
        elif e["source"] == "plaud":
            # Include the speaker: over the same stretch, "only you talking"
            # and "three people going back and forth" are completely different
            # activities (a lecture vs a discussion), and the words alone do not
            # distinguish them.
            who = e.get("speaker") or "?"
            txt = f"🗣 [{who}] {e.get('text', '')[:110]}"
        elif e["source"] == "chrome":
            txt = f"· {(e.get('title') or e.get('url', ''))[:80]}"
        elif e["source"] == "zsh":
            txt = f"$ {e.get('cmd', '')[:70]}"
        elif e["source"] == "git":
            txt = f"⎇ [{e.get('project')}] {e.get('action', '')[:60]}"
        elif e["source"] == "idle":
            txt = f"💤 nobody here ({(e.get('dur_ms') or 0)/60000:.0f}min no input)"
        elif e["source"] == "live":
            if e["kind"] == "window":
                txt = f"◪ {segment.app_name(e)} — {e.get('title', '')[:70]}"
            elif e["kind"] == "clip":
                body = e.get("text") or f"<{e.get('len')} chars, looks like a secret>"
                txt = f"⧉ copied: {body[:70]}"
            else:
                txt = f"◪ {e['kind']} {segment.app_name(e)}"
        else:
            mins = (e.get("dur_ms") or 0) / 60000
            name = segment.app_name(e)
            txt = f"▣ {name} ({mins:.0f}min)" if mins >= 1 else f"▣ {name}"
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt in seen:
            continue
        seen.add(txt)
        lines.append(txt)
        if len(lines) >= max_items:
            break
    return lines


def render(segs: list[dict]) -> str:
    out = []
    for s in segs:
        mins = round((s["end"] - s["start"]) / 60000)
        out.append(f"\n{_hm(s['start'])}–{_hm(s['end'])}  ({mins}min)  "
                   f"[{s['key']}]  {len(s['events'])} events")
        out += [f"    {line}" for line in describe(s)]
    return "\n".join(out)


def build(day: str, con=None) -> list[dict]:
    con = con or db.connect()
    lo, hi = day_bounds(day, con)
    events = db.load_range(con, lo, hi)
    return segment.segment(segment.collapse(events))


# ─────────────────────────── Precision audit ───────────────────────────

# Three tiers of evidence. This split is Umbra's one honest self-assessment: it
# separates "I know what you were doing" from "I only know what you had open".
TIER_A = "A"   # content level: concrete evidence; can say what was being done
TIER_B = "B"   # container level: which app or domain, but not what was happening
TIER_C = "C"   # no data

# Off-screen but known: sleep, workouts. Away from the computer, yet fully
# accounted for.
#
# A separate tier rather than folded into A, because it answers a different
# question. A/B/C measure "the resolution of time spent at the computer", and
# mixing sleep into A inflates coverage — eight hours of sleep would add 33% of
# "explained" out of nowhere while not removing one minute of on-screen blindness.
#
# It cannot fold into away either: away means "we do not know where the person
# was or what they were doing", a black hole outside the denominator. Sleep is
# precisely a black hole turned into explained time — which is the whole point of
# connecting WHOOP.
TIER_OFF = "OFF"

# Which whoop records represent "a stretch of time spent doing something".
# cycle is a whole-day summary and recovery is an instantaneous score; neither is
# an interval.
WHOOP_SPANS = ("sleep", "workout")

# Page titles carrying no content, only a container
GENERIC_TITLE = re.compile(
    r"^(Butler|Scout|Inbox|Gmail|Slack|Notion|Dashboard|New Tab|"
    r"Instagram|YouTube|Twitter|X|LinkedIn|Google)\s*$", re.I)

# Threshold for "parked on one app". While working normally the median gap
# between app switches is around 20 seconds, so a dozen minutes without a switch
# is anomalous — either deep focus, or nobody at the machine. Backfilled data
# cannot tell those apart; that needs the resident collector's idle detection.
STALE_FOCUS_MIN = 15


def coverage(day: str, con=None) -> dict:
    """Assign every minute of a day to a tier, and total them up."""
    con = con or db.connect()
    lo, hi = day_bounds(day, con)
    n_min = (hi - lo + 1) // 60000
    events = db.load_range(con, lo, hi)

    minutes: dict[int, str] = {}
    ambiguous: set[int] = set()
    away: set[int] = set()
    offscreen: set[int] = set()

    def span(m0: int, dur_min: int) -> range:
        """Event durations cross midnight and must be clamped into the day.

        Unclamped, minutes at or beyond 1440 still get counted into the tiers,
        so A+B+away can exceed the day's 1440 and C = 1440 - ... goes negative
        (measured on 2026-08-14: -228min).
        """
        return range(max(0, m0), max(0, min(n_min, m0 + dur_min)))

    def mark(m: int, tier: str):
        if not 0 <= m < n_min:
            return
        if minutes.get(m) != TIER_A:
            minutes[m] = tier

    for e in events:
        m0 = (e["ts"] - lo) // 60000
        if e["source"] == "whoop":
            # Only sleep/workout are intervals; cycle/recovery are a summary
            # and a score.
            if e.get("kind") in WHOOP_SPANS:
                dur_min = max(1, round((e.get("dur_ms") or 0) / 60000))
                # Sleep may cross midnight; clamp into the day
                offscreen.update(span(m0, dur_min))
            continue
        if e["source"] == "idle":
            # The only signal that establishes "nobody here". These minutes
            # belong in no coverage denominator.
            away.update(span(m0, max(1, round((e.get("dur_ms") or 0) / 60000))))
        elif e["source"] == "live":
            # A window title means we know what; only an app name means we
            # only know where
            mark(m0, TIER_A if (e.get("title") or "").strip() else TIER_B)
        elif e["source"] in ("app", "iphone"):
            # iPhone and knowledgeC share a tier: which app, not what was being
            # done. Phone time is not "off-screen known" — that is sleep and
            # workouts. Using a phone is screen time on a different screen.
            #
            # suspect = App.InFocus cannot see the screen lock, so this stretch
            # is most likely the phone sitting in a pocket. Counting it as B
            # would claim we know what those hours were — not knowing belongs in
            # "cannot say", and a metric must not improve by hallucinating.
            if e.get("suspect"):
                continue
            dur_min = max(1, round((e.get("dur_ms") or 0) / 60000))
            for m in span(m0, dur_min):
                mark(m, TIER_B)
            # The "parked on one app" suspicion applies only to the Mac:
            # a dozen minutes on WeChat on a phone means it was being used;
            # there is no "nobody at the machine" ambiguity.
            if e["source"] == "app" and dur_min >= STALE_FOCUS_MIN:
                ambiguous.update(span(m0, dur_min))
        elif e["source"] == "location":
            # The three kinds sit at completely different levels of knowledge
            # and cannot be treated alike:
            #
            #   transit -> A: "driving from home to the office" is what you were
            #                 doing. Commute time used to be a pure black hole;
            #                 this is location's single biggest contribution.
            #   stay    -> B: we know you were home, not what you were doing
            #                 there — the same class of knowledge as "Lark is in
            #                 the foreground". Container level.
            #   gap     -> not counted: the data stopped; the person could be
            #                 anywhere.
            kind = e.get("kind")
            if kind == "gap":
                continue
            dur_min = max(1, round((e.get("dur_ms") or 0) / 60000))
            tier = TIER_A if kind == "transit" else TIER_B
            for m in span(m0, dur_min):
                mark(m, tier)
        elif e["source"] == "media":
            # The only content-level phone data: which chapter of which book was
            # playing, not merely "inside WeRead". So tier A, alongside Claude
            # conversations and terminal commands.
            #
            # Listening runs in parallel with other things (while walking, while
            # coding), and mark()'s "A is never overwritten" rule happens to be
            # exactly right here: a minute with more specific on-screen evidence
            # is already A, and marking it again changes nothing.
            if e.get("suspect"):    # left playing while the person walked away;
                                    # same treatment as iphone
                continue
            dur_min = max(1, round((e.get("dur_ms") or 0) / 60000))
            for m in span(m0, dur_min):
                mark(m, TIER_A)
        elif e["source"] == "plaud":
            # Content level: we know **what was actually said** in that minute.
            # And it covers meetings, meals, walks, face-to-face conversation —
            # stretches no screen-based source can see at all.
            #
            # ⚠️ Mark only the minutes where someone was actually speaking, never
            #    the span of the whole recording. Measured: a 56-minute recording
            #    contained 1.6 minutes of speech (3%), and counting all 56 as A
            #    would claim those other 54 minutes were explained — which is
            #    precisely what the "explained" metric exists to expose, not to
            #    be dressed up with.
            #    This is also why the importer stores one row per speech segment
            #    rather than one row per recording.
            dur_min = max(1, round((e.get("dur_ms") or 0) / 60000))
            for m in span(m0, dur_min):
                mark(m, TIER_A)
        elif e["source"] == "chrome":
            title = (e.get("title") or "").strip()
            mark(m0, TIER_B if GENERIC_TITLE.match(title) or not title else TIER_A)
        elif e["source"] == "weread":
            # This source emits two kinds of row with opposite time semantics:
            #
            #   daily    a daily aggregate. "73 minutes of reading that day",
            #            with **no idea when**. Falling through to the catch-all
            #            below turns it into "reading during that one minute at
            #            midnight Beijing time" — an invented position, and one
            #            invented minute every single day.
            #   reading  a stretch bracketed by highlights. createTime is
            #            second-precision, a real position in time, and it says
            #            which chapter of which book — content level, alongside
            #            Claude conversations and terminal commands.
            if e["kind"] != "reading":
                continue
            dur_min = max(1, round((e.get("dur_ms") or 0) / 60000))
            for m in span(m0, dur_min):
                mark(m, TIER_A)
        else:                          # claude / git / zsh are all content level
            mark(m0, TIER_A)

    # Priority: on-screen evidence > off-screen known > idle.
    #
    # On-screen evidence ranks first because WHOOP's sleep boundaries are
    # algorithmically inferred and include "lying down but still scrolling"; a
    # minute with a real keystroke or page visit must not be overwritten by sleep.
    #
    # idle dropped to last on 2026-08-16. It never meant "nobody here", it meant
    # **"we are ignorant about this stretch"** — when it was built, no input on
    # the Mac was very nearly the same as knowing nothing. Location and WHOOP can
    # both speak now: one night had 481 minutes of idle, 437 of sleep and 762 at
    # home all overlapping, and giving idle priority ejected 886 minutes from the
    # denominator outright — letting the least informative source make the final
    # call.
    #
    # The earlier comment claimed "on-screen > idle > off-screen known", but the
    # reasoning it offered only established on-screen > sleep. idle > sleep came
    # along for the ride and was never argued for.
    known = set(minutes)
    a = sum(1 for t in minutes.values() if t == TIER_A)
    b = sum(1 for t in minutes.values() if t == TIER_B)
    off = len(offscreen - known)
    blind = away - known - offscreen    # only the genuinely unknowable minutes
    return {"day": day, "A": a, "B": b, "off": off,
            # n_min travels with cov: downstream (explained_pct) needs it as the
            # denominator and has no other way to know how long this day was.
            "n_min": n_min,
            "C": n_min - a - b - len(blind) - off,
            "away": len(blind),
            "ambiguous": len(ambiguous & known - blind),
            "events": len(events)}
