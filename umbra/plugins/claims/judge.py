"""Reading what a model answered: an extracted claim's fields, and a lookup's verdict and sources."""

import json


FOUND = ("right", "inaccurate", "wrong", "unsettled")


KINDS = ("number", "entity", "rule", "mechanism", "comparison", "other")


VERDICTS = ("confident_right", "unsure", "likely_wrong")


def owns_quote(window_text: str, quote: str, bar: float = 0.6) -> bool:
    """Is this claim **this window's** output?

    actions._owns_quote cannot be reused: its fallback ("a quote found nowhere
    still counts") was written for ctx=30 (found in the context → it belongs to a
    neighbour; found nowhere → the model smoothed out filler words, keep it). This
    lane runs at ctx=0, with no context, so "not found" simply means "not from
    here", and that fallback would let in every neighbour's claims.

    Fuzzy rather than literal matching: the model does smooth out filler ("uh,
    we, we'll" → "we'll"), and a literal comparison would throw those real
    claims away.
    """
    from ... import analyzer
    q = (quote or "").strip()
    if not q:
        return False
    qg = analyzer.grams(q)
    if not qg:
        return False
    return len(qg & analyzer.grams(window_text)) / len(qg) >= bar


def _norm(v, allowed, default):
    v = (v or "").strip().lower()
    return v if v in allowed else default


def _found(text: str) -> dict:
    """The lookup's JSON object: the last one in the answer, after the prose
    the prompt asks for (asked for JSON alone, Gemini stops searching), and
    perhaps in a code fence. Raises on anything that is not one of the four
    verdicts: a verdict nobody can act on is not a finding."""
    b = text.rfind("}")
    d = None
    for a in (i for i in range(b, -1, -1) if text[i] == "{"):
        try:
            d = json.loads(text[a:b + 1])
            break
        except ValueError:
            continue
    if not isinstance(d, dict):
        raise ValueError("no JSON object in the answer")
    if d.get("verdict") not in FOUND:
        raise ValueError(f"verdict {d.get('verdict')!r} is not one of {', '.join(FOUND)}")
    return {"verdict": d["verdict"], "actual": _unlinked(str(d.get("actual") or "")),
            "transcription_error": bool(d.get("transcription_error"))}


def _unlinked(text: str) -> str:
    """Markdown links taken out of a sentence. OpenAI writes its citations into
    the text as ([site](url)); the sources are kept separately, and the
    sentence is read where markdown does not render."""
    import re
    text = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", text)
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).strip()
