"""One record shape over two stores: the plugin table and the legacy actions table."""

from ... import analyzer


PLUGIN = "actions"


# ── One record shape over two stores ──────────────────────────
#
# Proposals found before this lane moved onto the core store are in the frozen
# `proposals` table; everything since is in analyzer_items. Both are read, and
# every function below works on records in the old table's vocabulary — one
# path through the logic, store-aware only where a record is written back.
#
# Frozen means no new findings go there. The lifecycle of findings already
# there continues in place: a proposal queued before the move is still sent,
# and your verdict on it still lands on it. Ids never collide — new finding ids
# start above every id the old table used (db.items_after_legacy) — so an id
# alone says which store a record lives in.

_LEGACY_COLS = ("id, day, t, task, quote, status, type, confidence, domain, "
                "marked, speakers, echo_score, echo_text, win_a, dispatch_id, "
                "verdict, verdict_note, result, model, created_ms")


def records(con, day: str | None = None, status=None) -> list[dict]:
    """Every proposal, old and new, as one list in the old table's shape."""
    q, args = f"SELECT {_LEGACY_COLS} FROM proposals", []
    if day is not None:
        q += " WHERE day=?"
        args.append(day)
    keys = [k.strip() for k in _LEGACY_COLS.split(",")]
    out = [dict(zip(keys, r), src="legacy") for r in con.execute(q, args)]
    for it in analyzer.load_items(con, PLUGIN, day=day):
        m = it["meta"]
        out.append({
            "id": it["id"], "day": it["day"], "t": it.get("t"),
            "task": it.get("task") or "", "quote": it["key"],
            "status": it["state"], "type": it.get("type"),
            "confidence": it.get("confidence"), "domain": it.get("domain"),
            "marked": 1 if it.get("marked") else 0,
            "speakers": it.get("speakers"),
            "echo_score": it.get("echo_score"), "echo_text": it.get("echo_text"),
            "win_a": it["win_a"], "dispatch_id": m.get("dispatch_id"),
            "verdict": m.get("verdict"), "verdict_note": m.get("verdict_note"),
            "result": m.get("result"), "model": it["model"],
            "dup_of": m.get("dup_of"),
            "created_ms": it["at_ms"], "src": "item"})
    if status is not None:
        want = {status} if isinstance(status, str) else set(status)
        out = [r for r in out if r["status"] in want]
    out.sort(key=lambda r: (r["day"], r["t"] or "", r["id"]))
    return out


def record(con, pid: int) -> dict | None:
    got = con.execute(f"SELECT {_LEGACY_COLS} FROM proposals WHERE id=?",
                      (pid,)).fetchone()
    if got:
        keys = [k.strip() for k in _LEGACY_COLS.split(",")]
        return dict(zip(keys, got), src="legacy")
    it = analyzer.get_item(con, PLUGIN, pid)
    if it is None:
        return None
    return next((r for r in records(con, day=it["day"]) if r["id"] == pid), None)


def _write(con, pid: int, status: str, expect=None, **fields) -> bool:
    """Move one proposal, in whichever store it lives. Returns whether it moved.

    expect is the status it must be in; a move from anywhere else is refused,
    so a verdict that landed first is not overwritten by a late send.
    """
    rec = record(con, pid)
    if rec is None:
        return False
    if expect is not None:
        allowed = {expect} if isinstance(expect, str) else set(expect)
        if rec["status"] not in allowed:
            return False
    if rec["src"] == "legacy":
        sets = ["status=?"] + [f"{k}=?" for k in fields]
        con.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id=?",
                    (status, *fields.values(), pid))
        con.commit()
        return True
    return analyzer.update_item(con, PLUGIN, pid, state=status,
                                meta={k: v for k, v in fields.items()})
