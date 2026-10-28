"""The place table: coordinates -> "home / office / gym".

**Reverse geocoding does not solve this problem.** Hand a coordinate to
Google or OSM and it returns "1 Infinite Loop, Cupertino" or "Starbucks" — it
knows what is there, not what it means to you. Your home coordinate comes back
as a street address and never says "this is home"; your office building holds
twenty companies. And every lookup hands your location to a third party once
more.

So the model is **you label it once, and matching is automatic afterwards**:
naming the ten longest-dwelt points of a month covers 90% of the time. Reverse
geocoding is only there to help you recall an unnamed cluster (see
scripts/places.py), never the main mechanism.

⚠️ places.json lives next to the database and **not in the repo** — it contains
a home address. As sensitive as events.db and the generated page, for the same
reasons.
"""

import hashlib
import json
import math
import os
import tempfile

from . import db

PATH = os.path.join(db.DB_DIR, "places.json")

# Used when no radius is given. A hundred metres or so covers GPS jitter
# without swallowing the building next door.
DEFAULT_RADIUS_M = 120

# Grid precision for the anonymous id. Three decimal places is about 110 m — the
# same order as the default radius, so "the same place" maps to the same id
# across repeat visits.
_GRID = 3


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points. Used for both the radius test
    and clustering."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load(path: str = PATH) -> list[dict]:
    """Read the place table. A missing or malformed file both return empty.

    Not having labelled anything yet is a normal state, not a fault — location
    still imports; everything is simply counted as "out".
    """
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return rows if isinstance(rows, list) else []


def save(rows: list[dict], path: str = PATH) -> None:
    """Atomic write, mode 600. There is a home address in here."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def match(lat: float, lon: float, known: list[dict]) -> str | None:
    """Which named place this coordinate falls inside. None if no match.

    On overlap, take the **nearest** rather than the first defined: home and the
    entrance to the complex may both be drawn generously, and taking definition
    order yields conclusions like "spent 8 hours at the complex entrance".
    """
    best, best_d = None, None
    for p in known:
        try:
            d = meters_between(lat, lon, float(p["lat"]), float(p["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d <= float(p.get("radius") or DEFAULT_RADIUS_M):
            if best_d is None or d < best_d:
                best, best_d = p.get("name"), d
    return best


def rivals(lat: float, lon: float, known: list[dict],
           margin_m: float = 40.0) -> list[str]:
    """The **other** places close enough that which one is meant is unclear.

    match() picks the nearest, but "nearest" is not "certain". A real example:
    inside the gym's 120 m radius also sit a bubble tea shop (73 m) and a
    Starbucks (91 m). The centre of a stay carries tens of metres of error by
    itself — at that scale the "nearest" that comes out is a guess.

    Everywhere else Umbra is willing to say it does not know (a gap row would
    rather report "these 10 hours are unclear" than pretend). The names returned
    here are for downstream: the visualisation can show "gym?", analyze can drop
    a tier, and a WHOOP workout event can cross-check it — an actual workout
    means the gym, no workout probably means a coffee.

    margin_m means "how close the two distances have to be before they cannot be
    told apart". 40 m is roughly ten times the error in a stay's centre (measured
    accuracy median 7 m, averaging out to 3-5 m) — wide enough to absorb the
    occasional bad fix, narrow enough not to flag a clearly separable 91 m as
    ambiguous.
    """
    hits = []
    for p in known:
        try:
            d = meters_between(lat, lon, float(p["lat"]), float(p["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d <= float(p.get("radius") or DEFAULT_RADIUS_M):
            hits.append((d, p.get("name")))
    if not hits:
        return []
    hits.sort(key=lambda x: x[0])
    best = hits[0][0]              # hits[0] is exactly what match() would pick
    return [n for d, n in hits[1:] if d - best <= margin_m]


def anon_id(lat: float, lon: float) -> str:
    """A stable anonymous id for an unnamed place; no coordinates stored.

    This keeps "went to the same unnamed place three times" visible while
    keeping where you went out of the generated HTML. Raw coordinates stay in D1
    and places.json, both of which are far better protected than a file that
    opens on a double-click.
    """
    key = f"{round(lat, _GRID):.{_GRID}f},{round(lon, _GRID):.{_GRID}f}"
    return "p" + hashlib.sha256(key.encode()).hexdigest()[:9]
