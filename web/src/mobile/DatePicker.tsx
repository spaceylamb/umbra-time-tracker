// The phone's date picker, opened from the date title —
// and the same month as a week picker, where a tap takes the whole row. The
// desktop's week picker (06c) is the same month in a popover, smaller.
//
// A month, Monday first. Two marks under a day, and only two: • a model has
// looked, ○ recorded but not judged. Days with nothing recorded are there to
// keep the calendar a calendar, but cannot be opened.
import { useQueries } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { Meta } from "../api/types";
import { dayQuery } from "../api/client";
import { eventsOf } from "../lib/day";
import { monthName } from "../lib/time";
import { isoWeek } from "../lib/week";
import { Sheet } from "../ui/Sheet";

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const Arrow = ({ d }: { d: string }) => (
  <svg width="14" height="14" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={d} /></svg>
);

/** date: the day open now — in week mode, any day of the week open now. */
export function DatePicker({ meta, date, onClose, week = false }: {
  meta: Meta; date: string; onClose: () => void; week?: boolean;
}) {
  return (
    <Sheet label={week ? "Pick a week" : "Pick a day"} onClose={onClose}>
      <div className="px-4 pt-2 pb-[calc(16px+env(safe-area-inset-bottom))]">
        <Month meta={meta} date={date} onClose={onClose} week={week} />
      </div>
    </Sheet>
  );
}

/** The month itself. compact: the desktop popover's sizes (06c). */
export function Month({ meta, date, onClose, week = false, compact = false }: {
  meta: Meta; date: string; onClose: () => void; week?: boolean; compact?: boolean;
}) {
  const nav = useNavigate();
  const [y0, m0] = date.split("-").map(Number);
  const [ym, setYm] = useState({ y: y0!, m: m0! - 1 });
  const first = new Date(ym.y, ym.m, 1);
  const lead = (first.getDay() + 6) % 7;
  const cells = Array.from({ length: Math.ceil((lead + new Date(ym.y, ym.m + 1, 0).getDate()) / 7) * 7 },
    (_, i) => new Date(ym.y, ym.m, 1 - lead + i));
  const has = new Set(meta.order);
  const inMonth = cells.map(iso).filter((d) => has.has(d));
  const qs = useQueries({ queries: inMonth.map((d) => dayQuery(d)) });
  const judged = new Map(inMonth.map((d, i) => [d, qs[i]?.data ? eventsOf(qs[i]!.data).length > 0 : null]));
  const shift = (n: number) => setYm(({ y, m }) => ({ y: m + n < 0 ? y - 1 : m + n > 11 ? y + 1 : y, m: (m + n + 12) % 12 }));
  const open = (d: string) => {
    onClose();
    if (week) nav({ to: "/week/$week", params: { week: isoWeek(d) }, resetScroll: false });
    else nav({ to: "/day/$date", params: { date: d }, resetScroll: false });
  };
  const curWeek = isoWeek(date);
  const rows = Array.from({ length: cells.length / 7 }, (_, r) => cells.slice(r * 7, r * 7 + 7));

  const nb = compact ? "size-[26px] rounded-[6px] hover:bg-hover" : "size-11";
  return (
      <div className={`flex flex-col ${compact ? "gap-3" : ""}`}>
        <div className={`flex items-center ${compact ? "gap-2" : ""}`}>
          <button type="button" aria-label="Previous month" onClick={() => shift(-1)}
            className={`flex items-center justify-center text-ink-muted ${nb}`}><Arrow d="M7 3L4 6L7 9" /></button>
          <h2 className={`grow text-center font-semibold ${compact ? "text-base leading-[18px]" : "text-md/snug tracking-[-0.01em]"}`}>{monthName(ym.m)} {ym.y}</h2>
          <button type="button" aria-label="Next month" onClick={() => shift(1)}
            className={`flex items-center justify-center text-ink-muted ${nb}`}><Arrow d="M5 3L8 6L5 9" /></button>
        </div>
        <div className={`flex items-center gap-[2px] font-mono text-2xs text-ink-subtle ${compact ? "px-1 leading-[14px]" : "h-[30px] pt-1.5 leading-4"}`}>
          {["M", "T", "W", "T", "F", "S", "S"].map((d, i) => <span key={i} className="grow basis-0 text-center">{d}</span>)}
        </div>
        <div className="flex flex-col gap-[2px]">
          {rows.map((row) => {
            const wk = isoWeek(iso(row[0]!));
            const rowOn = week && row.some((c) => has.has(iso(c)));
            const rowSel = week && wk === curWeek;
            return (
              <div key={wk} className={`flex gap-[2px] ${compact ? "rounded-md px-1 py-px" : "rounded-[10px]"} ${rowSel ? "bg-sunken" : ""}`}>
                {row.map((c) => {
                  const d = iso(c);
                  const j = judged.get(d);
                  const on = has.has(d);
                  const today = d === meta.today;
                  const sel = !week && d === date;
                  const ink = today ? "bg-ink text-canvas"
                    : sel ? "bg-sunken text-ink"
                    : on && c.getMonth() === ym.m ? "text-ink" : "text-ink-subtle";
                  return (
                    <button key={d} type="button" disabled={week ? !rowOn : !on} onClick={() => open(d)}
                      aria-current={sel ? "date" : undefined}
                      className={`flex grow basis-0 flex-col items-center justify-center font-mono ${ink}
                        ${compact ? "gap-[3px] rounded-[6px] py-[5px] text-sm/tight enabled:hover:bg-hover" : "h-11 gap-1 rounded-md text-[15px] leading-[18px]"}
                        ${today && compact ? "enabled:hover:bg-ink" : ""}`}>
                      {c.getDate()}
                      {j === true ? <i className={`size-[3px] rounded-full ${today ? "bg-canvas" : "bg-ink-subtle"}`} />
                        : j === false ? <i className={`size-[5px] rounded-full border ${today ? "border-canvas" : "border-ink-subtle"}`} />
                        : <i className="size-[3px]" />}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
        <div className={`flex text-xs/4 text-ink-subtle ${compact ? "gap-3.5" : "gap-5 py-[15px]"}`}>
          <span className="flex items-center gap-1.5"><i className="size-[3px] rounded-full bg-ink-subtle" />a model has looked</span>
          <span className="flex items-center gap-1.5"><i className="size-[5px] rounded-full border border-ink-subtle" />recorded, not judged</span>
        </div>
        <div className={`flex items-center gap-2.5 border-t border-border ${compact ? "pt-2.5" : "pt-3"}`}>
          <span className={`text-ink-subtle ${compact ? "text-xs/tight" : "text-sm leading-[18px]"}`}>{week ? "Pick a week, not a day" : "Tap a day to open it"}</span>
          <span className="grow" />
          {has.has(meta.today) && (
            <button type="button" onClick={() => open(meta.today)}
              className={`flex items-center bg-sunken font-medium ${compact ? "rounded-[6px] px-2.5 py-1 text-xs/tight hover:bg-fill-hover" : "h-[34px] rounded-md px-3.5 text-sm/tight"}`}>
              {week ? "This week" : "Today"}
            </button>
          )}
        </div>
      </div>
  );
}
