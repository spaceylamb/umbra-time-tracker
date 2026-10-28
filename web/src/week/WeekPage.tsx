import { useQueries, useQuery } from "@tanstack/react-query";
import { Pending } from "../ui/Pending";
import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { weekRoute } from "../router";
import { dayQuery, metaQuery, weekQuery } from "../api/client";
import type { Day, Meta } from "../api/types";
import { Month } from "../mobile/DatePicker";
import { useTheme } from "../theme";
import { eventsOf, minutesByCat } from "../lib/day";
import { hours } from "../lib/time";
import { addDays, mondayOf, shiftWeek, weekTitle } from "../lib/week";
import { Topbar, TOPBAR_H } from "../day/Topbar";
import { Button } from "../ui/Button";
import { WeekGrid } from "./WeekGrid";
import { likeForLike } from "../lib/clock";
import { Where, Versus } from "./Charts";
import { Priorities } from "./Priorities";
import { useMobile } from "../lib/mobile";
import { MobileWeek } from "../mobile/MobileWeek";
import { useStuck } from "../lib/stuck";
import { Pinned, InlineNumbers, stageOf, PIN_HEAD_H } from "../ui/Pinned";
import { useFlip, type Pair } from "../lib/flip";

const Chev = ({ d }: { d: string }) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={d} /></svg>
);

function sum(days: (Day | undefined)[]) {
  const m: Record<string, number> = {};
  for (const d of days) for (const [k, v] of Object.entries(minutesByCat(eventsOf(d)))) m[k] = (m[k] ?? 0) + v;
  return m;
}

/** The title opens a month to pick a week from — "take me to
 * June", where the arrows only step. A popover, closed by Escape or a click
 * anywhere else. */
/** ‹ › for weeks. quiet: the pinned header's ghost version. */
function WeekStepper({ week, canPrev, canNext, quiet = false }: { week: string; canPrev: boolean; canNext: boolean; quiet?: boolean }) {
  return (
    <div className="flex gap-[2px]">
      {([["Previous week", -1, canPrev, "M15 18l-6-6 6-6"], ["Next week", 1, canNext, "M9 18l6-6-6-6"]] as const).map(
        ([label, dir, ok, path]) => {
          const btn = (
            <Button variant={quiet ? "ghost" : "secondary"} icon disabled={!ok} aria-label={label} className="text-ink-muted">
              <Chev d={path} />
            </Button>
          );
          return ok ? (
            <Link key={label} to="/week/$week" params={{ week: shiftWeek(week, dir) }} resetScroll={false} tabIndex={-1}>{btn}</Link>
          ) : <span key={label}>{btn}</span>;
        },
      )}
    </div>
  );
}

/** compact: the pinned header's title — 15px, same picker. */
function WeekTitle({ meta, title, date, compact = false }: { meta: Meta; title: string; date: string; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("pointerdown", down);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("pointerdown", down); window.removeEventListener("keydown", key); };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog"
        className={`flex items-center rounded-md transition-colors duration-[80ms]
          ${compact ? "-ml-2 gap-1.5 px-2 py-0.5" : "-ml-2.5 gap-2 px-2.5 py-1"} ${open ? "bg-sunken" : "hover:bg-hover"}`}>
        {compact
          ? <span data-flip-to="week-title" className="text-[15px]/5 font-semibold tracking-[-0.01em]">{title}</span>
          : <h1 data-flip="week-title" className="text-[26px] leading-8 font-semibold tracking-tight">{title}</h1>}
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden className="text-ink-muted">
          <path d={open ? "M3 7.5L6 4.5L9 7.5" : "M3 4.5L6 7.5L9 4.5"} />
        </svg>
      </button>
      {open && (
        <div role="dialog" aria-label="Pick a week"
          className="absolute top-full -left-2.5 z-20 mt-2 w-[296px] origin-top-left rounded-[10px] bg-surface p-3.5
            shadow-(--shadow-popover) transition-[opacity,scale] duration-(--dur-moderate) ease-(--ease-spring) starting:scale-[0.97] starting:opacity-0">
          <Month compact week meta={meta} date={date} onClose={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}

export function WeekPage() {
  const { week } = weekRoute.useParams();
  const meta = useQuery(metaQuery);
  const wk = useQuery(weekQuery(week));
  const phone = useMobile();
  const [titleSentinel, titleGone] = useStuck<HTMLDivElement>(TOPBAR_H);
  // The priorities row appears as the cards' bottom edge reaches where the
  // row will end, so the row covers exactly the strip of card still showing:
  // no gap above the grid, and never a big and a small copy side by side.
  // The sentinel sits under the section's 44px bottom padding, so +44 more
  // puts the trigger at the cards' own bottom edge.
  const [prioSentinel, prioGone] = useStuck<HTMLDivElement>(TOPBAR_H + PIN_HEAD_H + 44 + 44, 16);
  const $ = (sel: string) => () => document.querySelector(sel);
  useFlip(titleGone, () => ["week-stepper", "week-title", "week-span",
    ...(meta.data?.groups ?? []).flatMap((g) => [`week-l-${g.key}`, `week-v-${g.key}`])]
    .map((k) => [$(`[data-flip="${k}"]`), $(`[data-flip-to="${k}"]`)] as Pair));
  useTheme(meta.data);

  const mon = mondayOf(week);
  const days = Array.from({ length: 7 }, (_, i) => addDays(mon, i));
  const last = days.map((d) => addDays(d, -7));
  const have = new Set(meta.data?.order ?? []);
  const want = [...days, ...last].filter((d) => have.has(d));
  const qs = useQueries({ queries: want.map((d) => dayQuery(d)) });
  const data: Record<string, Day | undefined> = Object.fromEntries(want.map((d, i) => [d, qs[i]?.data]));
  // The days arrive after the page does; until then "nothing judged" would
  // be a claim about the week, not about the network.
  const loading = qs.some((q) => q.isPending);

  if (!meta.data || !wk.data) return <Pending error={meta.error ?? wk.error} />;
  const today = meta.data.today;
  // "So far" ends at today, or at the last day there is any record of if that
  // is earlier — a week whose records stop on Tuesday is two days long for
  // every comparison, or it would read as five empty days.
  const lastData = meta.data.order.at(-1) ?? today;
  const edge = lastData < today ? lastData : today;
  const soFar = days.filter((d) => d <= edge);
  const n = soFar.length;
  const stopped = edge < today && n < 7;
  const cur = sum(soFar.map((d) => data[d]));
  // Like for like: a travel day (this week or the same weekday last week)
  // leaves that weekday out of the comparison. "Where the time went" keeps it.
  const like = likeForLike(soFar, last.slice(0, n), data);
  const curV = sum(like.cur.map((d) => data[d]));
  const prev = sum(like.prev.map((d) => data[d]));
  const first = meta.data.order[0];
  const canPrev = !!first && addDays(mon, -1) >= first;
  const canNext = days[6]! < today;

  const span = n === 0 ? (days[0]! > today ? "has not started" : "no records")
    : n < 7 ? `${n} day${n === 1 ? "" : "s"} ${stopped ? "recorded" : "so far"}` : "the whole week";
  const pinH = PIN_HEAD_H + (wk.data.priorities.length ? 44 : 0);   // pinned head + priorities row

  if (phone) {
    return <MobileWeek meta={meta.data} week={wk.data} days={days} data={data} soFar={soFar} stopped={stopped}
      cur={cur} curV={curV} prev={prev} left={like.left} canPrev={canPrev} canNext={canNext} loading={loading} />;
  }

  // The condensed header: the week and its numbers once the title row starts
  // under the topbar, then each priority's progress once the priorities start
  // under that — and the grid's day headers pin beneath it. The title and the
  // priorities morph out of their originals (lib/flip).
  const pinned = (
      <Pinned snapMore stage={stageOf(titleGone, prioGone)}
        head={
          <div className="flex h-full items-center gap-3 pt-1">
            <span data-flip-to="week-stepper"><WeekStepper quiet week={week} canPrev={canPrev} canNext={canNext} /></span>
            <WeekTitle compact meta={meta.data} title={weekTitle(days)} date={soFar.at(-1) ?? days[0]!} />
            <span data-flip-to="week-span" className="text-xs/tight text-ink-subtle">{span}</span>
            <span className="grow" />
            <span><InlineNumbers flip="week" items={meta.data.groups.map((g) => ({
              key: g.key, name: g.name, value: hours(g.cats.reduce((a, c) => a + (cur[c] ?? 0), 0)) }))} /></span>
          </div>
        }
        more={wk.data.priorities.length > 0 ? (
          <div className="flex h-11 items-start gap-8">
            {wk.data.priorities.map((p, i) => {
              const sess = p.unit === "sessions";
              const v = sess ? p.sessions : p.minutes / 60;
              return (
                <div key={i} className="pin-slide flex min-w-0 grow basis-0 items-center gap-2.5" style={{ transitionDelay: `${i * 30}ms` }}>
                  <span className="font-mono text-2xs text-ink-subtle">0{i + 1}</span>
                  <span className="min-w-0 truncate text-[13px]/4 text-ink-muted">{p.text}</span>
                  {p.target != null && (
                    <span className="h-[3px] w-14 shrink-0 overflow-clip rounded-[2px] bg-sunken">
                      <span className="block h-full bg-ink" style={{ width: `${Math.min(1, v / p.target) * 100}%` }} />
                    </span>
                  )}
                  <span className="shrink-0 font-mono text-xs/4">
                    {sess ? p.sessions : `${v.toFixed(1)}h`}{p.target != null ? <span className="text-ink-subtle">/{p.target}{sess ? "" : "h"}</span> : null}
                  </span>
                </div>
              );
            })}
          </div>
        ) : undefined} />
  );

  return (
    <main className="mx-auto w-full max-w-[1440px] px-10 pb-16">
      <Topbar meta={meta.data} view="week" date={soFar.at(-1) ?? days[0]} pinned={pinned} />
      <div className="page-in">
      <div ref={titleSentinel} aria-hidden className="h-px -mb-px" />
      <div className={`flex w-full items-center gap-5 pb-10 ${titleGone ? "opacity-0" : ""}`}>
        <div data-flip="week-stepper"><WeekStepper week={week} canPrev={canPrev} canNext={canNext} /></div>
        <div className="flex items-center gap-3">
          <WeekTitle meta={meta.data} title={weekTitle(days)} date={soFar.at(-1) ?? days[0]!} />
          <span data-flip="week-span" className="text-[13px] text-ink-subtle">
            {n === 0 ? (days[0]! > today ? "has not started" : "no records")
              : n < 7 ? `${n} day${n === 1 ? "" : "s"} ${stopped ? "recorded — the records stop there" : "so far"}`
              : "the whole week"}
          </span>
        </div>
        <div className="grow" />
        {meta.data.groups.length > 0 && (
          <dl className="flex items-center gap-6">
            {meta.data.groups.map((g) => (
              <div key={g.key} className="flex flex-col gap-px">
                <dt data-flip={`week-l-${g.key}`} className="text-xs/tight text-ink-subtle">{g.name}</dt>
                <dd data-flip={`week-v-${g.key}`} className="text-[22px] font-semibold leading-7 tracking-tight">
                  {hours(g.cats.reduce((a, c) => a + (cur[c] ?? 0), 0))}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      <Priorities week={wk.data} soFar={soFar.filter((d) => have.has(d))} />
      <div ref={prioSentinel} aria-hidden className="h-px -mb-px" />
      {/* The day headers pin under whatever the panel is showing *now*: if a
          fling outruns the step that opens the priorities row, they pin
          under the head instead of leaving a strip of page between. */}
      <WeekGrid meta={meta.data} days={days} data={data} stickTop={TOPBAR_H + (prioGone ? pinH : PIN_HEAD_H)} />
      <div className="flex w-full items-start gap-[72px]">
        <Where meta={meta.data} mins={cur} loading={loading} />
        <Versus meta={meta.data} cur={curV} prev={prev} n={Math.min(like.cur.length, 7)} left={like.left} />
      </div>
      </div>
    </main>
  );
}
