#!/usr/bin/env python3
"""The place list: find where you go most, and name those places.

    python3 scripts/places.py list          # unnamed clusters, by total time spent
    python3 scripts/places.py add Home 37.3318 -122.0305 [radius]
    python3 scripts/places.py show          # the current place list
    python3 scripts/places.py geocode       # roughly where unnamed clusters are (to jog memory)
    python3 scripts/places.py find "1 Infinite Loop, Cupertino, CA"   # address → coordinates

Naming is a one-time job: the ten places with the most time in a month cover
90% of it. Everywhere else counts as "out" — Umbra needs to say "at work
9 to 12", not "at the third junction".

⚠️ The list is places.json in Umbra's data directory. It holds a home address:
   mode 600, never in the repository.
"""

import collections
import datetime as dt
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from umbra import gplaces, net, places  # noqa: E402
from umbra.importers import location  # noqa: E402

# Nominatim's terms require a User-Agent and one request a second. net.py already
# sets one (added to get past a CDN that blocks the default; needed here too).
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_DELAY_S = 1.1


def fetch_points(since_ms=0):
    token = os.environ.get("UMBRA_LOCATION_TOKEN")
    if not token:
        raise SystemExit("UMBRA_LOCATION_TOKEN is not set. Put it in your env file.")
    base = os.environ.get("UMBRA_LOCATION_URL", location.DEFAULT_URL).rstrip("/")
    data = net.get_json(f"{base}/export?since={since_ms}",
                        {"Authorization": f"Bearer {token}"})
    return data.get("points") or []


def clusters(points):
    """Cluster stays by position, with total time.

    "The same place" on different days jitters by tens of metres, so clusters
    merge by distance, not equal coordinates — which would split home into
    thirty different places.
    """
    known = places.load()
    stays = [r for r in location.to_rows(points, known) if r[4] == "stay"]

    groups = []          # [{lat, lon, ms, n, hours:Counter, name}]
    for _, ts, dur, _, _, payload in stays:
        # to_rows returns no coordinates (on purpose), so group by the payload's
        # place, then take a representative coordinate from the raw points
        key = payload["place"]
        g = next((x for x in groups if x["key"] == key), None)
        if g is None:
            g = {"key": key, "named": payload["named"], "ms": 0, "n": 0,
                 "hours": collections.Counter(), "first": ts, "last": ts,
                 "lat": None, "lon": None}
            groups.append(g)
        g["ms"] += dur
        g["n"] += 1
        g["last"] = max(g["last"], ts)
        h = dt.datetime.fromtimestamp(ts / 1000).hour
        g["hours"][h] += 1

    # A representative coordinate per cluster: the mean of the points in it
    for g in groups:
        pts = [p for p in points
               if (places.match(p["lat"], p["lon"], known) or
                   places.anon_id(p["lat"], p["lon"])) == g["key"]]
        if pts:
            g["lat"] = sum(p["lat"] for p in pts) / len(pts)
            g["lon"] = sum(p["lon"] for p in pts) / len(pts)
    groups.sort(key=lambda g: -g["ms"])
    return groups


def cmd_list():
    pts = fetch_points()
    if not pts:
        raise SystemExit("No points in the Worker yet. Let Overland run for a while.")
    gs = clusters(pts)
    print(f"{len(pts)} points → {len(gs)} stay clusters\n")
    print(f"{'total':>8}  {'n':>4}  {'usual hours':<14} {'coordinates':<26} name")
    print("-" * 78)
    for g in gs[:20]:
        hrs = sorted(g["hours"], key=lambda h: -g["hours"][h])[:3]
        span = ",".join(f"{h:02d}h" for h in sorted(hrs))
        coord = (f"{g['lat']:.5f},{g['lon']:.5f}" if g["lat"] else "?")
        name = g["key"] if g["named"] else "(unnamed)"
        print(f"{g['ms']/3600000:>7.1f}h  {g['n']:>4}  {span:<14} {coord:<26} {name}")
    print("\nTo name a place:")
    print("  python3 scripts/places.py add Home <lat> <lon>")


def cmd_add(argv):
    if len(argv) < 3:
        raise SystemExit("usage: add <name> <lat> <lon> [radius in metres]")
    name, lat, lon = argv[0], float(argv[1]), float(argv[2])
    radius = float(argv[3]) if len(argv) > 3 else places.DEFAULT_RADIUS_M
    rows = places.load()
    rows = [r for r in rows if r.get("name") != name]     # the same name replaces
    rows.append({"name": name, "lat": lat, "lon": lon, "radius": radius})
    places.save(rows)
    print(f"✓ {name}  {lat},{lon}  radius {radius:.0f}m")
    print(f"  {len(rows)} places → {places.PATH}")


def cmd_show():
    rows = places.load()
    if not rows:
        print("The place list is empty. Run list first to see where you go.")
        return
    for r in rows:
        print(f"  {r['name']:<14} {r['lat']:.5f},{r['lon']:.5f}  "
              f"radius {r.get('radius', places.DEFAULT_RADIUS_M):.0f}m")
    print(f"\n{places.PATH}")


def reverse_geocode(lat, lon):
    """Coordinates → what is near. **Only to jog your memory**, never to name
    places automatically.

    It knows what is there, not what it means to you — your home comes back as a
    street address, never as "home". So only unnamed clusters are looked up; home
    and work never are (they are the most sensitive, and you recognise them).
    """
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "json",
                                "zoom": 18})
    try:
        d = net.get_json(f"{NOMINATIM}?{q}")
    except Exception as e:                              # noqa: BLE001
        return f"(lookup failed: {type(e).__name__})"
    a = d.get("address") or {}
    bits = [a.get(k) for k in ("amenity", "shop", "building", "road",
                               "neighbourhood", "suburb")]
    return ", ".join(b for b in bits if b) or d.get("display_name", "?")[:60]


def describe(lat, lon):
    """What is near these coordinates. Google Places first; Nominatim if Google
    is not configured.

    The difference is types: Google says "Hotel", "Gym", "Coffee Shop", while
    Nominatim mostly gives a street address in the US. To tell what you were
    doing there, only the former helps.
    """
    hits = gplaces.nearby(lat, lon, radius=120, limit=4)
    if hits:
        return "  |  ".join(f"{h['name']}（{h['type']}）" for h in hits)
    time.sleep(NOMINATIM_DELAY_S)          # Nominatim allows one request a second
    return reverse_geocode(lat, lon) + "  [Nominatim fallback]"


def cmd_geocode():
    pts = fetch_points()
    gs = [g for g in clusters(pts) if not g["named"] and g["lat"]]
    if not gs:
        print("No unnamed clusters.")
        return
    src = "Google Places" if gplaces.google_auth.credentials() else "Nominatim"
    print(f"Unnamed clusters only — home and work are never sent to a third party. (using {src})\n")
    for g in gs[:10]:
        print(f"  {g['ms']/3600000:>6.1f}h  {g['lat']:.5f},{g['lon']:.5f}")
        print(f"          ↳ {describe(g['lat'], g['lon'])}")


def cmd_find(argv):
    """Address or business name → coordinates.

    ⚠️ This sends what you type to Google. Running the command is your consent —
    but be clear it is not the same as Google inferring your home from iOS. That
    is buried in behavioural data; this goes into Cloud Logging with your account
    and a timestamp, exportable and searchable.
    """
    if not argv:
        raise SystemExit('usage: find "1 Infinite Loop, Cupertino, CA"')
    q = " ".join(argv)
    if not gplaces.google_auth.credentials():
        raise SystemExit(f"Google credentials are not configured. Set {gplaces.google_auth.CREDS_ENV} "
                         "to a service account JSON file.")
    hit = gplaces.search(q)
    if not hit:
        raise SystemExit(f"nothing found for \"{q}\"")
    print(f"  {hit['lat']:.6f}, {hit['lon']:.6f}")
    print(f"    {hit['name']}")
    print(f"    {hit['address']}")
    print("\nIf it looks right, add it to the place list:")
    print(f"  python3 scripts/places.py add <name> {hit['lat']:.6f} {hit['lon']:.6f}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        cmd_list()
    elif cmd == "add":
        cmd_add(sys.argv[2:])
    elif cmd == "show":
        cmd_show()
    elif cmd == "geocode":
        cmd_geocode()
    elif cmd == "find":
        cmd_find(sys.argv[2:])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
