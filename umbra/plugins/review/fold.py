"""Records -> actions.

`events` holds records, not actions, and a COUNT(*) over it counts records. On
one real history:

    one Chrome search              = 29 visits to one URL in 87 seconds
    Chrome overall           7840 rows -> 3189 visits             2.5 : 1
    "a home page 290 times a day"   -> really 19                    14 : 1

What these errors share is that they are **believably wrong**: a number
fourteen times too big looks exactly like a real one, and nothing anywhere
complains. Seven of ten conclusions in one early analysis were wrong for this
one reason.

So the fold ratio (rows / actions) always travels with the action count. It is
a measure of trust: 2.5 says the source is chatty, 14 says the number would
have been off by an order of magnitude.

The gap counts from the **previous** row: a click every nine minutes for an
hour is one long action, not seven.
"""

import datetime as dt

#: Minutes between rows that still make one action, per source. A redirect
#: storm is seconds; glancing at a page again is minutes.
GAP_MIN = {"chrome": 10, "agent": 15, "default": 30}

#: When Butler and the local Claude Code log record the same sentence: within
#: this many milliseconds and the same opening characters. The same pair of
#: constants segment.collapse() uses for the same question.
DUPE_MS = 180_000
DUPE_PREFIX = 40


class Folded:
    """The actions and how many rows they came from. Use them together."""

    __slots__ = ("actions", "n_raw", "n_actions", "ratio")

    def __init__(self, actions: dict, n_raw: int):
        self.actions = actions
        self.n_raw = n_raw
        self.n_actions = sum(len(v) for v in actions.values())
        # No rows is no inflation, not zero and not a crash.
        self.ratio = round(n_raw / self.n_actions, 2) if self.n_actions else 1.0


def fold(rows, gap_min: int) -> Folded:
    """rows: (ts_ms, key) pairs. Rows of one key within the gap are one action.

    Sorted first: callers often stitch two sources together, and folding in the
    wrong order gives a result with nothing visibly wrong about it.
    """
    gap = gap_min * 60_000
    last: dict = {}
    out: dict = {}
    n = 0
    for ts, key in sorted(rows, key=lambda r: r[0]):
        n += 1
        if key is None:
            continue
        prev = last.get(key)
        last[key] = ts
        if prev is not None and ts - prev <= gap:
            continue
        out.setdefault(key, []).append(ts)
    return Folded(out, n)


def day_of(ts_ms: int) -> dt.date:
    """Milliseconds -> the local calendar day. Rhythms are weeks long; a
    travel day's hour does not move one."""
    return dt.datetime.fromtimestamp(ts_ms / 1000).date()


def agent_dupe_index(rows) -> dict:
    """{opening characters: [ts...]} for is_agent_dupe. rows: (ts_ms, text)."""
    heads: dict = {}
    for ts, text in rows:
        heads.setdefault((text or "")[:DUPE_PREFIX], []).append(ts)
    return heads


def is_agent_dupe(heads: dict, ts: int, text: str, window_ms: int = DUPE_MS) -> bool:
    return any(abs(ts - t) < window_ms
               for t in heads.get((text or "")[:DUPE_PREFIX], ()))


def dedupe_agent(butler, local, window_ms: int = DUPE_MS):
    """A task sent through Butler to Claude Code is recorded on both sides.
    Keep the local one: it is part of the whole session, Butler's is only the
    opening. Returns [(ts, text, "local" | "butler")] in time order."""
    local = list(local)
    heads = agent_dupe_index(local)
    out = [(ts, text, "local") for ts, text in local]
    out += [(ts, text, "butler") for ts, text in butler
            if not is_agent_dupe(heads, ts, text, window_ms)]
    out.sort(key=lambda r: r[0])
    return out
