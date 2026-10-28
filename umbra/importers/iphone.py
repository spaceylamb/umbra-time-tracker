"""iPhone foreground-use importer (Biome, synced to the Mac through iCloud).

Source: ~/Library/Biome/streams/restricted/App.InFocus/remote/<device>/

Of the first seven sources only `app` (knowledgeC) says "which app you were
in", and only on the Mac. Time on the phone was a **hole** — everything after
the laptop closed read as "can't tell".
Some days had over 1,300 minutes of that, and most of it was not idleness, it
was a person on their phone.

The key difference from knowledgeC: these are **paired enter/leave events**
(field 3) that meet end to end when switching apps, so durations are exact.
knowledgeC only gives container-level stretches.

Why not part of the `app` source: "using WeChat on the Mac" and "using WeChat
on the phone" are different things. Mixed into one source, lanes and
segmentation would both be distorted.

⚠️ Needs Full Disk Access, and TCC checks **the chain of processes making the
request**. Something running under launchd with no GUI app in its chain never
can read it; a scheduled job launched by launchd can (`umbra schedule` installs
one).

The format is in umbra/segb.py — Apple does not document it; it is
reverse-engineered.
"""

import os

from .. import segb

SOURCE = "iphone"
# Something every normal day: this many days with nothing new means it has
# stopped, and STALE_HINT is where to look first (plugins/staleness.py).
STALE_DAYS = 2
STALE_HINT = ('Phone screen time reaches this Mac through iCloud. Check that the iPhone '
              'still shares across devices in Screen Time, and that the scheduled job '
              'still has Full Disk Access.')
PLATFORM = "darwin"
REQUIRES = ["fda"]

# This source **folds raw records into intervals**, so changing the algorithm changes
# the rows the same raw data produces. Import deletes the old rows for the span
# before writing — see db.replace_range and tests/test_resegment.py.
SEGMENTED = True

BIOME = os.path.expanduser("~/Library/Biome/streams/restricted")
STREAM = "App.InFocus"

# Protobuf fields (reverse-engineered 2026-08-15)
F_PHASE = 3        # 1 = came to the front, 0 = left
F_TIME = 4         # Apple absolute seconds (double)
F_BUNDLE = 6
F_VERSION = 9

# The plausible range for one stretch of foreground use.
#
# The ceiling is not fussiness. One missing "left" event (a truncated sync, an
# app killed by the system) merges everything between two stretches into tens
# of hours of "using WeChat". That is not imprecise, it is wrong, and it poisons
# segmentation for the whole timeline. When a duration cannot be trusted, better
# not to keep it.
MIN_SESSION_MS = 1
MAX_SESSION_MS = 6 * 3600 * 1000

# Longer than this is flagged suspect.
#
# App.InFocus records foreground switches; **it does not know whether the
# screen was locked**. An app left in front with the phone in a pocket for three
# hours reads as "three hours of Gmail". A handful of stretches like that can
# make up a large share of the total — hours a day of a phone in a pocket.
#
# Flagged, not deleted: raw events are append-only facts, and the criterion will
# change (with a screen-state stream it could really be told apart). Deleted is
# gone for good; a flagged event can be reinterpreted at any time — which is the
# reason this project stores only point events.
SUSPECT_AFTER_MS = 30 * 60 * 1000


def _is_focus_record(rec: dict) -> bool:
    """Does this decoded record look like an App.InFocus record?

    A sequential scan inevitably hits padding and CRCs that decode as plausible
    fields. The test lives here rather than in segb.py because only this importer
    knows App.InFocus's schema: f4 = time, f6 = bundle.
    """
    if not segb.in_apple_range(rec.get(F_TIME, [None])[0]):
        return False
    v = rec.get(F_BUNDLE, [None])[0]
    if v is None:
        return True                 # a record with a time but no bundle is dropped later
    if not isinstance(v, bytes):
        return False
    try:
        name = v.decode("ascii")
    except UnicodeDecodeError:
        return False
    return "." in name and name.isprintable()


def device_dirs() -> list[str]:
    """Every synced device directory.

    remote/ is other devices (iPhone, iPad); local/ is this Mac, whose foreground
    data the knowledgec importer already handles. Only remote is read here.
    """
    root = os.path.join(BIOME, STREAM, "remote")
    return segb.subdirs(root)


def _text(rec: dict, field: int) -> str:
    v = rec.get(field, [b""])[0]
    return v.decode(errors="replace") if isinstance(v, bytes) else ""


def to_rows(records: list[dict], since_ms: int | None = None) -> list[tuple]:
    """Pair enter and leave events into intervals, as events rows.

    Pending "enter" events are tracked per bundle: iOS interleaves system apps
    (Control Center, notifications) in the middle of a switch, and a single
    global "current app" would pair them wrongly.
    """
    events = []
    for r in records:
        if F_TIME not in r or F_BUNDLE not in r:
            continue
        bundle = _text(r, F_BUNDLE)
        if not bundle:
            continue
        events.append((segb.apple_ms(r[F_TIME][0]), bundle,
                       r.get(F_PHASE, [None])[0], _text(r, F_VERSION)))
    events.sort(key=lambda e: e[0])

    open_at: dict[str, tuple[int, str]] = {}
    rows = []
    for ts, bundle, phase, version in events:
        if phase == 1:
            # Two "enter"s with no "leave" between: keep the earlier, or this
            # stretch comes out short.
            open_at.setdefault(bundle, (ts, version))
        elif phase == 0:
            start = open_at.pop(bundle, None)
            if start is None:
                continue            # the file starts mid-session: an orphan "leave"
            dur = ts - start[0]
            if not MIN_SESSION_MS <= dur <= MAX_SESSION_MS:
                continue
            if since_ms is not None and start[0] < since_ms:
                continue
            rows.append((
                f"iphone:{bundle}:{start[0]}", start[0], dur, SOURCE, "focus",
                {"bundle": bundle,
                 "app": bundle.split(".")[-1],
                 "version": start[1] or version,
                 # the screen may have locked long ago; this duration is not to be trusted
                 "suspect": dur > SUSPECT_AFTER_MS},
            ))
    return rows


def collect(since_ms: int | None = None) -> list[tuple]:
    """Read every synced device.

    Nothing readable returns empty: no iPhone, no Share Across Devices, or no
    Full Disk Access are all "no data", not a fault, and must not stop the import.
    A permission problem shows up in the freshness report (this source never
    has new events).
    """
    records = []
    for d in device_dirs():
        records += segb.read_dir(d, valid=_is_focus_record)
    return to_rows(records, since_ms)


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "iphone", "kind": None,
          "origin": ("Biome·App.InFocus", "iPhone foreground app")}]


def render(e) -> str | None:
    """"phone" versus "computer" is load-bearing: the same bundle id in the
    foreground means different things on the two devices, and the model is asked
    to reconstruct one timeline across both."""
    from .. import render as R
    n = R.app_name(e)
    return f"📱 phone foreground: {n}" if n else None


def classify(e) -> str | None:
    return None if e.get("suspect") else "app"
