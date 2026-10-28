# ADR 0001 — `umbra serve`, and a built frontend that never touches the database

- **Status:** accepted
- **Decided:** 2026-08-26
- **Written down:** 2026-09-16 (retroactively — see "Why this is late")

## Why this is late

This decision was made in a working session and shaped several commits after it
— the Analyzer/Sink plan scopes `View` and `umbra serve` *out* with the note
"blocked on the frontend decision", which only makes sense if you know what the
decision was. It was never in `docs/`. Anyone reading the repo could see the
consequences and not the reasoning, which is the specific thing ADRs exist to
prevent. It is recorded here as decided, not re-litigated.

## Context

Umbra is a Python CLI that renders one self-contained HTML file. `viz.js` and
`viz.css` are hand-written, the page carries its whole dataset inline, and
adding a real interaction means editing a template string.

Three things push past that: search over months of history wants FTS5 and
incremental loading rather than a single inlined payload; plugins want to
contribute *views*, not just rows; and the page has grown past what one
hand-written file should carry.

The data layer is Python, and it is the part with the accumulated judgement in
it — sources, inference, taxonomy, claims. That is not moving.

## Decision

```
backend   Python (stdlib + sqlite3 + FTS5); `umbra serve` runs ThreadingHTTPServer
frontend  Vite + TS + TanStack (Query / Router / Virtual); build output ships in the wheel
boundary  one JSON Schema -> generated TS types + Python runtime validation
plugins   Source(py) · Provider(py) · Analyzer(py) + View(ts) · Sink(py)
export    `umbra export` -> self-contained HTML (feeds the deploy path)
```

**No JavaScript process ever touches the database.** The browser talks HTTP to
Python. Node is a *contributor build* dependency and never a user runtime one:
`pip install umbra` must not imply installing Node.

The JSON Schema at the boundary is the load-bearing part. It generates the TS
types and validates at the Python edge, so the two languages cannot drift
quietly, and the contract tests over it are what a future port to another
language would have to satisfy — which is how this decision stays reversible.

`umbra export` keeps the self-contained page alive for the deploy path.
Feature parity with `serve` is explicitly **not** promised; when they conflict,
`serve` wins and `export` degrades.

## Alternatives considered

**Next.js — rejected.** It needs a Node server in production. The data layer is
already Python, so this would mean either a second backend or a Node process
proxying to Python, and in both cases Node becomes a user runtime dependency.

**TanStack Start — rejected, same reason.** The appeal was staying inside one
TanStack story; the cost is identical.

**Keep the single generated file — rejected.** It cannot do incremental loading
or server-side search, and a plugin cannot contribute a view to it without
string-splicing into someone else's template.

## Consequences

- Plugins are dual-language: Python for anything touching data, TypeScript for
  anything drawing it. This is the main cost, and the main thing to watch — if
  it turns out to be the barrier to plugin contribution, revisit.
- `umbra/functions/`, `attrib.py`'s `node` subprocess shim and the Cloudflare KV
  dependency are deleted. (Done: see the `U8` commit — goal attribution is a
  Python Analyzer plugin now, and Umbra no longer needs a Cloudflare account.)
- The wheel gets bigger; it ships built assets.

## Still open

> **Settled 2026-09-24 by [ADR 0003](0003-react-and-tailwind.md):** React, and
> Tailwind v4 configured from the design tokens; the three-layer visual stack
> below is committed there.

Deliberately parked for the design-system round, and **not** settled by this ADR:

- Whether **React** is the explicit choice. It is currently only *implied* by
  shadcn, Recharts, Base UI and TanStack.
- Whether to adopt **Tailwind**. Choosing shadcn charts means choosing Tailwind,
  and that should not happen as a side effect of picking a chart library.

The proposed three-layer visual stack, also not yet committed: signature
visualisations (the 24h bar, the lanes, the 288-slot strip) hand-written SVG
plus `d3-scale`; ordinary charts via shadcn/Recharts; interaction primitives via
Base UI — wrapped in a `@umbra/view-sdk` so a plugin never imports Recharts
directly and the palette stays uniform (see [ADR 0002](0002-derived-category-colours.md)).

## Revisit when

- A desktop app (Tauri, Electron) is on the table — the calculus changes.
- A View needs heavy interaction: drag-to-edit, realtime.
- Python's HTTP layer becomes a measured bottleneck rather than a suspected one.
- Community feedback shows dual-language is what stops people writing plugins.
