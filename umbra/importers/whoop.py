"""WHOOP physiology importer.

Source: https://api.prod.whoop.com/developer/v2/* (OAuth bearer, refreshed automatically)

Why it belongs here: the other sources all describe what you did to a machine;
none knows **what state you were in**. "Four hours of debugging with no
progress" after five hours of sleep and after eight are different things that
look identical in an event stream.

All four kinds are stored as point events (sleep and workout with dur_ms), and
intervals are derived at analysis time as usual — sharing one timeline with the
other sources is the reason this is part of Umbra rather than a project of its own.

Environment: WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET (in your env file)
First authorisation: umbra auth whoop
"""

import datetime as dt
import os
import urllib.parse

from .. import net, whoop_auth

SOURCE = "whoop"
# Something every normal day: this many days with nothing new means it has
# stopped, and STALE_HINT is where to look first (plugins/staleness.py).
STALE_DAYS = 2
STALE_HINT = ('Run `umbra import --only whoop`: an expired sign-in shows as 403 or '
              'invalid_grant, and `umbra auth whoop` renews it.')
REQUIRES = ["env:WHOOP_CLIENT_ID", "env:WHOOP_CLIENT_SECRET"]
BASE = "https://api.prod.whoop.com/developer/v2"
PAGE = 25                # the v2 collection endpoints' maximum; more is truncated silently
MAX_PAGES = 400          # a backstop against a cursor bug paging forever


def check():
    """REQUIRES, plus the token the one-time authorisation leaves behind.

    Without that token the source is not set up, which is not the same as
    expired: someone who never authorised has nothing to fix, while a dead
    refresh token is a hard failure (whoop_auth.WhoopAuthExpired)."""
    for var in ("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET"):
        ok = bool(os.environ.get(var))
        yield (f"env:{var}", ok,
               "" if ok else f"env var {var} is not set (usually lives in your env file)")
    ok = os.path.exists(whoop_auth.TOKEN_PATH)
    yield ("whoop token", ok, "" if ok else "not authorized yet — run: umbra auth whoop")


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(dt.datetime.fromisoformat(
            iso.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, AttributeError):
        return None


def _dur(start: str | None, end: str | None) -> int | None:
    """Duration. An empty end is normal — the current sleep or cycle is not over."""
    a, b = _ts(start), _ts(end)
    return b - a if a is not None and b is not None else None


def _iso(ms: int | None) -> str | None:
    """Milliseconds -> the ISO 8601 UTC WHOOP wants. The shared loop passes
    milliseconds, like events.ts."""
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _fetch(path: str, token: str, since: str | None) -> list[dict]:
    """Every page of one collection endpoint."""
    out, cursor, pages = [], None, 0
    while pages < MAX_PAGES:
        params = {"limit": str(PAGE)}
        # Cursor first: once paging has started, start must not take part, or
        # it pulls back pages already read.
        if cursor:
            params["nextToken"] = cursor
        elif since:
            params["start"] = since
        data = net.get_json(f"{BASE}{path}?{urllib.parse.urlencode(params)}",
                            {"Authorization": f"Bearer {token}"})
        pages += 1
        out.extend(data.get("records") or [])
        # The request parameter is nextToken and the response field is
        # next_token — WHOOP is not symmetric. Read both; do not trust the
        # documentation and the implementation to agree.
        cursor = data.get("next_token") or data.get("nextToken")
        if not cursor:
            break
    return out


def _score(rec: dict) -> dict:
    """score can be null: the record is PENDING_SCORE or UNSCORABLE.

    rec["score"]["x"] raises TypeError on those, and they cluster in the last
    24 hours — exactly the records each import is most likely to meet.
    """
    return rec.get("score") or {}


def collect(since_ms: int | None = None) -> list[tuple]:
    """Fetch the four kinds of record.

    WhoopAuthExpired is not swallowed here: an expired authorisation propagates
    to cmd_import and stops the whole import (a hard failure chosen on
    2026-08-15). Network errors propagate too, into each source's own fault
    isolation.

    since_ms is the most valuable parameter in this source: a full fetch is about
    180 requests and hits the 100-a-minute rate limit twice; an incremental one
    takes a handful.
    """
    token = whoop_auth.ensure_access_token()
    since = _iso(since_ms)
    rows: list[tuple] = []

    for rec in _fetch("/activity/sleep", token, since):
        ts = _ts(rec.get("start"))
        if ts is None:
            continue
        s = _score(rec)
        stage = s.get("stage_summary") or {}
        rows.append((
            f"whoop:sleep:{rec['id']}", ts, _dur(rec.get("start"), rec.get("end")),
            SOURCE, "sleep",
            {"nap": bool(rec.get("nap")),
             "performance_pct": s.get("sleep_performance_percentage"),
             "efficiency_pct": s.get("sleep_efficiency_percentage"),
             "consistency_pct": s.get("sleep_consistency_percentage"),
             "respiratory_rate": s.get("respiratory_rate"),
             "deep_ms": stage.get("total_slow_wave_sleep_time_milli"),
             "rem_ms": stage.get("total_rem_sleep_time_milli"),
             "awake_ms": stage.get("total_awake_time_milli"),
             "disturbances": stage.get("disturbance_count")},
        ))

    for rec in _fetch("/recovery", token, since):
        # Recovery has no id and no start of its own — it hangs off a cycle, and
        # its time is created_at. Copying another endpoint's field names loses
        # the whole type.
        ts = _ts(rec.get("created_at"))
        if ts is None or rec.get("cycle_id") is None:
            continue
        s = _score(rec)
        rows.append((
            f"whoop:recovery:{rec['cycle_id']}", ts, None, SOURCE, "recovery",
            {"score": s.get("recovery_score"),
             "resting_hr": s.get("resting_heart_rate"),
             "hrv_ms": s.get("hrv_rmssd_milli"),
             "spo2": s.get("spo2_percentage"),
             "skin_temp_c": s.get("skin_temp_celsius"),
             "calibrating": bool(s.get("user_calibrating"))},
        ))

    for rec in _fetch("/cycle", token, since):
        ts = _ts(rec.get("start"))
        if ts is None:
            continue
        s = _score(rec)
        rows.append((
            f"whoop:cycle:{rec['id']}", ts, _dur(rec.get("start"), rec.get("end")),
            SOURCE, "cycle",
            {"strain": s.get("strain"),
             "kilojoule": s.get("kilojoule"),
             "avg_hr": s.get("average_heart_rate"),
             "max_hr": s.get("max_heart_rate")},
        ))

    for rec in _fetch("/activity/workout", token, since):
        ts = _ts(rec.get("start"))
        if ts is None:
            continue
        s = _score(rec)
        rows.append((
            f"whoop:workout:{rec['id']}", ts, _dur(rec.get("start"), rec.get("end")),
            SOURCE, "workout",
            {"sport": rec.get("sport_name") or "",
             "strain": s.get("strain"),
             "avg_hr": s.get("average_heart_rate"),
             "max_hr": s.get("max_heart_rate"),
             "kilojoule": s.get("kilojoule")},
        ))

    return rows


# ── How this source appears downstream ────────────────────────

# Two lanes from one source: sleep and workouts are entirely different time
# semantics, and drawing them in one band would render "lay down for 8 hours" and
# "trained for 40 minutes" as the same thing.
LANES = [
    {"key": "sleep", "kind": "sleep",
     "origin": ("WHOOP band", "api.prod.whoop.com · v2 sleep")},
    {"key": "workout", "kind": "workout",
     "origin": ("WHOOP band", "api.prod.whoop.com · v2 workout")},
]


def render(e) -> str | None:
    """recovery and cycle are **daily** figures, not moments.

    Rendering them invented a position they do not have: a recovery score once
    became a "workout" span out of nowhere, and the model believed it.
    """
    from .. import render as R
    k = e.get("kind")
    if k == "sleep":
        return f"⌚ WHOOP: asleep{R.span(e)}"
    if k == "workout":
        return (f"⌚ WHOOP: workout {e.get('sport') or ''}{R.span(e)}"
                .replace("  (", " ("))
    return None


def classify(e) -> str | None:
    k = e.get("kind")
    return k if k in ("sleep", "workout") else None
