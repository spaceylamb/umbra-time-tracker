// The condensed header a view pins under the topbar as its own header
// scrolls away — a second, smaller copy rather than the real header
// shrinking in place: shrinking a sticky block changes the page's height
// under the reader's eyes, and the list they are reading jumps.
//
// It arrives in two steps, each as its full-size original reaches the
// pinned surface: the title row (→ `head`), then the next piece — Day's bar,
// Week's priorities (→ `more`). At each step the original and the copy
// swap in the same frame and the copy morphs out of the original
// (lib/flip); scrolling back, the original morphs out of the copy. The
// surface behind grows and shrinks with the same curve and duration, so the
// two directions are one motion played forwards and backwards. Parts with no
// original to morph from (the numbers, the arrows, the hours) fade — mark
// them with className="pin-fade".
//
// Rendered inside the topbar (Topbar's `pinned`), so a bounce at either end
// of the page moves them as one.
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { MORPH_EASE, MORPH_MS, settling } from "../lib/flip";

/** Height of the pinned head row; the steps' trigger offsets use it. */
export const PIN_HEAD_H = 56;

/** snapMore: the surface takes the second row's height at once rather than
 * growing into it. Day: the bar's original vanishes as its copy morphs, and
 * a surface still growing showed the page under it for a moment — it read
 * as a wobble. Week: its day headers pin right under the full panel, and a
 * growing surface left a strip of page between the two in a fast scroll. */
export function Pinned({ stage, head, more, snapMore = false }: {
  stage: 0 | 1 | 2; head: ReactNode; more?: ReactNode; snapMore?: boolean;
}) {
  const show = stage >= 1;
  const open = show && stage >= 2 && !!more;
  const moreRef = useRef<HTMLDivElement>(null);
  const [moreH, setMoreH] = useState(0);
  useLayoutEffect(() => { setMoreH(moreRef.current?.offsetHeight ?? 0); }, [more]);
  const height = open ? PIN_HEAD_H + moreH : show ? PIN_HEAD_H : 0;
  // Instant: the hand-off is a swap; the motion is the morph. Opacity, not
  // visibility: an element that has been animated on the compositor and then
  // sat under a visibility:hidden parent could come back unpainted in Chrome
  // (the stepper vanished after a few steps until scrolled again); `inert`
  // takes it out of reach instead.
  const vis = (on: boolean) => (on ? "pointer-events-auto" : "pointer-events-none opacity-0");
  return (
    <div className="pointer-events-none absolute inset-x-0 top-full" inert={!show}>
      {/* The surface: one element whose height follows the steps; it covers
          the topbar's hairline, so topbar and panel read as one surface. */}
      <div aria-hidden className={`absolute inset-x-0 top-0 bg-canvas ${show ? "shadow-[0_1px_0_var(--color-border)]" : ""}`}
        style={{ height, transition: settling() || (snapMore && open) ? "none" : `height ${MORPH_MS}ms ${MORPH_EASE}` }} />
      <div className="relative mx-auto w-full max-w-[1440px] px-10">
        <div data-on={show} data-still={settling() || undefined} inert={!show} aria-hidden={!show}
          className={vis(show)} style={{ height: PIN_HEAD_H }}>{head}</div>
        {more && <div ref={moreRef} data-on={open} data-still={settling() || undefined} inert={!open} aria-hidden={!open}
          className={vis(open)}>{more}</div>}
      </div>
    </div>
  );
}

/** Two sentinels → the stage: 1 once the first has gone, 2 once both. */
export const stageOf = (a: boolean, b: boolean): 0 | 1 | 2 => (b ? 2 : a ? 1 : 0);

/** A group's number, inline — the condensed form of the headline row. */
/** flip: a name prefix, so each label and value morphs out of the
 * original's (data-flip="<flip>-l-<key>" / "<flip>-v-<key>"). */
export function InlineNumbers({ items, flip }: { items: { key: string; name: string; value: string }[]; flip?: string }) {
  return (
    <dl className="flex items-baseline gap-5">
      {items.map((g) => (
        <div key={g.key} className="flex items-baseline gap-1.5">
          <dt data-flip-to={flip && `${flip}-l-${g.key}`} className="text-xs/tight text-ink-subtle">{g.name}</dt>
          <dd data-flip-to={flip && `${flip}-v-${g.key}`} className="text-[15px]/5 font-semibold tracking-[-0.01em]">{g.value}</dd>
        </div>
      ))}
    </dl>
  );
}
