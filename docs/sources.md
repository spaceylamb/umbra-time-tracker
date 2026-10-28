# The built-in sources, and what each one taught us

`writing-a-source.md` covers how to add a source. This covers the thirteen that
ship: what each can and cannot see, and the traps that were found the hard way.
Most of them are pinned by a test; the point of writing them down is that the
next source will meet the same kind of trap.

## iPhone: the time after the laptop closes

The first seven sources all live on the Mac. Before this one, time on the phone
was a hole — some days had over 1,300 minutes of "can't tell", and most of that
was not idleness, it was a person on their phone.

Under one iCloud account, the iPhone's usage data syncs to the Mac's
`~/Library/Biome/`, in a format called **SEGB**. Apple does not document it;
`umbra/segb.py` is reverse-engineered. No cable, no jailbreak, no app — only
"Share Across Devices" turned on.

**The official API is a dead end.** Screen Time's `DeviceActivityReport` runs in
a sandbox that can neither reach the network nor write storage: the report can
be displayed, but the host app can never read the numbers. That is deliberate.
Meanwhile iCloud sync puts the same data, in the clear, on your own disk. **When
deciding whether data is reachable, don't only ask whether there is an API —
ask whether it lands somewhere you can read.**

Traps:

- **`App.InFocus` cannot see the screen lock.** An app left in the foreground
  with the phone in a pocket for three hours reads as "three hours of Gmail".
  A handful of sessions like that can make up a large share of the total.
  Sessions over 30 minutes are flagged `suspect`: **stored, but not counted
  towards coverage**. Deleting them is unrecoverable; counting them treats a
  hallucination as fact. Excluding them gives 3.3 h/day.
- Read only `remote/`. `local/` is this Mac, already covered by knowledgeC, and
  reading it would import everything twice.
- The tail index's offsets do not line up for records carrying a field-1 prefix,
  so the reader scans sequentially instead.
- Durations are capped at 6 hours. One missed "left" event merges everything
  between two sessions into tens of hours — not imprecise, wrong — and poisons
  segmentation for the whole timeline.
- Fixtures are cut from real files, never hand-assembled. Hand-made bytes
  describe a format that does not exist: the tests pass and production breaks.

## media: the one phone source that knows content

`iphone` can say "you were in the Books app" (the container). `media` can say
"you were listening to chapter IV of a particular audiobook" — **what you were
actually doing** — so it counts as tier A, alongside a Claude conversation or a
terminal command.

⚠️ **The raw record carries the sentence being read aloud (f8). It is
deliberately not stored.** Title and chapter already say what you were doing;
the sentence-by-sentence text adds little and would turn the local page into an
excerpt of every book you have read. Fields are an **allowlist**, not a
blocklist: when Apple adds a field, the default is to ignore it rather than
quietly store one more thing.

Two required steps:

- **Folding.** Progress is logged every few seconds — tens of thousands of
  records. Unfolded, this one source would swamp the events table and turn its
  lane into a solid bar. Grouping by (app, title, chapter) and breaking on gaps
  over five minutes turns them into a few hundred sessions.
- **`suspect`.** Music left playing while you walk away reads as "four hours of
  listening" — the same problem as `iphone`, handled the same way.

`f14` is the output device (an AirPods MAC address, or "Speaker"). **AirPods
connected means you were really listening** — an unexpectedly useful signal.

## intent: communication, which nothing else covered

`chrome` cannot see it and `knowledgeC` only knows "Messages was in front".
`App.Intent` records **actions**: a message sent, a call made. Like a git commit
or a terminal command, it is an instant rather than an interval, but enough to
say what that moment was.

⚠️ **The other party's number (f12) and the serialized blob (f8) are not
stored.** The blob is 2–3 KB of `NSKeyedArchiver` output: the contact's name,
conversation ids, the phone number in several spellings, and what looks like
message content.

**This source has no byte-level fixture, deliberately.** Scrubbing a real file
is whack-a-mole: remove one spelling of a number and another survives — a number
written with spaces and parentheses slipped straight past a pattern for "+1 and
ten digits" — and clearing one blob leaves the others. The container format is
already covered by two real fixtures (iphone and media share the parser), so
this one only needs its field mapping tested, and a dict does that.

Both `local` and `remote` are read, unlike iphone and media: those take only
`remote` because the Mac has other sources for itself, but nothing else on the
Mac sees an iMessage sent from it.

## location: the one source with no holes

Every other source **samples** — present only when the browser is open, the Mac
is awake, something is playing. Location is different: **stays and trips
partition the whole day**. Every minute you are either somewhere or on your way
between two places. The only condition is that the phone is with you.

```
iPhone (Overland) ──POST──> your Worker ──> your database ──GET /export?since=──> Umbra
```

**GPS cannot be read on the Mac** (measured 2026-08-15): the stores under
`~/Library/Caches/com.apple.routined/` do not exist there, and Significant
Locations stay on the phone. So it has to be collected.

Three kinds, at different levels of knowledge:

| kind | tier | why |
|---|---|---|
| `transit` | **A** | "driving from home to work" is what you were doing |
| `stay` | **B** | you were at home; not what you did there |
| `gap` | not counted | the data stopped; you could be anywhere |

⚠️ **Raw coordinates are not stored.** A named place is stored by its name; an
unnamed one by an anonymous id (`p3f9a2c1b8`). You can still see "the same
unnamed place, three times", but the page never shows where you went. The raw
coordinates stay in your own database and `places.json`.

**Reverse geocoding does not answer "what is this place".** It knows what is
there, not what it means to you — your home comes back as a street address,
never as "home". So you **name a place once and it is matched from then on**:

```bash
python3 scripts/places.py list      # unnamed clusters, by total time spent
python3 scripts/places.py add Home 37.3318 -122.0305
python3 scripts/places.py geocode   # looks up unnamed clusters only, to jog your memory
```

`geocode` uses OSM Nominatim (free, no key, one request a second). **Home and
work are never looked up** — they are the most sensitive, and you recognise
them anyway.

The `motion` field (driving / walking / cycling) comes from the iPhone's motion
coprocessor. **It is not guessed from speed.**

## plaud: the one source that hears life away from the screen

Transcripts from a wearable recorder (PLAUD NotePin S). Every other source
answers "what did you do at a screen". Meetings, meals, walks and conversations
are invisible to them — and those are often the densest parts of a day.

```bash
npm i -g @plaud-ai/cli && plaud login      # authorise once in the browser
python3 -m umbra import --only plaud
```

It uses the **official** third-party OAuth API, the same one as `@plaud-ai/cli`
and the official MCP. Umbra reads and writes the CLI's own
`~/.plaud/tokens.json` rather than keeping a copy: a refresh may return a **new**
refresh token, and two holders refreshing independently invalidate each other —
which shows up as "worked for a few days, then wants authorising again". Umbra
renews the token itself; re-authorising is still `plaud login`.

**Don't expect it to fill the timeline.** In one measured 56-minute recording,
1.6 minutes were speech (3%). This source gives sparse points with a very high
signal, not coverage.

Traps:

- **`start_at` and `created_at` are UTC, with no time-zone suffix**
  (`2026-08-09T15:05:50`). A bare `fromisoformat()` gives a naive datetime, read
  as local time, off by hours, with no error. Confirmed the other way round:
  assuming local time puts 9 of 41 recordings "synced before they finished
  recording", which is impossible.
- **Segment offsets are wall-clock milliseconds, and silence takes up room.**
  That 56-minute recording has a 3,270-second gap; the second sentence's offset
  is 3,327,290, not 56,690. Absolute time is `start_at + offset`.
- **Incremental import cannot be windowed on recording time.** Measured upload
  delay: median **40 hours**, P90 240 hours, maximum **362 hours**; 16 of 41
  recordings arrived later than `OVERLAP_MS` (72 hours). Arrival order and event
  order are unrelated, so the full list is fetched and diffed against imported
  `file_id`s, and the gate sits on fetching details (the list is a few requests;
  details are one per file).
- **Transcription is not real-time.** The chain is record → sync → transcribe in
  the cloud; the device itself does no AI. One file holds at most five hours, and
  a full day splits into several. Transcription is manual by default; automating
  it means turning on **AutoFlow** in the app.
- **Transcription minutes are billed on audio length**, whether or not audio is
  downloaded. Wearing it all day (about 900 minutes) needs the unlimited plan.

Umbra **takes only the transcript and never downloads audio** — the
`presigned_url` field is never read, and raw audio never lands on your disk.

## WHOOP: authorise once

The first six sources all describe what you did to a machine; none knows **what
state you were in**. "Four hours of debugging with no progress" after five hours
of sleep and after eight are different events that look identical in an event
stream.

```bash
umbra auth whoop          # one click in the browser; the token is saved
umbra import --only whoop
```

`WHOOP_CLIENT_ID` and `WHOOP_CLIENT_SECRET` go in your env file (see `[env]` in
`config.example.toml`). The token — which refreshes and is rewritten by the
program — is kept separately as `whoop_token.json` in Umbra's data directory,
mode 600. In the env file it would collide with your own edits; in `events.db`
it would break that table's append-only rule. The reasoning is in
`umbra/whoop_auth.py`.

⚠️ **An expired authorisation is the only error that stops a whole import**;
every other source fails on its own. Chosen because "a scheduled job failing
silently for weeks" costs far more than "one run skipped".

Traps, each pinned by a test:

- **The CDN in front of WHOOP blocks `Python-urllib/*` by User-Agent**, with
  403 and `error code: 1010`. The request never reaches the application, so
  everything the error seems to say about OAuth, signatures or authorization
  codes is misdirection, and it can eat an afternoon. Fixed by `USER_AGENT` in
  `net.py`, which every outbound call shares.
- The `offline` scope **is not a checkbox in the dashboard**; it is a request
  parameter, and it must be sent again on refresh, or the response may stop
  including a refresh token.
- A refresh response **may not** carry a new refresh token. Overwriting blindly
  stores `None` — "worked for a few days, then wants authorising again".
- `state` must be at least 8 characters; shorter, and WHOOP redirects with
  `invalid_state` (undocumented).
- The request parameter is `nextToken`; the response field is `next_token`.
- Recovery records have **no `id` and no `start`**: the key is `cycle_id` and the
  time is `created_at`. Copying another endpoint's field names silently loses
  the whole type.
- `score` can be `null` (PENDING_SCORE / UNSCORABLE), and those records cluster
  in the last 24 hours — exactly the ones each import is most likely to meet.

## knowledgeC: run it from your own terminal

It is protected by TCC, and the permission is granted **to the host GUI app**.
Anything running under a daemon (a parent process of launchd) can never get it.

```bash
python3 -m umbra doctor          # first: who is the host, and can it read?
python3 -m umbra import --only knowledgec
```

⚠️ **Grant it to the right app.** TCC recognises an executable, not the idea of
"the terminal". Using iTerm2 means granting iTerm.app; granting Terminal.app does
nothing. iTerm may be installed in `~/Applications/` rather than
`/Applications/` — pick the right one in the Full Disk Access list.

⚠️ **After granting, quit the app completely (Cmd-Q) and reopen it.** Closing
the window is not enough; a running process does not pick up a new grant.
`doctor` tells you whether it worked.

## Traps that belong to no single source

- **Chrome's History database is locked while Chrome runs.** Copy it together
  with its `-wal` file before reading, or the most recent visits are missing.
- **`visit_duration` is not attention.** It is how long the tab lived; a
  background tab accumulates hours (17,942 s has been seen). Real foreground time
  comes only from `knowledgeC`.
- **`type == "user"` is not "a person typed this".** There are `isMeta` (skill
  documents), `isCompactSummary` (compaction summaries), and orchestration
  instructions dressed as user messages, clean at the field level and only
  recognisable by pattern.
- **But don't drop those whole.** Injected templates often **wrap a real request**:
  a 2,000-character worktree preamble followed by the actual task, a
  knowledge-base assistant template ending in the person's real question.
  Dropping them whole once lost 27% of real content (2,425 → 3,089 once fixed).
  `prompts.clean()` extracts by **marker**, not position — templates change
  length and shape, and slicing by position does not survive that.
- **Worktree directory names are uuids.** The project name has to be found
  further up the path; see `claude_code.project_of()`.
- **The python.org framework build ships without CA roots**, so every https
  request fails `CERTIFICATE_VERIFY_FAILED`. `net.py` points at certifi when it
  is available.
- **`dur_ms` means different things in different sources.** In knowledgeC it is
  real foreground time; in Chrome it is tab lifetime. Treating them alike
  stretches the timeline (one event once spanned 81 minutes). Each source
  declares whether its durations are trustworthy (`DURATION`).
- **Don't filter short segments by time span.** A Chrome visit is a point
  event with zero span, so filtering by span deletes whole stretches of fast
  cross-site jumping — which is exactly what research looks like. Measured: a
  two-hour window cut from 534 events to 4 segments. Short segments are **merged
  into a neighbour** (`segment._absorb_short`), not deleted.
- **PyObjC registers every method of an NSObject subclass as an Objective-C
  selector**, guessing the argument count from the name (`emit` → selector
  `emit`, no arguments), and import fails with `BadPrototypeError`. Plain Python
  helpers need `@objc.python_method`; only names ending in `_` are real callbacks.
