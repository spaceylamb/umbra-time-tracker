"""Helpers for the render() a source writes.

A source's render() turns one of its events into the single line the model will
read. These are the pieces every source needs to do that consistently, kept in
their own module rather than on digest so that a source can import them without
importing the thing that calls it.

Nothing here decides *what* to say — that belongs to the source, which is the
only code that knows the shape of its own records.
"""

from . import apps


def hm(m, n_min: int = 1440) -> str:
    """Minute-of-day -> HH:MM. **Anything crossing a day boundary is labelled.**

    An idle stretch starting 23:17 the previous evening and continuing into the
    morning correctly lands in the 08:00 window (nobody did touch the keyboard).
    But its minute number is -43, and -43 // 60 % 24 = 23 while -43 % 60 = 17 —
    so it renders silently as "23:17" and the model reads it as that evening.

    Out-of-range values are labelled rather than silently corrected, the same way
    inference.expand treats out-of-range spans.
    """
    m = int(m)
    pre = "prev day " if m < 0 else ("next day " if m >= n_min else "")
    m %= 1440           # the clock face is real time, not the day's length
    return f"{pre}{m // 60:02d}:{m % 60:02d}"


def app_name(e) -> str:
    """The human name of the app an event happened in.

    apps.name() rather than apps.label(): this string goes into the digest, and
    the digest is the inference cache key. See the apps module docstring.
    """
    return apps.name(e.get("bundle")) or e.get("app") or ""


def span(e) -> str:
    """" (09:00 continuing until 16:00)" for an event with a real duration.

    Empty for anything under a minute — below that the annotation is noise
    rather than information.
    """
    d = e.get("dur_ms") or 0
    if d < 60000:
        return ""
    a = e["_min"]
    return f" ({hm(a)} continuing until {hm(a + d // 60000)})"
