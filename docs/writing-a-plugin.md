# Writing a plugin

A source says where data comes from. A **plugin** says what is made of it and
where the result goes. There are two interfaces and a plugin may implement
either, both, or — for a plugin that only draws — neither.

|              | it does                                                            | you write             |
| ------------ | ------------------------------------------------------------------ | --------------------- |
| **Analyzer** | reads a layer of your timeline, asks a model about it, writes rows | a prompt and a parser |
| **Sink**     | sends something out of Umbra, and optionally reads an answer back  | one function          |

A plugin is one Python file in `~/.config/umbra/plugins/<name>.py`, or a
directory `~/.config/umbra/plugins/<name>/` with an `__init__.py`. The filename
is the plugin name. Use a directory when the plugin has enough in it to want the
room — a sink is often twenty lines and a file; a lane with a prompt, a parser
and its own filters is a directory.

The plugins Umbra ships — `goals`, `claims`, `actions`, `staleness`, `review` — load through this
exact path. If it does not work for you it does not work for them either, which
is the point of them living in `umbra/plugins/` rather than in an examples
folder.

## An Analyzer

```python
"""How the day felt, from what was typed."""

INPUT  = "events:zsh"       # what to read
WINDOW = 30                 # minutes per question
PROMPT = "Rate the mood of this stretch.\n{text}"


def parse(items, window):
    """The model's JSON array -> the rows to store."""
    return [{"a": i["a"], "b": i["b"], "mood": i["mood"]} for i in items]
```

That is a complete Analyzer. Drop it in, run `umbra sync`, and it has rows.

### What the core does for you

Windowing, rendering, the model call, concurrency, storage, and — the one that
matters most — **incrementality**. A window whose input is byte-for-byte
identical to last round is not sent again. Re-running an unchanged day costs
nothing.

This is deliberately not yours to implement. Getting it wrong does not raise an
error; it re-calls the model every round and bills you, quietly, forever.

### Declarations

|                                           |                      |                                                                           |
| ----------------------------------------- | -------------------- | ------------------------------------------------------------------------- |
| `INPUT`                                   | required             | `"events:<source>"`, `"spans"`, or `"day_events"`                         |
| `WINDOW`                                  | default `30`         | minutes per window, or `"day"`                                            |
| `PROMPT`                                  | required             | a format string, or a callable returning one                              |
| `parse(items, window)`                    | required in practice | the model's array → your rows                                             |
| `params(con, day, window)`                | optional             | prompt inputs the rendered window cannot supply; `None` means no question |
| `CONTEXT_MIN`                             | default `0`          | minutes of context shown either side                                      |
| `render_window(events, a, b, lo_ms, ctx)` | optional             | replaces the default rendering                                            |
| `KEY`                                     | optional             | the field that identifies a finding; makes output accumulate              |
| `TABLES`                                  | optional             | your own schema, if you truly need one                                    |
| `sync(con, days, log)`                    | optional             | what a scheduled round does, when it is more than judging `days`          |

**`INPUT`** picks which layer you read:

* `events:plaud` — raw records from one source. Substitute any source name.

* `spans` — the model's first-pass activity intervals for the day.

* `day_events` — the second-pass calendar events.

The last two are Umbra's own output rather than a source's records, so the core
renders them itself — start, end, category, and what was happening, including
anything that was going on in parallel. You get text without writing a renderer.

An unrecognised value is reported as a load error, not skipped silently: a typo
is a plugin trying to participate and failing, which wants telling.

**`WINDOW`** is a number of minutes that divides 1440 evenly, or `"day"` for one
window over the whole day. A day is the largest slice on purpose. Anything wider
would let one part of a period invalidate another's cache — with a week-wide key,
Friday's new data re-judges Monday, so a number you read as 11.3 hours on
Wednesday becomes 10.8 on Friday with nothing having changed.

A width that does not divide a day is refused for a quieter reason: the trailing
partial window would be judged against less input than every other window, and
nothing downstream would say so.

**`PROMPT`** is `str.format`-ed with the placeholders below. It may also be a
callable returning the string, for a prompt chosen at run time — Umbra's claims
lane picks its wording by configured language, and a module-level string would
pin whichever was set when the process first loaded.

Placeholders:

* `{text}` — the rendered window, which is what the model actually reads

* `{day}` — `2026-09-09`

* `{a}` `{b}` — the window bounds, in minutes from midnight

* `{today}` — the wall-clock date, for telling a model its knowledge has a
  cutoff. It is **not** part of the cache key: it changes daily, and folding it
  in would re-run every window of every day forever for something ambient rather
  than evidence.

* anything your `params` returned

**`parse(items, window)`** receives the model's array, already dug out of
whatever it wrapped it in — fences, a preamble, an object with the array inside.
Return a list of dicts. `a` and `b` name the stretch each row is *about* and
default to the window; everything else is yours and is stored as JSON.

`window` is `{"a", "b", "text", "params"}` — the question that was actually
asked, and whatever your `params` supplied. You need it to judge rather than
merely reshape: Umbra's claims lane uses the text to drop a quote the model
attributed to this window that in fact appears in the next one, and its tasks
lane uses params to drop a proposal that merely echoes something dictated
nearby.

If the model's reply cannot be read at all, the core raises rather than handing
you an empty list — "the model said nothing happened" and "we failed to read the
model" are different facts, and only one of them is an answer.

**`params(con, day, window)`** supplies prompt inputs the rendered window cannot.
It is asked once per window, and `window` is `{"a", "b", "events", "lo_ms"}` — a
day-shaped analyzer ignores it and answers once because it has one window.

```python
WINDOW = "day"

def params(con, day, window):
    return {"goals": load_goals_for(con, day)}
```

Whatever it returns is folded into the cache key. Without that, editing a goal
would leave the cache valid and the page would keep showing numbers computed
against text you had already rewritten.

**Return** **`None`** **to say there is no question here** and the window is skipped
without a call. It is not the same as `{}`, which means "no parameters this
window". Umbra's goals lane uses it for a day whose week has no goals: there is
plenty of input, and asking which of nothing a stretch advanced is paying for an
empty answer. Nothing is recorded either — a day that was never judged must not
read later as a day where the model found nothing.

**`CONTEXT_MIN`** shows surrounding minutes so the model can see what led up to
the window, separated from the stretch it is judging. Zero when each answer must
stand on its own.

**`render_window`** replaces the default entirely. The default renders each
event through its own source's `render()` — the same code that builds the digest
for inference — and adds the separator between target and context. Override it
when you need something that cannot express, such as merging two recordings of
one sentence.

### Where your rows go

Into `analyzer_rows`, keyed by your plugin name. Read them back with:

```python
from umbra import analyzer
rows = analyzer.load_rows(con, "mood", "2026-09-09")
```

You do not write SQL and you do not own a table. Uninstalling is
`analyzer.drop(con, "mood")`, not a hunt through the filesystem.

### A judgement, or findings

By default your output is a **judgement** of each window: judge the window again
and its rows are replaced. That is right for anything that is "the current
answer" — Umbra's goals lane is one.

Declare `KEY` when what you produce are **findings** instead — a task heard, a
claim made — things that, once found, have something happen to them:

```python
KEY = "quote"        # the field that tells one finding from another, per day
```

Findings accumulate. Judging a window again after a late recording adds what is
new and touches nothing already kept, so a task that has gone out cannot vanish
or come back reworded and go out twice, and a finding someone dismissed stays
dismissed. A row with no value for `KEY` is dropped. A row may carry `state` if
it is found already held back.

```python
analyzer.load_items(con, "mood", day, state="new")
analyzer.update_item(con, "mood", item_id, state="sent", meta={"receipt": r},
                     expect="new")      # refused, not raised, if it moved on
```

What was found is written once; `state` and `meta` are yours to move along.
What was *sent* belongs to the sink, in `sink_dispatch`.

If you draw those rows anywhere, ask first whether they still match their input:

```python
analyzer.current(con, "mood", mod, "2026-09-09", model)   # -> bool
```

True means the stored answer was computed against exactly the data and
parameters that are true now. It covers both ways an answer goes stale — new
events in the window, and a changed parameter the window does not contain — and
it costs no model call, so a view can ask it on every draw. A day with nothing
to ask about is current: it has no outstanding question, and showing it as
pending would leave a warning on screen that nothing could ever clear.

If you genuinely need your own schema, declare `TABLES` with the DDL. It is a
declaration rather than DDL you run at import time so that "this plugin owns a
table" is readable by someone opening the file. The core never writes to it.
Create it before first use with `analyzer.ensure_tables(con, TABLES)`
(idempotent); `review` does this at the top of everything that touches its
tables.

### Taking part in a round

Every enabled analyzer is one step of `umbra sync` and `umbra voice`, named after
it and isolated from the others: yours failing costs only yours, and the page is
still built. A plugin that fails to import is a failed step, not a skipped one.

By default your round judges the days the scheduler hands over — the days that
just received data. Declare `sync(con, days, log)` when a round is more than
that. The bundled lanes do: `actions` and `claims` send what they found and
collect what came back, and `goals` judges its own two weeks whatever days
arrive, because a recording can land late and a priority set this morning
applies to Monday too.

```python
def sync(con, days, log):
    from umbra import analyzer
    import sys
    analyzer.run_days(con, "mood", sys.modules[__name__], days, log=log)
    log("  mood: also told my journal")
```

It is `sync` and not `round` because a module-level `round` shadows the builtin
for your whole plugin, and the first `round(x, 3)` in it then fails at run time.

A plugin that reads no layer of the timeline can still take part: declare
`sync` and no `INPUT`, and it is a step of every round with a switch of its own,
like any Analyzer. The bundled `staleness` is one — each round it checks every
source's newest record and sends a card for a source that stopped — and so is
`review`, which counts sixteen weeks at most once a day and sends a card a month.
`umbra plugins` lists it as a `check`; a plugin with neither `INPUT` nor `sync`
is a Sink and has no switch.

Your plugin runs on the model inference uses unless `[plugins.<name>] model`
names another (`analyzer.model_for(name)`). To have a model **search the web**
before it answers, ask `providers.lookup(model)` instead of
`providers.resolve(model)`: it returns `ask(prompt) -> {"text", "sources",
"searches"}`, where the sources come from the provider's own record of the
search, never from the answer text. `gemini*`, `gpt*` and `claude*` can search;
a local model behind an OpenAI-compatible URL cannot, and `lookup` says so at
once. `claims` is the worked example.

### In the page and on the command line

The core knows nothing about any plugin by name — not even the bundled ones. It
reads what a plugin declares, and calls what it defines:

| declare                | what the core does with it                                     |
| ---------------------- | -------------------------------------------------------------- |
| `TITLE`, `WHAT`        | its name and one line in Settings › Plugins (else the module name and docstring) |
| `ORDER`                | where it sits in that list                                     |
| `ENABLED_BY_DEFAULT`   | on before anyone says otherwise; off if absent                 |
| `SENDS`, `SEND_WHAT`   | it is useful here and can also send a copy to a sink: Settings shows a second switch, "Also send `SEND_WHAT` to …" |
| `SEND_ONLY`            | what it makes is only ever sent (an alert, a monthly list): being on *is* sending, so there is no second switch. Give it a `blocked()` for when no sink is named |
| `SETTINGS`             | `{key: (kind, default, constraint, what)}` the page may change, as `plugins.<name>.<key>` (kind is `bool`, `int` or `enum`) |
| `LEGACY_SECTION`       | an old `[<name>]` section in config.toml means it was in use   |

| define                          | what the core does with it                            |
| ------------------------------- | ----------------------------------------------------- |
| `blocked(conf) -> str \| None`  | why it cannot run, in words. Settings shows it and keeps the switch off; a plugin that cannot do its job should not half-run |
| `settings_row(conf) -> dict`    | more for its own block in Settings                    |
| `said(con, date, conf) -> dict` | its part of the day's drawer; only enabled plugins are asked, so a switched-off one has no tab |
| `SAID_KIND`, `said_mark(con, id, body)` | what the drawer's ✓ / ✕ / Seen on one of its items does; raise `LookupError` for an item you do not have |
| `cli(args)`, `cli_args(parser)`, `CLI_NAME`, `CLI_HELP` | a command of its own: `umbra <CLI_NAME>`. A core command's name cannot be taken |

The page renders the bundled plugins' drawer tabs and Settings blocks; a plugin
of your own gets its switch, its `blocked` line and its command, and its drawer
part reaches the API (`/api/day/{date}/said`) for anything that reads it.

A test keeps the core honest about this: no module outside `umbra/plugins/`
may import a bundled plugin or reach into one by name.

## A Sink

```python
def send(item):
    """What leaves Umbra. Return a receipt, or None."""
    append_to_my_notes(item["title"], item["body"])
    return None
```

That is a complete Sink. Two more functions are available and both are optional:

```python
def poll(receipt):
    """None while it is still working; the result once it has settled."""

def feedback():
    """Everything that changed, in one request: [{"receipt": ..., "result": ...}]"""

def render(result):
    """The result, as text a person reads."""
```

`poll` asks about one item; `feedback` asks what changed at all. Which one your
destination offers is a fact about it rather than a choice — a task queue can
answer "what happened to task X", while somewhere a person taps buttons reports
in bulk and has no per-item endpoint. Implement whichever fits, or neither.

A destination that cannot answer simply does not implement `poll`. That is the
whole difference between a Slack webhook and a backend with an agent behind it —
one interface, with an optional half, rather than two interfaces.

An `item` carries what any destination needs, in terms no destination owns:

|               |                                                     |
| ------------- | --------------------------------------------------- |
| `title`       | what it is called                                   |
| `body`        | the content                                         |
| `key`         | an idempotency key                                  |
| `visible`     | should a person see this, or is it a background job |
| `interactive` | should it wait for a person to act                  |

How those map is yours. `umbra/plugins/butler.py` turns `visible` into one set of
flags and its absence into another; a Slack sink would turn it into a channel, or
ignore it.

Two rules the built-ins follow and yours should:

1. **A failed send must raise.** Reporting a non-delivery as a delivery means the
   item never goes out and nothing ever says so.
2. **The item is data, never a command line.** It is model output and, often,
   somebody's transcribed words. The `command` sink passes it through
   environment variables and interpolates none of it.

Umbra ships `command` and `webhook` as configured sinks, and `butler` as a
worked example of one with a round trip.

## Configuration

```toml
[plugins.mood]
enabled = true              # installed is not enabled

[plugins.actions]
enabled = true
sink    = "todo"            # where output goes
send    = true              # and permission to actually send it

[sinks.todo]
kind    = "command"
command = "printf '%s\\n' \"$UMBRA_BODY\" >> ~/todo.md"

[sinks.team]
kind = "webhook"
url  = "https://hooks.example.com/..."
```

Three states, not two: **installed**, **enabled**, and **allowed to send**.
Naming a destination to see what would go out must not start it going out.
Analysis is local and costs cents; sending creates records in somebody else's
system.

A plugin is off unless it declares `ENABLED_BY_DEFAULT`. `goals` is the one
bundled plugin that does, because it only reads and draws. (`actions` and
`claims` declare `LEGACY_SECTION`: they also stay on for anyone whose
config.toml still has an `[actions]` or `[claims]` section from before they
were plugins — upgrading must not quietly stop a lane that was running.
`[plugins.<name>] enabled` always wins.)

A plugin in your own directory overrides a bundled one of the same name — all of
it, not just the parts you changed. Whoever wrote the file in their own directory
meant it.

## Checking it works

```bash
python3 -m umbra plugins                        # installed, on, and where each sends
python3 -m umbra plugins run mood --day 2026-09-09
python3 -m umbra sync                           # a whole round, every enabled plugin
```

`umbra plugins` prints a bad declaration or a load error instead of leaving the
plugin out, and `plugins run` exits non-zero when any call failed.

The counts a run reports are the diagnostic:

```
windows 48 · called 2 · reused 0 · empty 46 · rows 2      first run
windows 48 · called 0 · reused 2 · empty 46 · rows 0      second run, unchanged
```

`called 0` on an unchanged second run is the signal that incrementality is
working. If it is not zero, the input is changing between rounds — usually a
timestamp or a set being rendered in a different order.

`empty` counts windows with nothing in them, which are never sent. Asking a model
to judge a stretch it has no records for is paying it to guess.

## Three disciplines

These are the same three the built-in plugins follow, and the reasons are in
their source.

1. **Never invent a position in time.** A daily total has no clock time and a
   score is not an interval. Return nothing rather than placing them somewhere —
   a rendered daily figure once claimed reading at midnight, every single day,
   and the model believed it.
2. **Never disguise a failure as a result.** An empty answer means the model was
   asked and found nothing; a failed call means it must be asked again. Storing
   the second as the first means that stretch is never looked at again.
3. **Two switches, not one.** Analysis and sending are separate decisions,
   because only one of them has effects you cannot take back.
