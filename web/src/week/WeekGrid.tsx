// "The week, hour by hour". One chart, not seven: 3-hour hairlines
// run across all columns; blocks carry no labels (hover names them, a click
// opens the day); the future is not drawn at full weight; an unread day is
// hatched only up to now. Every column is to one scale, so a day's length
// reads at a glance: a ten-hour travel day stops short, a thirty-nine-hour
// one runs on below the others, and the grid grows only in such a week.
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { Day, DayEvent, Meta } from "../api/types";
import { cat, catName, eventsOf } from "../lib/day";
import { dayLen, switchesOf, wallRange } from "../lib/clock";
import { hours, nowMinute } from "../lib/time";
import { WEEKDAY, WEEKDAY_LONG, dayOfMonth } from "../lib/week";
import { TOPBAR_H } from "../day/Topbar";

const H = 560;
const px = (m: number) => (m / 1440) * H;

export function WeekGrid({ meta, days, data, stickTop = TOPBAR_H }: {
  meta: Meta; days: string[]; data: Record<string, Day | undefined>;
  /** Where the day headers pin: under the topbar and the week's pinned header. */
  stickTop?: number;
}) {
  const nav = useNavigate();
  const today = meta.today;
  const now = nowMinute();
  // The longest day sets the grid's height; past midnight its hours read +00:00.
  const span = Math.max(1440, ...days.map((d) => dayLen(data[d])));
  const marks = Array.from({ length: Math.ceil(span / 180) }, (_, i) => i * 180);
  const clock = (m: number) => `${m >= 1440 ? "+" : ""}${String(Math.floor(m / 60) % 24).padStart(2, "0")}:00`;
  const unread = days.filter((d) => d <= today && data[d] && !eventsOf(data[d]).length);
  const future = days.find((d) => d > today);
  // The readout: follows the pointer down the column and holds its
  // x to the column, so it never jumps sideways between 6px slivers. No delay
  // in; 120ms out, keeping its last text while it fades.
  const [hover, setHover] = useState<{ i: number; e: DayEvent; y: number } | null>(null);
  const [shown, setShown] = useState(false);

  return (
    <section className="flex flex-col gap-5 pb-11">
      <div className="flex w-full items-center gap-[14px]">
        <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">The week, hour by hour</h2>
        <div className="h-px grow bg-border" />
        <span className="shrink-0 text-sm/tight text-ink-subtle">
          midnight to midnight · hover a block to name it, click to open the day
        </span>
      </div>

      <div className="relative w-full" style={{ height: px(span) + 30 }}>
        {/* The rules and the weekend sit behind the columns. */}
        {/* The weekend runs through the day headers too, so a pinned Sat/Sun
            header (shaded the same) reads as part of it. */}
        <div className="absolute inset-y-0 right-0 w-[27.5%] bg-[color-mix(in_oklab,var(--color-sunken)_45%,transparent)]" />
        <div className="absolute inset-x-0 top-[30px]" style={{ height: px(span) }}>
          {marks.slice(1).map((m) => (
            <div key={m} className="absolute left-11 right-0 h-px bg-border" style={{ top: px(m) }} />
          ))}
        </div>
        <div className="absolute top-[30px] left-0 w-11" style={{ height: px(span) }}>
          {marks.map((m) => (
            <span key={m} className="absolute font-mono text-2xs leading-[14px] text-ink-subtle"
              style={{ top: m === 0 ? 0 : px(m) - 7 }}>
              {clock(m)}
            </span>
          ))}
        </div>

        {/* Behind the pinned day headers, full width: covers the hour labels
            as they scroll under, and gives the headers room below the pinned
            header's hairline (the headers' own -mt-3/pt-3). */}
        <div aria-hidden className="sticky z-[9] -mt-3 -mb-[30px] h-[42px] bg-canvas" style={{ top: stickTop }} />
        <div className="relative flex w-full gap-3 pl-11">
          {days.map((d, i) => {
            const day = data[d];
            const evs = eventsOf(day);
            const isToday = d === today;
            const isFuture = d > today;
            const total = evs.reduce((n, e) => n + (e.b - e.a), 0);
            const len = dayLen(day);
            const end = day?.end ?? (isToday ? Math.min(now, len) : len);
            return (
              <div key={d} className="flex grow basis-0 flex-col">
                {/* Pinned under the topbar while the hours scroll past. */}
                <div className={`sticky z-10 -mt-3 flex h-[42px] items-center gap-1.5 pt-3 pb-2 text-sm/tight
                  ${i >= 5 ? `bg-[color-mix(in_oklab,var(--color-sunken)_45%,var(--color-canvas))] ${i === 5 ? "-mr-3 -ml-1.5 pr-3 pl-1.5" : ""}` : "bg-canvas"}`} style={{ top: stickTop }}>
                  <span className={isToday ? "text-ink" : "text-ink-muted"}>{WEEKDAY[i]}</span>
                  {isToday ? (
                    <span className="rounded-sm bg-ink px-[7px] py-px text-base/[18px] font-semibold text-canvas">{dayOfMonth(d)}</span>
                  ) : (
                    <span className={`text-base/[18px] font-semibold ${isFuture ? "text-ink-subtle" : "text-ink-muted"}`}>{dayOfMonth(d)}</span>
                  )}
                  <span className="grow" />
                  {day?.travel ? <span className="font-mono text-2xs whitespace-nowrap text-ink-subtle">{Math.round(len / 60)}h day</span>
                    : total > 0 && <span className="font-mono text-2xs text-ink-subtle">{hours(total)}</span>}
                </div>
                <div className="relative w-full" style={{ height: px(len) }}
                  onPointerMove={(ev) => { if (ev.target === ev.currentTarget) setShown(false); }}
                  onPointerLeave={() => setShown(false)}>
                  {!isFuture && day && !evs.length && (
                    <div className="hatch absolute inset-x-0 top-0 rounded-[2px]" style={{ height: px(end) }} />
                  )}
                  {evs.map((e) => (
                    <button key={`${e.a}`} type="button"
                      aria-label={`${e.title} · ${catName(meta, e.cat)} · ${wallRange(e.a, e.b, day?.tz ?? [])}`}
                      onClick={() => nav({ to: "/day/$date", params: { date: d } })}
                      onPointerMove={(ev) => {
                        if (ev.pointerType !== "mouse") return;
                        const r = ev.currentTarget.parentElement!.getBoundingClientRect();
                        setHover({ i, e, y: ev.clientY - r.top });
                        setShown(true);
                      }}
                      className="absolute inset-x-0 cursor-pointer rounded-[2px]"
                      style={{ top: px(e.a), height: Math.max(px(e.b - e.a) - 2, 2), background: cat(e.cat) }} />
                  ))}
                  {hover?.i === i && shown && (
                    <div className="pointer-events-none absolute inset-x-0 rounded-[3px] shadow-[inset_0_0_0_2px_var(--color-ink)]"
                      style={{ top: px(hover.e.a), height: Math.max(px(hover.e.b - hover.e.a) - 2, 2), background: cat(hover.e.cat) }} />
                  )}
                  {hover?.i === i && (
                    <div className={`pointer-events-none absolute z-20 flex max-w-[280px] flex-col gap-1 rounded-[6px] bg-ink px-[9px] py-[6px]
                      shadow-[0_1px_3px_#00000033] transition-opacity ${shown ? "opacity-100 duration-0" : "opacity-0 duration-(--dur-moderate-out)"}
                      ${i === 0 ? "left-0" : i === days.length - 1 ? "right-0" : "left-1/2 -translate-x-1/2"}`}
                      style={{ top: Math.max(hover.y - 58, -30) }}>
                      <span className="flex items-center gap-2 whitespace-nowrap">
                        <i className="size-[7px] shrink-0 rounded-full" style={{ background: cat(hover.e.cat) }} />
                        <span className="text-xs/tight font-semibold text-canvas">{catName(meta, hover.e.cat)}</span>
                        <span className="font-mono text-2xs leading-[14px] text-canvas/70">{wallRange(hover.e.a, hover.e.b, data[days[hover.i]!]?.tz ?? [])}</span>
                        <span className="font-mono text-2xs leading-[14px] text-canvas/50">{hover.e.b - hover.e.a}m</span>
                      </span>
                      <span className="truncate text-xs/4 text-canvas/80">{hover.e.title}</span>
                    </div>
                  )}
                  {isToday && (
                    <>
                      <div className="absolute inset-x-0 h-px bg-neutral-500" style={{ top: px(end) }} />
                      <span className="absolute font-mono text-[10px] leading-3 text-neutral-500" style={{ top: px(end) + 6 }}>now</span>
                    </>
                  )}
                  {/* Where the clock changed: a hairline of ink and both clocks. */}
                  {day?.tz && switchesOf(day.tz).map((s) => (
                    <div key={s.at} className="pointer-events-none absolute inset-x-0" style={{ top: px(s.at) }}>
                      <div className="h-px bg-ink" />
                      <span className="absolute top-[3px] left-1 rounded-[3px] bg-canvas px-[3px] font-mono text-2xs leading-[14px] whitespace-nowrap text-ink">
                        {s.label.split(" · ")[0]}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <p className="text-xs/4 text-ink-subtle">
        {unread.length > 0 &&
          `${unread.map((d) => WEEKDAY_LONG[days.indexOf(d)]).join(" and ")} ${unread.length > 1 ? "are" : "is"} hatched: the records are in, no model has judged them yet. `}
        {future && `${WEEKDAY_LONG[days.indexOf(future)]} onward has not happened.`}
      </p>
    </section>
  );
}
