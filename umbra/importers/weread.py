"""WeRead — how long you read each day.

    Umbra ──POST /api/agent/gateway {"api_name": "/readdata/detail"}──> WeRead

**This source has no timeline, and that is its most important property.** The
gateway says "73 minutes on 1 August", never at what time. So it cannot draw a
lane and **contributes no minute to coverage**
(see analyze.py, which excludes it explicitly).

Then why have it? Because it is the other half of what Umbra lacks. Biome
already knows **when** the WeRead app was in use — media.py has listening
sessions with start and end, iphone.py has com.tencent.weread's foreground
time. Together they can do what neither can alone:

    Biome    when you were reading     exact interval; not whether reading or idle
    WeRead   how long that day         exact total; not at what time

media.py currently **guesses** "music left on, person walked away" with
SUSPECT_AFTER_MS (over 90 minutes is suspect). A daily ground truth turns the
guess into arithmetic: Biome says 90 minutes in the app, this says 45 minutes
read, and the difference was open but unread.

⚠️ **Days are Beijing days.** Every key comes back at 16:00 UTC — midnight in
Beijing, not your local midnight. Aligning them as local dates is silently off
by eight hours and looks entirely normal, so each row carries a window saying so.

Environment: WEREAD_API_KEY (in your env file; format wrk-xxxx)
"""

import os
import time

from .. import net

SOURCE = "weread"
REQUIRES = ["env:WEREAD_API_KEY"]

# This source **folds raw records into intervals**, so changing the algorithm changes
# the rows the same raw data produces. Import deletes the old rows for the span
# before writing — see db.replace_range and tests/test_resegment.py.
SEGMENTED = True

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
API_NAME = "/readdata/detail"

# The gateway requires it on every call. Without it the reply is "missing
# api_name parameter" — an error that points the wrong way entirely (api_name
# is right there in the body).
SKILL_VERSION = "1.0.4"

# How many months back a full import goes. monthly returns one month of daily
# data per call, so a year is 12 calls.
HISTORY_MONTHS = 14
MONTH_S = 30 * 86_400


def _post(key: str, body: dict) -> dict:
    """One gateway request.

    Request parameters must sit **flat at the top level of the body**. Wrapped in
    params, the gateway does not forward them and answers with the defaults — it
    looks like broken pagination, but the parameters never arrived.
    skill_version goes on every call; without it the misleading "missing
    api_name parameter" comes back.
    """
    return net.post_json(GATEWAY, {**body, "skill_version": SKILL_VERSION},
                         {"Authorization": f"Bearer {key}"})

# Highlights further apart than this are two reading sessions. Half an hour is a
# common-sense value: two highlights more than half an hour apart in one sitting
# usually had something else between them. Tune it once there is data.
MARK_GAP_S = 30 * 60


def marks_to_rows(marks: list[dict], since_ms: int | None = None) -> list[tuple]:
    """Highlights -> reading sessions.

    **The only thing this source can put on the timeline.** Every highlight has a
    createTime to the second, and they go back years — nearly all of it outside
    Biome's coverage (Biome keeps about a month).

    One highlight is a moment, not an interval; two or more say "read for at
    least this long". A real example: three highlights at 07:34:00, 07:35:44 and
    07:43:56 — nine minutes of an audiobook in the car, while the location source
    showed the same stretch as a drive.

    ⚠️ **markText is not stored.** Events are stored, not the text: "in chapter I
    of a book, three highlights" is a record of behaviour; the highlighted
    passage is an excerpt, and would turn a page that opens on a double-click
    into years of reading notes.
    The same judgement as leaving out F8 in media.py.
    """
    rows, cur = [], []
    for m in sorted(marks, key=lambda x: (x.get("book") or "", x["ts"])):
        if cur and m.get("book") == cur[-1].get("book") \
                and m["ts"] - cur[-1]["ts"] <= MARK_GAP_S:
            cur.append(m)
            continue
        if cur:
            rows.append(_mark_row(cur))
        cur = [m]
    if cur:
        rows.append(_mark_row(cur))
    rows.sort(key=lambda r: r[1])
    if since_ms is not None:
        rows = [r for r in rows if r[1] >= since_ms]
    return rows


def _mark_row(group: list[dict]) -> tuple:
    first, last = group[0], group[-1]
    return (
        f"weread:read:{first['ts']}", first["ts"] * 1000,
        (last["ts"] - first["ts"]) * 1000, SOURCE, "reading",
        # marks is the fact of how many highlights. What was highlighted is not
        # kept — see above.
        {"book": first.get("book") or "?", "author": first.get("author") or "",
         "chapter": first.get("chapter") or "", "marks": len(group)},
    )


def _notebooks(key: str) -> list[dict]:
    """Every book with notes. sort is the time of the book's latest note, which
    the incremental fetch filters on."""
    out, last = [], None
    while True:
        body = {"api_name": "/user/notebooks", "count": 100}
        if last is not None:
            body["lastSort"] = last
        data = _post(key, body)
        got = data.get("books") or []
        out += got
        if not data.get("hasMore") or not got:
            return out
        last = got[-1].get("sort")


def collect_marks(since_ms: int | None = None) -> list[tuple]:
    """Fetch highlights and turn them into reading sessions.

    Incrementally, only books whose latest note is after since are fetched —
    dozens of books over years, and fetching all of them every time burns quota
    on books that stopped changing long ago.
    """
    key = os.environ.get("WEREAD_API_KEY")
    if not key:
        return []
    try:
        books = _notebooks(key)
    except Exception:                                   # noqa: BLE001
        return []
    if since_ms is not None:
        books = [b for b in books if (b.get("sort") or 0) * 1000 >= since_ms]

    marks: list[dict] = []
    for b in books:
        try:
            d = _post(key, {"api_name": "/book/bookmarklist",
                            "bookId": b["bookId"]})
        except Exception:                               # noqa: BLE001
            continue                                    # one book failing must not sink the rest
        if d.get("errcode"):
            continue
        meta = d.get("book") or {}
        chapters = {str(c.get("chapterUid")): c.get("title") or ""
                    for c in (d.get("chapters") or [])}
        for m in (d.get("updated") or []):
            if not m.get("createTime"):
                continue
            marks.append({"ts": int(m["createTime"]),
                          "book": meta.get("title") or b.get("bookId"),
                          "author": meta.get("author") or "",
                          "chapter": chapters.get(str(m.get("chapterUid")), "")})
    return marks_to_rows(marks, since_ms)


def to_rows(read_times: dict, since_ms: int | None = None) -> list[tuple]:
    """{Beijing day start in seconds: seconds read} -> one row a day."""
    rows = []
    for key, secs in (read_times or {}).items():
        try:
            day_s, secs = int(key), int(secs)
        except (TypeError, ValueError):
            continue
        if secs <= 0:                     # a day with no reading needs no row
            continue
        ts = day_s * 1000
        if since_ms is not None and ts < since_ms:
            continue
        rows.append((
            # The uid depends only on the date: today is re-imported while you
            # are still reading, and a uid that changed with the duration would
            # pile up twenty rows for one day at one import an hour.
            f"weread:day:{day_s}", ts,
            # dur_ms means "takes up this long from this moment". We know the
            # amount, not the position — putting it in dur would claim it was read
            # in one sitting from Beijing midnight.
            0,
            SOURCE, "daily",
            {"read_s": secs, "read_min": round(secs / 60),
             "window": "beijing-day", "window_ms": 86_400_000},
        ))
    rows.sort(key=lambda r: r[1])
    return rows




def _fetch(key: str, base_time: int) -> dict:
    """One month of daily data."""
    return _post(key, {"api_name": API_NAME, "mode": "monthly",
                       "baseTime": base_time})


def collect(since_ms: int | None = None) -> list[tuple]:
    """No key returns empty — that is "WeRead not set up yet", not a fault."""
    key = os.environ.get("WEREAD_API_KEY")
    if not key:
        return []

    # Incrementally, this month only (baseTime=0 is the current period); a full
    # import goes back further.
    if since_ms is None:
        now = int(time.time())
        bases = [0] + [now - i * MONTH_S for i in range(1, HISTORY_MONTHS)]
    else:
        bases = [0]

    merged: dict = {}
    for base in bases:
        try:
            data = _fetch(key, base)
        except Exception:                               # noqa: BLE001
            # Reading is one source of thirteen; a gateway hiccup must not sink
            # the whole import.
            continue
        if data.get("errcode"):
            continue
        merged.update(data.get("readTimes") or {})
    # Both kinds of row together: daily totals (no position in time) and
    # highlight-bounded reading sessions (with one). See analyze.py on kind.
    return to_rows(merged, since_ms) + collect_marks(since_ms)


# ── How this source appears downstream ────────────────────────

# Only reading has a position in time. daily is a whole-day total, and giving it
# a lane would mean drawing a block that claims continuous reading from midnight.
LANES = [{"key": "reading", "kind": "reading",
          "origin": ("WeRead API", "highlight createTime, second precision")}]


def render(e) -> str | None:
    """daily has no moment attached. Rendered, it claimed reading at midnight
    Beijing time — every single day."""
    if e.get("kind") != "reading":
        return None
    return f"📖 reading 《{e.get('book') or '?'}》{e.get('chapter') or ''}".rstrip()


def classify(e) -> str | None:
    return "read" if e.get("kind") == "reading" else None
