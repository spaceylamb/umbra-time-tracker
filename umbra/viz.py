"""The page's data layer: each day's payload (resident and on-demand), cached
per day.
The page itself is the app in umbra/web/ — `umbra serve` reads this live, and
`write()` / umbra/export.py export it with these payloads as static files.

⚠️ Output goes next to the database by default (Umbra's data directory),
**not into the repository**. The reason is the same as .gitignore excluding
*.db: the file embeds browsing history, clipboard text and AI conversations in
full — one 1.8 MB build held over a thousand email addresses and contacts.
Keeping it physically outside the repository beats relying on .gitignore —
the latter leaks the moment someone runs `git add -f` or edits the ignore
rules; the former cannot be committed by mistake.
"""
import datetime as dt
import base64
import gzip
import json
import os

from . import (analyze, blocks as B, config, correct, db,
               inference as INF, segment, tz)

OUT = os.path.join(db.DB_DIR, "umbra-viz.html")
# The exported static site (for deploy; umbra/export.py). The single file at OUT
# is still written: it is the product promise, the form that opens on a
# double-click, and the only offline backup form.
SITE_DIR = os.path.join(db.DB_DIR, "umbra-site")

# The line is drawn by **when it is needed**, not by the shape of the data:
# everything visible without drilling into a moment is resident.
#
# Resident data is all computed from ev — wCats() walks evsOf() for minutes per
# category, and the week view, the headline numbers, goal attribution and any
# future line chart depend on it. At 2.8 KB a day it belongs in the first load.
# On-demand is the other end: 1,096 KB a day, and a given day's transcript may
# be looked at once in a lifetime.
#
#     resident 8.7 KB/day  :  on-demand 1,096 KB/day  =  1 : 129
#
# 99.2% of the size serves under 1% of the visits — that ratio is the whole
# argument for sharding.
RESIDENT = ("ev", "evuse", "bcat", "counts", "pct", "cov", "raw_omitted",
            "failed", "n_min", "tz", "travel")
ONDEMAND = ("lanes", "bruns", "ai", "dg", "raw")

# Only the recent stretch is drawn by default. All of it was 1,333 days: the
# calendar scrolls forever, years with only WHOOP have empty lanes and almost no
# information, and the HTML swells to 1.8 MB.
# --all shows the whole history.
DEFAULT_SINCE = "2026-07-01"
LANE_GAP_SEC = 180          # same-source events closer than this join one band

# A lane is (key, source, kind); kind None means the whole source.
# WHOOP splits into two: sleep and workouts mean entirely different things in
# time, and one band would draw "lay down for 8 hours" and "trained for 40
# minutes" as the same kind of thing.
LANES = [
    ("place",   "location", "stay"),
    ("transit", "location", "transit"),
    ("sleep",  "whoop",  "sleep"),
    ("workout","whoop",  "workout"),
    ("chrome", "chrome", None),
    ("claude", "claude", None),
    ("butler", "butler", None),
    ("app",    "app",    None),
    ("iphone", "iphone", None),
    ("media",  "media",  None),
    # Talking sits above the dashed line with sleep, workouts and listening:
    # they answer "what was going on away from the computer", which is not
    # what screen data means.
    ("talk",   "plaud",  None),
    # Only reading (stretches bounded by highlights) has a position in time.
    # daily is a day total; a lane would draw it as a block, claiming it was
    # read in one sitting from Beijing midnight — invented.
    ("reading", "weread", "reading"),
    ("intent", "intent", None),
    ("live",   "live",   None),
    ("git",    "git",    None),
    ("zsh",    "zsh",    None),
    ("idle",   "idle",   None),
]
SOURCES = [k for k, _, _ in LANES]

# Where each lane comes from: (a short line under the lane name; a long one for
# the drill-down panel)
#
# The short one **must not restate the lane name**. "Claude Code ← Claude Code"
# or "Terminal ← Terminal" says nothing — the question is who recorded what is
# on this line.
ORIGIN = {
    "place":   ("iPhone GPS", "Overland → Cloudflare Worker → D1"),
    "transit": ("iPhone GPS", "between two stays; mode from the motion coprocessor"),
    "sleep":   ("WHOOP band", "api.prod.whoop.com · v2 sleep"),
    "workout": ("WHOOP band", "api.prod.whoop.com · v2 workout"),
    "media":   ("Biome·NowPlaying", "playback records synced from the iPhone"),
    "talk":    ("PLAUD recorder", "transcript pieces; time = recording start + offset"),
    "iphone":  ("Biome·App.InFocus", "iPhone foreground app"),
    "intent":  ("Biome·App.Intent", "iPhone send actions, no content"),
    "reading": ("WeRead API", "highlight createTime, second precision"),
    "chrome":  ("Chrome History", "local SQLite"),
    "claude":  ("~/.claude", "Claude Code session logs"),
    "butler":  ("Butler API", "agent task records"),
    "app":     ("knowledgeC.db", "macOS's own foreground-window log"),
    "live":    ("Umbra daemon", "window title + clipboard collector"),
    "git":     ("git log", "commits across local repos"),
    "zsh":     ("~/.zsh_history", "terminal commands"),
    "idle":    ("Umbra daemon", "no-input detection"),
}


# One character per slot, for drawing the band. '.' = empty.
BCODE = {"sleep": "s", "workout": "w", "transit": "t", "code": "c",
         "read": "r", "listen": "m", "browse": "b", "app": "a", "place": "p",
         "talk": "k"}
# The names follow the tiers rather than being copied here — a copy sooner or
# later misses a new tier.
BNAME = B.CAT_NAME


def pack(events: list[dict], lo_ms: int) -> str:
    """Compress a day's raw events into one base64 string, for the drill-down.

    The drill-down wants **the original text**: a whole question to Claude, a
    task's full description, a visit's full URL. Truncated, the feature is
    pointless — you open it to judge for yourself, not to read another summary.

    The cost is size: 4,767 records a day, a 728 KB payload. So
      · timestamps become "second of the day", ten digits saved per record
      · dur_ms is dropped; the drill-down filters by moment and never uses it
      · gzip (7.8× measured) + base64, decoded in the browser by DecompressionStream
      · **one block per day**, decoded only for the day clicked — opening time
        does not grow with the number of days
    """
    slim = [{**{k: v for k, v in e.items() if k != "dur_ms"},
             "ts": max(0, (e["ts"] - lo_ms) // 1000)} for e in events]
    raw = json.dumps(slim, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(gzip.compress(raw, 9)).decode("ascii")


def packj(obj) -> str:
    """Any object → gzip + base64, decoded by the browser on demand.

    One digest entry runs to thousands of characters, 48 windows to 200 KB a
    day — a month of it in plain text would be six or seven megabytes in the
    page. It takes the same road as raw: one block per day, decoded on click.
    """
    return base64.b64encode(gzip.compress(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(),
        9)).decode("ascii")
LABEL = {"chrome": "Browser", "claude": "Claude Code", "butler": "Agent tasks",
         "app": "Foreground app", "live": "Window/clipboard", "git": "Git",
         "zsh": "Terminal", "idle": "Away", "sleep": "Sleep",
         "workout": "Workout", "whoop": "WHOOP", "iphone": "iPhone",
         "media": "Listening", "intent": "Messages/calls", "place": "Where",
         "transit": "In transit", "location": "Location", "talk": "Speech",
         "plaud": "Speech", "reading": "Reading", "weread": "Reading"}


def runs_for(events, src, kind=None):
    """Fold one source's events (optionally one kind) into [start, end, label]
    bands.

    WHOOP durations are real intervals from the server and skip the
    trusted_duration_sources() heuristics — this is not guessing how long a
    point event lasted; the record itself has a start and an end.
    """
    pts = []
    for e in events:
        if e["source"] != src or (kind and e.get("kind") != kind):
            continue
        a = e["ts"]
        if src in ("whoop", "iphone", "media", "location"):
            b = a + (e.get("dur_ms") or 0)
            tag = (e.get("sport") or e.get("track") or e.get("title")
                   or e.get("place")
                   or (f"{e.get('from','?')}→{e.get('to','?')} {e.get('mode','')}"
                       if e.get("to") else "")
                   or e.get("app") or ("Nap" if e.get("nap") else ""))
        else:
            b = a + (e.get("dur_ms") or 0
                     if src in segment.trusted_duration_sources() else 0)
            tag = ""
        pts.append((a, b, tag))
    pts.sort()
    out = []
    for a, b, tag in pts:
        # Never merge between sleeps and workouts — two separate sessions joined
        # would read as one long one.
        gap = 0 if src in ("whoop", "iphone", "media", "location") else LANE_GAP_SEC * 1000
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
            if tag and tag not in out[-1][2]:
                out[-1][2] = (out[-1][2] + "/" + tag).strip("/")
        else:
            out.append([a, b, tag])
    return out


def _union(spans, end: int) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted((max(0, a), min(b, end)) for a, b in spans):
        if a >= b:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def minutes_of(evs, windows, end: int) -> dict:
    """Every minute of [0, end) counted once, as one of three things: covered by
    an event, inside a window whose call failed, or something the model said
    nothing about.

    The page used to subtract the summed event lengths and then the failed
    windows from the end. An event lying over a failed window — merge often
    writes one — was taken away twice, and "said nothing about" went negative.
    Events come first: if something was composed there, that minute is covered,
    whatever happened to one of its calls. A refused key (kind "auth") is said
    once at the top of the page, not counted here.
    """
    cov = _union([(e["a"], e["b"]) for e in evs], end)
    covered = sum(b - a for a, b in cov)
    failed = 0
    for a, b in _union([(w["a"], w["b"]) for w in windows
                        if w.get("kind", "call") == "call"], end):
        failed += (b - a) - sum(max(0, min(b, y) - max(a, x)) for x, y in cov)
    return {"end": end, "covered": covered, "failed": failed,
            "silent": end - covered - failed}


def explained_pct(cov: dict) -> float:
    """The share of the day where what you were doing can be named.

    Numerator = A (content evidence) + known off-screen (sleep / workouts).
    **B is not included** — knowing which app was in front is not knowing what
    you were doing, and that blind spot is exactly what this measure exposes.

    The denominator is the day's length, not "the minutes with data". The latter
    would show "one hour at a computer, all of it recorded" as 100%, praising
    the least covered days as the best. And it is the day's real length, not
    1440: a 600-minute travel day with 300 explained is 50%, not 20.8%. An old
    cov without n_min counts as an ordinary day.
    """
    return round(min(100.0, 100.0 * (cov["A"] + cov["off"])
                     / (cov.get("n_min") or 1440)), 1)


def _m(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


# ── Calendar event → how it was judged ────────────────────────
#
# This chain is deterministic, so it is computed **at build time**, not in the
# browser. The page has no test net for it, and a mistake shows as "two
# stretches missing from the detail" — which looks exactly like "there was
# only one".

PREFER_MODEL = "gemini"


def pick_model(names):
    """Draw one model only. The others' data stays in the database.

    Matched by prefix, not full name: versions change (3.7 → 3.8), and pinning
    the full name would silently drop the preference on a minor upgrade. With no
    match the first one is used — returning None would make the whole AI section
    vanish, which looks like "the model said nothing".
    """
    if not names:
        return None
    for n in names:
        if n.startswith(PREFER_MODEL):
            return n
    return names[0]


def _parse_from(s):
    """'09:03–09:30' → (543, 570). Unrecognised returns None."""
    try:
        a, b = str(s).replace("\u2013", "-").replace("\u2014", "-").split("-")
        return _m(a.strip()), _m(b.strip())
    except (ValueError, AttributeError, IndexError):
        return None


def spans_of(spans, ev):
    """Which first-pass spans this event was composed from — as indices.

    Matched exactly against the model's own `from` first; failing that, by
    overlap in time. **Better too many than none** — none reads as "this event
    has no basis", which on the page cannot be told from "the model really
    gave none".
    """
    want = {w for w in (_parse_from(x) for x in (ev.get("from") or [])) if w}
    hit = [i for i, s in enumerate(spans) if (s["a"], s["b"]) in want]
    if hit:
        return hit
    # Touching ends do not overlap: a span ending at 543 does not belong to an
    # event starting at 543, or every event would carry the tail of the one
    # before and look like the model contradicting itself.
    return [i for i, s in enumerate(spans)
            if s["a"] < ev["b"] and s["b"] > ev["a"]]


CONTEXT_MIN = 60


def window_of(spans, n_min: int = 1440):
    """What the first pass actually looked at: the union of these spans, **an
    hour either side**.

    The trace must report that range honestly. Saying "it only saw these 27
    minutes" understates it — much of a judgement comes from context (09:03
    read as "with a colleague" because of the run of work talk after 09:15).

    Clamped to the day: a sleep at 00:16 extended an hour back would report a
    range of the previous day this day cannot read. No spans returns None —
    a hard-coded 0–1440 would show "the model saw all 5,038 records today",
    which is false.
    """
    if not spans:
        return None
    return (max(0, min(s["a"] for s in spans) - CONTEXT_MIN),
            min(n_min, max(s["b"] for s in spans) + CONTEXT_MIN))


# The modules that build a day's payload. Changing any of them invalidates the
# whole cache — **without anyone remembering to bump a version**: a forgotten
# bump shows as the page silently staying as it was, which is a cache's most
# dangerous failure.
_FP_MODULES = ("viz", "blocks", "analyze", "segment", "inference",
               "taxonomy", "apps", "correct")


def _code_fp() -> str:
    import hashlib
    h = hashlib.sha256()
    for m in _FP_MODULES:
        with open(os.path.join(os.path.dirname(__file__), m + ".py"), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def _day_fp(con, day: str, lo: int, hi: int, want_raw: bool, code: str) -> str:
    """A fingerprint of all of a day's input. The events side must match
    load_range exactly — otherwise "what the page drew" and "what the
    fingerprint watched" are different rows, the cache is forever fresh and
    the page forever stale."""
    ev = con.execute(
        "SELECT COUNT(*), COALESCE(MAX(ts),0), COALESCE(MAX(rowid),0) "
        "FROM events WHERE ts <= ? AND ts + COALESCE(dur_ms, 0) >= ?",
        (hi, lo)).fetchone()
    inf = [con.execute(
        f"SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM {t} WHERE day = ?",
        (day,)).fetchone() for t in ("inferences", "day_events", "digests",
                                     "infer_failures")]
    # The cut goes in too: after a timezone change nothing above moves, and the
    # page would silently keep the old timezone's shape.
    # An undone correction changes a row in place, which the counts above miss.
    return "|".join(map(str, [code, int(want_raw), *ev,
                              *(x for r in inf for x in r), tz.fp(con, day),
                              correct.withdrawn(con, lo, hi)]))


def build(since: str | None = DEFAULT_SINCE, fresh: bool = False,
          raw_days: int | None = None):
    """raw_days: embed raw records for only this many recent days (0 = all). The
    HTML's size is almost all raw — older days keep their calendar events and
    lanes, and the drill-down says plainly "not included in this build". The
    line is drawn from **the newest day in the data**, not today: measured from
    today, opening the page after a few weeks without new data would drop every
    day out of the window and the page would suddenly lose all raw
    records while the data had not changed."""
    con = db.connect()
    days = day_list(con, since)

    if raw_days is None:
        raw_days = int((config.load().get("viz") or {}).get("raw_days", 30))
    cutoff = ""
    if raw_days and days:
        import datetime as _dt
        last = _dt.date.fromisoformat(days[-1])
        cutoff = (last - _dt.timedelta(days=raw_days - 1)).isoformat()

    code = _code_fp()
    payload = {"days": {}, "order": days, "since": since}
    for day in days:
        d = day_cached(con, day, day >= cutoff, code=code, fresh=fresh)
        if d is not None:
            payload["days"][day] = d
    payload.update(page_fields(con))
    return payload


def day_list(con, since: str | None = None) -> list[str]:
    """Every local day that has events, oldest first."""
    q = ("SELECT DISTINCT date(ts/1000,'unixepoch','localtime') d FROM events "
         + ("WHERE date(ts/1000,'unixepoch','localtime') >= ? " if since else "")
         + "ORDER BY d")
    return [r[0] for r in con.execute(q, (since,) if since else ())]


def day_cached(con, day: str, want_raw: bool, code: str | None = None,
               fresh: bool = False):
    """One day's payload, through viz_cache. None when the day has no events.

    Shared by build() and umbra serve so the two can never disagree about
    what a day is — one fingerprint, one cache, one builder.
    """
    code = code or _code_fp()
    lo, hi = analyze.day_bounds(day, con)
    fp = _day_fp(con, day, lo, hi, want_raw, code)
    if not fresh:
        row = con.execute("SELECT fp, payload FROM viz_cache WHERE day=?",
                          (day,)).fetchone()
        if row and row[0] == fp:
            return json.loads(row[1])
    d = _day_payload(con, day, lo, hi, want_raw)
    if d is None:
        return None
    with con:
        con.execute("INSERT OR REPLACE INTO viz_cache (day, fp, payload) "
                    "VALUES (?,?,?)",
                    (day, fp, json.dumps(d, ensure_ascii=False)))
    return d


def page_fields(con) -> dict:
    """The per-page part of the payload: time, freshness, today.

    Split out of build() so umbra serve's /api/meta computes it the same way
    — the freshness rule in particular exists in one place (stale_fields).
    """
    payload = {}
    import time
    payload["tz"] = time.strftime("%Z")          # PDT / PST, changes with the season
    payload["tzOffset"] = time.strftime("%z")
    from . import ops
    # **Absolute times only, never "how long ago".** A value like run_age_min =
    # 651.9572 is right only at the instant it was computed, and starts rotting
    # once stored in a static file; worse, the verdict freezes with it — after a
    # failed deploy the page shows "fresh ✓" forever, exactly when it most
    # needs to warn.
    #
    # The rule still exists once, in Python (stale_fields): the page receives a
    # conclusive bad, a timestamp and a threshold, and does one subtraction, with
    # no room to restate the rule. The 2026-08-20 divergence happened because the
    # JavaScript had room to write its own conditions.
    #
    # A source standing aside is never stale: turned off, or never set up and
    # never once imported. Its failures are from before it stood aside, and the
    # Sources page lists it under "Not set up". One that *did* import and has
    # since lost its setup (a token file gone) still warns — that is news.
    from . import sources
    off, unset = sources.aside()

    def standing_aside(f):
        return f["source"] in off or (f["source"] in unset and f["last_ok"] is None)
    payload["fresh"] = [
        {k: v for k, v in f.items() if not k.endswith("_age_min")}
        | (stale_fields(f, ops.stale_min_for(f["source"]))
           if not standing_aside(f) else
           {"bad": False, "check_ts": None, "stale_min": ops.stale_min()})
        for f in db.freshness(con)]
    # The page's own age is left to the browser too, for the same reason.
    payload["built_ms"] = int(time.time() * 1000)
    payload["page_stale_min"] = ops.page_stale_min()
    # "When was this page built" and "what day is it" — both computed at build
    # time. On 2026-08-21 a long hunt for "the page didn't update" ended with the
    # page new and the data present, and nothing anywhere to confirm either.
    # today is not left to the browser: a time-zone difference marks the wrong
    # cell, and a wrong mark looks no different from none.
    payload["generated"] = time.strftime("%Y-%m-%d %H:%M")
    payload["today"] = dt.date.today().isoformat()
    return payload


def stale_fields(f: dict, stale_min: float) -> dict:
    """The three things the verdict needs — **the one copy of the rule**.

      bad        bad regardless of time: FAIL_STREAK consecutive failures, never
                 succeeded, or nothing to compare at all. One failure is not
                 enough — it may be a network blip, or a manual run without Full
                 Disk Access (the data has not stopped; flagging it would send
                 someone to fix something that is not broken).
      check_ts   the absolute time to compare with "now"
      stale_min  this source's alarm line, in minutes

    The page only does `bad || (check_ts != null && now - check_ts > stale_min*60000)`.
    Every condition is here, with no room to write it again in JavaScript —
    the 2026-08-20 incident was two verdicts evolving apart.
    tests/test_page_freshness.py feeds both from one table to pin it.
    """
    if f["live"]:
        # The collector writes continuously and has no "import" step — judged
        # by last_run it would always be bad. It does not follow the import
        # period either: two hours of silence means it really died.
        return {"bad": False, "check_ts": f["last_event"], "stale_min": 120}
    return {"bad": (f["fails"] >= db.FAIL_STREAK
                    or f["last_ok"] is None
                    or f["last_run"] is None),
            "check_ts": f["last_run"], "stale_min": stale_min}


def stale(f: dict, stale_min: float) -> bool:
    """Should this source wear a ⚠ (the Python side; the page uses stale_fields
    and one subtraction)."""
    d = stale_fields(f, stale_min)
    if f["live"]:
        return (f["event_age_min"] or 0) > d["stale_min"]
    if d["bad"]:
        return True
    return f["run_age_min"] > d["stale_min"]


def _ai_block(con, day: str, ai=None, evs=None):
    """The `ai` block of the payload: models / spans / use / ag.

    `use` is written only on days with calendar events — without them the page
    falls back to `models[0]`. The asymmetry is historical, but both sides rely
    on it; do not "tidy" it here.
    """
    ai = INF.load(con, day) if ai is None else ai
    if not ai:
        return None
    names = sorted(ai)
    blk = {
        "models": names,
        # Spans, not 288 slots: slots are spans laid out, and storing them would
        # repeat activity and evidence N times. The page looks them up by minute.
        "spans": {m: [{"a": _m(x["start"]), "b": _m(x["end"]),
                       "a0": x["start"], "b0": x["end"],
                       "cat": x["cat"], "act": x.get("activity", ""),
                       "conf": x.get("confidence", ""),
                       "who": x.get("with", ""),
                       # **All** the evidence. It used to be ev[:4], which cut
                       # 276 lines across three models — and the only basis for
                       # "no keyboard or mouse" in the 06:40 slot was the fifth.
                       # The page folds the extra; the data layer never drops it.
                       "ev": x.get("evidence", []),
                       "also": x.get("also", [])}
                      for x in ai[m]] for m in names},
    }
    n_min = tz.day_minutes(con, day)
    per = {m: INF.expand(ai[m], n_min=n_min) for m in names}
    blk["ag"] = INF.agreement([per[m] for m in names])
    evs = INF.load_events(con, day) if evs is None else evs
    if evs and pick_model(sorted(evs)):
        blk["use"] = pick_model(names) or pick_model(sorted(evs))
    return blk


def _lay_corrections(con, day: str, d: dict, evs: dict, use, blk, dg) -> None:
    """Your corrections over the drawn model's events (correct.apply).

    Only the drawn events change; d["ai"].spans are the model's own words and
    stay as they were — comparing models, the trace panel and attribution all
    rest on them. Each block of yours gets the same indices as a model event
    (which spans, which digest), and whether the model agrees, judged against
    the **first-pass spans**, not the calendar (see correct.disagrees):

        pending it has not been asked since you said it (read_by_model)
        vs      it holds to something else (soft: the same thing the other
                way round, which is not a conflict)
        nosay   it said nothing about this span — not a disagreement
    """
    corr = correct.load(con, day)
    if not corr or not use:
        return
    sp = blk["spans"][blk["use"]] if blk else None
    merged = correct.apply(list(evs[use]), corr)
    for e in merged:
        if not e.get("mine"):
            continue
        if sp is not None:
            e["sp"] = spans_of(sp, e)
            w = window_of([sp[i] for i in e["sp"]], tz.day_minutes(con, day))
            if w:
                e["win"] = list(w)
        if dg.get(use):
            e["dg"] = INF.overlap_idx(dg[use], e["a"], e["b"])
        if correct.read_by_model(e, dg.get(use) or []) is False:
            # Not re-asked since you said it: no verdict either way yet.
            e["pending"] = True
            continue
        v = correct.disagrees(e, sp or [])
        if v:
            e["vs"] = v
        elif v is None:
            e["nosay"] = True
    evs[use] = merged
    d["ev"] = evs


def _day_payload(con, day: str, lo: int, hi: int, want_raw: bool):
    n_min = (hi - lo + 1) // 60000
    events = db.load_range(con, lo, hi)
    if not events:
        return None

    lanes = {}
    for key, src, kind in LANES:
        rs = runs_for(events, src, kind)
        if rs:
            # Sleep crossing midnight is clamped to the day, or the block draws
            # off the track
            lanes[key] = [[round(max(0, (a - lo) / 60000), 1),
                           round(min(n_min, max((b - lo) / 60000,
                                               (a - lo) / 60000 + 0.5)), 1),
                           tag]
                          for a, b, tag in rs]

    bs = B.blocks(events, lo, n_min)
    cov = analyze.coverage(day, con)
    counts = {}
    for e in events:
        counts[e["source"]] = counts.get(e["source"], 0) + 1

    c = {k: cov[k] for k in ("A", "B", "off", "C", "away", "n_min")}
    # segs (the rule-based segmentation text) is no longer in the payload: its
    # only reader, the segment detail, was removed on 2026-08-19. Kept, it
    # would be pure size.
    d = {"lanes": lanes, "counts": counts,
         "cov": c, "pct": explained_pct(c),
         # The page's 1440, made per day: the length, the clocks it was read
         # on, and whether it is left out of comparisons across days.
         "n_min": n_min, "tz": tz.segments(con, day),
         "travel": tz.is_travel_day(con, day),
         # 5-minute slots, stored twice: bcat is 288 characters (one per slot)
         # for drawing the band; bruns is the merged runs, for lists. All 288 as
         # objects would add two megabytes; the two together are a few KB a day.
         "bcat": "".join(BCODE.get(b["cat"], ".") for b in bs),
         "bruns": [{k: r[k] for k in
                    ("t", "end", "min", "cat", "label", "also", "cf",
                     "conflict", "note", "why")} for r in B.runs(bs)]}
    if want_raw:
        # Raw events, decompressed only when the day is clicked. See pack().
        d["raw"] = pack(events, lo)
    else:
        # "Not included" and "none" must stay apart: with this flag the page says
        # "this build only carries the last N days" rather than "no records
        # this day".
        d["raw_omitted"] = True

    # Model inference. **A different kind of thing from the above**: bruns is
    # what rules derived from observation; this is what a model guessed. Its own
    # key, its own look, never mixed into bruns. Days without inference have no
    # such key, so the page hides the whole section rather than showing an empty
    # comparison (which looks like "the model said nothing").
    #
    # The second pass: calendar events. Kept **apart** from the spans above —
    # the two granularities differ by an order of magnitude (spans 1–30 minutes,
    # events 18–390), and mixed, "the model's judgement of this half hour" and
    # "what this day was made of" could no longer be told apart.
    evs = INF.load_events(con, day)
    ai = INF.load(con, day)
    blk = _ai_block(con, day, ai=ai, evs=evs)
    if blk:
        d["ai"] = blk
    if evs:
        d["ev"] = evs
        # One model drawn. The others' spans stay in ai.spans — comparing three
        # again is only more rendering in the page, not another model run.
        use = pick_model(sorted(evs))
        if use and blk:
            sp = blk["spans"][blk["use"]]
            # The indices must point into the list the page actually renders.
            # Both sides use the same comprehension order, but a shift would point
            # at the wrong span silently — pin the length.
            assert len(sp) == len(ai[blk["use"]]), "span lists out of step"
            for e in evs[use]:
                e["sp"] = spans_of(sp, e)
                w = window_of([sp[i] for i in e["sp"]], tz.day_minutes(con, day))
                if w:
                    e["win"] = list(w)
        # The input side: the text actually given to the model.
        # Inference without a digest = that run predates this table — not the
        # same as "this day never ran", and the page has to say which.
        dg = INF.load_digests(con, day)
        if dg.get(use):
            rows = dg[use]
            if want_raw:
                d["dg"] = packj(rows)
            for e in evs[use]:
                e["dg"] = INF.overlap_idx(rows, e["a"], e["b"])
        d["evuse"] = use
        _lay_corrections(con, day, d, evs, use, blk, dg)
    elif (corr := correct.load(con, day)):
        # The model said nothing that day, but you did: draw it, or a
        # correction changes nothing on the page while every log is green.
        d["ev"] = {"me": correct.apply([], corr)}
        d["evuse"] = "me"
    # Windows whose call failed. Without them a failed half hour is drawn the
    # same as one the model read and found nothing in — an outage shown as a
    # finding. Keyed to the drawn model when there is one; otherwise to the
    # model the next run will use, since that is whose failures matter now.
    fails = INF.load_failures(con, day)
    if fails:
        from . import infer
        who = d.get("evuse") if d.get("evuse") in fails else (
            infer.MODEL if infer.MODEL in fails else sorted(fails)[0])
        d["failed"] = {"model": who, "windows": fails[who]}
    return d


def write(out: str = OUT, raw_days: int | None = None) -> str:
    """The page the scheduled run leaves next to the database: the web app
    (umbra/web/), exported read-only with every payload inline, as one file
    that opens on a double-click and still works copied elsewhere — the
    product promise the old hand-written page made, kept by the new one.

    It replaced viz.html/css/js (retired 2026-10): those drew the same days
    from the same payloads a second time, in a second UI, and every view had
    to be built twice. Now there is one UI and two ways to reach it: live
    (`umbra serve`) and exported (this, and `umbra export` for a site).
    """
    from . import export
    export.write_single(out, raw_days=raw_days)
    return out
