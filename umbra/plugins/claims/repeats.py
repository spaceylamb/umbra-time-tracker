"""The same claim said again: near-duplicates across rounds, by minute, quote and wording."""

from ... import analyzer


# ── Repeats across rounds ─────────────────────────────────────
#
# A window is re-extracted whole when its text changes (a late recording, PLAUD
# rewriting the transcript), and the same assertion comes out again in other
# words. KEY = quote only stops an identical quote, and two rounds often quote
# different parts of the same passage. Calibrated across a month of rounds, all
# pairs within two minutes: these thresholds marked only real repeats, and two
# different things said in the same minute overlapped 0.67 on the claim — the
# claim threshold must stay above that.
DUP_NEAR_MIN = 2          # said again further apart is really said again


DUP_QUOTE = 0.8           # quote 3-gram containment


DUP_CLAIM = 0.75          # claim 2-gram containment (Chinese words are two characters)


DUP_MIN_CHARS = 8         # a shorter quote has too few grams to trust


_PUNCT = set("，。、；：？！（）()「」“”‘’《》【】,.;:?!'\"-—/ ")


_SUSPICION = {"confident_right": 0, "unsure": 1, "likely_wrong": 2}


def _minute(hhmm) -> int | None:
    try:
        h, m = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None


def _quote_sim(a: str, b: str) -> float:
    """Containment of the shorter quote's 3-grams in the other's."""
    ga, gb = analyzer.grams(a or ""), analyzer.grams(b or "")
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def _claim_sim(a: str, b: str) -> float:
    """How alike two claims are: 2-gram containment, punctuation and spaces out.

    Not 3-grams: a claim is the model's rewording, and in Chinese a word is two
    characters, so two phrasings of "not open-sourced" barely share a 3-gram.
    """
    def g(s):
        s = "".join(c for c in (s or "").lower()
                    if c not in _PUNCT and not c.isspace())
        return {s[i:i + 2] for i in range(len(s) - 1)}
    ga, gb = g(a), g(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def near_dup(prev, t, claim: str, quote: str):
    """The row in prev that is another wording of this one, or None.

    Quote and claim are both compared, and either being close enough counts. An
    unplaceable t is not judged: unsure whether it is the same moment, better to
    check one claim more.
    """
    at = _minute(t)
    if at is None:
        return None
    for row in prev:
        m = _minute(row["t"])
        if m is None or abs(m - at) > DUP_NEAR_MIN:
            continue
        # A short quote has few grams, and they scatter into any long sentence.
        short = min(len((quote or "").replace(" ", "")),
                    len((row["quote"] or "").replace(" ", "")))
        if short >= DUP_MIN_CHARS and _quote_sim(quote, row["quote"]) >= DUP_QUOTE:
            return row
        if _claim_sim(claim, row["claim"]) >= DUP_CLAIM:
            return row
    return None
