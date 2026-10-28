"""Suggestions -> one card a month, through a sink.

One card per period, not one per suggestion: a judging pass once produced
hundreds, and even five cards is five things to open, when what a person wants
is everything on one screen. Each line carries its #id, because ruling on one
is `umbra review verdict <id>` and a card without the numbers sends you
digging through the database.

## The gates

The first voice-dispatched tasks were 82% "already done". Here one pass found
over four hundred, and no one reads four hundred lines. So:

    needs_you = 0        only what the model says can go on without you
    not already over     a next date in the past means it has probably stopped
                         — nearly half of the first four hundred, dozens of
                         them a month over. On the card, that is asking you to
                         unsubscribe from mail that stopped coming in July.
    six per lever        one kind had 166, another 16; without a cap per group
                         the first fills the card and the second never shows
    24 in all            one screen
    most frequent first  so a ×3 never pushes out a ×51
    the rest wait        for next month's card; not dropped

The ledger is per suggestion (`card` on each row). A card per period alone
would seal the period with the first card, and nothing judged later could ever
get onto one.
"""

import collections
import datetime as dt
import json
import time

from . import detect, judge

PLUGIN = "review"
PER_LEVER = 6
MAX_ITEMS = 24
KEY_PREFIX = "umbra:review:v1:"


def period_of(today: str | None = None) -> str:
    d = dt.date.fromisoformat(today) if today else dt.date.today()
    return d.strftime("%Y-M%m")


def dispatchable(con, today: str | None = None) -> list[dict]:
    """The suggestions that go on this period's card. See the gates above."""
    now = today or str(dt.date.today())
    rows = [r for r in judge.load(con)
            if not r["needs_you"] and not r["card"]
            and (not r["next_day"] or r["next_day"] >= now)]
    rows.sort(key=lambda r: -(r["n"] or 0))
    per, out = collections.Counter(), []
    for r in rows:
        lev = r["lever"] or "other"
        if per[lev] >= PER_LEVER:
            continue
        per[lev] += 1
        out.append(r)
        if len(out) >= MAX_ITEMS:
            break
    return out


def item(rows: list[dict], period: str) -> dict:
    """The card, in the shape every sink takes. **Preview and send both build
    it here**: two builders would let what you previewed differ from what went."""
    by = collections.OrderedDict()
    for r in rows:
        by.setdefault(r["lever"] or "other", []).append(r)
    parts = [f"What Umbra counted you doing on a rhythm that could go on without "
             f"you — {len(rows)} things, grouped by what can be done ({period}).\n"]
    for lev, rs in by.items():
        parts.append(f"\n## {detect.LEVER_WORDS.get(lev, lev)} ({len(rs)})\n")
        for r in rs:
            ev = json.loads(r["evidence"] or "[]")
            first = (ev[0].get("text") if ev else "") or ""
            nxt = f" · next ~{r['next_day']}" if r["next_day"] else ""
            parts.append(f"- **#{r['id']} {r['title']}**  {r['cadence']} · {r['n']} times "
                         f"· last {r['last_day']}{nxt}\n"
                         f"  {r['why']}\n"
                         f"  evidence: {first[:70]}")
    parts.append("\n\nRule on one: `umbra review verdict <id> useful|already|not_worth|wrong`. "
                 "Anything ruled on, or not worth it, does not come back.")
    return {"key": f"{KEY_PREFIX}{period}",
            "title": f"{len(rows)} things you keep doing ({period})"[:80],
            "body": "\n".join(parts), "visible": True}


def preview(con, today: str | None = None) -> dict | None:
    """Exactly what the next card would say. Sends nothing; needs no sink."""
    rows = dispatchable(con, today=today)
    if not rows:
        return None
    period = period_of(today)
    return {"period": period, "ids": [r["id"] for r in rows], "item": item(rows, period)}


def sent(con, period: str) -> bool:
    judge.ensure(con)
    return con.execute("SELECT 1 FROM review_cards WHERE period=?", (period,)).fetchone() is not None


def send(con, name: str, today: str | None = None, log=print) -> int:
    """Send this period's card to the named sink. Returns 1 if it went, else 0.
    A failure records nothing: recorded, the suggestions on it would never
    reach any card."""
    from ... import sink as S
    period = period_of(today)
    if sent(con, period):
        return 0
    prev = preview(con, today)
    if not prev:
        return 0
    try:
        S.send(con, PLUGIN, period, prev["item"], name)
    except RuntimeError as e:
        if not isinstance(e.__cause__, S.Duplicate):
            con.commit()
            log(f"  ✗ review: the {period} card did not go to {name}: {e.__cause__ or e}")
            return 0
        # The destination already has this period's card: that is sent.
        S.record(con, PLUGIN, period, name, S.ANSWERED, external_id=e.__cause__.receipt)
    with con:
        con.execute("INSERT OR REPLACE INTO review_cards (period, sink, created_ms, items) "
                    "VALUES (?,?,?,?)", (period, name, int(time.time() * 1000), len(prev["ids"])))
        con.executemany("UPDATE review_suggestions SET card=? WHERE id=?",
                        [(period, i) for i in prev["ids"]])
    log(f"  review: the {period} card went to {name} ({len(prev['ids'])} things)")
    return 1
