"""What became of a to-do: done, not a task, undone — from the page, the CLI or a sink."""

import re

from .store import _write, record, records


# ── What you tapped, coming back ──────────────────────────────

_KEY = re.compile(r"^umbra:(\d{4}-\d{2}-\d{2}):(\d+)$")


# The three buttons at the destination → the local state.
#
# "Already done" and "not a task" both close a proposal, but **only the second
# means extraction was wrong**. Already done means it was extracted perfectly and
# is merely stale — mixing it into the negatives would teach the model to miss
# that kind of real task. The two buttons sit close together in the UI; here
# they must stay far apart.
VERDICTS = {"ran": "approved",
            "not_a_task": "dismissed",
            "already_done": "dismissed"}


def record_verdicts(con, items) -> int:
    """Match what was tapped at the destination back to local proposals, by
    externalEventKey.

    Keys that are not recognised are skipped quietly — the destination also holds
    tasks from other sources, with keys in other shapes, and that is not an
    error.
    """
    n = 0
    for it in items or []:
        m = _KEY.match(str((it or {}).get("externalEventKey") or ""))
        if not m:
            continue
        verdict = (it.get("verdict") or "").strip()
        status = VERDICTS.get(verdict)
        if not status:
            continue
        pid = int(m.group(2))
        rec = record(con, pid)
        if rec is None or rec["day"] != m.group(1):
            continue
        if _write(con, pid, status, verdict=verdict,
                  verdict_note=it.get("verdictNote")):
            n += 1
    con.commit()
    return n


def close(con, pid: int, verdict: str) -> bool:
    """A verdict given here rather than at the destination — the page's ✓ and
    ✕, for anyone whose sink only receives. Same two words, same meaning:
    only not_a_task is an extraction error and lands in negatives().

    Only an open proposal closes; one already closed keeps the verdict it
    has. A proposal closed before it was sent is never sent: dispatch takes
    only `proposed`.
    """
    if verdict not in ("already_done", "not_a_task"):
        raise ValueError(f"unknown verdict {verdict!r}")
    return _write(con, pid, VERDICTS[verdict], expect=("proposed", "dispatched"),
                  verdict=verdict, verdict_note="marked on the page")


def reopen(con, pid: int) -> bool:
    """Undo close() or a duplicate hold: back to sent if it had gone out,
    else to proposed."""
    rec = record(con, pid)
    if rec is None or rec["status"] not in ("dismissed", "duplicate"):
        return False
    back = "dispatched" if rec.get("dispatch_id") else "proposed"
    return _write(con, pid, back, expect=("dismissed", "duplicate"),
                  verdict=None, verdict_note=None)


def negatives(con, limit: int = 50) -> list[dict]:
    """The negatives: the ones you explicitly marked "not a task".

    Only recorded for now — using them to change the prompt is another matter,
    which needs enough samples to see which phrasings the false extractions
    cluster in. Putting a handful into the prompt early would calibrate an
    unmeasured bias against noise.
    """
    rows = [r for r in records(con) if r["verdict"] == "not_a_task"]
    rows.sort(key=lambda r: (r["day"], r["t"] or ""), reverse=True)
    return [{"day": r["day"], "t": r["t"], "task": r["task"], "quote": r["quote"],
             "type": r["type"], "note": r["verdict_note"]} for r in rows[:limit]]


# ── Local state, and the old local outlet ─────────────────────

def queued(con) -> int:
    """How many proposals are waiting to go out, whatever max_per_run lets
    through in one round."""
    return len(records(con, status="proposed"))


def echoes(con) -> list[dict]:
    """Proposals held back as echoes: said aloud while dictating the same thing
    to an agent. Kept so "found and held back" stays distinguishable from
    "never found"."""
    rows = sorted(records(con, status="echo"),
                  key=lambda r: (r["day"], r["t"] or ""))
    rows.sort(key=lambda r: r["day"], reverse=True)
    return [{"day": r["day"], "t": r["t"], "task": r["task"], "quote": r["quote"],
             "score": r["echo_score"] or 0, "match": r["echo_text"] or ""}
            for r in rows]


def pending(con) -> list[tuple]:
    return [(r["id"], r["day"], r["t"], r["task"], r["quote"], r["type"],
             r["status"]) for r in records(con, status=("proposed", "dispatched"))]


def set_status(con, pid: int, status: str, result: str | None = None):
    _write(con, pid, status, result=result)
