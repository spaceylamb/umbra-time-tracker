# ADR 0003 — React and Tailwind, and the visual stack under them

- **Status:** accepted
- **Decided:** 2026-09-24
- **Settles:** the two questions [ADR 0001](0001-serve-and-a-built-frontend.md) left open

## Context

ADR 0001 chose Vite + TypeScript + TanStack for the frontend and parked two
questions for the design-system round: whether React is the explicit choice,
and whether to adopt Tailwind. It parked them on purpose — choosing shadcn
charts means choosing Tailwind, and that should not happen as a side effect of
picking a chart library.

The design-system round is now done. It produced a token set, a component
spec and the screens those components compose.
The two questions can be answered against something concrete instead of in the
abstract.

## Decision

**React is the explicit choice.** It was already implied three times over:
TanStack Query/Router/Virtual, Base UI and Recharts are all React-first. Picking
anything else would mean giving up those, not swapping a renderer.

**Tailwind v4, configured from the design tokens.** The tokens were authored
in Tailwind v4's `@theme` shape from the start
(`--color-*`, `--text-*`, `--spacing-*`, `--radius-*`, …), so this is a
transcription, not a translation. One `theme.css` holds them; there is no
`tailwind.config.js`.

The visual stack, which ADR 0001 proposed but did not commit, is committed here:

```
signature views   24h bar, lanes, day strip, week grid — hand-written SVG + d3-scale
ordinary charts   shadcn charts (Recharts)
primitives        Base UI (dialog, popover, tabs, toggle, tooltip, …)
plugin surface    @umbra/view-sdk — a plugin never imports Recharts or Base UI directly
```

### Two rules that come with the tokens

1. **Category colours are not tokens.** They are computed by `umbra/palette.py`
   ([ADR 0002](0002-derived-category-colours.md)) and served with the data. The
   frontend sets them as CSS variables at runtime. Writing fixed
   `--color-cat-*` values into `theme.css` would fork the palette the moment a
   user adds a category.
2. **The shell is monochrome.** Tokens for ink, surface, sunken, border,
   hover/active and the two non-data colours (`--color-live`, `--color-alert`)
   are the whole shell palette. A category hue never means anything else.

Dark mode is a token swap under `[data-theme="dark"]`, defaulting to
`prefers-color-scheme`; components do not branch on theme.

## Alternatives considered

**Preact / Solid / Svelte — rejected.** Each would cost TanStack's React
bindings or Base UI or both, for a bundle saving that does not matter to a page
served from localhost.

**CSS Modules or vanilla-extract — rejected.** Both work, but the tokens already
exist in Tailwind's shape and shadcn charts assume Tailwind. Choosing another
styling system would mean maintaining a bridge to the one the chart layer
needs anyway.

## Consequences

- The contributor toolchain is **Bun** (package manager and script runner;
  chosen 2026-09-24). Users still need neither Bun nor Node — ADR 0001's rule
  holds, the built assets ship in the wheel.
- [docs/design.md](../design.md) is the component spec, including the button ladder
  (36 / 28, primary · secondary · tertiary · ghost, press collapses a 1px
  spread rather than scaling).
- A plugin's View inherits the tokens by using the SDK; it cannot reach for its
  own colours without going around it.

## Still open

Not settled here, and not blocking the first slice (desktop Day view):

- ~~**What the phone gets.**~~ **Decided 2026-09-28: the exported static
  page.** The phone reads a read-only export of the same React app, deployed
  to the person's own host, rather than reaching `umbra serve` over the
  network — which would mean opening a server that holds the whole raw
  history beyond localhost and adding auth to it, and would stop working away
  from home. Consequence: on the phone, everything that writes (settings,
  import, priorities) is shown but not actionable, and says it is done on the
  Mac.
- **Chinese UI.** `[ui] lang` exists; the page's design is English only.
