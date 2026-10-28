"""Now-playing importer (Media.NowPlaying, Biome synced to the Mac through iCloud).

Source: ~/Library/Biome/streams/restricted/Media.NowPlaying/remote/<device>/

**The only phone source that knows content.** App.InFocus can say "you were in
the Books app" (the container); this can say "you were listening to chapter IV
of an audiobook" — what you were
actually doing, which is the kind of evidence this project is after.

⚠️ f8 is not stored. The raw record carries **the sentence being read aloud**,
updated sentence by sentence. Deliberately left out: title and chapter already
say what you were doing, the text adds little, and it would turn the local page
into an excerpt of every book you have read.

The format is in umbra/segb.py — Apple does not document it; it is
reverse-engineered.
"""

import os

from .. import segb

SOURCE = "media"
PLATFORM = "darwin"
REQUIRES = ["fda"]

# This source **folds raw records into intervals**, so changing the algorithm changes
# the rows the same raw data produces. Import deletes the old rows for the span
# before writing — see db.replace_range and tests/test_resegment.py.
SEGMENTED = True

BIOME = os.path.expanduser("~/Library/Biome/streams/restricted")
STREAM = "Media.NowPlaying"

# Protobuf fields (reverse-engineered 2026-08-15). Numbered **completely
# differently** from App.InFocus — every Biome stream has its own schema.
F_TIME = 2         # Apple absolute seconds (double)
F_STATE = 3        # 1 / 3, apparently playing / paused
F_TITLE = 4        # book / album
F_TRACK = 5        # chapter / track
F_TEXT = 8         # the text being read aloud — **deliberately not kept**
F_DEVICE = 14      # output device (an AirPods MAC address, or "Speaker")
F_BUNDLE = 15      # app

# The fields kept, listed. An allowlist rather than a blocklist: when Apple adds
# a field, the default is not to keep it, rather than quietly store one more thing.
KEEP_FIELDS = (F_TIME, F_STATE, F_TITLE, F_TRACK, F_DEVICE, F_BUNDLE)

# Records further apart than this are two sessions. Progress is logged every
# few seconds, so a pause of minutes means it was interrupted.
GAP_MS = 5 * 60 * 1000

# The same reason as iPhone foreground: music left playing while you walk away
# reads as "four hours of listening".
SUSPECT_AFTER_MS = 90 * 60 * 1000
MAX_SESSION_MS = 6 * 3600 * 1000


def _is_media_record(rec: dict) -> bool:
    """Does this look like a Media.NowPlaying record?

    A sequential scan hits padding and CRCs that decode as plausible fields —
    measured, it produces junk like `f1=1.07e+195`, a checksum read as a double.
    """
    if not segb.in_apple_range(rec.get(F_TIME, [None])[0]):
        return False
    v = rec.get(F_BUNDLE, [None])[0]
    return v is None or isinstance(v, bytes)


def device_dirs() -> list[str]:
    """Synced device directories. remote/ only — local/ is what the Mac itself
    is playing, which says little about what the person was doing, and five
    other sources already cover the Mac."""
    root = os.path.join(BIOME, STREAM, "remote")
    return segb.subdirs(root)


def _text(rec: dict, field: int) -> str:
    v = rec.get(field, [b""])[0]
    if not isinstance(v, bytes):
        return ""
    try:
        return v.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def to_rows(records: list[dict], since_ms: int | None = None) -> list[tuple]:
    """Fold sentence-level progress into "what was playing, from when to when".

    One audiobook session can produce thousands of records, tens of thousands
    over a long listen. Unfolded, this one source would swamp the events table and
    turn its lane into a solid bar.

    A change in any of (app, title, chapter) starts a new session: changing
    chapter is continuous in time, but it is two things.
    """
    events = []
    for r in records:
        t = r.get(F_TIME, [None])[0]
        if not segb.in_apple_range(t):
            continue
        title, track = _text(r, F_TITLE), _text(r, F_TRACK)
        if not title and not track:
            continue        # cannot say what was playing; would only pollute the timeline
        events.append((segb.apple_ms(t), _text(r, F_BUNDLE), title, track,
                       _text(r, F_DEVICE)))
    events.sort()

    # **Group by track first, then split each group by time.**
    #
    # On a track change this stream writes the old track and the new one with
    # near-identical timestamps. Sorted by time, the two records of one song have
    # another song between them:
    #
    #     09:12:37   Track A           <- starts
    #     09:15:43   Track B           <- cuts in
    #     09:15:43   Track A           <- ends, 3 min 06 s later = the song
    #
    # The first version walked the stream in time order comparing keys with
    # **only the session just before**, so one interruption broke the chain and
    # those 3 minutes 06 became two zero-length sessions. Over two-thirds of
    # stored sessions had zero duration, most of them tracks split this way.
    #
    # Audiobooks never showed it: one chapter throughout, nothing cutting in.
    by_key: dict[tuple, list] = {}
    for ts, bundle, title, track, device in events:
        by_key.setdefault((bundle, title, track), []).append((ts, device))

    rows = []
    for key, items in by_key.items():
        cur = None
        for ts, device in items:            # events are time-sorted; groups stay in order
            if cur and ts - cur["end"] <= GAP_MS:
                cur["end"] = ts
                continue
            if cur:
                rows.append(_row(cur))
            cur = {"key": key, "start": ts, "end": ts, "device": device}
        if cur:
            rows.append(_row(cur))
    rows.sort(key=lambda r: r[1])            # grouping shuffled the order; sort back by time

    out = [r for r in rows if r[2] <= MAX_SESSION_MS]
    if since_ms is not None:
        out = [r for r in out if r[1] >= since_ms]
    return out


def _row(cur: dict) -> tuple:
    bundle, title, track = cur["key"]
    dur = cur["end"] - cur["start"]
    return (
        f"media:{bundle}:{cur['start']}", cur["start"], dur, SOURCE, "playing",
        {"bundle": bundle,
         "app": bundle.split(".")[-1] if bundle else "",
         "title": title,
         "track": track,
         "device": cur["device"],
         # left playing while you walked away — the same problem as iPhone, the same fix
         "suspect": dur > SUSPECT_AFTER_MS},
    )


def collect(since_ms: int | None = None) -> list[tuple]:
    """Read every synced device. Nothing readable returns empty, not a fault."""
    records = []
    for d in device_dirs():
        records += segb.read_dir(d, valid=_is_media_record)
    return to_rows(records, since_ms)


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "media", "kind": None,
          "origin": ("Biome·NowPlaying", "playback records synced from the iPhone")}]


def render(e) -> str | None:
    t = " — ".join(x for x in (e.get("title"), e.get("track")) if x)
    return f"🎧 listening: {t}" if t else None


def classify(e) -> str | None:
    return "listen" if (e.get("title") or e.get("track")) else None
