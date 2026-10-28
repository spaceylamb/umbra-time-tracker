import type { DayEvent } from "../api/types";
import { wallRange, type Seg } from "../lib/clock";
import { cat, rows } from "../lib/day";
import { useFluidHover } from "../ui/fluid";

export function EventList({
  evs, selected, onSelect, end, failed = [], segs = [], onCorrect,
}: {
  /** A stretch the model said nothing about: say what it was. */
  onCorrect?: (a: number, b: number) => void;
  /** The clocks the day was read on: times are shown as the wall read them. */
  segs?: Seg[];
  failed?: { a: number; b: number }[];
  evs: DayEvent[];
  selected?: DayEvent;
  onSelect: (e: DayEvent) => void;
  end: number;
}) {
  const fluid = useFluidHover<HTMLOListElement>();
  return (
    <ol ref={fluid.ref} {...fluid.handlers} className="relative flex w-[560px] shrink-0 flex-col">
      {fluid.highlight()}
      {rows(evs, end, failed).map((r) =>
        r.kind === "failed" ? (
          <li key={`f${r.a}`} className="flex items-center gap-[14px] rounded-[6px] px-3 py-[9px] text-ink-subtle">
            <span className="w-[102px] shrink-0 font-mono text-xs/tight">{wallRange(r.a, r.b, segs)}</span>
            <span className="w-10 shrink-0 text-right font-mono text-xs/tight">{r.b - r.a}m</span>
            <i className="hatch size-2 shrink-0 rounded-[2px] bg-sunken" />
            <span className="grow text-base leading-[18px]">The call for this window failed — retried next run</span>
          </li>
        ) : r.kind === "gap" ? (
          <li key={`g${r.a}`} className="flex items-center gap-[14px] rounded-[6px] px-3 py-[9px] text-ink-subtle/80">
            <span className="w-[102px] shrink-0 font-mono text-xs/tight">{wallRange(r.a, r.b, segs)}</span>
            <span className="w-10 shrink-0 text-right font-mono text-xs/tight">{r.b - r.a}m</span>
            <i className="size-2 shrink-0 rounded-[2px] border border-dashed border-border-strong" />
            <span className="grow text-base leading-[18px]">The model said nothing about this stretch</span>
            {onCorrect && (
              <button type="button" onClick={() => onCorrect(r.a, r.b)}
                className="shrink-0 text-[13px] text-ink underline decoration-1 underline-offset-2 hover:text-ink-muted">Correct</button>
            )}
          </li>
        ) : (
          <li key={`e${r.e.a}`} data-fluid className="relative">
            <button
              type="button"
              onClick={() => onSelect(r.e)}
              aria-current={r.e === selected ? "true" : undefined}
              className={`flex w-full items-center gap-[14px] rounded-[6px] px-3 py-[9px] text-left transition-colors duration-[80ms]
                active:bg-active ${r.e === selected ? "bg-surface" : ""}`}
            >
              <span className="w-[102px] shrink-0 font-mono text-xs/tight text-ink-muted">{wallRange(r.e.a, r.e.b, segs)}</span>
              <span className="w-10 shrink-0 text-right font-mono text-xs/tight text-ink-muted">{r.e.b - r.e.a}m</span>
              <i className="size-2 shrink-0 rounded-[2px]" style={{ background: cat(r.e.cat) }} />
              <span className={`grow text-base leading-[18px] ${r.e === selected ? "font-medium" : ""}`}>
                {r.e.title}
              </span>
              {r.e.mine && <span className="shrink-0 font-mono text-2xs text-ink-muted" title="Your correction">✎</span>}
            </button>
          </li>
        ),
      )}
    </ol>
  );
}
