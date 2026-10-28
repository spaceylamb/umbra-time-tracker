// "What was recorded": the raw lanes, grey on purpose.
// Colour would mean a model has judged something, and none has.
import { useQuery } from "@tanstack/react-query";
import type { Day, Meta } from "../api/types";
import { detailQuery } from "../api/client";
import { dayLen } from "../lib/clock";
import { isStale } from "../lib/fresh";
import { ago, shortDate } from "../lib/time";

const AWAY = new Set(["place", "transit", "sleep", "workout", "media", "talk", "reading"]);

function Lane({ meta, day, k, runs, end, full }: {
  meta: Meta; day: Day; k: string; runs: [number, number, string][]; end: number; full: boolean;
}) {
  const src = meta.labels.lane_source[k] ?? k;
  const len = dayLen(day);
  const n = day.counts[src];
  // The phone's sources sheet (12f) also says how fresh each lane is, and on
  // today draws the stretch since its last record as a dashed box: time
  // that may still arrive, not time with nothing in it.
  const f = full ? meta.fresh.find((x) => x.source === src) : undefined;
  const stale = f ? isStale(f) : false;
  const lastMin = f?.last_event && day.date === meta.today
    ? (() => { const d = new Date(f.last_event!); return d.getHours() * 60 + d.getMinutes(); })() : null;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px]/4 font-medium">{meta.labels.source[k] ?? k}</span>
        <span className="font-mono text-xs/4 text-ink-subtle">{meta.labels.origin[k]?.[0]}</span>
        <span className="grow" />
        {stale && f ? (
          <span className="font-mono text-xs/4 text-ink-muted">
            <span className="text-alert" aria-hidden>⚠</span> last imported {f.last_ok ? shortDate(new Date(f.last_ok).toISOString().slice(0, 10)) : "never"}
          </span>
        ) : n != null && (
          <span className="font-mono text-xs/4 text-ink-muted">
            {n.toLocaleString()} records{f && (f.live ? " · live" : f.last_run ? ` · ${ago(f.last_run)}` : "")}
          </span>
        )}
      </div>
      <div className={`relative h-2 ${full ? "w-full" : "w-[69%]"}`}>
        <div className="absolute inset-y-0 left-0 rounded-[2px] bg-sunken" style={{ width: `${(end / len) * 100}%` }} />
        {runs.map(([a, b], i) => (
          <div key={i} className="absolute inset-y-0 rounded-[1px] bg-neutral-500 dark:bg-neutral-600"
            style={{ left: `${(a / len) * 100}%`, width: `max(${((b - a) / len) * 100}%, 2px)` }} />
        ))}
        {lastMin != null && lastMin < end && !stale && (
          <div className="absolute -inset-y-px rounded-[2px] border border-dashed border-ink-subtle"
            style={{ left: `${(lastMin / len) * 100}%`, width: `${((end - lastMin) / len) * 100}%` }} />
        )}
      </div>
    </div>
  );
}

/** The lanes alone, split into away-from-a-screen and machine-watched —
 * also what the phone's Day sources sheet (12f) shows under its bar. */
export function Lanes({ meta, day, end, full = false }: { meta: Meta; day: Day; end: number; full?: boolean }) {
  const detail = useQuery(detailQuery(day.date));
  const lanes = Object.entries(detail.data?.lanes ?? {}) as [string, [number, number, string][]][];
  const away = lanes.filter(([k]) => AWAY.has(k));
  const machine = lanes.filter(([k]) => !AWAY.has(k));
  return (
    <>
      {[["Away from a screen", away], ["What a machine watched you do", machine]].map(([title, ls], i) =>
        (ls as typeof lanes).length ? (
          <div key={title as string}
            className={`flex flex-col gap-4 ${full && i > 0 ? "border-t border-dashed border-border-strong pt-5" : ""}`}>
            <h3 className="text-xs/4 text-ink-subtle">{title as string}</h3>
            {(ls as typeof lanes).map(([k, runs]) => (
              <Lane key={k} meta={meta} day={day} k={k} runs={runs} end={end} full={full} />
            ))}
          </div>
        ) : null,
      )}
    </>
  );
}

export function Recorded({ meta, day, end, phone = false }: { meta: Meta; day: Day; end: number; phone?: boolean }) {
  return (
    <section className="flex flex-col gap-5">
      <div className="flex w-full items-center gap-[14px]">
        <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">What was recorded</h2>
        <div className="h-px grow bg-border" />
        <span className="shrink-0 text-sm/tight text-ink-subtle">
          {phone ? "measured, not judged" : "measured, not judged — none of this is a category yet"}
        </span>
      </div>
      <Lanes meta={meta} day={day} end={end} full={phone} />
      <p className="max-w-[760px] text-xs/[18px] text-ink-subtle">
        Every lane here is grey on purpose. Colour on this page would mean a model has judged something, and none has.
      </p>
    </section>
  );
}
