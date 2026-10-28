"""Cut a day into 5-minute slots and say what each one was.

The difference from segment.py is that **the slots are fixed**: that module
segments by event density, so two idle hours collapse to a single line. Here a
day is always 288 slots, and time spent doing nothing takes up exactly as much
space as time spent doing something. Only the latter can answer "where did the
time go".

Judgement follows a **ladder of evidence strength**, strongest first:

    can say what was being done   coding / reading / listening / browsing /
                                  workout / transit
    only knows where              inside some app / at some place
    known off-screen              sleep
    ⚠ cannot say                  the sources contradict each other
    (empty)                       no events at all

The distinction between the last two is what this module is really for.
Measured across the 288 slots of 2026-08-15, the count of "has events but no
content-level evidence" was **0** — idle / whoop / location cover continuously,
so every slot has at least one of them. What actually defeats judgement is not
emptiness, it is contradiction:

    chrome + idle   11 slots   the browser recorded visits, yet the Mac saw no
                               key or mouse input. Were you reading, or did a
                               background tab refresh itself?

Emptiness gets filled by connecting more sources. A contradiction means **one
of the sources already connected is lying**, and adding more will not make it
go away. Merged into one category, "we should add data" and "we should fix an
assumption" become indistinguishable.
"""

SLICE_MIN = 5
BLOCKS = 1440 // SLICE_MIN

# Sources that can say "what was being done". chrome depends on the title: a
# generic one (New Tab and friends) establishes nothing.
CONTENT = ("claude", "butler", "zsh", "git")

# Sources that count as "somebody is working this Mac" when detecting
# contradictions. Two deliberate differences from CONTENT:
#
#   + chrome  browsing sits below coding on the ladder (a title does not
#             necessarily say what is happening), but it is just as strong when
#             put up against "nobody is here".
#   - butler  the user confirmed on 2026-08-16 that it runs both synchronously
#             and asynchronously. A butler prompt arriving during idle has only
#             three explanations: sent from the phone PWA, an async run
#             reporting back, or dictated (which _notes handles) — none of them
#             a contradiction on this Mac. Someone actually sitting here typing
#             would never trigger idle in the first place; typing is key input.
#             The cost was measured: over three days, butler alone caused
#             contradictions in only 1 / 2 / 6 slots.
MAC_ACTIVE = ("claude", "zsh", "git", "chrome")


def _mac_active(e: dict) -> bool:
    """Can this record prove somebody was working **this Mac**?

    Chrome History is a **cross-device synced** database. At 14:54 on
    2026-08-15 there were several searches in a row for "hadrian villa", which
    looks like active use — yet idle reported five minutes with no key or mouse
    input. Neither was wrong: those searches happened on the phone and were
    synced into this machine's History SQLite. Measured, 65% of chrome records
    landing inside idle stretches arrive that way.

    "you were browsing on your phone" and "somebody is working this Mac" are
    different facts, and only the second one conflicts with "no key or mouse
    input".

    `synced` is a **cross-source convention**, not a chrome-specific field: any
    source able to tell that a record did not originate on this machine can set
    it and automatically stop counting as Mac activity. Written to recognise
    only chrome, the next person connecting a synced data source would have to
    discover this allowlist first.
    """
    return e["source"] in MAC_ACTIVE and not e.get("synced")


def _at_keyboard(hits: list[dict]) -> bool:
    """Is there **positive** evidence that a person was actually at the machine?

    Only a clipboard change counts. The reason is that it is very nearly
    impossible for a machine to produce — a background process does not write
    the sentence you just dictated into your clipboard. live/window and
    live/activate do not qualify: background apps refresh their titles
    constantly, so treating that as evidence would make this predicate
    permanently true, which is the same as not having it.
    """
    return any(e["source"] == "live" and e["kind"] == "clip" for e in hits)


def _notes(hits: list[dict]) -> list[str]:
    """**Known blind spots in a signal** — kept separate from contradictions.

    A contradiction means "these two cannot both be true, you have to decide".
    A blind spot means "this source cannot see this stretch — it is not wrong,
    it is measuring something else". Merging them devalues the ⚠: the ones that
    genuinely need you to stop and look would drown in a pile of things already
    explained.

    There is one so far: **dictation produces no HID events**. collector.py
    measures idleness with
    CGEventSourceSecondsSinceLastEventType(kCGAnyInputEventType), which counts
    only keyboard, mouse and trackpad. macOS dictation goes through the text
    input system (insertText:) and synthesises no keyboard events. So five
    minutes of talking into the microphone never resets that timer — to this
    signal it is identical to five minutes away from the machine.

    Hit at 09:38:29 on 2026-08-15: a full dictated sentence sitting in the
    clipboard, idle beginning 41 seconds later, and the slot flagged "no key or
    mouse input, yet the Mac shows activity". Tuning the threshold does not
    help; the signal is not measuring this. The clipboard is the counter-proof,
    already available.
    """
    if any(e["source"] == "idle" for e in hits) and _at_keyboard(hits):
        return ["No keyboard/mouse input here, but the clipboard kept changing — likely dictation (voice is not key/mouse input)"]
    return []


def _hm(i: int) -> str:
    m = i * SLICE_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


def _span(e: dict, lo_ms: int, nb: int = BLOCKS) -> range:
    """Which slots this event covers. A point event (duration 0) still occupies
    one slot — it did actually happen."""
    ms = SLICE_MIN * 60_000
    a = (e["ts"] - lo_ms) // ms
    dur = e.get("dur_ms") or 0
    # Minus one millisecond: sleeping until exactly 01:00 should not also mark
    # the 01:00-01:05 slot as asleep.
    b = (e["ts"] + max(dur - 1, 0) - lo_ms) // ms
    # nb is **this day's** slot count, not the constant 288: a short day would
    # IndexError, a long one would have its end clamped away with no error.
    return range(max(0, int(a)), min(nb - 1, int(b)) + 1)


import re

# Nothing that looks like an id goes into a label. An unreadable label is the
# same as no label, except that it also occupies the space and implies there is
# information there.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-", re.I)


def _suffix(name: str | None) -> str:
    name = (name or "").strip()
    return f"·{name}" if name and not _UUID.search(name) else ""


def _label(cat: str, e: dict) -> str:
    """The short label for one slot.

    A source may declare its own label(); the per-category defaults below are
    the fallback. A source with no opinion gets the generic label for its
    category rather than an invented one.
    """
    from . import registry
    own = registry.label(cat, e)
    if own is not None:
        return own
    if cat == "transit":
        mode = {"driving": "driving", "walking": "walking",
                "cycling": "cycling", "running": "running"}.get(e.get("mode"), "")
        return f"{mode}{e.get('from', '?')} → {e.get('to', '?')}".strip()
    if cat == "workout":
        return f"Workout{(' (' + e['sport'] + ')') if e.get('sport') else ''}"
    if cat == "sleep":
        return "Sleeping"
    if cat == "code":
        if e["source"] == "butler":
            # butler stores a session id in project, and title is usually
            # empty. With no readable name, say only who was running it — a
            # UUID is worse than nothing, taking up the space and saying
            # nothing.
            return "Butler·" + (e.get("provider") or "?") + _suffix(e.get("title"))
        who = {"claude": "Claude", "zsh": "terminal", "git": "Git"}[e["source"]]
        return who + _suffix((e.get("project") or e.get("cwd") or "").split("/")[-1])
    if cat == "talk":
        # Put the actual words, not "talking". Same reasoning as browse using
        # the page title — a label's entire value is in the few words that let
        # you remember what was going on.
        return "Said: " + " ".join((e.get("text") or "").split())[:24]
    if cat == "listen":
        return "Listening: " + " — ".join(x for x in (e.get("title"), e.get("track")) if x)
    if cat == "read":
        return f"Reading 《{e.get('book', '?')}》{e.get('chapter', '')}".strip()
    if cat == "browse":
        return (e.get("title") or e.get("url") or "").strip()
    if cat == "app":
        return f"In {e.get('app') or e.get('bundle') or '?'}"
    if cat == "place":
        return f"At {e.get('place', '?')}" + (" (inferred)" if e.get("inferred") else "")
    return ""


# The decision ladder. The order *is* who gets the final say.
#
# It used to be a list of (category, predicate) pairs where every predicate
# matched on a source name, which made it the third of four places a source had
# to be written into by hand. Now each source declares its own category through
# classify() and this is purely the priority order over those categories — so a
# new source picks up the right precedence without editing anything here.
#
# Workout and transit rank above coding because they are **containers**: driving
# is not something you can stop doing, and listening to a book is layered on top
# of it. The other way round (listening as the primary label) chops a single
# continuous commute into "driving / listening / driving", which reads as pulling
# over to read for a while — measured on the morning of 8-16, that is exactly how
# one commute got cut into four segments.
#
# Talking outranks coding: typing a couple of lines during a meeting at the desk
# is real, but "was talking to someone" explains those five minutes better than
# "was writing code". It still sits below the containers — talking while driving
# means driving is the shape of that stretch.
#
# Below sleep is fallback: we know which app or which place, but not what was
# being done.
LADDER = ("workout", "transit", "talk", "code", "read", "listen",
          "browse", "sleep", "app", "place")

FALLBACK = ("app", "place", "sleep")

# Display name per rung. **Next to the ladder, and the only copy in the
# project** — both the CLI and the visualisation read it from here. Hit on
# 2026-08-17: connecting PLAUD added the talk rung here, viz.py's BNAME was
# updated to match, and __main__.py's CAT_NAME was forgotten — so talking showed
# up as "(empty)" throughout the CLI, with two "(empty)" rows side by side in
# the summary. Three near-identical dictionaries; missing one raises nothing and
# only displays wrongly.
# Names come from taxonomy — the rule-based keys are a subset of taxonomy keys
# anyway, and hand-copying them would make a fourth copy of the same dictionary
# (the first three already drifted twice).
from . import taxonomy as _tax                     # noqa: E402
CAT_NAME = {k: _tax.label(k, "en") for k in
            ("sleep", "workout", "transit", "code", "talk", "read",
             "listen", "browse", "app", "place")}


def _cats(hits: list[dict]) -> dict:
    """category -> the first event evidencing it, asked of each source.

    First writer wins per category, matching the old ladder's behaviour of
    taking the first event a predicate matched.
    """
    from . import registry
    out: dict = {}
    for e in hits:
        c = registry.classify(e)
        if c and c not in out:
            out[c] = e
    return out


def _rank(cat: str) -> int:
    """Where a category sits on the ladder.

    A category the ladder has never heard of — which a source using a custom
    taxonomy can produce — ranks after every known one rather than being
    discarded. Dropping it would make the source's own classification silently
    ineffective, which is the failure this whole change is about.
    """
    return LADDER.index(cat) if cat in LADDER else len(LADDER)


def _also(hits: list[dict], primary: str | None) -> list[str]:
    """Other things happening in parallel in the same slot. There can be only
    one primary label, but the rest must not be lost."""
    cats = _cats(hits)
    return [_label(c, cats[c])
            for c in sorted(cats, key=lambda c: (_rank(c), c))
            if c != primary and c not in FALLBACK]


def _pick(hits: list[dict]) -> tuple[str | None, dict | None]:
    """Pick the one that gets the final say, by evidence strength."""
    cats = _cats(hits)
    if not cats:
        return None, None
    best = min(cats, key=lambda c: (_rank(c), c))
    return best, cats[best]


def _conflicts(hits: list[dict], primary: str | None = None) -> list[str]:
    """Where the sources fight. **Never decide for the user; just show it.**

    Only real contradictions count: listening to a book while driving is
    perfectly normal, and the Mac being idle while the phone is active is normal
    too — that is a person switching screens, not bad data. What actually
    conflicts is "nobody is here" and "somebody is working this Mac" holding at
    once.

    Sleep gets its own rule: **whenever WHOOP says asleep and the primary label
    is not sleep, say so**, regardless of which source outranked it. Hit at
    00:45 on 2026-08-15 — WHOOP's sleep of 00:25→07:57 covered that slot, Git
    had a commit, the ladder let "coding" win, so the label showed only Git and
    sleep disappeared entirely. The user's first reaction on seeing it was
    "WHOOP must not have imported".

    The data had been there all along (whoop is in why); the display ate it —
    which is the one mistake this project least ought to make. We spend whole
    days separating "no data" from "data present but not read", and then
    conflated the two ourselves, on screen.

    This cannot fire only when "somebody is working this Mac": falling asleep
    to an audiobook means media outranks sleep, and media is not activity — the
    old logic dropped sleep from that slot silently.
    """
    out = []
    asleep = any(e["source"] == "whoop" and e["kind"] == "sleep" for e in hits)
    if asleep and primary not in (None, "sleep"):
        out.append("WHOOP says this stretch was sleep")

    if not any(_mac_active(e) for e in hits):
        return out
    for e in hits:
        if e["source"] == "location" and e["kind"] == "transit":
            out.append("Location says in transit, but the Mac was being used")
        elif e["source"] == "idle" and not _at_keyboard(hits):
            # With clipboard evidence this is not a contradiction but a known
            # blind spot — _notes says it instead.
            out.append("idle says no key/mouse input, yet the Mac shows activity")
    return out


def blocks(events: list[dict], lo_ms: int, n_min: int = 1440) -> list[dict]:
    """A day -> 288 slots, each carrying cat / label / why (which sources were
    used) / conflict.

    why has to stay: a sanity check is only possible when the basis for the
    judgement is visible. Conclusions without their basis mean you cannot tell
    why a right answer was right, and cannot find where a wrong one went wrong.
    """
    # How many slots **this day** has: 276 / 300 on the daylight-saving days,
    # anywhere from 120 to 468 on a day crossing timezones.
    nb = -(-int(n_min) // SLICE_MIN)
    per: list[list[dict]] = [[] for _ in range(nb)]
    for e in events:
        for i in _span(e, lo_ms, nb):
            per[i].append(e)

    out = []
    for i, hits in enumerate(per):
        cat, e = _pick(hits)
        out.append({
            "i": i, "t": _hm(i), "cat": cat,
            "label": _label(cat, e) if cat else "",
            "also": _also(hits, cat),
            "why": sorted({h["source"] for h in hits}),
            "conflict": _conflicts(hits, cat),
            "note": _notes(hits),
        })
    return out


def runs(bs: list[dict]) -> list[dict]:
    """Collapse consecutive identical slots into one run. 288 rows cannot be
    read, and cannot be sanity-checked.

    Runs merge on "same category + same label + same parallel items".
    Contradictions merge with **or** — one conflicting slot anywhere in a run
    means the whole run is flagged. That is precisely what most needs to be
    seen, and it must not be washed out by the eleven normal slots beside it.
    """
    out: list[dict] = []
    for b in bs:
        key = (b["cat"], b["label"], tuple(b["also"]))
        if out and out[-1]["key"] == key:
            cur = out[-1]
            cur["n"] += 1
            cur["cf"] = cur["cf"] or bool(b["conflict"])
            cur["why"] = sorted(set(cur["why"]) | set(b["why"]))
            for fld in ("conflict", "note"):
                for c in b[fld]:
                    if c not in cur[fld]:
                        cur[fld] = cur[fld] + [c]
            cur["end"] = _hm(b["i"] + 1)
            continue
        out.append({"key": key, "i": b["i"], "t": b["t"],
                    "end": _hm(b["i"] + 1), "n": 1,
                    "cat": b["cat"], "label": b["label"], "also": b["also"],
                    "cf": bool(b["conflict"]),
                    "conflict": b["conflict"], "note": b["note"],
                    "why": list(b["why"])})
    for r in out:
        del r["key"]
        r["min"] = r["n"] * SLICE_MIN
    return out
