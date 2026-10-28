"""Intent importer (App.Intent) — "you sent a message", "you made a call".

Source: ~/Library/Biome/streams/restricted/App.Intent/{local,remote}/

Unlike the other sources, this records **actions**, not time spent. A
`SendMessage` is an instant, like a git commit or a terminal command: no
interval, but enough to say what that moment was. Nothing else covered
communication — chrome cannot see it,
and knowledgeC only knows "Messages was in front".

⚠️ **Only allowlisted fields are kept.** Every record carries a 2–3 KB
NSKeyedArchiver blob (f8) holding the whole serialized intent: the contact's
name, conversation ids, the phone number in several spellings, and what looks
like message content. f12 is the other party's number.

The judgement matches media's f8: knowing "you sent a message" already says
what you were doing; knowing to whom and what is another order of
sensitivity. **That is also why this source has no byte-level fixture**
— scrubbing a real file is whack-a-mole; see tests/test_intent.py.

The format is described in umbra/segb.py.
"""

import os

from .. import segb

SOURCE = "intent"
PLATFORM = "darwin"
REQUIRES = ["fda"]

# This source **folds raw records into intervals**, so changing the algorithm changes
# the rows the same raw data produces. Import deletes the old rows for the span
# before writing — see db.replace_range and tests/test_resegment.py.
SEGMENTED = True

BIOME = os.path.expanduser("~/Library/Biome/streams/restricted")
STREAM = "App.Intent"

# Protobuf fields (reverse-engineered 2026-08-15). Another numbering again —
# every Biome stream differs.
F_TIME = 1         # Apple absolute seconds (double)
F_BUNDLE = 2       # the app that started it
F_CLASS = 4        # INSendMessageIntent / INStartCallIntent
F_VERB = 5         # SendMessage / StartCall
F_BLOB = 8         # NSKeyedArchiver serialization — **deliberately not kept**
F_HANDLE = 12      # the other party's phone number — **deliberately not kept**

# Allowlist. New fields stay out of the payload by default: when Apple adds
# something, the default is "not kept", not "quietly stored one more thing".
KEEP_FIELDS = (F_TIME, F_BUNDLE, F_CLASS, F_VERB)


def _is_intent_record(rec: dict) -> bool:
    if not segb.in_apple_range(rec.get(F_TIME, [None])[0]):
        return False
    v = rec.get(F_BUNDLE, [None])[0]
    return v is None or isinstance(v, bytes)


def stream_dirs() -> list[str]:
    """Both local and remote.

    Unlike iphone and media, which take only remote because the Mac has other
    sources for itself. Intents are different: an iMessage sent on the Mac and
    one sent on the iPhone are both invisible to every other source, so both
    are kept.
    """
    out = []
    root = os.path.join(BIOME, STREAM)
    for side in ("local", "remote"):
        d = os.path.join(root, side)
        # subdirs raises PermissionError — "cannot read" must not look like
        # "nothing there". A missing directory returns [], which is normal
        # (there may be only one side).
        subs = [x for x in segb.subdirs(d) if os.path.basename(x) != "tombstone"]
        if subs:
            out += subs
        elif os.path.isdir(d):
            out.append(d)
    return out


def _text(rec: dict, field: int) -> str:
    v = rec.get(field, [b""])[0]
    if not isinstance(v, bytes):
        return ""
    try:
        return v.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def to_rows(records: list[dict], since_ms: int | None = None) -> list[tuple]:
    """To point events. No duration — sending a message is instantaneous."""
    rows = []
    for r in records:
        t = r.get(F_TIME, [None])[0]
        if not segb.in_apple_range(t):
            continue
        verb = _text(r, F_VERB) or _text(r, F_CLASS)
        if not verb:
            continue        # cannot say what was done; would only pollute the timeline
        ts = segb.apple_ms(t)
        if since_ms is not None and ts < since_ms:
            continue
        bundle = _text(r, F_BUNDLE)
        rows.append((
            f"intent:{bundle}:{verb}:{ts}", ts, None, SOURCE, "intent",
            {"bundle": bundle,
             "app": bundle.split(".")[-1] if bundle else "",
             "verb": verb,
             "intent": _text(r, F_CLASS)},
        ))
    return rows


def collect(since_ms: int | None = None) -> list[tuple]:
    records = []
    for d in stream_dirs():
        records += segb.read_dir(d, valid=_is_intent_record)
    return to_rows(records, since_ms)


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "intent", "kind": None,
          "origin": ("Biome·App.Intent", "iPhone send actions, no content")}]


def render(e) -> str | None:
    """Actions only, never content — that is how it is collected."""
    from .. import render as R
    return f"✉️ {R.app_name(e)} · {e.get('verb') or e.get('intent') or ''}".rstrip()


def classify(e) -> str | None:
    """No category on purpose. "Sent a message" is not an activity somebody
    spent a five-minute slot doing."""
    return None
