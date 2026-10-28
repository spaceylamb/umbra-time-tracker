"""Location importer — "at home / driving from home to work / at work".

Pipeline: Overland on the iPhone ──POST──> your Worker ──> your database
          Umbra ──GET /export?since=──> here

One structural difference from the other sources: **stays and trips partition
the whole day**. Every minute you are either somewhere or on the way between
two places, with no gaps. The others all sample — present only when the browser
is open, the Mac is awake, something is playing. So location may raise coverage
more than any other source; the only condition is that the phone is with you.

⚠️ **Raw coordinates are not stored.** A named place is stored by name, an
unnamed one by an anonymous id. The page embeds the payload in full, and where
you have been does not belong there. Raw coordinates stay in your database and
places.json, which are far better protected than a page that opens on a double-click.

Environment: UMBRA_LOCATION_TOKEN (in your env file)
"""

import os

from .. import net, places

SOURCE = "location"
# Something every normal day: this many days with nothing new means it has
# stopped, and STALE_HINT is where to look first (plugins/staleness.py).
STALE_DAYS = 2
STALE_HINT = ('The phone app that sends your location (Overland): is tracking on, is '
              'anything waiting in its upload queue, is location permission still '
              '"Always", and does it still post to your receiver (UMBRA_LOCATION_URL)? '
              'It queues what it cannot send and catches up once it can.')
REQUIRES = ["env:UMBRA_LOCATION_TOKEN", "env:UMBRA_LOCATION_URL"]

# This source **folds raw records into intervals**, so changing the algorithm changes
# the rows the same raw data produces. Import deletes the old rows for the span
# before writing — see db.replace_range and tests/test_resegment.py.
SEGMENTED = True

# No public default: each person deploys their own Worker (see docs/sources.md).
DEFAULT_URL = ""

# Stay detection. Set by common sense first; tune once there is real data —
# tuning now would be guessing.
STAY_RADIUS_M = 150        # GPS jitter plus Overland's 100 m accuracy setting
MIN_STAY_MS = 5 * 60_000   # shorter is passing through (a red light, traffic)

# A gap longer than this gets its own decision: "didn't move" or "data lost".
#
# "No points" should first be read as **didn't move** — Overland is designed
# not to send while stationary (Min Distance). Quiet is it working, not failing.
#
# But it can also be a flat battery or a killed app, and then you could be
# anywhere. The test is at **both ends of the gap**: still in the same place
# when it ends → you were there all along; 50 km away → genuinely unknown.
MAX_GAP_MS = 90 * 60_000

# The longest silence in one place that can be read as a stay. Beyond it, it
# is not credible — a phone off all day and a day at home look identical, and
# the former is more common. 16 hours = a night's sleep
# plus a morning at home; longer looks like the phone, not your routine.
MAX_INFERRED_STAY_MS = 16 * 3600_000

# Accuracy thresholds. Real data (2026-08-16, 39 points): median 7 m, 24 of 39
# within 10 m, with a tail of 89/95/109/1633 m. The old limit of 500 did
# nothing — 100 m-scale junk all got in.
MAX_ACCURACY_M = 250        # worse than this is discarded
TRUST_ACCURACY_M = 100      # only points this good can claim "it's somewhere else"

# Assumed when accuracy is missing or 0. Real data does contain 0 — that means
# "unknown", not "infinitely accurate". Treat it as middling; it must never
# get overwhelming weight.
ASSUMED_ACCURACY_M = 50

# The floor on accuracy in weighting. Consumer GPS is not sub-metre; a claim
# better than this is a data error, not a miracle. Uncapped, a bad point
# claiming 0.001 m outweighs a 5 m point twenty-five million times and alone
# decides the centre of the whole stay.
MIN_ACCURACY_M = 5.0


# Speed ceilings per mode of travel, to work back the latest possible departure.
# Walking 500 m must not be costed at 144 km/h as 12 seconds — that would
# stretch the stay almost to the next point, counting time that never happened.
MAX_SPEED_MPS = {
    "walking": 2.5,      # 9 km/h, a brisk walk
    "running": 6.0,      # 21.6 km/h
    "cycling": 12.0,     # 43 km/h
    "automotive": 40.0,  # 144 km/h
    "driving": 40.0,
}
DEFAULT_MAX_SPEED_MPS = 40.0

MOVING = tuple(MAX_SPEED_MPS)


def _in_motion(p: dict) -> bool:
    """Is this point "moving" or "already stopped"?

    The difference matters: moving means you **just** left, so travel time
    works back to a departure; stopped means you may have arrived hours ago,
    and nothing follows.
    """
    return any(m.strip() in MOVING for m in (p.get("motion") or "").split(","))


def _min_travel_ms(lat: float, lon: float, p: dict) -> int:
    """The least time it takes to get from (lat, lon) to p.

    **The core of the upper-bound method**: a speed ceiling gives the shortest
    possible trip, which works back to the latest possible departure. The
    physics is hard — you cannot drive faster than this, so you cannot have left
    later. The error therefore has a known direction: it can overstate a stay,
    never understate it.

    Straight-line distance is used (the real route is longer → the real trip is
    longer → the real departure is earlier), so that direction is safe too.
    """
    dist = places.meters_between(lat, lon, p["lat"], p["lon"])
    speeds = [MAX_SPEED_MPS[m.strip()] for m in (p.get("motion") or "").split(",")
              if m.strip() in MAX_SPEED_MPS]
    return int(dist / (max(speeds) if speeds else DEFAULT_MAX_SPEED_MPS) * 1000)


def _mode(pts: list[dict]) -> str:
    """How this stretch was travelled.

    motion comes from the iPhone's motion coprocessor (the one Health uses to
    tell walking from running from cycling) — **it is not guessed from speed**.
    The most frequent non-stationary mode in the stretch wins.
    """
    counts: dict[str, int] = {}
    for p in pts:
        for m in (p.get("motion") or "").split(","):
            m = m.strip()
            if m and m != "stationary":
                counts[m] = counts.get(m, 0) + 1
    if not counts:
        return "unknown"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _label(lat: float, lon: float, known: list[dict]) -> tuple[str, bool]:
    name = places.match(lat, lon, known)
    return (name, True) if name else (places.anon_id(lat, lon), False)


def _accuracy(p: dict) -> float:
    """How accurate this point claims to be. Missing, 0 or negative all mean
    "unknown", never "perfect"."""
    try:
        a = float(p.get("accuracy") or ASSUMED_ACCURACY_M)
    except (TypeError, ValueError):
        a = ASSUMED_ACCURACY_M
    return max(a, MIN_ACCURACY_M)


def _centre(pts: list[dict]) -> tuple[float, float]:
    """The centre of a stay — **weighted by accuracy**, not a plain mean.

    Weight ∝ 1/accuracy² (inverse-variance weighting). Not an aesthetic choice:
    GPS accuracy is a standard deviation, and inverse variance is exactly the
    minimum-variance unbiased weight. A 5 m point weighs ten thousand times a
    500 m one.

    The cost of a plain mean was measured: one 109 m point among five 7 m ones
    dragged the centre about 20 metres — the difference between the gym and the
    coffee shop next door.
    """
    ws = [1.0 / _accuracy(p) ** 2 for p in pts]
    total = sum(ws)
    return (sum(p["lat"] * w for p, w in zip(pts, ws)) / total,
            sum(p["lon"] * w for p, w in zip(pts, ws)) / total)


# The speed above which a point is a "teleport". Faster than any airliner, so
# no real trip is caught, and the three-point test below already rules out
# sustained high speed.
MAX_JUMP_SPEED_MPS = 100.0      # 360 km/h


def _speed(a: dict, b: dict) -> float:
    secs = (b["ts"] - a["ts"]) / 1000
    if secs <= 0:
        return 0.0
    return places.meters_between(a["lat"], a["lon"], b["lat"], b["lon"]) / secs


def usable(points: list[dict]) -> list[dict]:
    """Sort by time; drop points that are too inaccurate or that **teleport**.

    "How accurate it claims to be" and "whether it is right" are different. A
    real case (2026-08-16 13:19): a point claiming 64 m jumped 1.3 km away while
    the points a second either side stayed put. The accuracy threshold let it
    through, it split the cluster, and a six-minute stay became two one-second
    stays, both shorter than MIN_STAY_MS — the whole stay vanished.

    The test looks at **three points**: if removing the middle one joins the
    neighbours up, the middle one is bad. Looking only backwards blames the
    wrong point — from the previous point to it was a normal 3.8 m/s; what is
    impossible is from it to the next.

    This cannot catch a flight: every leg of it is fast, removing any one point
    still leaves prev→next too fast, so the condition is never met.
    """
    pts = [p for p in sorted(points, key=lambda p: p["ts"])
           if (p.get("accuracy") or 0) <= MAX_ACCURACY_M]
    out: list[dict] = []
    for i, p in enumerate(pts):
        prev = out[-1] if out else None
        nxt = pts[i + 1] if i + 1 < len(pts) else None
        if prev is not None and nxt is not None \
                and (_speed(prev, p) > MAX_JUMP_SPEED_MPS
                     or _speed(p, nxt) > MAX_JUMP_SPEED_MPS) \
                and _speed(prev, nxt) <= MAX_JUMP_SPEED_MPS:
            continue                    # without it the neighbours join up — it is bad
        out.append(p)
    return out


def to_rows(points: list[dict], known: list[dict],
            since_ms: int | None = None) -> list[tuple]:
    """Point stream -> stay / transit / gap intervals."""
    pts = usable(points)
    if not pts:
        return []

    # 1. Split into "stay clusters" and "moving points". A cluster is consecutive
    #    points within the radius.
    segments, cur = [], [pts[0]]
    for p in pts[1:]:
        lat, lon = _centre(cur)
        # "Left the radius" only counts when the point itself is accurate. A
        # 200 m point showing 300 m off says nothing — that is its error. Let it
        # split the cluster and a stay at home becomes two, with a transit
        # conjured between them.
        moved = places.meters_between(lat, lon, p["lat"], p["lon"]) > STAY_RADIUS_M
        silence = p["ts"] - cur[-1]["ts"]

        if silence > MAX_GAP_MS:
            # "Still in place" holds two ways: didn't move (within 150 m), or moved
            # a few hundred metres but **stayed inside one named place**. A beach,
            # a campus, a mall are meant to be walked around — the place's own
            # radius is its real scale. Without this, an afternoon walking a beach
            # end to end — two points 700 m apart, both inside its 600 m radius —
            # would count as "changed place".
            here = places.match(lat, lon, known)
            same = here is not None and here == places.match(p["lat"], p["lon"], known)
            if (not moved or same) and silence <= MAX_INFERRED_STAY_MS:
                cur.append(p)
                continue
            # The gap ends with you **moving** → you left partway through; you
            # were not "somewhere unknown". Add the latest possible departure to
            # close the stay; only the rest is travel. Without it, the real stay
            # inside the silence is thrown away with it — say, a whole night at
            # home discarded because the gap ended in a drive.
            depart = p["ts"] - _min_travel_ms(lat, lon, p)
            if (_in_motion(p) and silence <= MAX_INFERRED_STAY_MS
                    and depart > cur[-1]["ts"]):
                cur.append({"lat": lat, "lon": lon, "ts": depart,
                            "accuracy": ASSUMED_ACCURACY_M, "estimated": True})
                segments.append(("cluster", cur))
                cur = [p]
                continue
            # Already stopped somewhere else (arrival time unknown), or silent so
            # long it looks more like the phone was off.
            segments.append(("cluster", cur))
            segments.append(("gap", [cur[-1], p]))
            cur = [p]
            continue

        if moved and _accuracy(p) <= TRUST_ACCURACY_M:
            segments.append(("cluster", cur))
            cur = [p]
        else:
            cur.append(p)
    segments.append(("cluster", cur))

    # 2. Clusters long enough are stays; the remaining points are on the way,
    #    and become transit.
    #
    # transit must **meet the neighbouring stays end to end**, not just cover the
    # moving points — otherwise a hole opens between "the stay ended" and the
    # first moving point, and "stays + trips = the whole day" is this source's
    # core property.
    rows, pending, prev = [], [], None      # prev = (end time, place name)
    for kind, group in segments:
        if kind == "gap":
            rows.append(_gap_row(group))
            pending, prev = [], None
            continue
        span = group[-1]["ts"] - group[0]["ts"]
        if span >= MIN_STAY_MS:
            lat, lon = _centre(group)
            place, named = _label(lat, lon, known)
            if prev is not None and (pending or group[0]["ts"] > prev[0]):
                rows.append(_transit_row(pending, prev[0], group[0]["ts"],
                                         prev[1], place))
            # Did this stretch fill a silence by reading "no points" as "didn't
            # move"? Inferred and seen are not the same, and what comes downstream
            # (coverage, the page) is entitled to know the difference.
            inferred = any(b["ts"] - a["ts"] > MAX_GAP_MS
                           for a, b in zip(group, group[1:]))
            rows.append((
                f"loc:stay:{group[0]['ts']}", group[0]["ts"], span, SOURCE, "stay",
                {"place": place, "named": named,
                 # Only points actually seen. An inferred departure is not an observation.
                 "points": sum(1 for q in group if not q.get("estimated")),
                 "inferred": inferred},
            ))
            pending, prev = [], (group[-1]["ts"], place)
        else:
            pending += group

    rows.sort(key=lambda r: r[1])
    rows = _merge_round_trips(rows)
    if since_ms is not None:
        rows = [r for r in rows if r[1] >= since_ms]
    return rows


def _merge_round_trips(rows: list[tuple]) -> list[tuple]:
    """Fold "stay(P) + P→P + stay(P)" back into one stay.

    Walking around inside a big place produces this sandwich: 500 m from the
    car park to the shore splits the cluster in two, with a "trip" in the middle
    whose start and end are the same. The numbers are right, but "beach → beach"
    says nothing, and the lane looks as if you left and came back.

    Merged only when **both ends are the same place**. Between different places
    it is a real trip, and the ends are information (home → work = commute).
    """
    out: list[tuple] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        # Look for the sandwich stay / transit (same place) / stay.
        # Only whether the two stays are the same place is checked. transit's from
        # == to need not be: those labels are the two stays' names, the same
        # condition written twice.
        if (i + 2 < len(rows) and r[4] == "stay"
                and rows[i + 1][4] == "transit"
                and rows[i + 2][4] == "stay"
                and r[5]["place"] == rows[i + 2][5]["place"]):
            tail = rows[i + 2]
            merged = dict(r[5])
            # points is "how many I actually saw"; merging must not lose evidence
            merged["points"] = r[5]["points"] + tail[5]["points"]
            merged["inferred"] = bool(r[5].get("inferred")
                                      or tail[5].get("inferred"))
            rows[i + 2] = (r[0], r[1], tail[1] + tail[2] - r[1],
                           r[3], "stay", merged)
            i += 2                                  # skip the transit and keep going
            continue
        out.append(r)
        i += 1
    return out


def _transit_row(pts: list[dict], start: int, end: int,
                 src: str, dst: str) -> tuple:
    """Between two stays = on the way.

    The interval is **from the end of one stay to the start of the next**, not
    from the first moving point to the last — that leaves a hole at each end.
    Both ends are known, so the stretch carries meaning: home → work is a commute.
    """
    return (
        f"loc:transit:{start}", start, max(end - start, 60_000), SOURCE, "transit",
        {"from": src, "to": dst, "mode": _mode(pts), "points": len(pts)},
    )


def _gap_row(pair: list[dict]) -> tuple:
    """The data stopped. Where you were is unknown — say so; don't pretend."""
    start = pair[0]["ts"]
    return (
        f"loc:gap:{start}", start, pair[1]["ts"] - start, SOURCE, "gap",
        {"reason": "no points"},
    )


def collect(since_ms: int | None = None) -> list[tuple]:
    """Fetch points from the Worker and turn them into intervals.

    No token returns empty — that is "location not set up yet", not a fault, and
    must not stop the import.
    """
    token = os.environ.get("UMBRA_LOCATION_TOKEN")
    if not token:
        return []
    base = os.environ.get("UMBRA_LOCATION_URL", DEFAULT_URL).rstrip("/")
    if not base:
        raise RuntimeError("UMBRA_LOCATION_URL is not set (the address of your own location relay)")
    try:
        data = net.get_json(f"{base}/export?since={since_ms or 0}",
                            {"Authorization": f"Bearer {token}"})
    except Exception:                                   # noqa: BLE001
        raise
    return to_rows(data.get("points") or [], places.load(), since_ms)


# ── How this source appears downstream ────────────────────────

# Two lanes: being somewhere and moving between somewheres are different kinds
# of time, and only one of them says what you were doing.
LANES = [
    {"key": "place", "kind": "stay",
     "origin": ("iPhone GPS", "Overland → Cloudflare Worker → D1")},
    {"key": "transit", "kind": "transit",
     "origin": ("iPhone GPS",
                "between two stays; mode from the motion coprocessor")},
]


def render(e) -> str | None:
    """A gap renders as nothing: it means the data stopped, so the person could
    be anywhere. Rendered, it once produced "at ?" — which reads as a place
    called ?."""
    from .. import render as R
    k = e.get("kind")
    if k == "transit":
        return (f"📍 in transit: {e.get('from') or '?'} → {e.get('to') or '?'}"
                f" {e.get('mode') or ''}{R.span(e)}").rstrip()
    if k == "stay":
        return f"📍 at: {e.get('place') or '?'}{R.span(e)}"
    return None


def classify(e) -> str | None:
    """transit is content level — "driving from home to the office" is what you
    were doing. stay is container level: we know you were home, not what you
    were doing there."""
    k = e.get("kind")
    if k == "transit":
        return "transit"
    return "place" if k == "stay" else None
