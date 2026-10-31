# UX

How the page behaves: what the places are, how you move between them, what
each state says, and how the phone differs. For how it looks, see
[design.md](design.md).

## What the page is for

One question, answered auditably: **what was I actually doing?** Every screen
either shows the answer or shows how it was reached. Two rules sit above every
decision below:

1. **No un-auditable conclusion.** Anything the model said can be opened down
   to the raw records it came from.
2. **Never let two different truths look the same.** Not-imported isn't
   empty, unjudged isn't quiet, and a failed call isn't silence (the README's
   [honesty rules](../README.md#honesty-rules)). When a state is added, its
   first job is to look unlike its neighbours.

## Places

Three top-level places: **Day**, **Week**, **Sources**. Desktop shows them
as one segmented control in the topbar after the wordmark. The phone shows
them as the tab bar. Settings is the gear at the far right.

Not places, on purpose:

- **Said aloud** (to-dos and claims from voice recordings) is a per-day output
  of the same 30-minute windows, so it belongs to Day: a trigger beside the
  date (`4 to do · 4 to check`) opens a drawer. Each half of the trigger opens
  its own tab.
- **Settings** is a dialog over the page, not a destination.

No sidebar. With three or four destinations it costs timeline width and buys
nothing. Revisit only if there are five or six real places.

The dot on Sources is the only badge in the product, and it means one thing:
a source has stopped and needs you. The gear is a button rather than a place
because Settings opens over the page you were reading instead of replacing it.

## URLs and moving around

| URL | |
|---|---|
| `/` | the **newest day with data**, not the calendar's today, which may have nothing yet |
| `/day/2026-09-08` | a day |
| `/week/2026-W37` | an ISO week, in the user's local calendar, the same weeks the server computes |
| `/sources` | |

- **Switching views keeps your place.** Day → Week opens the week containing
  the day you were on; Week → Day opens that day.
- **The stepper is `‹ Today ›`** (macOS Calendar's shape). Today sits between
  the arrows because it's the same kind of move, just a long one. It stays put
  (disabled) when you're already there, so the arrows never shift. Where
  nothing was recorded today it reads **Latest**. Week has the same `‹ ›`, and
  its title opens a month to pick a week from.
- **Stepping keeps your scroll position.** Opening a new view starts at the
  top; Back returns to where you were.
- **The strip** (three weeks above the bar) is a scrubber: press and drag, and
  the day under the pointer opens as you pass. It replaces history rather than
  pushing 21 entries onto Back. The window holds still while you drag (it
  would slide out from under the pointer otherwise) and re-centres on release.
- Dates are ISO days in the user's calendar and are never reinterpreted
  through the browser's timezone (`lib/time.ts`).
- The exported site routes in the hash (`#/day/…`), because a static host
  can't fall back to the app.

### Keyboard

Designed, **not yet built** except `Esc`:

| key | |
|---|---|
| `D` `W` `S` | Day, Week, Sources |
| `←` `→` | previous / next, by whatever the place counts in: days on Day, weeks on Week |
| `T` | back to today, or this week |
| `,` | Settings |
| `Esc` | close whatever is on top (built) |

Every key acts instantly. Nothing a key does is animated, because it's done a
hundred times a day. Keys never do anything the mouse can't.

Useful while working: `?theme=dark|light`, `?settings=<page>`,
`?said=todo|check&pick=<id>`, `?edit` (Week priorities).

## Day

Top to bottom: the date and stepper, headline numbers, the three-week strip,
the 24-hour bar with its coverage line, then the list of events beside the
detail of the selected one.

- **The bar is the day's shape in one look.** It never scrolls horizontally.
  If more resolution is ever needed, the answer is a second zoomed bar under
  the selection, not a scrolled one. Blocks carry no labels; the readout names
  whatever is under the pointer (0 ms in, 120 ms out). A click selects.
- **The coverage line** ("covered 1,370 of 1,440 min · 70 min the model said
  nothing about") is the honest summary of the bar. The server counts it
  (`Day.minutes`): each minute once, as covered, failed or said nothing, so
  the three add up to the day so far and none goes negative.
- **A day is as long as it was.** 1440 minutes usually; 1380 or 1500 when the
  clocks change; ten hours the day you fly east, thirty-nine the day you fly
  back. The bar is the day whatever its length, its ticks follow the clock on
  the wall, and a change of clock is a hairline of ink with both clocks named.
  The line beside the date says it: `CST → PDT · a 39-hour day · travel`.
  Times in the list are the wall clock's too.
- **A day opens on its own default event**, the longest one that isn't sleep, so the
  detail column is never an empty box, and a new day doesn't keep the last
  day's selection.
- **The list** has one row per event and a muted row for any gap of 15 minutes
  or more ("The model said nothing about this stretch"). Shorter seams stay
  seams on the bar. A failed window gets its own row, unlike a gap.
- **The detail is four numbered steps, each a real count, never a paraphrase:**
  01 what the composing pass saw, 02 the window the spans were judged against
  and what was in it, 03 what the model itself cited (its picks, not its
  input, and it says so with ⚠), 04 every raw record in the window, verbatim.
  Steps 03 and 04 expand in place.
- **Pinned header.** As the date row and then the bar scroll away, a condensed
  copy pins under the topbar in two steps: date and stepper, then a slim bar
  that still scrubs. You never lose the day's shape while reading the list.
- **Said aloud** is a 520 px drawer on the right. It's **non-modal**: the bar
  stays visible and the picked item's 30-minute window is outlined on it,
  which is the point of opening it here rather than on its own page. It closes
  on Escape, ×, or a click anywhere outside. Claim verdicts are ranked by ink
  weight (likely wrong → unsure → confident right), never by hue.
- Each to-do carries **✓ Done** and **✕ Not a task**, each claim **✓ Seen**.
  A closed one stays listed — faded, struck through, last, with **Undo** —
  and leaves the count on the trigger: "you dealt with it" must not look like
  "never heard". Only "not a task" counts as an extraction error
  (`umbra tasks negatives`). In the exported copy the buttons show, disabled.

## Week

- **Priorities**: up to three, each with a target. Which time counts toward a
  priority is the **model's judgement** (it reads the day), not a category
  rule, so "sleep before midnight" works. Changing a target never re-asks the
  model. One stretch may count toward two priorities. A day not yet judged
  against them is *unasked*, not zero.
- **The week, hour by hour** is one chart with seven columns, not seven
  charts: 3-hour hairlines run across all of them, blocks carry no labels
  (hover names them, a click opens the day), and the future isn't drawn at
  full weight ("Thursday onward has not happened."). Every column is to one
  scale, so a ten-hour travel day stops short and a thirty-nine-hour one runs
  on below the others; the grid grows only in such a week.
- **Comparisons are like for like.** Three days against the same three days
  last week, never a part-week against a whole one, which would show every
  category falling. A travel day — this week or the same weekday last week —
  leaves that weekday out of both sides, and the footnote names it. Its time
  is still in "Where the time went".
- The head pins like Day's: the title row, then the priorities condensed, then
  the day headers right under them.

## Sources

Every source, grouped by what it needs from you: **Needs you** (the only part
that asks for action), **Recording**, **Cannot be checked**, **Not set up**.

- Nothing is hidden. A source that can't run is listed and says why.
- **Don't show a tick you can't back up.** A permission that can't be probed
  (Full Disk Access) shows an em dash and the reason, not a guessed ✓.
- When something needs you, the topbar status says so ("● 1 source needs
  you") in place of "updated 4m ago".

## Settings

Modal, centred, over a scrim. Unlike the drawer, nothing on the page needs to
stay in view while settings change.

- **Every control writes exactly one line of `config.toml`**, keeps the
  user's comments, and shows what the file now says.
- Structures (providers, categories, deploy) stay **Open in editor**. A form
  that half-models a TOML table is worse than the file.

## States

Every state that isn't "judged" uses the same block under the bar
(`day/Notice.tsx`): **a title, one paragraph that says what is true and what
is not, and at most one action.**

| state | bar | what it says |
|---|---|---|
| first run, nothing imported | plain ground | what will be imported, and one button |
| importing | stays unread until every source is in. Half an import drawn as a day looks like a quiet day, not an unfinished one. | progress per source |
| recorded, no model key | hatched; raw lanes grey | "Recorded, not yet read" · Add a model key |
| the model refused the key | hatched | which model, when, its words · Check the key |
| recorded, not yet judged | hatched | "No model has looked at this day yet" |
| one window's call failed | hatched over that window | its own list row, retried next run |
| today | drawn up to `now`; nothing after — events too, even ones stored before merge stopped at now | the `now` line; "covered … so far" |
| a travel or clock-change day | the bar is the day, at its own length; a hairline of ink where the clock changed | the clocks, the length, and `travel` beside the date |

Order matters when several apply: a missing key explains a refused call, so it
wins.

## The phone

Below **640 px** the page is the phone layout. It isn't the desktop squeezed.
Components that differ in structure branch on `useMobile()`. Ones that only
differ in size use Tailwind's `sm:`.

- **The phone reads the exported site, never `umbra serve` over the
  network.** It's **read-only**: everything that writes is still shown,
  disabled, with one line saying changes are made in Umbra on your Mac. Hiding
  them would make the phone look like a different product.
- **Frame**: a mark row on top that scrolls away (the gear sits at its right
  end on every tab), the date row that sticks, and the tab bar where a thumb
  is.
- **What the desktop shows beside the page, the phone slides up as a sheet**:
  the event detail, Said aloud, the day's sources, the date picker. Sheets
  dismiss on a flick down (distance or velocity), the scrim, or Escape. The
  page behind doesn't scroll while one is open.
- **The bar scrubs by press-and-drag.** The block under the finger takes an
  outline and the readout shows while the finger is down. A tap without a drag
  opens that event.
- **Week turns on its side**: one 26 px bar per day, midnight to midnight, the
  same shape as the Day bar. A tap opens the day.
- **The date picker** marks a day with • (a model has looked) or ○ (recorded,
  not judged), and only those two. Days with nothing recorded are shown, so
  the calendar stays a calendar, but can't be opened.

## The public demo

`python3 -m umbra.devseed --site OUT` builds the exported site from devseed's
three made-up weeks, and `.github/workflows/demo.yml` publishes it to GitHub
Pages on every push to `main`. It is the exported copy with two differences,
both keyed on the flag it carries (`lib/demo.ts`):

- **One line above the page** says what it is and links to the README. It
  stands in for the topbar's "updated …", which the demo does not show.
- **Its clock stands still** at `devseed.DEMO_NOW`, the moment its imports
  ran. Ages are otherwise computed when the page is read; for days that were
  never real, that would only ever say "stopped".

Nothing in it needs you: devseed's expired login, there to show the Sources
page's "Needs you", is left out of the demo.

## Writing for the page

- **Say what is true and what is not**, in that order. "Every record is here
  and unread" beats "No data".
- **Counts, not adjectives.** "Cited 4 records", "37 records in this window".
  If the number exists, show it.
- **Name the next step once**, as the one action, not as a sentence plus a
  button saying the same thing.
- **No product-speak for things Umbra is not.** It's "to do", not "Todos";
  Umbra isn't a task manager.
- **Raw values in mono, verbatim.** Don't prettify a path, URL or command the
  user might want to match against their own system. Paths from the Mac are
  written as `~/…` in the export.
- Sentence case everywhere. No exclamation marks.

## Accessibility and input

What holds today:

- Escape closes the drawer, Settings, the week picker and sheets.
- Hover is never the only way to a fact: everything a readout shows is in
  the list or the detail, one click away.
- `prefers-reduced-motion` keeps fades and drops travel.
- Colour is never the only carrier of meaning: categories have labels, ⚠
  carries the alert, and verdicts carry words and weight.
- The phone's bar is 44 px tall, so a scrub starts anywhere along it.

Known gaps (fix these before adding to them):

- Focus is not returned to the trigger when a drawer, dialog or sheet closes.
- Thin blocks on the bar have no enlarged touch target. A sliver under ~3 px
  should get an invisible 44 px hit area while staying visually true to its
  duration.
- The bar and strip have no keyboard scrubbing, and the shortcuts above
  aren't wired up.

## Before you ship a UI change

- [ ] Which place does it live in? If it seems to need a new one, re-read
      [Places](#places).
- [ ] Every new state looks unlike its neighbours, and reads as one of the
      [States](#states) or has a row added there. For empty stretches, check
      [the four kinds of nothing](design.md#the-four-kinds-of-nothing).
- [ ] Any number it shows can be traced to the records behind it.
- [ ] Checked on desktop and below 640 px, light and dark, and on the
      exported (read-only) copy if it writes anything.
- [ ] `bun run --cwd web build`, `bun test --cwd web`, and
      `python3 -m unittest discover -s tests -t .`.
