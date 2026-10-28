# Writing a data source

A source is one Python file in `~/.config/umbra/sources/<name>.py`;
the filename is the source name. Same-named files override built-ins
(a feature: drop in your own `chrome.py` to change its filtering
without touching the repo).

Minimal working source:

```python
def collect(since_ms=None):
    """Return event rows. since_ms is the incremental cursor (UTC ms); None = full pull."""
    return [
        # (uid,       ts_ms,         dur_ms, source,  kind,   payload_dict)
        ("mysrc:42", 1755600000000,  None,   "mysrc", "note", {"text": "hi"}),
    ]
```

Field contract:

| field | meaning |
|---|---|
| `uid` | `source:natural-key`. **The idempotency anchor** — re-imports dedupe on it; must be stable |
| `ts_ms` | UTC epoch ms. **Never local time** — a timezone slip shifts the whole timeline by hours |
| `dur_ms` | real duration if the source has one (drawn as a span); `None` for point events |
| `source` | name written to the store; usually equals the filename |
| `kind` | record type within the source (`visit` / `command` / `speech`…) |
| `payload` | dict, anything — but see the privacy rule below |

Optional module-level declarations:

```python
SOURCE = "mysrc"            # only if store name differs from filename
PLATFORM = "darwin"         # declare when the data only exists on one OS
REQUIRES = [                # checked one by one by `umbra sources` / `umbra setup`
    "env:MYSRC_TOKEN",      # environment variable
    "file:~/.mysrc/db",     # file exists
    "cli:mysrc",            # command on PATH
    "fda",                  # Full Disk Access (macOS; cannot be probed, shown as a hint)
]
SEGMENTED = True            # collect() aggregates raw records into spans whose algorithm
                            # may change → re-imports delete-and-rewrite the window
REWRITE = True              # rows returned each run should overwrite old ones (e.g. a
                            # transcript completed later); never set on full-dump sources
def check():                # custom health check, overrides per-item REQUIRES probing
    return [("my check", True, "")]
```

## Saying when it has stopped

```python
STALE_DAYS = 2              # something arrives every normal day; this many quiet
                            # days means it has stopped
STALE_HINT = "Is the phone app still uploading? Has its sign-in expired?"
```

Declare these only if the source should have something every day. The
`staleness` plugin watches the newest record of every source that does, and
sends one card per outage with your hint in it — stops almost always happen at
the far end (a device, a sign-in, a permission), so the hint should point
there. Leave them out for a source that is quiet for days for no reason: an
alert that fires on an ordinary week teaches people to ignore it.

## Being visible downstream

Collecting is only half of connecting a source. Four things decide whether it is
ever seen again, and each is one optional declaration. Skip any of them and the
source still imports and still shows its raw records in the drill-down — it just
does not take part in that layer. Absence is the deliberate default: a guessed
answer would be fed to the model as fact.

```python
def render(event) -> str | None:
    """The one line the model reads.

    Return None for a record with no position in time — a daily total, a score,
    a gap. This is the single most important thing on this page: whatever you
    return here is what the model judges from, and returning nothing means the
    source is invisible to it while still looking present everywhere else.
    """
    return f"🏃 {event.get('sport')} {event.get('km')}km"


def classify(event) -> str | None:
    """The taxonomy category this event evidences, for the offline rule layer.

    None is a legitimate answer. "Somebody was at the keyboard" is evidence
    without being an activity anyone spent a slot doing.
    """
    return "workout"


def label(cat, event) -> str | None:
    """Optional. Your own short label for a slot; None falls back to the
    generic label for the category."""
    return f"Ran {event.get('km')}km"


LANES = [{"key": "mysrc", "kind": None,
          "origin": ("My Device", "how the data was actually captured")}]
# A list because one source can carry several kinds of time that must not be
# drawn together — WHOOP is one source and two lanes, sleep and workouts, and
# merging them would draw "lay down for 8 hours" and "trained for 40 minutes"
# as the same thing. `kind` narrows a lane to one event kind; None means all.
# The short half of `origin` must say who recorded it, not restate the lane name.

DURATION = "trusted"        # dur_ms is real attention time. Default is "point".
# Only claim this if your duration means someone was actually engaged. Chrome's
# visit_duration is tab lifetime and reaches hours for a background tab (one real
# record: 17,942s); treated as attention it blows the timeline apart.
```

Verify a source is fully wired:

```bash
python3 -m umbra import --only mysrc
python3 -m umbra blocks $(date +%F)     # does it get a category?
python3 -m umbra viz --no-open          # does it get a lane?
```

Three disciplines (every built-in source follows them; the scars are
collected in [sources.md](sources.md), and in each importer's docstring):

1. **Overlap your increments.** Sources backfill and revise past
   records (recovery scores, transcripts). Pulling only "since last
   time" silently loses data.
2. **Flag suspicious durations, never delete them.** An app in the
   foreground for three hours may mean the person walked away — mark
   `suspect` and let the analysis layer decide; deleted data cannot be
   recovered.
3. **Filter privacy at import, not afterwards.** Collect fields by
   allowlist; whatever lands in the store ends up in the rendered HTML.

Verify:

```bash
python3 -m umbra sources
python3 -m umbra import --only mysrc
python3 -m umbra stats
```
