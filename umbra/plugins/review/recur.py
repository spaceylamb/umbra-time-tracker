"""Is something repeating **on a rhythm**, and when is it next?

## Three days make a rhythm

Two points are one gap, and one gap says nothing: "bought on 4 July and on 17
August" and "every six weeks" look identical on two points. Twenty times on one
day is one point too — that is how dense one action was, not a rhythm.

## The window has to be much longer than the rhythm

Measured on five months of one browser history:

    window   rhythms found   every two weeks   monthly or rarer
     2 wk          50               0                 0
     4 wk         152               8                 0
     8 wk         284              47                19
    12 wk         326              60                60

Looking at a month of data finds nothing that happens once a month. That is the
arithmetic of three points, not a bug — so the window (16 weeks) and how often a
report goes out (monthly) are separate decisions.

## Irregular is still returned

Not being able to name a rhythm is not "it does not happen": an irregular one
comes back with regular=False. Only fewer than three days returns None, because
then there is genuinely nothing to compute.
"""

import datetime as dt
import statistics

from .fold import day_of

MIN_DAYS = 3

#: Median gap in days (inclusive upper bound) -> words.
_NAMES = [(1.5, "daily"), (3.5, "every 2-3 days"), (9, "weekly"),
          (17, "every two weeks"), (40, "monthly")]


def _name(med: float) -> str:
    return next((name for hi, name in _NAMES if med <= hi), "now and then")


def cadence(days) -> dict | None:
    """Dates -> a rhythm, or None with fewer than three distinct days.

    `jitter` is the median deviation of the gaps from their median: 0 is a gap
    that never varies (a fortnightly report was 14±0). Regular allows
    jitter <= max(2, median / 2), because a weekly habit drifts by a couple of
    days. But the median alone calls two clusters a rhythm — 1st, 2nd, 20th,
    21st have gaps [1, 18, 1], median 1, deviation 0: "daily" — so the largest
    gap may not be far beyond the median either.
    """
    ds = sorted(set(days))
    if len(ds) < MIN_DAYS:
        return None
    gaps = [(ds[i + 1] - ds[i]).days for i in range(len(ds) - 1)]
    med = statistics.median(gaps)
    jitter = statistics.median([abs(g - med) for g in gaps])
    return {
        "n": len(ds),
        "median_gap": med,
        "jitter": jitter,
        "regular": jitter <= max(2, med / 2) and max(gaps) <= max(med * 3, med + 7),
        "name": _name(med),
        "first": ds[0],
        "last": ds[-1],
        "next_expected": ds[-1] + dt.timedelta(days=round(med)),
        "span_days": (ds[-1] - ds[0]).days,
    }


def detect(actions: dict, min_days: int = MIN_DAYS) -> list[dict]:
    """{key: [ts...]} -> the ones with a rhythm, **most recent first**. Not by
    count: "still doing it last week" is worth more than "twenty times three
    months ago", and sorting by count keeps stopped habits on top for ever."""
    out = []
    for key, tss in actions.items():
        c = cadence([day_of(t) for t in tss])
        if c and c["n"] >= min_days:
            out.append(dict(c, key=key, hits=len(tss)))
    out.sort(key=lambda r: (r["last"], r["n"]), reverse=True)
    return out
