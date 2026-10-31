"""Reading what the model answered: the fields of a proposal, normalised, and
whether its quote and its marks really belong to the window it came from."""

from .prompts import AFTER_MARK, TARGET_MARK


# The four kinds. **A destination need not read this**; it exists on Umbra's
# side so you can look back at what was sent. A value outside these four is not
# guessed at — it is recorded as unknown.
TYPES = ("question", "errand", "followup", "idea")


# Work / personal. **Used only to filter at send time, never in extraction** —
# "no reminders for work" holds today and may not tomorrow, and written into the
# prompt it could not be undone with one line of config.
DOMAINS = ("work", "personal")


# ── Hardware marks ────────────────────────────────────────────
# The button pressed mid-recording. It is **the only direct human input** in the
# whole chain — confidence is the model grading itself, speakers is inferred
# from the room, echo_score is a text comparison: all guesses. So a mark
# overrides those gates rather than sitting beside them.
#
# But it means "probably something here, look twice", not "certainly something
# here", and certainly not "only here" — scanning only marked windows would
# reduce the system to a manual notepad.
MARK_NEAR_MIN = 5     # how close a proposal must be to a mark to count as marked.


# ── Judging ───────────────────────────────────────────────────

# Confidence order. **unknown is not in it** — see _above_bar.
CONF_ORDER = ("low", "medium", "high")


def _norm_conf(v) -> str:
    v = (v or "").strip().lower()
    return v if v in CONF_ORDER else "unknown"


def _owns_quote(window_text: str, quote: str) -> bool:
    """Is this proposal **this window's** output?

    Every window carries 30 minutes of context either side (to help the model
    follow the conversation). But what is said in the context belongs to **other**
    windows — without this, a sentence near a boundary is extracted by both
    neighbours and becomes two identical cards. Measured: one sentence at 16:45
    was extracted twice exactly this way.

    The (day, quote) key cannot stop it: the two quotes differ by a few
    characters (one picked up a trailing particle), so the keys differ. The right
    place to stop it is ownership, not deduplication.

    A quote found nowhere (the model smoothed out filler words) **counts**: a
    miss costs more than a duplicate, and the window that owns the sentence
    would extract it too — the worst case is two cards, not none.
    """
    q = (quote or "").strip()
    if not q:
        return False
    target = window_text.split(TARGET_MARK, 1)[-1].split(AFTER_MARK, 1)[0]
    if q in target:
        return True
    return q not in window_text


def marks_in(events, a: int, b: int, lo_ms: int) -> list[dict]:
    """Hardware marks within the target stretch [a, b). Marks in the context do
    not count — the same rule as quote ownership: a mark belongs to its window."""
    out = []
    for e in events:
        if e.get("source") != "plaud" or e.get("kind") != "mark":
            continue
        m = (e["ts"] - lo_ms) // 60000
        if a <= m < b:
            out.append({**e, "_min": m})
    return sorted(out, key=lambda x: x["_min"])


def _norm_domain(v) -> str:
    return v if (v := (v or "").strip().lower()) in DOMAINS else "unknown"


def _speakers_in(events, lo_ms: int, a: int, b: int) -> int:
    """How many distinct speakers in [a, b).

    **Counted locally, never asked of the model** — it is a deterministic fact,
    and handing it to a model costs money and gets it wrong.

    **The maximum across recordings**, not the union. Speaker labels are per
    recording and mean nothing across recordings, which rules out both obvious
    ways: counting labels makes two tracks' "Speaker 1" collide (four people
    counted as two); counting (file_id, label) counts one person once per track
    (measured: a nine-person meeting, nine labels on each of two tracks, union 18).

    Why the maximum: two tracks are independent diarizations of one scene, and
    neither recorded anyone extra — there were at least as many people as the
    track that heard most. In a meeting on headphones the wearable hears only you
    and the software hears four remote voices; the answer is 4.

    ⚠️ The claims lane's MIN_SPEAKERS gate counts the same thing; change both.
    """
    per: dict = {}
    for e in events:
        if not (a <= (e["ts"] - lo_ms) // 60000 < b):
            continue
        if not (e.get("text") or "").strip():
            continue
        per.setdefault(e.get("file_id"), set()).add(e.get("speaker") or "?")
    return max((len(v) for v in per.values()), default=0)


def _norm_type(v) -> str:
    """An unrecognised kind is not guessed at. A guess invents information, and a
    wrong one only pollutes looking back at what was sent."""
    v = (v or "").strip().lower()
    return v if v in TYPES else "unknown"


def _minute_of(hhmm, fallback: int) -> int:
    """The model's HH:MM → minute of the day. If it cannot say, the window's
    start — half an hour off does not change a ±20-minute judgement."""
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError, TypeError):
        return fallback
