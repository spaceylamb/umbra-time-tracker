import { useQueries } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import type { Meta } from "../api/types";
import { dayQuery } from "../api/client";
import { dayLen } from "../lib/clock";
import { cat, eventsOf, minutesByCat } from "../lib/day";
import { shortDate } from "../lib/time";

const N = 21;
const H = 36;

/** Three weeks, one column per day: sleep at the bottom, the first group
 * (productive time) above it, everything else on top — enough to see which
 * days were unlike the others. A day no model has looked at is hatched.
 *
 * Press and drag along it to move through the days: the day under
 * the pointer opens as it passes, replacing history rather than stacking 21
 * entries on Back. The window holds still while dragging — it would slide
 * out from under the pointer otherwise — and re-centres on release. */
export function DayStrip({ meta, date }: { meta: Meta; date: string }) {
  // A 21-day window ending on this day; near the start of history it slides
  // right instead, so it stays full and this day is simply not the last one.
  const nav = useNavigate();
  const row = useRef<HTMLDivElement>(null);
  const [held, setHeld] = useState<number | null>(null);
  const i = Math.max(meta.order.indexOf(date), 0);
  const start = held ?? Math.min(Math.max(0, i - N + 1), Math.max(0, meta.order.length - N));
  const days = meta.order.slice(start, start + N);
  const last = days.at(-1);
  // Always 21 slots, so the strip does not change width near the first day.
  const pad = N - days.length;
  const go = (x: number, replace: boolean) => {
    const r = row.current!.getBoundingClientRect();
    const k = Math.floor(((x - r.left) / r.width) * N) - pad;
    const d = days[Math.min(Math.max(k, 0), days.length - 1)];
    if (d && d !== date) nav({ to: "/day/$date", params: { date: d }, replace, resetScroll: false });
  };
  const qs = useQueries({ queries: days.map((d) => dayQuery(d)) });
  // Draw the three weeks together or not at all: filling in column by column
  // as each day arrives reads as the data changing, not as it loading.
  const ready = qs.every((q) => q.data !== undefined || q.isError);
  const prod = meta.groups.find((g) => g.key !== "sleep");
  return (
    <div className="flex w-full items-end gap-5">
      <div className="flex flex-col gap-2">
        <div ref={row} className={`flex touch-none items-end gap-1 select-none ${held != null ? "cursor-grabbing" : "cursor-grab"}`}
          // Capturing the pointer sends the click to the row, not the link
          // under it — so a press opens its day here, and a click is just a
          // drag that never moved.
          onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); setHeld(start); go(e.clientX, false); }}
          onPointerMove={(e) => { if (held != null) go(e.clientX, true); }}
          onPointerUp={() => setHeld(null)}
          onPointerCancel={() => setHeld(null)}>
          {Array.from({ length: pad }, (_, i) => (
            <div key={`p${i}`} className="h-9 w-[14px]" />
          ))}
          {days.map((d, i) => {
            const evs = ready ? eventsOf(qs[i]?.data) : [];
            const m = minutesByCat(evs);
            const sleep = m.sleep ?? 0;
            const work = prod ? prod.cats.reduce((n, c) => n + (m[c] ?? 0), 0) : 0;
            const rest = Math.max(0, Object.values(m).reduce((a, b) => a + b, 0) - sleep - work);
            const px = (v: number) => (v / dayLen(qs[i]?.data)) * H;
            const sel = d === date;
            return (
              <Link key={d} to="/day/$date" params={{ date: d }} title={shortDate(d)} draggable={false}
                className={`flex h-9 w-[14px] shrink-0 flex-col overflow-clip rounded-[2px] bg-sunken
                  ${sel ? "shadow-[0_0_0_1px_var(--color-ink)]" : "opacity-30 hover:opacity-60"}
                  ${ready && qs[i]?.data && !evs.length ? "hatch" : ""}`}>
                {evs.length > 0 && (
                  <div className="flex h-full flex-col transition-opacity duration-200 starting:opacity-0">
                    <div style={{ height: px(rest), background: cat("other") }} />
                    <div style={{ height: px(work), background: cat("code") }} />
                    <div style={{ height: px(sleep), background: cat("sleep") }} />
                  </div>
                )}
              </Link>
            );
          })}
        </div>
        <div className="flex w-full font-mono text-2xs leading-[14px] text-ink-subtle">
          <span>{days[0] ? shortDate(days[0]) : ""}</span>
          <span className="grow" />
          <span>{last === meta.today ? "today" : last ? shortDate(last) : ""}</span>
        </div>
      </div>
      <div className="flex flex-col gap-[3px] pb-5 text-sm/tight">
        <p className="text-ink-muted">Drag to move through the last three weeks.</p>
        <p className="text-ink-subtle">
          Sleep, productive time, everything else — enough to see which days were unlike the others.
        </p>
      </div>
    </div>
  );
}
