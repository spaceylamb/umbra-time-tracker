"""Collapse and segment the event stream — the one layer in the pipeline with
no objectively correct answer.

Two steps:
  1. collapse()  remove noise (Chrome's redirect storms, interstitials)
  2. segment()   merge what remains into "activity blocks"

The whole segmentation policy is concentrated in POLICY and should_merge()
below. Changing those two changes the shape of the timeline without touching any
other code — which is also why the events table stores point events and never
intervals: swap the policy and re-run at any time, and the raw data never moves.

⚠️ APP_NAMES below duplicates apps.NAMES, with different values for the same
   bundle ids ("Google Chrome" here, "Chrome" there). Two hand-maintained copies
   of the same table, which taxonomy.py's docstring explains always drift. It is
   left as-is for now because merging them changes bucket keys and therefore the
   shape of existing segments; it should be collapsed into one table before
   release.
"""

import re
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────
# Segmentation policy. These numbers directly decide what the timeline looks
# like.
# ─────────────────────────────────────────────────────────────
POLICY = {
    # Two events in the same bucket closer than this many seconds -> one segment
    "merge_gap_sec": 300,
    # Segments shorter than this are dropped (unless they carry a high-value
    # signal — see should_keep)
    "min_segment_sec": 90,
    # More than oscillation_min_flips A<->B switches inside this window -> merge
    # into one segment. (Real case: an afternoon spent bouncing a dozen
    # times between online banking and an accounting app, reconciling.)
    "oscillation_window_sec": 600,
    "oscillation_min_flips": 4,
}

# Chrome interstitials — redirects, checks, loading screens. No information.
# Kept multilingual on purpose: this matches the user's own page titles, not this
# source code, so every language's wording stays active at once (same rule as the
# patterns in privacy.py). Add your own below.
INTERSTITIAL = re.compile(
    r"^(just a moment|redirecting|loading|请稍候|,?\s*$"
    r"|checking your browser|attention required)", re.I)


# The last segment of a bundle id is often not a word (us.zoom.xos -> "xos").
# Kept at the display layer rather than the import layer, so changing it needs no
# re-import.
APP_NAMES = {
    "us.zoom.xos": "Zoom",
    "com.tencent.xinWeChat": "WeChat",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.larksuite.larkApp": "Lark",
    "com.electron.lark": "Lark",
    "com.apple.MobileSMS": "Messages",
    "com.googlecode.iterm2": "iTerm",
    "com.bloombuilt.dayone-mac": "DayOne",
    "com.tencent.weread": "WeRead",
    "com.netease.163music": "NetEase Music",
    "com.openai.codex": "Codex",
    "com.apple.systempreferences": "System Settings",
    "com.spotify.client": "Spotify",
    "com.tdesktop.Telegram": "Telegram",
    "com.apple.Preview": "Preview",
    "com.apple.finder": "Finder",
    "com.apple.Notes": "Notes",
    "com.google.Chrome": "Google Chrome",
    "com.apple.Safari": "Safari",
    "company.thebrowser.Browser": "Arc",
    "com.microsoft.edgemac": "Edge",
    "com.brave.Browser": "Brave",
    "com.figma.Desktop": "Figma",
    "com.todesktop.230313mzl4w4u92": "Cursor",
    "com.microsoft.VSCode": "VS Code",
    "notion.id": "Notion",
    "com.apple.mail": "Mail",
}


def app_name(e: dict) -> str:
    """Normalise to a human-readable app name.

    ⚠️ The same app carries different fields depending on the payload's kind:
         window/activate -> {"bundle": "com.google.Chrome", "app": "Google Chrome"}
         clip            -> {"app": "com.google.Chrome"}   <- app holds a bundle id
       Without normalisation Chrome splits into two buckets, `app:Google Chrome`
       and `app:com.google.Chrome`, cutting one stretch of activity in half.
    """
    for key in (e.get("bundle"), e.get("app")):
        if key and key in APP_NAMES:
            return APP_NAMES[key]
    name = e.get("app") or e.get("bundle") or ""
    # Something shaped like com.foo.bar is a bundle id, not a name for people
    if name.count(".") >= 2:
        return name.rsplit(".", 1)[-1]
    return name or "?"


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "?").lower()
    return host[4:] if host.startswith("www.") else host


def bucket_key(e: dict) -> str:
    """Which "activity bucket" an event belongs to. Same bucket + close in time
    = same segment."""
    if e["source"] == "chrome":
        return f"web:{_domain(e.get('url', ''))}"
    if e["source"] == "claude":
        return f"code:{e.get('project', '?')}"
    if e["source"] == "butler":
        # Bucketed by provider rather than projectId — the latter is a UUID and
        # unreadable.
        return f"ai:{e.get('provider', '?')}"
    if e["source"] == "git":
        return f"code:{e.get('project', '?')}"
    if e["source"] == "app":
        return f"app:{app_name(e)}"
    if e["source"] == "plaud":
        # One bucket, never split by speaker. "Speaker 1/2" is numbered
        # **within a single recording** and is not the same person across
        # recordings — bucketing by it manufactures a crowd of fake identities,
        # and cuts one conversation into several segments by who was talking.
        return "talk"
    if e["source"] == "idle":
        return "idle:nobody here"
    if e["source"] == "live":
        # Same bucket as knowledgeC's app events — both sources record the same
        # fact (which app is in the foreground), live simply adds the window
        # title. Separate buckets would cut them in half.
        return f"app:{app_name(e)}"
    return "terminal"


def trusted_duration_sources() -> set:
    """Sources whose dur_ms represents real attention time.

    Each source answers for itself now, through DURATION in its own module; this
    was the fourth and last place that kept a hand-maintained list of source
    names. The reasoning that used to live here now lives beside each
    declaration:

      knowledgeC's /app/usage records foreground focus, so it qualifies —
      without it a 30-minute Zoom call collapses to an instant and vanishes.
      plaud qualifies too: its dur_ms is how long one utterance took, given by
      the ASR's segment boundaries.
      ⚠️ chrome's visit_duration is how long the tab stayed alive, and
      background tabs accumulate for hours (one real record: 17,942s). Treated
      as attention it blows the timeline apart, so chrome declares "point".

    A source that declares nothing gets "point", which is the safe direction: an
    understated duration loses resolution, an overstated one invents time that
    was never spent.
    """
    from . import registry
    return registry.trusted_duration()


def end_of(e: dict) -> int:
    """When an event ends.

    Most sources emit point events (end == ts), but knowledgeC's /app/usage
    carries a real duration — a 30-minute Zoom call is one event with a dur_ms,
    not a point inside 30 minutes. Ignoring it compresses the meeting into an
    instant and it vanishes from the timeline.
    """
    if e["source"] not in trusted_duration_sources():
        return e["ts"]
    return e["ts"] + (e.get("dur_ms") or 0)


# A browser's app-foreground records are redundant — the chrome source already
# gives URL-level detail. But their durations are far more trustworthy than
# Chrome's own visit_duration, which counts tab lifetime.
BROWSER_BUNDLES = {"com.google.Chrome", "com.apple.Safari", "company.thebrowser.Browser",
                   "com.microsoft.edgemac", "com.brave.Browser"}


def collapse(events: list[dict]) -> list[dict]:
    """Collapse Chrome's duplicate storms.

    Observed in real data: the same university sign-in page appearing five
    times within five seconds, a journal article six times — Chrome records a
    visit for every redirect and every iframe. Of 212 events, roughly 40 carried
    any information.

    Rule: for the same (domain, title), keep only the first within 30 seconds and
    accumulate the durations onto it.
    """
    # Butler and the local jsonl record the same sentence: for a task Butler
    # dispatched to Claude Code, the opening prompt exists on both sides
    # (measured: 732 duplicates out of 1,877). Keep the local one — it is part of
    # a complete session, while Butler has only the opening line.
    claude_heads: dict[str, list[int]] = {}
    for e in events:
        if e["source"] == "claude":
            claude_heads.setdefault((e.get("text") or "")[:40], []).append(e["ts"])

    def is_duplicate(e: dict) -> bool:
        near = claude_heads.get((e.get("text") or "")[:40], ())
        return any(abs(e["ts"] - t) < 180_000 for t in near)

    out, last_seen = [], {}
    for e in events:
        if e["source"] == "butler":
            if e["kind"] == "task" and is_duplicate(e):
                continue
            out.append(e)
            continue
        if e["source"] == "app":
            # Drop browsers (the chrome source is finer-grained); keep every
            # other native app — those are Chrome's blind spot.
            if e.get("bundle") not in BROWSER_BUNDLES:
                out.append(e)
            continue
        if e["source"] != "chrome":
            out.append(e)
            continue
        title = (e.get("title") or "").strip()
        if INTERSTITIAL.match(title):
            continue
        key = (_domain(e.get("url", "")), title)
        prev = last_seen.get(key)
        if prev and e["ts"] - prev["ts"] < 30_000:
            prev["dur_ms"] = (prev.get("dur_ms") or 0) + (e.get("dur_ms") or 0)
            continue
        last_seen[key] = e
        out.append(e)
    return out


# ─────────────────────────────────────────────────────────────
# 👇 This function is the one meant to be edited. What follows is a working
#    default; change it and the timeline changes immediately.
# ─────────────────────────────────────────────────────────────
def should_merge(seg: dict, e: dict, policy: dict) -> bool:
    """Should event e join the segment currently being accumulated?

    Available signals:
      seg["key"] / seg["end"] / seg["events"]   the segment's bucket, end time,
                                                and events so far
      e["ts"] / bucket_key(e) / e["source"]     the new event

    The trade-off:
      - merging only on matching buckets -> "six minutes on Stack Overflow for
        this bug" becomes its own segment: a finer timeline, but a choppier one
      - allowing cross-bucket merges (any event close enough in time joins) -> a
        tidier timeline, but "writing code" and "scrolling Twitter" end up as one
        block
    """
    gap = (e["ts"] - seg["end"]) / 1000
    return bucket_key(e) == seg["key"] and gap <= policy["merge_gap_sec"]


def _new_segment(e: dict) -> dict:
    return {"key": bucket_key(e), "start": e["ts"], "end": end_of(e), "events": [e]}


def should_keep(seg: dict, policy: dict) -> bool:
    """Whether to keep a segment.

    ⚠️ Only "isolated and contentless" segments may be dropped here; never filter
    by time span. The lesson: a chrome visit is a point event, so its span is
    always 0 — filtering by span deletes every stretch of "rapid hopping between
    domains", which is precisely what research looks like. Measured over the
    14:45-17:00 window, 534 events were cut down to 4 segments and an entire
    stretch of "researching meditation" vanished.
    The right treatment for a short segment is absorption into its neighbour
    (see _absorb_short), not deletion.
    """
    return len(seg["events"]) >= 2 or any(
        x["source"] in ("claude", "git", "zsh", "plaud") for x in seg["events"])


def _dominant_key(events: list[dict]) -> str:
    """The most representative bucket in a mixed pile of events — whichever
    occurs most often."""
    counts: dict[str, int] = {}
    for e in events:
        k = bucket_key(e)
        counts[k] = counts.get(k, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _absorb_short(segs: list[dict], policy: dict) -> list[dict]:
    """Merge consecutive short segments into one rather than discarding them.

    "Fifteen minutes hopping across eight domains" should be one stretch of
    research — not eight deleted fragments, and not eight lines of noise.
    """
    out: list[dict] = []
    buf: list[dict] = []

    def flush():
        if not buf:
            return
        events = [e for s in buf for e in s["events"]]
        out.append({"key": _dominant_key(events), "start": buf[0]["start"],
                    "end": max(s["end"] for s in buf), "events": events})
        buf.clear()

    for s in segs:
        is_short = (s["end"] - s["start"]) / 1000 < policy["min_segment_sec"]
        if not is_short:
            flush()
            out.append(s)
        elif buf and (s["start"] - buf[-1]["end"]) / 1000 > policy["merge_gap_sec"]:
            flush()                      # too far apart; start a new one
            buf.append(s)
        else:
            buf.append(s)
    flush()
    return sorted(out, key=lambda s: s["start"])


def segment(events: list[dict], policy: dict = POLICY) -> list[dict]:
    segs: list[dict] = []
    for e in events:
        if segs and should_merge(segs[-1], e, policy):
            segs[-1]["end"] = max(segs[-1]["end"], end_of(e))
            segs[-1]["events"].append(e)
        else:
            segs.append(_new_segment(e))
    segs = _merge_oscillation(segs, policy)
    segs = _absorb_short(segs, policy)
    return [s for s in segs if should_keep(s, policy)]


def _merge_oscillation(segs: list[dict], policy: dict) -> list[dict]:
    """Merge an A->B->A->B->A oscillation into one segment.

    This comes from real data: bouncing a dozen times between online banking and
    an accounting app is **one activity** (reconciling), but bucketed strictly it
    becomes a dozen fragments.
    """
    out, i = [], 0
    while i < len(segs):
        j, keys = i, set()
        while (j < len(segs)
               and (segs[j]["end"] - segs[i]["start"]) / 1000
                   <= policy["oscillation_window_sec"]):
            keys.add(segs[j]["key"])
            j += 1
        flips = j - i
        if flips >= policy["oscillation_min_flips"] and 2 <= len(keys) <= 3:
            merged = {"key": " + ".join(sorted(keys)),
                      "start": segs[i]["start"], "end": segs[j - 1]["end"],
                      "events": [e for s in segs[i:j] for e in s["events"]]}
            out.append(merged)
            i = j
        else:
            out.append(segs[i])
            i += 1
    return out
