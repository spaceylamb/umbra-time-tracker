"""Echoes and double recordings: is a quote something you dictated to an agent,
and are two transcript lines one sentence heard twice?"""


# ── Echoes ────────────────────────────────────────────────────
# Some people dictate tasks to an agent by voice. The moment it is said aloud the
# work **is already under way** — extracting it and pushing it back as a to-do
# turns the day's finished work into cards. Measured: on one day, 31 of 32
# proposals were that.
#
# The parameters were measured (two days), not guessed:
#   delay   median 1 minute, max 18          → window ±20
#   score   bimodal both days, split at 36%↔1% and 45%↔29% → threshold 0.35
ECHO_WINDOW_MIN = 20


ECHO_THRESHOLD = 0.35


ECHO_GRAM = 3


# A quote shorter than this is never judged an echo: a few characters make few
# grams, and any long text may cover them by chance. Better to miss an echo
# than to kill a real task.
ECHO_MIN_CHARS = 8


# ── Two recordings of one sentence ────────────────────────────
# With the wearable and Plaud Desktop both running in a meeting, one sentence
# has two transcripts. The cost is not looks: it is half of context_max_lines'
# eight-line budget. The measurements behind the threshold are in
# importers/plaud.py, beside the code that does the merging.
DEDUPE_THRESHOLD = 0.9


DEDUPE_MIN_CHARS = 8  # shorter than this (whitespace removed) never merges


# The time gate is in **seconds**: measured, the two tracks align within 1.4 s,
# and ±5 s leaves 3.5× margin (see importers/plaud.py).
DEDUPE_NEAR_MS = 5000


def _grams(s: str, n: int = ECHO_GRAM) -> set:
    from ... import analyzer
    return analyzer.grams(s, n)


def echo_score(quote: str, cands, window=None, at=None) -> float:
    """How much of this quote falls inside some nearby text. 0..1.

    **Coverage, not Jaccard.** The agent prompt is often much longer than the
    sentence (links added, context filled in, a template mixed in), and Jaccard
    is diluted by length so real echoes fail to match. The question is "how much
    of the quote does that text cover", one way.

    **Not literal containment.** Dictation garbles words — a product name heard
    as something else (say, "Kestrel" as "custard"). Measured,
    literal containment caught almost none; 3-grams caught them all.
    """
    return echo_match(quote, cands, window=window, at=at)[0]


def echo_match(quote: str, cands, window=None, at=None) -> tuple:
    """(score, the text it matched). The text is kept so the call can be
    checked afterwards."""
    q = (quote or "").strip()
    if len(q.replace(" ", "")) < ECHO_MIN_CHARS:
        return 0.0, None
    qg = _grams(q)
    if not qg:
        return 0.0, None
    win = ECHO_WINDOW_MIN if window is None else window
    best = (0.0, None)
    for m, text in cands:
        if at is not None and abs(m - at) > win:
            continue
        g = _grams(text or "")
        if not g:
            continue
        sc = len(qg & g) / len(qg)
        if sc > best[0]:
            best = (sc, text)
    return best


def same_utterance(a: str, b: str) -> float:
    """How much two texts look like the same sentence. 0..1.

    **Containment (how much of the shorter lies in the longer), not Jaccard.**
    Two tracks split sentences differently as a rule; one cuts a sentence in two
    and the other does not, so the longer piece has extra, and Jaccard's union
    denominator punishes that — measured, it missed more than half the real
    duplicates. The question is "is the short one wholly covered by the other".

    Same direction and same 3-grams as echo_score; echo_score always takes the
    quote as numerator, this takes the shorter.

    ⚠️ The measure **is only trustworthy on long enough samples**. A short
    sentence has few grams, and they scatter into any long one for a high score
    (a real false merge: a five-character "uh, I'm on it" scored 0.75 against an
    unrelated turn). The length gate is in dedupe_recordings — the measure
    answers "how alike", and "is this sample worth trusting" is another question;
    mixing them makes both unclear.
    """
    ga, gb = _grams(a or ""), _grams(b or "")
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def echo_pool(events, lo_ms: int) -> list[tuple]:
    """The candidates: agent prompts plus clipboard text.

    The clipboard is the stronger evidence, measured: on one day five quotes
    matched only 4% on the agent side and 61–90% on the clipboard. The clipboard
    holds the dictation tool's near-verbatim, unedited text; the copy in the
    agent's history has often been edited once already.
    """
    out = []
    for e in events:
        src, kind = e.get("source"), e.get("kind")
        if src in ("butler", "claude") or (src == "live" and kind == "clip"):
            text = (e.get("text") or "").strip()
            if text:
                out.append(((e["ts"] - lo_ms) // 60000, text))
    return out
