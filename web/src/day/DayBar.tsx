// The day bar and its follow-cursor readout. The
// bar is the day, whatever its length: 1440 minutes usually, 600 on the day
// you flew out, 2340 on the day you flew back. Ticks follow the clock on the
// wall, and a change of clock is a hairline of ink, labelled.
//
// Events are laid on the day at their minutes; nothing fills the seams
// between them — where the model said nothing, the ground shows through.
// Filling it would make every day look 100% explained, which is inventing.
//
// The readout is a scrubber, not a tooltip: no delay in, 120ms out; it
// follows the time axis and pins across it; the chip moves, its text swaps
// instantly.
import { scaleLinear } from "d3-scale";
import { useRef, useState } from "react";
import type { DayEvent, Meta } from "../api/types";
import { clockTicks, switchesOf, wallHm, wallRange, type Seg } from "../lib/clock";
import { cat, catName } from "../lib/day";

const ORDINARY: Seg[] = [{ at: 0, wall: 0, zone: "", abbr: "", off_s: 0 }];

export function DayBar({
  meta, evs, selected, onSelect, now, failed = [], unread = false, mark, phone = false, slim = false, flip,
  n = 1440, segs = ORDINARY,
}: {
  /** The day's length in minutes (Day.n_min). */
  n?: number;
  /** The clocks the day was read on (Day.tz). */
  segs?: Seg[];
  /** data-flip name on the track, for a shared-element morph (lib/flip). */
  flip?: string;
  /** The pinned, condensed header's bar: 24px, small ticks, no room below. */
  slim?: boolean;
  /** Phone: 44px tall, four ticks, and the readout only while a finger
   * is on the bar — press and drag scrubs, the block under it takes a 2px
   * outline; a tap without a drag opens that event. */
  phone?: boolean;
  /** A window to outline — the Said-aloud item being looked at. */
  mark?: { a: number; b: number; label: string };
  meta: Meta;
  evs: DayEvent[];
  selected?: DayEvent;
  onSelect: (e: DayEvent) => void;
  /** Minute of day when this is today; the rest has not happened. */
  now?: number;
  /** Windows whose call failed: drawn with the same hatch as unjudged time,
   * because that is what they are — no model has looked (17c). */
  failed?: { a: number; b: number }[];
  /** Nothing imported at all: plain ground, not the hatch — the hatch means
   * "recorded, not judged", and nothing has been recorded (17). */
  unread?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ x: number; e?: DayEvent } | null>(null);
  const down = useRef<number | null>(null);
  const width = ref.current?.clientWidth ?? 1360;
  const x = scaleLinear().domain([0, n]).range([0, 100]);
  const judged = evs.length > 0;
  const end = now ?? n;
  const switches = switchesOf(segs);

  const at = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    const px = Math.min(Math.max(clientX - r.left, 0), r.width);
    const m = (px / r.width) * n;
    return { x: px, e: evs.find((e) => e.a <= m && m < e.b) };
  };

  // The readout is for the pointer only. The selected event is marked on the
  // bar itself (a 2px outline), not with a chip that never goes away.
  const shown = hover?.e;
  const chipX = hover ? hover.x : 0;

  return (
    <div className={`relative flex w-full flex-col ${phone ? "gap-2" : slim ? "gap-1.5" : "gap-3 pb-[14px]"}`}>
      <div
        ref={ref}
        data-flip={flip}
        className={`relative w-full overflow-clip ${hover?.e ? "cursor-pointer" : "cursor-default"} rounded-sm bg-sunken ${phone ? "h-11 touch-pan-y" : slim ? "h-6" : "h-10"}`}
        onPointerDown={(ev) => { down.current = ev.clientX; if (phone) setHover(at(ev.clientX)); }}
        onPointerMove={(ev) => { if (!phone || down.current != null) setHover(at(ev.clientX)); }}
        onPointerUp={() => { if (phone) setHover(null); }}
        onPointerCancel={() => { down.current = null; setHover(null); }}
        onPointerLeave={() => setHover(null)}
        onClick={(ev) => {
          // A drag along the bar was a scrub, not a pick.
          const moved = down.current != null && Math.abs(ev.clientX - down.current) > 6;
          down.current = null;
          const h = at(ev.clientX);
          if (h.e && !moved) onSelect(h.e);
        }}
      >
        {/* No model has looked at this day, or at this part of today. */}
        {!judged && !unread && (
          <div className="hatch absolute inset-y-0 left-0" style={{ width: `${x(end)}%` }} />
        )}
        {evs.map((e) => (
          <div
            key={`${e.a}-${e.b}`}
            className="absolute inset-y-0"
            style={{
              left: `${x(e.a)}%`,
              width: `max(calc(${x(e.b) - x(e.a)}% - 1px), 1px)`,
              background: cat(e.cat),
            }}
          />
        ))}
        {failed.map((w) => (
          <div key={w.a} className="hatch absolute inset-y-0"
            style={{ left: `${x(w.a)}%`, width: `${x(w.b) - x(w.a)}%` }} />
        ))}
        {now != null && (
          <>
            <div className="absolute inset-y-0 w-[2px] bg-ink" style={{ left: `${x(now)}%` }} />
            <div className="absolute inset-y-0 right-0 bg-canvas" style={{ left: `calc(${x(now)}% + 2px)` }} />
          </>
        )}
        {/* The selected block is drawn again on top, so a neighbour that
            overlaps it (two events the model let touch) never shows inside
            its outline. */}
        {selected && !phone && (
          <div className="pointer-events-none absolute inset-y-0"
            style={{ left: `${x(selected.a)}%`, width: `max(calc(${x(selected.b) - x(selected.a)}% - 1px), 1px)`, background: cat(selected.cat) }} />
        )}
        {selected && !phone && (
          <div className="pointer-events-none absolute inset-y-0 rounded-[3px] shadow-[inset_0_0_0_2px_var(--color-ink)]"
            style={{ left: `${x(selected.a)}%`, width: `calc(${x(selected.b) - x(selected.a)}% - 1px)` }} />
        )}
        {hover && !phone && <div className="pointer-events-none absolute inset-y-0 w-px bg-ink" style={{ left: hover.x }} />}
        {phone && hover?.e && (
          <div className="pointer-events-none absolute inset-y-0"
            style={{ left: `${x(hover.e.a)}%`, width: `${x(hover.e.b) - x(hover.e.a)}%`, background: cat(hover.e.cat) }} />
        )}
        {phone && hover?.e && (
          <div className="pointer-events-none absolute inset-y-0 rounded-[3px] shadow-[inset_0_0_0_2px_var(--color-ink)]"
            style={{ left: `${x(hover.e.a)}%`, width: `${x(hover.e.b) - x(hover.e.a)}%` }} />
        )}
        {switches.map((s) => (
          <div key={s.at} className="pointer-events-none absolute inset-y-0 w-px bg-ink" style={{ left: `${x(s.at)}%` }} />
        ))}
        {mark && (
          <div className="pointer-events-none absolute inset-y-0 rounded-[2px] shadow-[inset_0_0_0_2px_var(--color-ink)]"
            style={{ left: `${x(mark.a)}%`, width: `${x(mark.b) - x(mark.a)}%` }} />
        )}
      </div>

      {now != null && (
        <span className="absolute -top-5 font-mono text-2xs leading-[14px] text-ink-muted"
          style={{ left: `calc(${x(now)}% + 6px)` }}>
          now {wallHm(now, segs)}
        </span>
      )}

      {!slim && switches.map((s) => (
        <span key={s.at} className="absolute -top-5 whitespace-nowrap font-mono text-2xs leading-[14px] text-ink"
          style={{ left: `calc(${x(s.at)}% + 6px)` }}>
          {s.label}
        </span>
      ))}

      {mark && !hover && (
        <span className="absolute -top-5 -translate-x-1/2 whitespace-nowrap font-mono text-2xs leading-[14px] text-ink"
          style={{ left: `${x((mark.a + mark.b) / 2)}%` }}>
          {wallRange(mark.a, mark.b, segs)} · {mark.label}
        </span>
      )}

      {shown && !(mark && !hover) && (
        <div
          className="pointer-events-none absolute -top-[34px] flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-[6px] bg-ink px-[9px] py-[5px] shadow-[0_1px_3px_#00000033]"
          style={{ left: Math.min(Math.max(chipX, 110), width - 110) }}
        >
          <i className="size-[7px] rounded-full" style={{ background: cat(shown.cat) }} />
          <span className="text-xs/tight font-semibold text-canvas">{catName(meta, shown.cat)}</span>
          <span className="font-mono text-2xs leading-[14px] text-canvas/70">{wallRange(shown.a, shown.b, segs)}</span>
          <span className="font-mono text-2xs leading-[14px] text-canvas/50">{shown.b - shown.a}m</span>
        </div>
      )}

      <div data-flip={flip && `${flip}-ticks`}
        className={`relative w-full font-mono text-ink-subtle ${phone || slim ? "h-[14px] text-2xs leading-[14px]" : "h-4 text-xs/tight"}`}>
        {clockTicks(segs, n, phone ? 6 : undefined).map((t) => (
          <span key={t.m} className="absolute" style={{ left: `${x(t.m)}%` }}>{t.label}</span>
        ))}
      </div>
    </div>
  );
}
