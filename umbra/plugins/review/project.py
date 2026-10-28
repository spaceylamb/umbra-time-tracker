"""A batch of new sites that came up together is one thing.

burst.py works per site, but a real project spans several, each below its bar:
one cluster about sleep devices was eight new domains over 11 days, each active
five days at under 0.5 density. burst saw none of them, recur dropped the span
as too short — and the whole project vanished. The unit is the project.

Three tests, the third the one that matters:

    all new          present at the window's start is an old habit
    together         first seen within a short stretch
    active together  two unrelated sites started the same week (a new
                     newspaper, a new bank) pass the first two; their active
                     days do not overlap.

Content is deliberately not read. The shape in time is the evidence — it is
explainable, reproducible and free. Saying what the project was is a later
layer's job.
"""

import datetime as dt
import re

from .fold import day_of

TOGETHER_DAYS = 10    # first seen within this many days of each other
MIN_OVERLAP = 0.4     # shared active days / the smaller set
MIN_KEYS = 3          # two sites meet by accident too easily
MIN_DAYS_PER_KEY = 2  # one visit and gone does not take part
#: A project is short-lived by definition. Without this, a site active for three
#: months overlaps everything and once pulled 129 sites over 110 days into one
#: "project". The same line as burst.MAX_SPAN.
MAX_SPAN = 28
EDGE_DAYS = 3         # first seen this early in the window predates it
QUIET_DAYS = 7

#: Local addresses run every day and overlap everything, gluing unrelated sites
#: together (one cluster held a dev port, a hotel and a hardware store). They
#: are what you are building, not where you went for something.
_LOCAL = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.|192\.168\.|"
                    r"172\.(1[6-9]|2\d|3[01])\.)[:.]?", re.I)


def detect(actions: dict, today: str | None = None,
           window_start: str | None = None) -> list[dict]:
    """{key: [ts...]} -> projects, most recent first."""
    now = dt.date.fromisoformat(today) if today else dt.date.today()
    edge = (dt.date.fromisoformat(window_start) + dt.timedelta(days=EDGE_DAYS)
            if window_start else None)
    days = {}
    for key, tss in actions.items():
        ds = sorted({day_of(t) for t in tss})
        if (len(ds) < MIN_DAYS_PER_KEY or (edge and ds[0] < edge)
                or (ds[-1] - ds[0]).days + 1 > MAX_SPAN or _LOCAL.match(key)):
            continue
        days[key] = set(ds)

    # Greedy by first appearance: a site belongs to one project.
    order = sorted(days, key=lambda k: min(days[k]))
    used, out = set(), []
    for i, seed in enumerate(order):
        if seed in used:
            continue
        first = min(days[seed])
        group = [seed]
        for other in order[i + 1:]:
            if other in used:
                continue
            if (min(days[other]) - first).days > TOGETHER_DAYS:
                break
            a, b = days[seed], days[other]
            if len(a & b) / max(1, min(len(a), len(b))) >= MIN_OVERLAP:
                group.append(other)
        if len(group) < MIN_KEYS:
            continue
        alld = set().union(*[days[k] for k in group])
        lo, hi = min(alld), max(alld)
        if (hi - lo).days + 1 > MAX_SPAN:
            continue
        used.update(group)
        quiet = (now - hi).days
        out.append({"keys": sorted(group), "days": len(alld),
                    "span": (hi - lo).days + 1, "first": lo, "last": hi,
                    "quiet_days": quiet,
                    "state": "done" if quiet >= QUIET_DAYS else "active"})
    out.sort(key=lambda p: (p["last"], len(p["keys"])), reverse=True)
    return out
