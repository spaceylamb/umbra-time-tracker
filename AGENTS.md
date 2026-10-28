# AGENTS.md

Guidance for coding agents (and the people driving them) working on Umbra.
Read this first; follow the links when a task touches that area.

## What Umbra is

A local-first time tracker. It pulls point events from many sources (browser,
shell, git, phone screen time, location, wearable recorder transcripts, WHOOP…)
into one SQLite file, then asks a model to reconstruct what the person was
actually doing, as auditable calendar events. Everything runs on the user's
machine; the only network traffic is the model calls they configure.

```
sources (umbra/importers/, ~/.config/umbra/sources/)
  └─→ events.db              append-only point events      umbra/db.py
        ├─→ 5-minute blocks  rule-based, offline           umbra/blocks.py, segment.py
        ├─→ digest → model → spans → calendar events        umbra/digest.py, infer.py
        ├─→ plugins: Analyzers read a layer, Sinks send out umbra/analyzer.py, sink.py, plugins/
        └─→ the page: Day / Week / Sources                 web/ (source) → umbra/web/ (built)
```

## Repository map

| path | what lives there |
|---|---|
| `umbra/__main__.py` | the `umbra` CLI: `sync`, `serve`, `sources`, `plugins`, `setup`, `export`… |
| `umbra/importers/` | the built-in sources, one file each |
| `umbra/sources.py`, `registry.py`, `render.py` | source discovery, health checks, how a source's records render into the digest |
| `umbra/digest.py`, `infer.py`, `inference.py` | the text the model reads, the model call, and the guesses it returns |
| `umbra/analyzer.py`, `sink.py` | the plugin machinery: windowing, caching, the model call, sending |
| `umbra/plugins/` | bundled plugins: `goals`, `claims`, `actions`, `staleness`, `review`, plus `butler` (reference Sink) |
| `umbra/privacy.py` | what is dropped or redacted at import time |
| `umbra/taxonomy.py`, `palette.py` | activity categories and their derived colours |
| `umbra/api.py`, `serve.py`, `viz.py`, `export.py` | the HTTP API, the local server, the page's data, the static export |
| `umbra/schema/api.schema.json` | the API contract; TypeScript types are generated from it |
| `web/` | the React + TypeScript page (Vite, Tailwind, TanStack) |
| `umbra/web/` | **the built page, committed** — this is what users run |
| `tests/` | `unittest`, offline, never touches a real database |
| `docs/` | the long-form guides (see below) |

## Commands

```bash
# Python: 3.11+, standard library only
python3 -m unittest discover -s tests -t . -q        # the whole suite; -t . matters
TZ=UTC python3 -m unittest discover -s tests -t . -q # run it in a second timezone too
python3 -m umbra --help

# The page (contributors use Bun)
bun install --cwd web
bun test --cwd web
bun run --cwd web build        # rebuilds umbra/web/ and stamps SOURCE_HASH

# Develop the page against synthetic data, never your real database
python3 -m umbra.devseed /tmp/umbra-dev
UMBRA_DATA_DIR=/tmp/umbra-dev UMBRA_CONFIG_DIR=/tmp/umbra-dev umbra serve --api-only &
bun run --cwd web dev
```

Run tests through `-t .`: `tests/__init__.py` points `UMBRA_DATA_DIR`,
`UMBRA_CONFIG_DIR` and `UMBRA_LOG_DIR` at a temporary directory, and only does
so when `tests` is imported as a package. Without it the suite reads the real
`~/.config/umbra/config.toml`.

CI (`.github/workflows/test.yml`) runs the suite on macOS and Ubuntu, Python
3.11 and 3.13, then installs from a wheel and smoke-tests the CLI.

## Rules that are easy to break

These are enforced by tests or by cost; an agent that does not know them will
break them while doing something reasonable.

1. **No runtime dependencies.** `dependencies = []` in `pyproject.toml` is a
   promise, held by `tests/test_packaging.py`. Use the standard library (the
   repo signs JWTs with `openssl` and parses protobuf by hand rather than add a
   package). The only optional extra is `collector` (pyobjc, macOS).
2. **Commit the built page.** Any change under `web/src` (or to the API schema)
   needs `bun run --cwd web build`, and the resulting `umbra/web/` committed in
   the same change. `tests/test_web_build.py` fails when the build is older than
   its sources.
3. **Prompt text is a cache key.** Every window sent to a model is hashed;
   unchanged input is not sent again. Anything rendered into a prompt (prompt
   wording, separators such as `TARGET_MARK`/`AFTER_MARK`, a source's
   `render()`) is part of that key. Changing it makes every existing user re-run
   their whole history through the model at their own expense. Do it on purpose,
   and say so in the PR. Never put volatile values (wall-clock time, unordered
   sets, dict order you do not control) into rendered input.
4. **Prompts come in pairs.** Most prompts exist as `PROMPT_EN` and `PROMPT_ZH`,
   chosen by `[ui] lang`. Change both, or explain why not. English is the
   default. Chinese elsewhere in the code is deliberate: regexes matching
   Chinese input, app display names in `apps.py`, Chinese speech in fixtures.
5. **Privacy is applied at import, by allowlist.** A source keeps only the
   fields it names. Never store raw GPS coordinates, phone numbers, message
   contents, audio, or anything from password managers and auth pages. Read
   [PRIVACY.md](PRIVACY.md) before touching an importer or `privacy.py`.
6. **Timestamps are UTC epoch milliseconds.** Local time exists only at the
   edges (`umbra/tz.py` decides which day a moment belongs to).
7. **Honesty over tidiness.** Keep these distinguishable everywhere, in data and
   on the page: no data vs not imported yet; the model said nothing vs we failed
   to read the model; an inferred guess vs a measured fact vs a known blind spot.
   A failure must never be stored or shown as an empty result.
8. **Tests are offline.** No network, no real database, no real config. Model
   calls and HTTP are faked. Fixtures are synthetic: no real names, places,
   coordinates or recordings.

## Extending Umbra

Both extension points load user code from `~/.config/umbra/`; the bundled ones
go through the exact same path, so they are the best examples.

**A data source** — one file in `~/.config/umbra/sources/<name>.py` with a
`collect(since_ms)` returning `(uid, ts_ms, dur_ms, source, kind, payload)`
rows. `uid` must be stable (it is the dedupe key). Guide:
[docs/writing-a-source.md](docs/writing-a-source.md); what each built-in sees
and the traps found the hard way: [docs/sources.md](docs/sources.md).

**A plugin** — `~/.config/umbra/plugins/<name>.py` or a directory with
`__init__.py`. An *Analyzer* declares `INPUT`, `WINDOW`, `PROMPT` and
`parse()`; the core owns windowing, rendering, the model call, concurrency,
storage and incrementality. A *Sink* implements `send(item)` and optionally
`poll` / `feedback` / `render`. Guide:
[docs/writing-a-plugin.md](docs/writing-a-plugin.md). The disciplines every
plugin follows:

- Never invent a position in time. A daily total has no clock time.
- Never disguise a failure as a result. A failed call raises; it is not `[]`.
- Two switches, not one: being enabled (analysis) and `send = true` (effects in
  someone else's system) are separate decisions.
- A Sink treats the item as data, never as a command line, and a failed send
  raises.

When a plugin "doesn't run", `python3 -m umbra plugins` prints load errors and
bad declarations; `umbra plugins run <name> --day YYYY-MM-DD` reports
`windows · called · reused · empty · rows`. `called` should be 0 on an unchanged
second run.

## The page

The page has a deliberate, documented design system. **Before changing anything
under `web/src`, read the relevant parts of these two files**, and keep to them
rather than to general UI habits:

| file | covers |
|---|---|
| [docs/design.md](docs/design.md) | principles, tokens (neutral ramp, semantic and category colour, type, space, elevation, motion), the four kinds of nothing, timeline primitives, components, the logomark, how to change the design |
| [docs/ux.md](docs/ux.md) | what the page is for, places and navigation, keyboard, Day / Week / Sources / Settings, states, the phone, how the page writes, accessibility, the pre-ship checklist |

The points most often gotten wrong:

- **Monochrome shell, colour only for data.** Selected, hovered and current use
  ink, outline or weight, never a hue. A hue that isn't data is a bug.
- **Tokens only.** Components use the semantic tokens in `web/src/theme.css`;
  never a ramp step, a literal hex, or a branch on theme. Dark mode swaps tokens
  and nothing else.
- **Never look more certain than the evidence.** Measured and guessed never
  share a visual; the four kinds of nothing (nothing happened, the model said
  nothing, no model has looked, nothing imported) never look alike; absence is
  drawn, never filled; a missing value is an em dash, never `0`.
- **Stillness by default.** New motion uses a duration token and an existing
  pattern. Nothing load-bearing lives behind a hover.
- **New things have a home first.** A new state is placed among the states in
  `ux.md`; a new shell colour gets a token with one stated meaning in both
  themes; a category needs no colour work, the palette derives it.
- **Check both themes and both widths** (`?theme=light|dark`, desktop and
  below 640 px), then work through "Before you ship a UI change" at the end of
  `ux.md`.
- If a change has to depart from these documents, update the document in the
  same change and say why. The code, then `design.md`, are the source of truth.

The browser only talks HTTP to Python, and the API contract is
`umbra/schema/api.schema.json`. Architecture decisions, with the options that
were rejected, are in [docs/decisions/](docs/decisions/).

## Style

- Match the surrounding code. Comments explain *why* — the failure a line
  prevents, the measurement behind a threshold — not what the code says.
- When a test guards against something non-obvious, its docstring says what
  would go wrong without it.
- Python is linted with ruff using the settings in `pyproject.toml`.
- Commit messages are plain English sentences describing the change from the
  user's side, without type prefixes.
- Everything in the repository is written in English.
