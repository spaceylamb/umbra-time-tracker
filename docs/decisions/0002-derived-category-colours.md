# ADR 0002 — Category colours are derived, not written down

- **Status:** accepted
- **Decided / implemented:** 2026-09-16
- **Code:** `umbra/palette.py`, `umbra/taxonomy.py`, `tests/test_palette.py`

## Context

`taxonomy._TABLE` held seventeen hand-picked hex values, and the fallback for a
category without one was a single grey. Anyone adding a category in
`config.toml` therefore got `#7a8494` — **a user-defined category was invisible
as a category**, which is the exact failure `taxonomy.py` was written to end
("a stretch of the colour bar that is permanently unidentifiable grey"),
arriving through the config file instead of through a hand-copied table.

Hexes chosen independently also collide. Every pair in the palette audit came
from exactly that — browsing against research, coding against at-a-place, sleep
against transit against social — and each hand-fix moved the collision somewhere
else, four rounds running. That is the signature of a set with more members than
the space holds, not of a badly chosen value.

Users declare categories. They do not declare colours, and should not have to.

## Decision

A category names a **family**; the family owns one number, its hue; the two
members of a family differ by a step.

```
colour(category, theme) = oklch( L[theme], C[theme], hue[family] ± step )
```

`L` is fixed per theme and `C` is capped, so no mark can be muddier, darker or
louder than another, the area rule stops being a per-colour judgement call, and
the two themes are one hue at two lightnesses rather than two tables that drift.

**Hue separates eight to twelve things** at the size the timeline draws them —
an 8px dot in the event list, a 6px sliver in the day bar. That is a property of
eyes, not of palettes, so the way past it is not a finer subdivision but a
second question:

- **hue answers *which family*** — asked of the whole bar, at a glance, without
  reading anything;
- **the step answers *which member*** — asked of one block, where its label
  already is.

Eight families of two is sixteen categories, none of which ever has to be told
apart from the other fifteen.

### Not `taxonomy.groups()`

Those are *metric* groups — "productivity = code + read + research". They may
overlap, they are arithmetic, and they are usually absent. Borrowing them for
colour would mean an unconfigured install has no palette at all, and that adding
`sleep` to a productivity total would recolour it.

### Capacity is geometry

There is no budget constant. `palette.next_hue()` answers from what is already
placed and returns `None` when the widest remaining gap can no longer hold a
family without its members reading as relatives of the families either side
(`MIN_GAP = 2 * (MIN_SEPARATION + 2 * STEP_DEGREES)`). With the eight built-in
families, two more fit. After that, categories join the neutral tail ramp: still
drawn, still countable, still labelled — they have only stopped claiming to be
memorable.

Two built-in families (`ambient`, `screen`) ship with one member on purpose. Packed
full, `family = "..."` in `config.toml` would be a setting that can never be honoured.

### Assigned once, written down

`<data dir>/palette.json`, atomic write, mode 600. "Midpoint of the largest
remaining gap" is only stable while the gaps are; recomputed every load,
**deleting one category would silently recolour every category declared after
it**. Entries are never pruned, including for categories removed from config —
`known()` exists precisely so months of old rows still display, and they have to
display in the colour they have always had.

A failure to write is not raised. The in-memory assignment is correct for this
run; losing stability is bad, refusing to draw the page because the disk is full
is worse.

### `color =` in config is still honoured

As a **request for a hue, not a pixel value**: keep the angle, replace lightness
and chroma with the system's. It is declined when it lands too close to a family
already placed — the user asked for "pinkish", not for that exact pixel.

## Three things the implementation found that the mockups could not

1. **Family angles cannot be evenly spaced, and hue degrees are the wrong unit.**
   A degree buys far less separation through green and teal than through
   magenta; at even-ish 45° spacing, Chores and Coding came out the same teal.
   The angles are searched against **OKLab distance** (`palette.distance()`),
   objective: the closest cross-family pair sits at least twice as far apart as
   the within-family step. Relatives may look related; strangers may not.
   **Re-run that search before hand-editing one of those numbers** — the test
   fails otherwise.

2. **The 50–130° band browns before it reaches either theme's lower lightness
   stop.** Dark yellow at modest chroma simply *is* mud. This is the sRGB gamut,
   not a preference, and it is the same root cause as the old Coding (`#d37600`)
   and Meal (`#975800`). A family living there holds both members at the brighter
   stop, and no family may straddle an edge of the band.

3. **The neutral ramp had to split in two.** With one ramp, an overflow category
   rendered identically to `unknown` — an answer looking like a non-answer.
   `CIRCUMSTANCE` (other / unknown / using-apps) recedes; `TAIL` (overflow)
   stays present.

## Consequences

- **Every colour on the page changed.** The taxonomy tests already pinned
  properties rather than hex values, and say why.
- `viz.js`'s `BCOL` — a second hand-written category table, three lines under a
  comment warning that a table copied twice will drift, and duly drifted:
  Talking was pink in the AI band and lime in the blocks chart on the same page
  — now derives from the injected `AICOL`.
- The lane colours in `viz.css` look like a third copy and **are not one**. The
  `sleep` lane is Whoop data, not the `sleep` category; they share a name and
  nothing else. `tests/test_viz.py::test_every_lane_key_has_a_css_color` catches
  anyone deleting them on that assumption.
- Tuning the palette now means editing four numbers (two lightnesses, the chroma
  cap, the warm band) rather than seventeen values. Those four carry more of how
  the product feels than any individual hue in it.

## Revisit when

- A colour-blindness audit is run against the generated set. The old comment
  claimed a measured worst pair; the new set has not been measured that way, and
  `palette.distance()` is normal-vision only.
- More than two extra families are needed in practice — that is the signal that
  the built-in eight are mis-drawn, not that the ceiling is wrong.
- The frontend lands ([ADR 0001](0001-serve-and-a-built-frontend.md)) and the
  palette needs to reach TypeScript. It should cross as generated tokens from
  the same schema, never as a second table.

## Amended — 2026-09-18

Three things in the decision above did not survive contact with the rendered
palette. Each was reported by eye first and confirmed by measurement after.

**"L is fixed per theme" was too strong.** A hue's available chroma at a fixed
lightness varies threefold around the wheel (0.101 at cyan, 0.274 at magenta,
both at L 0.66), so one pair of stops buys a clean violet, an olive and a murky
teal, all nominally equal weight. Three arcs now carry their own stops
(`palette.BANDS`): 50–140° is lifted because below its gamut peak it is brown;
185–262° is lifted because it is the gamut's deepest valley; 263–282° is
*dropped*, because blue is the reverse case — richest when dark (C 0.267 at
L 0.52). Every band keeps a step, pointing the same way as the shared stops.
The light-theme lightness window widened from about 0.08 to 0.23 as a result,
and is now bounded in OKLab L rather than relative luminance, which overweights
yellow-green 2.7× at equal L.

**The strangers-to-kin ratio is no longer a test.** It fell from 2.20 to 1.69 to
1.14 across three fixes, and each time the fix was the cause — moving Meal away
from Transit is what raised the kin floor that had held it up. Measured, the
thing it guarded was not there either: upkeep's two members sit further apart
than two unrelated categories do. What is enforced instead is an absolute floor
(0.06 ΔOKLab) between any two categories of different families, and the same
floor between every family's free slot and every other family — because the
free slot is where `family = "..."` in config.toml lands, and a slot that is
some built-in's twin makes the documented control produce a duplicate.

**Coding is its own family, and the one exception to the area rule.** It had
shared `focus` with Research in the cool arc, alongside Chores and Browsing —
four bright cool blocks at nearly one lightness, which no angle search could
separate. It now sits at 263° in the deep band (`build`), at step 0, which makes
it the most present colour on the page. That inverts the area rule for the
largest category, deliberately: in a tool that exists to account for working
time, it is the thing the page is about. Research keeps `focus` alone.

Nine families, four of them with one member (ambient, focus, screen, build).
Two more still fit before `next_hue()` returns `None`.
