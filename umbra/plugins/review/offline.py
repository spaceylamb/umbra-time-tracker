"""What happened away from the screen: places, workouts, calls, messages, what
you listened to and read.

A review that reads only the browser and the agents can only conclude things
about the screen. These sources are exactly the part that is not.

This layer produces actions, not advice. "Swimming every five days" is a life,
not something to automate — but hard-coding "workouts are fine" would also
swallow "the pharmacy every two weeks", which can be helped and looks the same
in the data. The model, which reads what the key says, decides.

Once a day: one gym visit can be several stays, one workout several segments.
"""

import json

from .fold import day_of


def _rows(con, source, lo, hi, kind=None):
    q = ("SELECT ts, payload FROM events WHERE source=? "
         + ("AND kind=? " if kind else "") + "AND ts>=? AND ts<? ORDER BY ts")
    for ts, payload in con.execute(q, (source, kind, lo, hi) if kind else (source, lo, hi)):
        try:
            yield ts, json.loads(payload)
        except (ValueError, TypeError):
            continue          # a bad row is skipped, not the whole round


def actions(con, lo: int, hi: int) -> dict:
    """{key: [ts...]}, once a day. Keys are words a person reads on the card —
    a bundle id or a coordinate hash would mean nothing there."""
    raw: dict = {}

    def add(key, ts):
        if key:
            raw.setdefault(key, []).append(ts)

    for ts, p in _rows(con, "location", lo, hi, "stay"):
        # An unnamed stay is a hash nobody would recognise.
        if p.get("place") and p.get("named") is not False:
            add(f"at:{p['place']}", ts)
    for ts, p in _rows(con, "whoop", lo, hi, "workout"):
        add(f"workout:{p['sport']}" if p.get("sport") else None, ts)
    for ts, p in _rows(con, "intent", lo, hi):
        if p.get("verb") == "StartCall":
            add("call", ts)       # the phone app and the call service are one call
        elif p.get("verb") == "SendMessage":
            add(f"message:{p.get('app')}", ts)
    for ts, p in _rows(con, "media", lo, hi, "playing"):
        add(f"listening:{p['title']}" if p.get("title") else None, ts)
    for ts, p in _rows(con, "weread", lo, hi, "reading"):
        add(f"reading:{p['book']}" if p.get("book") else None, ts)

    out = {}
    for key, tss in raw.items():
        seen, keep = set(), []
        for ts in sorted(tss):
            d = day_of(ts)
            if d not in seen:
                seen.add(d)
                keep.append(ts)
        out[key] = keep
    return out
