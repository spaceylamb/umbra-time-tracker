// The Week view on a phone.
//
// The desktop's hour-by-hour grid turns on its side: one 26px bar per day,
// midnight to midnight, the same shape as the Day bar — so a week reads as
// seven days stacked, and a tap on one opens it. "Where the time went" and
// "against last week" fold into one list: the bar is this week, the last
// column is the difference from the same days last week.
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { Day, Meta, Week } from "../api/types";
import { cat, eventsOf } from "../lib/day";
import { dayLen, leftNote, type Left } from "../lib/clock";
import { ago, hours, nowMinute } from "../lib/time";
import { status } from "../lib/fresh";
import { WEEKDAY, WEEKDAY_LONG, dayOfMonth, shiftWeek, weekTitle } from "../lib/week";
import { catName } from "../lib/day";
import { PhonePriorities } from "../week/Priorities";
import { DatePicker } from "./DatePicker";
import { MarkRow, Metrics, PhoneNav, TabBar, TAB_PAD } from "./Shell";
import { demo } from "../lib/demo";

const NUM = ["no", "one", "two", "three", "four", "five", "six", "seven"];

function Head({ title, right }: { title: string; right?: string }) {
  return (
    <div className="flex items-baseline px-4">
      <h2 className="text-[15px] leading-[18px] font-semibold tracking-[-0.01em]">{title}</h2>
      <span className="grow" />
      {right && <span className="font-mono text-2xs leading-[14px] text-ink-subtle">{right}</span>}
    </div>
  );
}

export function MobileWeek({ meta, week, days, data, soFar, stopped, cur, curV = cur, prev, left = [], canPrev, canNext, loading = false }: {
  meta: Meta; week: Week; days: string[]; data: Record<string, Day | undefined>;
  soFar: string[]; stopped: boolean; cur: Record<string, number>; prev: Record<string, number>;
  /** cur over only the days compared with last week — travel days left out. */
  curV?: Record<string, number>;
  left?: Left[];
  canPrev: boolean; canNext: boolean; loading?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  const today = meta.today;
  const now = nowMinute();
  const n = soFar.length;
  const nV = n - left.length;          // the days compared with last week
  const { last } = status(meta);
  const have = new Set(meta.order);
  const unread = days.filter((d) => d <= today && data[d] && !eventsOf(data[d]).length);
  const future = days.find((d) => d > today);
  const judged = Object.values(cur).some((v) => v > 0);
  const rows = Object.entries(cur).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = rows.reduce((t, [, v]) => t + v, 0);
  const max = rows[0]?.[1] ?? 1;
  const hasPrev = Object.keys(prev).length > 0;
  const span = n === 0 ? (days[0]! > today ? "has not started" : "no records")
    : n < 7 ? `${n} day${n === 1 ? "" : "s"} ${stopped ? "recorded" : "so far"}` : "the whole week";

  return (
    <main className={`mx-auto w-full max-w-[640px] ${TAB_PAD}`}>
      <div className="px-4 pt-[10px] pb-[2px]"><MarkRow /></div>
      <div className="page-in">
      <PhoneNav title={weekTitle(days)} onPick={() => setPicking(true)}
        prev={canPrev ? { to: "/week/$week", params: { week: shiftWeek(week.week, -1) }, label: "Previous week" } : undefined}
        next={canNext ? { to: "/week/$week", params: { week: shiftWeek(week.week, 1) }, label: "Next week" } : undefined}
        live={!!last} status={demo() ? "" : `${span} · ${last ? `updated ${ago(last)}` : "never updated"}`} />

      <div className="px-4 pt-[18px] pb-[22px]"><Metrics meta={meta} mins={cur} judged={judged} /></div>

      <PhonePriorities week={week} soFar={soFar.filter((d) => have.has(d))} />

      <section className="flex flex-col gap-3.5 pt-[26px]">
        <Head title="The week, day by day" right="tap a day to open it" />
        <div className="flex flex-col gap-2.5">
          {days.map((d, i) => {
            const day = data[d];
            const evs = eventsOf(day);
            const isToday = d === today;
            const isFuture = d > today;
            // Each bar is its own day, as on the Day page: a travel day's
            // length is said in words beside it, not by a shorter bar.
            const len = dayLen(day);
            const end = day?.end ?? (isToday ? Math.min(now, len) : len);
            const pct = (m: number) => (m / len) * 100;
            const sum = evs.reduce((t, e) => t + (e.b - e.a), 0);
            const body = (
              <>
                <span className="flex items-center gap-1.5">
                  <span className="text-xs/tight text-ink-subtle">{WEEKDAY[i]}</span>
                  {isToday ? (
                    <span className="flex h-[17px] items-center rounded-[4px] bg-ink px-[5px] text-xs leading-[15px] font-semibold text-canvas">{dayOfMonth(d)}</span>
                  ) : (
                    <span className={`text-sm/tight font-semibold ${isFuture || !day ? "text-ink-muted" : ""}`}>{dayOfMonth(d)}</span>
                  )}
                  <span className="grow" />
                  {day?.travel && <span className="font-mono text-2xs leading-[14px] text-ink-subtle">{Math.round(len / 60)}h day · </span>}
                  {sum > 0 ? <span className="font-mono text-2xs leading-[14px] text-ink-subtle">{hours(sum)}</span>
                    : day && !isFuture ? <span className="font-mono text-2xs leading-[14px] text-ink-subtle">not judged yet</span> : null}
                </span>
                <span className="relative block h-[26px] w-full overflow-clip rounded-[4px] bg-sunken">
                  {day && !evs.length && !isFuture && (
                    <span className="hatch absolute inset-y-0 left-0" style={{ width: `${pct(end)}%` }} />
                  )}
                  {evs.map((e) => (
                    <span key={e.a} className="absolute inset-y-0"
                      style={{ left: `${pct(e.a)}%`, width: `max(calc(${pct(e.b - e.a)}% - 1px), 1px)`, background: cat(e.cat) }} />
                  ))}
                  {isToday && <span className="absolute inset-y-0 w-px bg-ink-subtle" style={{ left: `${pct(end)}%` }} />}
                </span>
              </>
            );
            return have.has(d) ? (
              <Link key={d} to="/day/$date" params={{ date: d }} className="flex flex-col gap-[5px] px-4 active:opacity-70">{body}</Link>
            ) : (
              <div key={d} className="flex flex-col gap-[5px] px-4">{body}</div>
            );
          })}
        </div>
        <div className="flex px-4 font-mono text-2xs leading-[14px] text-ink-subtle">
          {[0, 6, 12, 18].map((h) => <span key={h} className="grow basis-0">{String(h).padStart(2, "0")}:00</span>)}
        </div>
        {(unread.length > 0 || future) && (
          <p className="max-w-[340px] px-4 text-xs leading-[18px] text-ink-subtle">
            {unread.length > 0 &&
              `${unread.map((d) => WEEKDAY_LONG[days.indexOf(d)]).join(" and ")} ${unread.length > 1 ? "are" : "is"} hatched: the records are in, no model has judged ${unread.length > 1 ? "them" : "it"} yet. `}
            {future && `${WEEKDAY_LONG[days.indexOf(future)]} onward has not happened.`}
          </p>
        )}
      </section>

      <section className="flex flex-col gap-3 pt-7">
        <Head title="Where the time went" right={`${(total / 60).toFixed(1)}h judged`} />
        {rows.length ? (
          <div className="flex flex-col gap-1.5">
            {rows.map(([c, v]) => {
              const d = (curV[c] ?? 0) - (prev[c] ?? 0);
              const flat = Math.abs(d) < 3;
              return (
                <div key={c} className="flex h-[22px] items-center gap-2 px-4">
                  <span className="w-[62px] shrink-0 truncate text-sm/tight">{catName(meta, c)}</span>
                  <span className="flex h-2 grow basis-0 overflow-clip rounded-[2px] bg-sunken">
                    <span style={{ width: `${(v / max) * 100}%`, background: cat(c) }} />
                  </span>
                  <span className="w-[42px] shrink-0 text-right font-mono text-2xs/tight text-ink-muted">{hours(v)}</span>
                  {hasPrev && (
                    <span className="w-11 shrink-0 text-right font-mono text-2xs/tight text-ink-subtle">
                      {flat ? "0.0h" : `${d > 0 ? "+" : "−"}${(Math.abs(d) / 60).toFixed(1)}h`}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className={`px-4 text-sm/tight text-ink-subtle ${loading ? "pending" : ""}`}>
            {loading ? "Loading the week…" : "No day this week has been judged yet."}</p>
        )}
        {rows.length > 0 && hasPrev && (
          <p className="max-w-[340px] px-4 text-xs leading-[18px] text-ink-subtle">
            {left.length > 0 ? `The right column is against the same ${NUM[nV]} day${nV === 1 ? "" : "s"} last week. ${leftNote(left)}`
              : n >= 7 ? "The right column is against last week."
              : `The right column is against the same ${NUM[n]} day${n === 1 ? "" : "s"} last week — ${NUM[n]} against ${NUM[n]}, not ${NUM[n]} against seven, which would show every category falling.`}
          </p>
        )}
      </section>

      </div>
      <TabBar meta={meta} view="week" date={soFar.at(-1) ?? days[0]} />
      {picking && <DatePicker week meta={meta} date={soFar.at(-1) ?? days[0]!} onClose={() => setPicking(false)} />}
    </main>
  );
}
