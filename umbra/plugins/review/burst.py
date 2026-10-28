"""A few dense days, then gone: a visa application, a trip, a clinic visit.

recur.py looks for steady rhythms, and this shape reads there as irregular and
is dropped — which removes nearly all planning from the report. Five months of
one browser history had plenty: a visa system on 8 days of 15, a medical portal
on 5 of 10.

The lever is different too. A rhythm can be automated; a burst cannot — next
time it is a different visa. What helps is laying the last one's path out when
the next one starts.

A burst and a rhythm exclude each other. A dashboard opened daily is perfectly
dense and would be a five-month "burst"; MAX_SPAN is the hard line: past four
weeks it is a habit, recur's business.
"""

import datetime as dt

from .fold import day_of

MIN_DAYS = 4          # fewer is a few clicks
MAX_SPAN = 28         # longer is a habit — also the burst/rhythm boundary
MIN_DENSITY = 0.5     # active on at least half the days of its span
QUIET_DAYS = 7        # quiet this long is finished; shorter is still going


def detect(actions: dict, today: str | None = None, min_days: int = MIN_DAYS,
           max_span: int = MAX_SPAN, min_density: float = MIN_DENSITY) -> list[dict]:
    """{key: [ts...]} -> bursts, most recent first. Many times on one day is
    one day."""
    now = dt.date.fromisoformat(today) if today else dt.date.today()
    out = []
    for key, tss in actions.items():
        days = sorted({day_of(t) for t in tss})
        if len(days) < min_days:
            continue
        span = (days[-1] - days[0]).days + 1
        if span > max_span or len(days) / span < min_density:
            continue
        quiet = (now - days[-1]).days
        out.append({"key": key, "days": len(days), "span": span,
                    "density": round(len(days) / span, 2),
                    "first": days[0], "last": days[-1], "quiet_days": quiet,
                    "state": "done" if quiet >= QUIET_DAYS else "active",
                    "hits": len(tss)})
    out.sort(key=lambda b: (b["last"], b["days"]), reverse=True)
    return out
