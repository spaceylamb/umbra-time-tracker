"""One record shape over two stores: the plugin table and the legacy claims table."""

import time

from ... import analyzer


# ── The old window cache ──────────────────────────────────────

# This lane's own cache table, separate from the tasks lane's on purpose: one
# shared table would make a re-run of either invalidate the other's work.
DIGEST_TABLE = "claim_digests"


def load_digests(con, day: str, model: str) -> dict:
    from ... import analyzer
    return analyzer.load_digests(con, DIGEST_TABLE, day, model)


# ── Extraction ────────────────────────────────────────────────

PLUGIN = "claims"


# ── One record shape over two stores ──────────────────────────
#
# Claims found before this lane moved onto the core store are in the frozen
# `claims` table, with their digest deliveries in claim_cards and
# claim_card_items; everything since is in analyzer_items, with the same
# bookkeeping in each item's meta. Every function below works on records in the
# old table's vocabulary and writes back through _write, which knows which store
# a claim lives in. Frozen means no new claims go there; the lifecycle of the
# claims already there — checked, carded, delivered — continues in place. Ids
# never collide (db.items_after_legacy), so an id alone says where a claim is.

_LEGACY_COLS = ("id, day, t, win_a, speaker, claim, quote, kind, verdict, "
                "absolute, why, model, status, check_task, report, card, "
                "attempt, created_ms")


_LIFECYCLE = ("check_task", "report", "card", "attempt")


def records(con, day: str | None = None, status=None) -> list[dict]:
    """Every claim, old and new, as one list in the old table's shape, plus
    `delivered`: the digest it went out in, or None."""
    keys = [k.strip() for k in _LEGACY_COLS.split(",")]
    q, args = f"SELECT {_LEGACY_COLS} FROM claims", []
    if day is not None:
        q += " WHERE day=?"
        args.append(day)
    sent = dict(con.execute("SELECT claim_id, task FROM claim_card_items"))
    out = []
    for r in con.execute(q, args):
        rec = dict(zip(keys, r), src="legacy", lookup=None)
        rec["delivered"] = sent.get(rec["id"])
        out.append(rec)
    for it in analyzer.load_items(con, PLUGIN, day=day):
        m = it["meta"]
        out.append({
            "id": it["id"], "day": it["day"], "t": it.get("t"),
            "win_a": it["win_a"], "speaker": it.get("speaker"),
            "claim": it.get("claim") or "", "quote": it["key"],
            # A later round can raise the first verdict (see _mark_repeats);
            # the payload is written once, so the raised one lives in meta.
            "kind": it.get("kind"), "verdict": m.get("verdict") or it.get("verdict"),
            "absolute": 1 if (m.get("absolute") or it.get("absolute")) else 0,
            "why": it.get("why"),
            "model": it["model"], "status": it["state"],
            "check_task": m.get("check_task"), "report": m.get("report"),
            "card": m.get("card"), "attempt": m.get("attempt") or 0,
            "created_ms": it["at_ms"], "delivered": m.get("delivered"),
            "lookup": m.get("lookup"), "src": "item"})
    if status is not None:
        want = {status} if isinstance(status, str) else set(status)
        out = [r for r in out if r["status"] in want]
    out.sort(key=lambda r: (r["day"], r["t"] or "", r["id"]))
    return out


def _write(con, rec: dict, status: str | None = None, **fields) -> None:
    """Move one claim along, in whichever store it lives. No commit: the
    callers move a whole day's batch and commit once."""
    if rec["src"] == "legacy":
        sets = ([("status", status)] if status else []) + list(fields.items())
        if sets:
            con.execute(f"UPDATE claims SET {', '.join(k + '=?' for k, _ in sets)} "
                        "WHERE id=?", (*[v for _, v in sets], rec["id"]))
        return
    now = int(time.time() * 1000)
    cur = analyzer.get_item(con, PLUGIN, rec["id"])
    meta = {**cur["meta"], **fields}
    con.execute("UPDATE analyzer_items SET state=?, meta=?, updated_ms=? "
                "WHERE plugin=? AND id=?",
                (status or cur["state"], __import__("json").dumps(
                    meta, ensure_ascii=False), now, PLUGIN, rec["id"]))


def _deliver(con, rec: dict, task: str, now: int) -> None:
    """Record that a claim went out in a digest. One ledger entry per claim, so
    a recording arriving late can go out in a supplement instead of being
    sealed out of a day that already had its card."""
    if rec["src"] == "legacy":
        con.execute("INSERT OR IGNORE INTO claim_card_items (claim_id,task,created_ms) "
                    "VALUES (?,?,?)", (rec["id"], task, now))
    else:
        _write(con, rec, delivered=task, delivered_ms=now)
