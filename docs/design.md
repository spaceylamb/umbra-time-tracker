# Design

How the page looks, and why. For how it behaves (places, navigation, states,
the phone), see [ux.md](ux.md). For how category colours are derived, see
[ADR 0002](decisions/0002-derived-category-colours.md).

Where this document and the code disagree, **the code wins**;
[Designed, not built](#designed-not-built) lists the disagreements nobody
has settled yet.

## Principles

> Umbra reconstructs a day you have already lived. Every decision below
> follows from one obligation: never let the page look more certain than
> the evidence is.

The first five aren't visual preferences. They come out of the product
itself, and they're the reason the rest looks the way it does.

**What the product refuses to do**

1. **The database is the truth. The page is an artefact.** Anything the page
   shows can be thrown away and rebuilt. Anything it invents can't be taken
   back.
2. **Four kinds of nothing must not look alike.** Nothing happened, the model
   said nothing, no model has looked, nothing was imported. Collapsing any two
   into one blank is the worst mistake this interface can make. See
   [The four kinds of nothing](#the-four-kinds-of-nothing).
3. **Measured and guessed never share a visual.** A contradiction you must
   settle, a blind spot already understood, and a model's inference are three
   different claims. Giving them one appearance says they carry equal weight.
4. **Never quietly truncate what the model said.** Cutting leaves no mark on
   screen, which is why it has to be forbidden rather than reviewed. Fold the
   extra away, and say how much was folded.
5. **Anything that can go stale must age in front of you.** A freshness
   verdict computed when the page was built is frozen at that instant, and it
   will read "fresh" on exactly the morning the pipeline broke. Ages are
   computed when the page is read (`lib/fresh.ts`).

**How that looks**

6. **Design in monochrome. Colour is an encoding.** The shell is grey so the
   day can be the only thing with colour. Nothing is tinted because it's good,
   important or favourable.
7. **Earn every surface.** Spacing first, then alignment, then type, then a
   change in density. A border is the last resort, and a card inside a card is
   never the answer.
8. **Default to stillness.** Motion is a budget and most of the interface
   spends none of it. What you reach for a hundred times a day never animates.
9. **Nothing load-bearing lives behind a hover.** Hover may repeat what the
   page says. It may not be the only place the page says it. The bar naming
   its blocks under the cursor is allowed because every name is already in
   the list underneath.

Three consequences that most review comments trace back to:

- **A hue that isn't data is a bug.** This has shipped three times: a link in
  a category blue, `today` marked in the browse colour, and the ⚠ glyph in
  the Coding colour. Selected, hovered and current use ink, outline or
  weight.
- **Absence is drawn, never filled.** Where the model said nothing, the
  ground shows through. Painting over it would make every day look 100%
  explained.
- **Every figure says where it came from.** A measured number names its
  instrument in the label ("Sleep · WHOOP"). A judged one doesn't, because the
  model is named once per page. No value is an em dash in `ink-subtle`, never
  `0`: zero would be a finding, and this is the absence of one.

## Tokens

The source is `web/src/theme.css` (Tailwind v4 `@theme`). Components use the
semantic names. They never use a ramp step or a literal hex, and **never
branch on theme**: dark mode swaps the semantic tokens and nothing else.

### Neutral ramp

One cool-neutral ramp passes through both themes. The anchors swap: in light
the ground carries the ink, in dark the ink carries the ground. Same two
values, so there's no second palette to keep in sync.

| step | hex       | light role               | dark role                 |
| ---- | --------- | ------------------------ | ------------------------- |
| 50   | `#FAFBFC` | raised                   |                           |
| 100  | `#F4F5F8` | **canvas**               | **ink**                   |
| 200  | `#E8EAEF` | sunken, border           |                           |
| 300  | `#D8DBE2` | border-strong, fill-open |                           |
| 400  | `#B4B8C2` |                          | ink-muted                 |
| 500  | `#8B909C` | ink-subtle               | ink-subtle                |
| 600  | `#676C78` | ink-muted                |                           |
| 700  | `#4A4E58` |                          | border-strong             |
| 800  | `#33363D` |                          | border, fill-open, active |
| 900  | `#222326` | **ink**                  | **canvas**                |
| 950  | `#16171A` |                          | sunken                    |

### Semantic colour

| token           | light           | dark        | means                                                                                                                                        |
| --------------- | --------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `canvas`        | 100             | 900         | the page. One continuous ground, no panels.                                                                                                  |
| `surface`       | `#FFFFFF`       | `#2A2B2F`   | raised and earned: selection, a field you type into, a drawer or dialog. Three on one screen means one is decoration.                        |
| `sunken`        | 200             | 950         | the empty track behind data (bar, lane, progress rail), chips, secondary buttons. Recessed, so it never reads as a card that failed to load. |
| `border`        | 200             | 800         | hairlines                                                                                                                                    |
| `border-strong` | 300             | 700         | stronger edges, the off toggle, and the `hatch`                                                                                              |
| `ink`           | 900             | 100         | primary text, selection outlines, the "on" state                                                                                             |
| `ink-muted`     | 600             | 400         | secondary text                                                                                                                               |
| `ink-subtle`    | 500             | 500         | metadata, ticks, absent values                                                                                                               |
| `hover`         | `#ECEEF2`       | `#2A2B2F`   | ghost hover, list highlight                                                                                                                  |
| `active`        | `#E4E6EB`       | `#33363D`   | ghost press                                                                                                                                  |
| `fill-hover`    | `#E0E2E8`       | `#25262A`   | secondary-button hover, halfway to open                                                                                                      |
| `fill-open`     | 300             | 800         | a secondary button whose thing is open                                                                                                       |
| `live`          | `#2A9C60`       | `#4ECB86`   | **the pipeline is alive** ("updated 4m ago", installed, key found). A 6 px dot and a word. Never a pill, never a category's green.           |
| `alert`         | `#A93226`       | `#F0938A`   | **always beside ⚠**, never alone. Outside the data palette so it can't be read as a category.                                                |
| `scrim`         | `#16171A` @ 35% | black @ 55% | behind a modal. A sheet opened from a sheet gets a lighter one, not a second full one.                                                       |

### Category colours

Category colours aren't tokens. `umbra/palette.py` derives them, and the page
sets `--cat-<key>` on `:root` from `/api/meta` (`theme.ts`). **Never copy a
category hex into** **`web/`.** A second table drifts the first time someone adds
a category, and that has happened twice.

- `colour(category, theme) = oklch(L[theme], C, hue[family] ± step)`. **Hue
  says which family**, glanceable across the whole bar. **The step says which
  member**, read on one block where its label already is. Hue separates eight
  to twelve things at 8 px, which is a property of eyes, not palettes.
- Users declare categories, never colours. `color =` in config is a request
  for a hue, not a pixel value.
- `other`, `unknown` and app use (the circumstance ramp) recede in both
  themes. "We don't know" must never look like an answer.
- **Light-theme floor:** fills stay lighter than muted body text (OKLab L
  0.531). Below it, data reads as a second column of prose and competes with
  the text for the top of the hierarchy. That's why the light stops sit at
  L 0.58 / 0.66 and why Tailwind's `-600` shades are too dark to borrow.
- Audit confusable pairs **at 8 px** (the list's dots) and at 6 px (thin
  slivers on the bar), never on a swatch grid. A colour-blindness pass
  against the generated set is still owed.
- Text on a fill: nothing in Umbra puts a word on a coloured fill any more
  (the bar and week grid dropped their labels for the readout). If a future
  view does, it inherits `luminance(fill) ≥ 0.25 ? #222326 : #F4F5F8`, not a
  new rule.

### Type

**Geist** is Umbra speaking. **Geist Mono** is the machine speaking. The split
is semantic: **if a value came out of the database verbatim** (timestamp,
duration, URL, command, path, id, model name), **it's mono.** They're drawn as
a pair, so the two voices share a baseline.

| token  | size / leading | weight       | used for                       |
| ------ | -------------- | ------------ | ------------------------------ |
| `3xl`  | 44 / 48        | 600, −0.02em |                                |
| `2xl`  | 34 / 40        | 600, −0.02em | headline numbers               |
| `xl`   | 26 / 32        | 600, −0.02em | the date title                 |
| `lg`   | 20 / 28        | 600, −0.02em | section titles                 |
| `md`   | 16 / 24        | 500          | event titles, phone body       |
| `base` | 14 / 22        | 400          | body. Dense, never small.      |
| `sm`   | 13 / 20        | 400          | secondary text, controls (500) |
| `xs`   | 12 / 16        | 400          | the floor for sans; mono ticks |
| `2xs`  | 11 / 14        | 400          | mono meta only                 |

- Weights are 400, 500 and 600. No 700, and weight never animates.
- Leading tokens are px (`tight` 16, `snug` 20, `normal` 22, `relaxed` 26), so
  the rhythm stays identical whichever CJK face a machine resolves.
- Tracking: `tight` −0.02em at 20 px and up, `wide` 0.08em on the rare caps
  label.
- **CJK is content, not locale.** App names, book titles, transcripts and
  quotes arrive in Chinese whatever `[ui] lang` says. Geist has no CJK glyphs,
  and that's the mechanism: the browser falls through per glyph.
  - Stacks: `Geist, "PingFang SC", "Microsoft YaHei", "Noto Sans SC"` and
    `"Geist Mono", "Sarasa Mono SC"`. Only Noto and Sarasa are bundled (both
    OFL). Sarasa matters because a Chinese line in a raw-record column must
    still align.
  - CJK settings: tracking 0 (Han already fills its em box), leading 26 at
    14 px, emphasis 500 not 600 (stroke density reads a step heavier), never
    italic (browsers fake a broken slant), truncate per character (no spaces
    to break on).

### Space, size, radius

| token       | value                 |                                               |
| ----------- | --------------------- | --------------------------------------------- |
| spacing     | Tailwind's 4 px scale | 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64 |
| page gutter | 40 desktop, 16 phone  |                                               |
| breakpoint  | 640                   | below it the phone layout (`lib/mobile.ts`)   |
| `radius-xs` | 3                     | outlines drawn on data                        |
| `radius-sm` | 5                     | chips, segmented items                        |
| button      | 6                     |                                               |
| `radius-md` | 8                     |                                               |
| `radius-lg` | 12                    | panels, dialogs                               |
| sheet       | 16                    | the phone's bottom sheet                      |

### Elevation

Defined once per theme, outside `@theme`: Tailwind inlines theme shadows, and
then dark values can't replace them. Use them as `shadow-(--shadow-popover)`.

| token              | for                                      |
| ------------------ | ---------------------------------------- |
| `--shadow-popover` | something that will close: popover, menu |
| `--shadow-overlay` | dialog                                   |
| `--shadow-drawer`  | the right-hand drawer                    |

Light separates a raised surface with shadow. Dark separates it with a
hairline of light plus a much deeper shadow. **A shadow says "this will
close"**, so nothing permanent borrows one.

### Motion

| token                     | value                          | for                                                                    |
| ------------------------- | ------------------------------ | ---------------------------------------------------------------------- |
| `--dur-fast` / `-out`     | 90 / 70 ms                     | hover highlight, small toggles                                         |
| `--dur-moderate` / `-out` | 185 / 140 ms                   | popovers, tabs, a view's content arriving                              |
| `--dur-slow` / `-out`     | 275 / 185 ms                   | dialogs, drawers, the pinned header surface                            |
| `--ease-spring`           | `linear(…)`                    | a critically damped spring sampled into `linear()`. The default curve. |
| `--ease-out`              | `cubic-bezier(.23, 1, .32, 1)` | button press return                                                    |
| drawer curve              | `cubic-bezier(.32, .72, 0, 1)` | pinned morphs (340 ms), the phone sheet (360 ms)                       |

## The four kinds of nothing

Principle 02, made into parts. The same empty stretch can mean four
different things, and each must look different in every place it appears.
The intended design is on the left; the **as built** column is what the code
does today.

| what is true                                                                                                | bar                                         | list                       | in words                                     | as built                                                                                      |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------------- | -------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Nothing happened.** The collector ran and saw no input. A measured absence is a finding.                  | filled grey                                 | `Away`                     | "no input for 46 minutes"                    | not built: no source reports idle time yet                                                    |
| **The model said nothing.** Records exist and a model read them, and it still declined to name the stretch. | a dashed outline over that stretch          | dashed dot, "said nothing" | "70 min the model said nothing about"        | the ground shows through (no outline). The list row and the coverage line are built.          |
| **No model has looked.** Records are in the store; inference hasn't run.                                    | the `hatch`                                 | "not judged"               | "No model has looked at this day yet"        | built, including a failed call's window                                                       |
| **Nothing was imported.** A source stopped.                                                                 | identical to "nothing happened", on purpose | ⚠ since 3 Sep              | "Browser has not imported since 3 September" | built in the source lanes and Sources; the only state that may never be shown by colour alone |

The `hatch` (`theme.css`) is the only pattern in the app and means exactly
one thing: *no model has looked*. A failed call is drawn hatched over its
window because that's what it is.

## Timeline primitives

Three marks carry the product: the **day bar**, the **source lane**, and the
**three-week strip**. None is a chart a library draws. They're custom layouts
in the time domain on a linear scale, all mute at rest, and they name
themselves under the cursor. Everything else on the page exists to label
them.

- **Day bar.** The day to scale — 1440 minutes usually, but a day is as long
  as it was (`Day.n_min`: 600 flying east, 2340 flying back, 1500 when the
  clocks go back). Ticks follow the wall clock (`lib/clock.ts`); a change of
  clock is a 1 px ink hairline with both clocks named above it, the only mark
  on the bar that is not data. Gaps left open, no labels. It never
  scrolls horizontally: its job is the shape of a day in one look. If more
  resolution is ever needed, add a second zoomed bar under the selection.
  Blocks are separated by a 1 px gap so same-family neighbours stay two
  blocks.
- **Source lane.** Who recorded it. **Always grey, always visible, never on
  hover.** Colour belongs to the bar because it's a judgement; lanes are the
  records the judgement was made from, and the two must never read as the
  same kind of fact. A dashed divider separates what happened away from a
  screen (location, sleep, speech) from what a machine watched you do. It's
  the only boundary on that part of the page, and it marks a real one. The
  right-hand column is count and age together, because a number without an
  age can't be trusted.
- **Strip cell.** Twenty-one of them carry three weeks in under 400 px. Each
  stacks sleep, then productive time, then everything else. Unselected days
  sit at 30% so the selected one needs no border to win. A day no model has
  read is the same hatch the bar uses: the strip and the bar must never
  disagree about what's known.
- **Readout.** The only inverted surface (ink on canvas). It's a scrubber,
  not a tooltip: 0 ms in, 120 ms out, it follows the time axis (x on the bar,
  y on the week columns) and stays fixed across it. The chip moves; its text
  swaps instantly. One object, four facts: dot, category, range, duration.
  The honest cost: the bar is mute at rest, so a print or a screenshot
  doesn't name its blocks. The list underneath carries every name, which is
  why that's acceptable.

## Motion

> Vercel decides whether a thing moves; Fluid decides how the few that do.

A forensic tool should feel instant, not springy. Motion is allowed when it
explains a state change, preserves continuity, or confirms a press. The first
question is always how many times a day someone will see it.

| how often it's seen                     | decision               |
| --------------------------------------- | ---------------------- |
| 100+ times a day, or keyboard-initiated | no animation, ever     |
| tens of times a day                     | reduce, or remove      |
| occasional                              | standard, under 300 ms |
| rare or first-run                       | room for delight       |

**300 ms is the ceiling, not the target.** Past it, motion stops reading as
response and starts reading as waiting. (The pinned morph and the sheet run
340 to 360 ms as movement across the screen. They're the only exceptions,
and they never gate reading.)

### Hard rules

These are reviewable in a diff, not a matter of taste.

| rule                                                                    | not this                     | this                                                      |
| ----------------------------------------------------------------------- | ---------------------------- | --------------------------------------------------------- |
| Only transform and opacity animate                                      | `transition: all 300ms`      | `transition: transform …`                                 |
| Nothing enters from nothing                                             | `scale(0)`                   | `scale(0.97); opacity: 0`                                 |
| A popover grows from its trigger; modals stay centred                   | `transform-origin: center`   | origin at the trigger                                     |
| Transitions, not keyframes, for anything that can fire again mid-flight | `animation: slideIn`         | `transition: opacity …`                                   |
| Exit is quicker than entry (roughly two thirds)                         | 240 / 240                    | the `-out` tokens                                         |
| Touch fires hover; gate it                                              | `.row:hover`                 | `(hover: hover) and (pointer: fine)`                      |
| Reduced motion means fewer and gentler, not none                        | `animation: none !important` | drop movement, keep opacity                               |
| Never `ease-in` on UI                                                   | `ease-in`                    | `--ease-spring` or `--ease-out`                           |
| Never gate reading behind an animation                                  |                              | the evidence is on screen before anything finishes moving |

No parallax, no cinematic transitions, no bounce anywhere.

### Every interaction, decided

| interaction                   | as built                                                                                                                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Day ↔ Week ↔ Sources          | the topbar holds still; only the content fades in (`.page-in`, moderate). Never a whole-page crossfade, which double-exposes two pages and reads as a flash.                               |
| Stepping a day or week        | none. Scroll position is kept.                                                                                                                                                             |
| Hover the bar or a week block | 0 ms in, 120 ms out (the readout)                                                                                                                                                          |
| Hover a list                  | one highlight glides to the nearest row (`ui/fluid.tsx`, fast tier). Gaps belong to the nearest row. Entering fades it in under the pointer, with no travel from where it was. Mouse only. |
| Select an event               | the detail swaps instantly                                                                                                                                                                 |
| Any press                     | the button's surface collapses exactly 1 px a side (80 ms in, 180 ms back on `--ease-out`). No scale: scaling warps wide buttons.                                                          |
| Popover (week picker)         | moderate, from `scale(0.97)` + opacity                                                                                                                                                     |
| Settings dialog               | slow, from `scale(0.98)` + opacity, over a fading scrim                                                                                                                                    |
| Said-aloud drawer             | slides from its edge on the slow tier, leaves on `slow-out`                                                                                                                                |
| Phone sheet                   | rises on the drawer curve (360 ms). A flick dismisses it on distance (120 px) or velocity (0.11 px/ms).                                                                                    |
| Pinned header                 | see below                                                                                                                                                                                  |
| Page edges                    | no rubber band (`overscroll-behavior: none`); a browser would bounce the toolbar and pinned header with the content                                                                        |
| Reduced motion                | every one of the above keeps its fade and drops its travel                                                                                                                                 |

**The pinned header** (`ui/Pinned.tsx`, `lib/flip.ts`, `lib/stuck.ts`): as a
view's header scrolls away, a smaller *copy* pins under the topbar. The
header doesn't shrink in place, because that would change page height under
the reader. It pins in two steps, the title row then the next piece (Day's
bar, Week's priorities). At each step the original and copy swap in one frame
and the copy morphs out of the original (FLIP, transform only). Scrolling
back plays the same motion in reverse. Parts with no original fade
(`.pin-fade`) or drop in a few pixels (`.pin-slide`). Crossings are read
synchronously on scroll, so a fast fling never lets content slip under before
the step arrives. Navigation suppresses all of it (`markNavigation`): a
scroll reset isn't the reader scrolling.

## Components

`web/src/ui/` holds what has recurred at least three times. A pattern earns a
component by recurring, not by being anticipated.

**Section header.** The title on the left, the caveat on the right (the
footnote nobody would read otherwise), and one hairline between them. It's
the only horizontal rule the system allows.

**Buttons** (`ui/Button.tsx`). A fill, not an outline, is what says "press
me".

| variant     | look                | for                                                                        |
| ----------- | ------------------- | -------------------------------------------------------------------------- |
| `primary`   | ink fill            | the one thing the screen is asking for. One per screen.                    |
| `secondary` | sunken fill         | everything else you can press: ‹ ›, the said-aloud trigger, Open in editor |
| `tertiary`  | 1 px ring           | held in reserve, for a control on a sunken ground. Unused today.           |
| `ghost`     | no fill until hover | the way out, or the quieter of two: Cancel, ×                              |

Sizes: `default` 36 (page-level actions) and `compact` 28 (anything in a row,
panel or bar). Phone full-width actions are 40. Labels are 13 / 12 Geist
Medium, a verb, never uppercase, never a trailing arrow. Padding 16 / 12,
4 px tighter on the icon side. Primary hovers at 90% ink and presses at 80%.
Secondary hovers halfway to its open fill and holds that fill while what it
opened is showing. Focus is a 1 px ink ring, 2 px offset, keyboard only.

**Icon buttons.** The glyph and the target are two numbers: 30 / 16 (the
desktop gear, the only filled one, because it sits among text), 28 / 16
(quiet: close, prev, next), 44 / 20 on the phone, where the target is
invisible.

**Fields and chips.** Focus is the ink border plus a soft ring, never a
colour. A unit is a control, not a suffix ("4 sessions" and "10 hours" are the
same field answering different questions). A category chip carries its dot and
nothing else. A literal chip is mono and quoted, because it's matched against
text rather than judged.

**Rows.** Event rows have four fixed lanes (start, duration, dot, title) so
the eye can run down any column. Selected earns a surface and nothing else:
no border, no accent, no left rule. The "said nothing" row is the only one
that changes its dot, because it's the only one that isn't an event.

**Numbers and targets.** The target bar is 3 px of ink. The number is the
finding, the remainder is the next action, and the bar only says which end of
the week you're on.

**Settings controls** (`settings/controls.tsx`). Each writes one config key.
Segmented: two to four choices on a sunken track; a fifth turns it into a
select. The topbar's Day · Week · Sources is the same part. Toggle: ink when
on, `border-strong` when off, no colour, because a switch being on isn't good
news. Stepper: a small integer that carries its unit ("30 days"). Select: mono,
because its values are identifiers. Nested switch: running and sending are two
switches, never one, and the second is off until someone turns it on.

**Things that open on top.**

|                       | rule                                                                                                                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| dialog (Settings)     | 880 × 640, radius 12, over a scrim; section list on canvas, pane on surface. Esc closes and puts you back exactly where you were.                                                 |
| drawer (Said aloud)   | 520 px, right, **non-modal**: the bar stays visible and is part of the answer. Esc, ×, or a press outside closes it.                                                              |
| popover (week picker) | anchored to its trigger; Esc or any outside press closes it                                                                                                                       |
| sheet (phone)         | rises to just below the header so you can still see which day you were on; 36 × 5 grabber                                                                                         |
| warning               | sunken, never tinted, the alert colour spent only on the glyph. Reserved for what can't be taken back (a file already downloaded). A stale source uses the inline ⚠ mark instead. |

## The logomark

An umbra is the darkest part of a shadow, the region during an eclipse where
the light source is completely hidden. The name isn't a metaphor we invented,
so the mark isn't one either: two discs, one occluding the other, using
Lucide's `eclipse` geometry with the shadow filled and the crescent cut out.
It survives 16 px because it's solid shape rather than line, and it inverts
cleanly between the two themes. Orbit was rejected: it says "things going
around", and its five strokes smudge into a ring at favicon size.

## Designed, not built

Where the intended design and the code disagree today. Each needs a decision:
build it, or change this document.

- **Keyboard shortcuts**: `D` `W` `S` for the places, `←` `→`
  to step by the place's unit, `T` for today or this week, `,` for Settings,
  `Esc` for whatever is on top. Only `Esc` exists.
- **Nothing happened** (filled grey `Away`) has no source behind it yet.
- **The model said nothing** is designed as a dashed outline over the bar's
  stretch; the code leaves the ground showing.
- **Mobile priorities**: editing priorities and "what counts" on the phone are
  designed but not built.
- **A colour-blindness audit** of the generated palette, at 8 px.
- **Nothing was drawn** (an earlier draft of principle 02): an export carries
  raw records only for its last `raw_days`. For older days the detail's
  record counts come from a request that isn't in the export, and the page
  must say "not in this export" rather than count zero. Check
  `day/EventDetail.tsx` against an export with `--raw-days 1`.

## Changing the design

- **A new colour in the shell** needs a token with one stated meaning, values
  for both themes, and a row in [Semantic colour](#semantic-colour). If it
  could be mistaken for a category, it's the wrong colour.
- **A new category** needs nothing here; the palette places it.
- **A new state** gets placed in [the four kinds of nothing](#the-four-kinds-of-nothing)
  first. A fifth kind needs its own visual, and this table changes.
- **New motion** uses a duration token and one of the patterns above, or
  explains in a comment why none fits.
- **Both themes, every time** (`?theme=light|dark`). After a palette change,
  check that the bar's gaps stayed empty.
- If your change disagrees with something written here, update this file in
  the same change, so the next person doesn't build from a stale description.
- Run `bun run --cwd web build` before committing. `tests/test_web_build.py`
  fails if `umbra/web/` is older than `web/src`.
