"""Google Places (New) — coordinates <-> places.

**Places only, never the Geocoding API.** The latter lives on
maps.googleapis.com and is a 2010s-era web service that recognises only an API
key in the URL; it was never wired into Google Cloud's IAM, and calling it with a
service account returns REQUEST_DENIED. Places lives on places.googleapis.com,
is a standard Cloud API, and works with a service account directly.
(Rule of thumb: to tell whether a Google API accepts a service account, look at
the domain first.)

Places also covers both directions in one API, which makes Geocoding redundant:

    coordinate -> place   searchNearby  also returns formattedAddress, i.e.
                                        reverse geocoding
    place -> coordinate   searchText

What it genuinely has over the free Nominatim is **types**: it returns
gym / cafe / hotel / parking_garage, which is activity semantics directly.
In the US, Nominatim can only give a street address, which says nothing
about what you were doing; "Hotel" does.

⚠️ **Every call hands one coordinate to a third party.** This module makes no
   judgement about that; the caller decides — scripts/places.py only calls it
   for unnamed clusters, so home and the office are never sent.
"""

from . import google_auth, net

NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
TEXT_URL = "https://places.googleapis.com/v1/places:searchText"

# The FieldMask decides both the billing tier and the response size. Ask only
# for what is used — anything requested and unused is money for nothing, and
# fields like photos/reviews inflate the response many times over.
NEARBY_FIELDS = ("places.displayName,places.formattedAddress,"
                 "places.primaryTypeDisplayName,places.types")
TEXT_FIELDS = ("places.displayName,places.formattedAddress,places.location")

DEFAULT_RADIUS_M = 150.0
DEFAULT_LIMIT = 5


def _auth() -> tuple[str, str] | None:
    """(access_token, project_id). Returns None when Google is not configured.

    Unconfigured is a normal state ("not connected yet"), not a fault — location
    imports as usual, the clusters simply stay unnamed. It must never abort the
    whole import.
    """
    sa = google_auth.credentials()
    if not sa:
        return None
    token = google_auth.ensure_access_token(sa=sa)
    if not token:
        return None
    return token, sa.get("project_id") or ""


def _headers(token: str, project: str, mask: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            # Places uses standard Cloud auth; without this header it 403s —
            # it names who gets billed
            "X-Goog-User-Project": project,
            "X-Goog-FieldMask": mask}


def _name(p: dict) -> str:
    return (p.get("displayName") or {}).get("text") or "?"


def nearby(lat: float, lon: float, radius: float = DEFAULT_RADIUS_M,
           limit: int = DEFAULT_LIMIT) -> list[dict]:
    """What is around this coordinate. Empty list when nothing is found or
    Google is not configured."""
    auth = _auth()
    if auth is None:
        return []
    token, project = auth
    body = {"maxResultCount": limit,
            "locationRestriction": {"circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": radius}}}
    try:
        data = net.post_json(NEARBY_URL, body,
                             _headers(token, project, NEARBY_FIELDS))
    except Exception:                                   # noqa: BLE001
        # Location is one source among many; Google having a bad day must not
        # take the whole import down
        return []
    return [{"name": _name(p),
             "type": (p.get("primaryTypeDisplayName") or {}).get("text") or "?",
             "types": p.get("types") or [],
             "address": p.get("formattedAddress") or ""}
            for p in (data.get("places") or [])]


def search(text: str) -> dict | None:
    """A name or address -> a coordinate. None when not found or not configured.

    ⚠️ This sends the query string to Google. Looking up your own home address
    with it hands that address over in structured form — which is not the same as
    Google inferring it from iOS, where it is buried in behavioural data. This
    goes into Cloud Logging attached to your account. The caller weighs that.
    """
    auth = _auth()
    if auth is None:
        return None
    token, project = auth
    try:
        data = net.post_json(TEXT_URL, {"textQuery": text, "maxResultCount": 1},
                             _headers(token, project, TEXT_FIELDS))
    except Exception:                                   # noqa: BLE001
        return None
    hits = data.get("places") or []
    if not hits:
        return None
    p = hits[0]
    loc = p.get("location") or {}
    if "latitude" not in loc or "longitude" not in loc:
        return None
    return {"lat": loc["latitude"], "lon": loc["longitude"],
            "name": _name(p), "address": p.get("formattedAddress") or ""}
