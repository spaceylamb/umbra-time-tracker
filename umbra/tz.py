"""Which timezone you were in — and the "day" that follows from it.

`events.ts` stores UTC epoch ms, and that layer is always right however often it
is recomputed. But **"what time" and "which day" are functions of the local
timezone**, and the machine's timezone travels with its owner. Before this
module, the code everywhere used bare `localtime` / `datetime.fromtimestamp()`,
which read the timezone **of the process right now** — so on landing abroad,
years of history were re-cut into days, silently. The same events counted under
two timezones give visibly different totals for the same dates.

Two things must be kept apart; mixing them was the earlier design's worst
mistake:

    offset_at(ts)   the clock reading at that moment   ← "when did I get up"
                    indifferent to how long the day was, so comparable
                    across timezones
    day_starts()    which day an event belongs to      ← the page, coverage,
                    inference windows. It must be continuous, and only a scan
                    along the time axis guarantees that.

## Why not "compute each date's bounds on its own"

`analyze.day_bounds()` used to take "2026-10-05" and compute its start and end
independently. Anchoring each day to its own timezone in that shape makes gaps
and overlaps **inevitable**, as a pair:

    China → US   one day anchored to CST, the next to PDT
                 → fifteen hours of UTC belong to no day
    US → China   → the same fifteen hours are counted in two days, and trends
                   gain a day's worth from nowhere

The right shape scans UTC in one direction: each time the local clock reads
00:00, cut. Between two cuts is a day. **Joining end to end is guaranteed by
construction**; no filling logic is needed.

The price is that a travel day is not 24 hours (ten hours flying east, thirty-
nine flying back) — which is the day actually lived. Padding it to 24 would be
the fabrication.

## Days that are not 24 hours are not a travel quirk

Daylight saving gives two a year: 2026-03-08 is 23 hours and 2026-11-01 is 25.
A defensive `min(1440, …)` clamp in inference.expand() hid them while those days
held little; it turned into a mask — it let an overflow pass without an error,
so nobody learned the assumption no longer held.

So `day_minutes()` is the most important thing this module offers: every
hard-coded 1440 should become it.

## A travel day is "the zone changed", not "the offset changed"

On the offset alone, the two daylight-saving days a year would be dropped from
trends as travel. So `tz_marks` must store the zone name (America/Los_Angeles),
not only the seconds.
"""

import datetime as dt
import os
import sqlite3
import time

DAY_MS = 86_400_000
_EPOCH = dt.date(1970, 1, 1)

SCHEMA = """
-- Which timezone you were in, from when. **A row only on a change** — one per
-- sync would pile up over a year, and "when did it change" is this table's only
-- information.
--
-- Append-only, like events. Dropping the table loses no raw event, but it loses
-- "which offset applies to which stretch of history", and every cross-timezone
-- day changes shape again — so it is not a cache.
CREATE TABLE IF NOT EXISTS tz_marks (
    ts    INTEGER PRIMARY KEY,   -- from this moment on (UTC epoch ms)
    off_s INTEGER NOT NULL,      -- UTC offset in seconds (US Pacific summer = -25200)
    zone  TEXT NOT NULL,         -- IANA name; tells a zone change from daylight saving
    src   TEXT NOT NULL          -- auto = observed by sync; config = a trip entered by
                                 -- hand; seed = backfilled from the machine
);
"""

#: The moment the machine switches timezone **is not** the moment you land — a
#: Mac often waits until it is opened and online, measured to lag by more than
#: ten hours. So a travel day is widened by this much on each side.
#:
#: It does not affect how days are cut (that is guaranteed by construction),
#: only which days are left out of trends.
TRAVEL_HALO_MS = 12 * 3600_000


# ── The machine's timezone now ────────────────────────────────

def machine_offset(ts_ms: int | None = None) -> int:
    """The machine's UTC offset (seconds) at a moment.

    Not `time.timezone / time.altzone`: those are the daylight-saving state
    **when the process started**, so asking for last November's offset gets
    today's answer. `astimezone()` interprets the given instant.
    """
    t = time.time() if ts_ms is None else ts_ms / 1000
    off = dt.datetime.fromtimestamp(t).astimezone().utcoffset()
    return int(off.total_seconds()) if off else 0


def machine_zone() -> str:
    """The IANA zone name, falling back to the abbreviation. An abbreviation
    cannot tell US Central from China Standard, but it can tell "changed" from
    "unchanged", which is all the travel test needs.
    """
    env = os.environ.get("TZ")
    if env:
        return env
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return time.tzname[0]


# ── Reading and writing the history ───────────────────────────

def history(con: sqlite3.Connection) -> list[tuple[int, int, str]]:
    """[(start ms, offset s, zone), …] ascending — a piecewise-constant line."""
    return [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT ts, off_s, zone FROM tz_marks ORDER BY ts")]


def record(con: sqlite3.Connection, ts_ms: int | None = None,
           off_s: int | None = None, zone: str | None = None,
           src: str = "auto") -> bool:
    """Record one observation. **Nothing changed, nothing written**; returns
    whether a row was written.

    It compares with "the row in force at ts_ms", not "the table's last row",
    so a trip entered by hand (src="config") in the middle of history is still
    de-duplicated correctly.
    """
    ts_ms = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
    off_s = machine_offset(ts_ms) if off_s is None else int(off_s)
    zone = machine_zone() if zone is None else zone

    cur = con.execute(
        "SELECT off_s, zone FROM tz_marks WHERE ts <= ? "
        "ORDER BY ts DESC LIMIT 1", (ts_ms,)).fetchone()
    if cur and cur[0] == off_s and cur[1] == zone:
        return False
    with con:
        con.execute("INSERT OR REPLACE INTO tz_marks (ts, off_s, zone, src) "
                    "VALUES (?,?,?,?)", (ts_ms, off_s, zone, src))
    return True


def offset_at(hist, ts_ms: int) -> int:
    """The UTC offset (seconds) at that moment.

    Before every record, **the earliest** applies. It is the only choice that
    invents nothing: where you were before the first record is unknown, and "as
    when first seen" is at least a stated assumption. Only an empty table falls
    back to the machine — a new database, where "like today" is right.
    """
    if not hist:
        return machine_offset(ts_ms)
    off = hist[0][1]
    for row in hist:
        if row[0] > ts_ms:
            break
        off = row[1]
    return off


def zone_at(hist, ts_ms: int) -> str:
    if not hist:
        return machine_zone()
    zone = hist[0][2]
    for row in hist:
        if row[0] > ts_ms:
            break
        zone = row[2]
    return zone


# ── Cutting days along the time axis ──────────────────────────

def _date_of(t_ms: int, off_s: int) -> str:
    """Integer arithmetic, no float: epoch ms are past what a float holds exactly."""
    return (_EPOCH + dt.timedelta(
        days=(t_ms + off_s * 1000) // DAY_MS)).isoformat()


def _next_midnight(t_ms: int, off_s: int) -> int:
    local = t_ms + off_s * 1000
    return (local // DAY_MS + 1) * DAY_MS - off_s * 1000


def _next_change(hist, t_ms: int) -> int | None:
    for row in hist:
        if row[0] > t_ms:
            return row[0]
    return None


def day_starts(hist, t0_ms: int, t1_ms: int) -> list[tuple[str, int]]:
    """[(date, start ms), …] covering [t0, t1). **Days join end to end.**

    The algorithm walks UTC forward, each step jumping to the earlier of "the
    next local midnight" and "the next timezone change", and cuts whenever a new
    date label appears.

    A label already seen is never cut again — flying east, the local clock runs
    backwards (leave Shanghai at 16:00, land in California at 01:00 the same
    date), and without this guard one day would be cut in two with a piece in
    between from nowhere. With it, that day simply grows (39 hours), which is
    what happened.

    ⚠️ The **first item is usually half a day** — it starts at t0, which is
    rarely a midnight. For exact bounds use bounds(), which scans two days early.

    Crossing the date line, a date may not appear at all (the offset jumps by
    more than 24 hours). Not a bug: that day did not exist in the life lived.
    """
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    t = t0_ms
    while t < t1_ms:
        off = offset_at(hist, t)
        label = _date_of(t, off)
        if label not in seen:
            seen.add(label)
            out.append((label, t))
        nxt = _next_midnight(t, off)
        change = _next_change(hist, t)
        t = min(nxt, change) if change is not None else nxt
    return out


def naive_bounds(day: str) -> tuple[int, int]:
    """The old machine-timezone algorithm. Only a fallback for a date the scan
    never reaches (the date line), and for callers with no connection."""
    d = dt.datetime.strptime(day, "%Y-%m-%d")
    return (int(d.timestamp() * 1000),
            int((d + dt.timedelta(days=1)).timestamp() * 1000) - 1)


def bounds(con: sqlite3.Connection, day: str, hist=None) -> tuple[int, int]:
    """This day's [start, end] (end inclusive, as db.load_range expects).

    It scans two days early: day_starts' first item is half a day, and any
    offset is within ±14 hours, so two days ahead guarantees `day` itself gets
    a whole start.
    """
    hist = history(con) if hist is None else hist
    anchor = int(dt.datetime.strptime(day, "%Y-%m-%d")
                 .replace(tzinfo=dt.UTC).timestamp() * 1000)
    starts = day_starts(hist, anchor - 2 * DAY_MS, anchor + 3 * DAY_MS)
    for i, (label, start) in enumerate(starts):
        if label == day:
            end = starts[i + 1][1] if i + 1 < len(starts) else start + DAY_MS
            return start, end - 1
    return naive_bounds(day)


def day_minutes(con: sqlite3.Connection, day: str, hist=None) -> int:
    """How many minutes this day has. Usually 1440; 1380 / 1500 on daylight-
    saving days; 600 or 2340 when travelling.

    Every hard-coded 1440 should become this — that constant plays three roles
    at once (window tiling, bounds checks, the coverage denominator), and each
    needs a different fix.
    """
    lo, hi = bounds(con, day, hist)
    return (hi - lo + 1) // 60_000



def elapsed_minutes(con: sqlite3.Connection, day: str, now_ms: int | None = None,
                    hist=None) -> int:
    """How much of this day has happened by now: the whole day once it is over,
    0 before it starts, and for today the minutes since its midnight.

    Today is drawn up to now and nothing after, so this — not day_minutes — is
    the end of today's bar and the denominator of today's coverage.
    """
    lo, hi = bounds(con, day, hist)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return max(0, min((hi - lo + 1) // 60_000, (now - lo) // 60_000))


def abbr(zone: str, off_s: int, ts_ms: int) -> str:
    """"PDT", "CST" — what the clock on the wall was called. The offset is
    the record's, not the zone database's guess, so "+08" is said rather than
    a wrong name when the two disagree or the zone is unknown."""
    try:
        import zoneinfo
        t = dt.datetime.fromtimestamp(ts_ms / 1000, zoneinfo.ZoneInfo(zone))
        if int(t.utcoffset().total_seconds()) == off_s and t.tzname():
            return t.tzname()
    except (ValueError, KeyError, OSError):
        pass
    h, m = divmod(abs(off_s) // 60, 60)
    return f"{'+' if off_s >= 0 else '-'}{h:02d}" + (f":{m:02d}" if m else "")


def segments(con: sqlite3.Connection, day: str, hist=None) -> list[dict]:
    """The clocks this day was read on, from the minute each began.

    Usually one. Two on a daylight-saving day (PDT, then PST from 02:00) or a
    travel day. A change exactly at the day's end belongs to the next day — a
    zero-length segment is never returned.

    wall is the clock on the wall when the segment began, in minutes after
    midnight. Not always 0 for the first: the day after a flight east begins
    the moment the last one ended — 01:00 in Shanghai, the hour before never
    having happened to this person.
    """
    wall = lambda t, o: ((t + o * 1000) // 60_000) % 1440
    hist = history(con) if hist is None else hist
    lo, hi = bounds(con, day, hist)
    n = (hi - lo + 1) // 60_000
    off, zone = offset_at(hist, lo), zone_at(hist, lo)
    out = [{"at": 0, "wall": wall(lo, off), "zone": zone, "abbr": abbr(zone, off, lo), "off_s": off}]
    for t, o, z in hist:
        at = (t - lo) // 60_000
        if 0 < at < n and (o, z) != (out[-1]["off_s"], out[-1]["zone"]):
            out.append({"at": at, "wall": wall(lo + at * 60_000, o), "zone": z,
                        "abbr": abbr(z, o, t), "off_s": o})
    return out

# ── Travel days ───────────────────────────────────────────────

def changes(con: sqlite3.Connection, hist=None) -> list[int]:
    """The moments the **zone** changed. Daylight saving does not count — the
    test is whether the zone name changed.

    The first record is not a change: before it nothing is known, which is not
    "it changed".
    """
    hist = history(con) if hist is None else hist
    return [row[0] for prev, row in zip(hist, hist[1:]) if prev[2] != row[2]]


def is_travel_day(con: sqlite3.Connection, day: str, hist=None) -> bool:
    """Whether this day is left out of trends.

    **It affects aggregates, never records.** That day's events, lanes and
    inferences are all there and can be opened as usual — only cross-day
    comparisons ("hours of sleep on average") leave it out, because a ten-hour
    day and a thirty-nine-hour day do not belong in one column.
    """
    hist = history(con) if hist is None else hist
    marks = changes(con, hist)
    if not marks:
        return False
    lo, hi = bounds(con, day, hist)
    return any(lo - TRAVEL_HALO_MS <= m <= hi + TRAVEL_HALO_MS for m in marks)


def fp(con: sqlite3.Connection, day: str, hist=None) -> str:
    """The piece that goes into the page cache's fingerprint.

    Without it, nothing in the day's fingerprint changes after a timezone change
    (event count, max ts, row counts all the same), and the page would silently
    keep the old timezone's shape — the most dangerous way a cache fails.
    """
    hist = history(con) if hist is None else hist
    lo, hi = bounds(con, day, hist)
    # The zone goes in too: two zones with one offset cut the same bounds, but
    # the name shown on the page differs, and without it the old name would stay.
    return f"{lo}-{hi}-{zone_at(hist, lo)}-{int(is_travel_day(con, day, hist))}"


# ── Backfilling an old database ───────────────────────────────

def _bisect_change(lo_ms: int, hi_ms: int, off_lo: int) -> int:
    """Narrow an offset change down to the minute. About 17 calls.

    Scanning by day alone would already make **day bounds** exact (day_starts'
    seen-guard absorbs the error), but offset_at would be an hour off on the day
    of the change — and offset_at is what "when did I get up" rests on.
    """
    while hi_ms - lo_ms > 60_000:
        mid = (lo_ms + hi_ms) // 2
        if machine_offset(mid) == off_lo:
            lo_ms = mid
        else:
            hi_ms = mid
    return hi_ms


def seed(con: sqlite3.Connection, t0_ms: int, t1_ms: int) -> int:
    """Backfill tz_marks from the machine's daylight-saving history. Returns
    how many rows were written.

    **Only on an empty table.** migrate runs on every connect; without the gate
    a trip entered by hand (src="config") would be overwritten from the machine's
    zone each time.

    Why this backfill is right: before this existed nobody's trips were
    recorded, so **the machine's timezone history is the whole truth** — asking
    the OS "what was the offset that day" gives exactly the old
    analyze.day_bounds(), day by day (tests/test_tz.py pins a whole year).

    ⚠️ **Only up to now, no prediction.** Scanning a year ahead would place the
    next daylight-saving change early, but those future marks would sort
    **after** a real travel mark — fly abroad in October, and a November mark
    written from the home zone's rules would switch the timezone back home.
    Future changes are observed as they happen by record() on each sync.
    """
    if con.execute("SELECT 1 FROM tz_marks LIMIT 1").fetchone():
        return 0
    zone = machine_zone()
    prev = machine_offset(t0_ms)
    rows = [(t0_ms, prev, zone, "seed")]
    t = t0_ms
    while t < t1_ms:
        nxt = min(t + DAY_MS, t1_ms)
        off = machine_offset(nxt)
        if off != prev:
            rows.append((_bisect_change(t, nxt, prev), off, zone, "seed"))
            prev = off
        t = nxt
    with con:
        con.executemany("INSERT OR REPLACE INTO tz_marks "
                        "(ts, off_s, zone, src) VALUES (?,?,?,?)", rows)
    return len(rows)


# ── Trips entered by hand ─────────────────────────────────────

def apply_config(con: sqlite3.Connection, entries) -> int:
    """Write config.toml's [[travel]] entries into tz_marks. Returns how many
    rows were written.

        [[travel]]
        from = "2026-10-05T10:00"   # the clock **where you left from**
        tz   = "Asia/Shanghai"

    Why `from` is read in the departure zone: "leaving 10-05 at 10:00" is read
    off the airport's clock. Read in the destination's zone the mark moves by
    fifteen hours and the day's length comes out wrong — while looking normal.

    **All config rows are deleted and the whole list replayed**, so:
      · applying again piles up nothing (every sync applies it)
      · a multi-leg trip (US → China → US) resolves in order — the second leg's
        clock is the first leg's destination, already in history when it parses
      · deleting an entry from config removes its mark

    Observed marks (src="auto") are untouched: what the machine saw is a fact,
    what was entered is a supplement.
    """
    import zoneinfo

    with con:
        con.execute("DELETE FROM tz_marks WHERE src='config'")
    n = 0
    for e in sorted(entries or [], key=lambda x: str(x.get("from") or "")):
        raw, name = e.get("from"), e.get("tz")
        if not raw or not name:
            continue
        try:
            target = zoneinfo.ZoneInfo(str(name))
        except Exception as exc:                        # noqa: BLE001
            # A mistyped zone must raise. Skipped silently, the config change
            # does not take effect, and the symptom ("that day is still 24
            # hours") points nowhere near the cause.
            raise RuntimeError(
                f"[[travel]] has a timezone that is not recognised: {name!r} "
                f"({exc})") from exc
        try:
            naive = dt.datetime.fromisoformat(str(raw))
        except ValueError as exc:
            raise RuntimeError(
                f"[[travel]] has a `from` that cannot be read: {raw!r} "
                f"(expected something like 2026-10-05T10:00)") from exc

        as_utc = int(naive.replace(tzinfo=dt.UTC).timestamp() * 1000)
        ts = as_utc - offset_at(history(con), as_utc) * 1000
        off = target.utcoffset(naive)
        if record(con, ts, int(off.total_seconds()) if off else 0,
                  str(name), src="config"):
            n += 1
    return n
