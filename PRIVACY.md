# Privacy design

Umbra's job is to record your life. That makes its privacy posture the
most important part of the design. These are the rules the codebase
follows; they are enforced at import time, not cleanup time.

## Where data lives

- `~/Library/Application Support/Umbra/events.db` (or the platform
  equivalent) — the only store. Never inside the repo, never synced to
  iCloud paths (Desktop/Documents), never committed.
- The generated HTML timeline embeds your raw history verbatim. It is
  written next to the database, **outside any repo checkout**, on
  purpose: a `.gitignore` can be bypassed by `git add -f`; a file that
  physically is not under the repo cannot be committed by accident.
- Config and custom sources live in `~/.config/umbra/`; secrets in an
  env file (`~/.config/umbra/env` or a file you point to). Neither is
  read by the repo's code at import time into the store.

## What is never stored

- **Raw GPS coordinates.** Stays are stored as your own place names
  ("home", "gym") or anonymous cluster ids (`p3f9a2c1b8`). Coordinates
  stay on your relay server and in your local places file.
- **Phone numbers and message contents.** The iPhone intent source
  keeps "sent a message / made a call" and drops the counterpart and
  the serialized payload that contains contact names and numbers.
- **Spoken-sentence text from audiobook players** (the currently-read
  sentence field) — title and chapter are enough.
- **Audio.** The voice source reads transcripts only; the audio
  download URL field is never accessed.
- **Password managers and auth flows.** Dropped entirely at import:
  Dashlane/1Password/…, OAuth pages, "sign in" titles.

## What is redacted

Finance, tax and medical browsing keep the **domain only** — "was on
the bank's site 40 minutes" survives, page titles do not.

Fields are collected by **allowlist**, not blocklist: when a source
adds a new field upstream, the default is that it is *not* stored.

## Model calls

Inference sends compressed digests of your day to whatever model you
configure. That is the only way data leaves the machine, it is opt-in,
and the exact text sent is stored locally (`digests` table) so you can
audit after the fact what the model saw. Use a local model (Ollama /
vLLM via the openai provider) for a fully offline pipeline.

## For contributors

- Test fixtures must not contain real personal data. The binary Biome
  fixtures in `tests/fixtures/` are cut from real files with device
  identifiers rewritten in place; keep it that way.
- No default URLs pointing at anyone's personal infrastructure — a
  wrong default sends a stranger's data to someone else's server.
