// The Day view on a phone.
//
// One column, top to bottom: the mark row (scrolls away), the date row
// (sticks), the headline numbers and the bar, two rows that open sheets —
// what was said aloud, and the day's sources — then the list. What the
// desktop shows beside the list (the event, the drawer) the phone slides up
// as a sheet over it.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import type { Day, DayEvent, Meta } from "../api/types";
import { saidQuery } from "../api/client";
import { cat, minutesByCat, minutesOf, rows } from "../lib/day";
import { dayLen, tzLine, wallHm } from "../lib/clock";
import { ago, phoneDate } from "../lib/time";
import { status } from "../lib/fresh";
import { DayBar } from "../day/DayBar";
import { EventDetail } from "../day/EventDetail";
import { Lanes, Recorded } from "../day/Recorded";
import { SaidDrawer, type Tab } from "../day/Said";
import { anything, itemsOf, tabsOf } from "../lib/said";
import { Sheet } from "../ui/Sheet";
import { MarkRow, Metrics, PhoneNav, TabBar, TAB_PAD } from "./Shell";
import { DatePicker } from "./DatePicker";

type Open = { k: "event"; e: DayEvent } | { k: "said"; tab: Tab } | { k: "sources" } | { k: "date" } | null;

const Wave = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" aria-hidden className="shrink-0 text-ink-muted">
    <path d="M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3" />
  </svg>
);

const Chev = () => <span aria-hidden className="pl-0.5 text-md leading-[18px] text-ink-subtle">›</span>;

function DateRow({ meta, date, day, onPick }: { meta: Meta; date: string; day: Day; onPick: () => void }) {
  const i = meta.order.indexOf(date);
  const prev = i > 0 ? meta.order[i - 1] : undefined;
  const next = i >= 0 && i < meta.order.length - 1 ? meta.order[i + 1] : undefined;
  const { last } = status(meta);
  return (
    <PhoneNav title={phoneDate(date)} onPick={onPick}
      prev={prev ? { to: "/day/$date", params: { date: prev }, label: "Previous day" } : undefined}
      next={next ? { to: "/day/$date", params: { date: next }, label: "Next day" } : undefined}
      live={!!last}
      status={`${[tzLine(day, date === meta.today), last ? `updated ${ago(last)}` : "never updated"].filter(Boolean).join(" · ")}`} />
  );
}

function Row({ onClick, children, className = "" }: { onClick: () => void; children: ReactNode; className?: string }) {
  return (
    <button type="button" onClick={onClick}
      className={`flex h-[52px] w-full shrink-0 items-center gap-2 border-t border-border px-4 text-left active:bg-hover ${className}`}>
      {children}
    </button>
  );
}

export function MobileDay({ meta, day, date, evs, end, failed, unjudged }: {
  meta: Meta; day: Day; date: string; evs: DayEvent[]; end: number;
  failed: { a: number; b: number }[]; unjudged: ReactNode;
}) {
  const said = useQuery(saidQuery(date)).data;
  const [open, setOpen] = useState<Open>(() => {
    const q = new URLSearchParams(location.search);
    const s = q.get("said");
    return s === "todo" || s === "check" ? { k: "said", tab: s } : q.has("sources") ? { k: "sources" } : null;
  });
  const [saidSel, setSaidSel] = useState<{ tab: Tab; id: number } | null>(null);
  useEffect(() => { setOpen((o) => (o?.k === "event" ? null : o)); setSaidSel(null); }, [date]);

  const isToday = date === meta.today;
  const mins = minutesByCat(evs);
  const mm = minutesOf(day, evs, end);
  const models = day.ev ? Object.keys(day.ev).length : 0;
  const { stale } = status(meta);
  const recording = Object.values(day.counts).filter((n) => (n ?? 0) > 0).length;
  const recordings = day.counts.plaud;

  const picked = said && saidSel
    ? itemsOf(said, saidSel.tab).find((x) => x.id === saidSel.id)
    : undefined;
  const mark = picked ? { a: picked.win[0]!, b: picked.win[1]!, label: `said at ${picked.t}` } : undefined;

  return (
    <main className={`mx-auto w-full max-w-[640px] ${TAB_PAD}`}>
      <div className="px-4 pt-[10px] pb-[2px]"><MarkRow /></div>
      <div className="page-in">
      <DateRow meta={meta} date={date} day={day} onPick={() => setOpen({ k: "date" })} />

      <section className="flex flex-col gap-[18px] px-4 pt-[18px] pb-5">
        <Metrics meta={meta} mins={mins} judged={evs.length > 0} />
        <div className="flex flex-col gap-2 pt-1">
          <DayBar phone meta={meta} evs={evs} onSelect={(e) => setOpen({ k: "event", e })}
            now={isToday ? end : undefined} failed={failed} mark={mark} n={dayLen(day)} segs={day.tz} />
          <div className="flex flex-col gap-[2px] pt-[2px] font-mono text-xs/tight">
            {evs.length ? (
              <>
                <span className="text-ink-muted">covered {mm.covered.toLocaleString()} of {mm.end.toLocaleString()} min{isToday ? " so far" : ""}</span>
                <span className="text-ink-subtle">{mm.silent.toLocaleString()} min the model said nothing about</span>
                {mm.failed > 0 && <span className="text-ink-subtle">{mm.failed} min where the call failed</span>}
              </>
            ) : (
              <>
                <span className="text-ink-muted">0 of {end.toLocaleString()} min judged so far</span>
                {isToday && <span className="text-ink-subtle">{wallHm(end, day.tz ?? [])} onward has not happened yet</span>}
              </>
            )}
          </div>
        </div>
      </section>

      {evs.length === 0 && <div className="border-t border-border px-4 pt-5">{unjudged}</div>}

      {said && anything(said) && (
        <Row onClick={() => setOpen({ k: "said", tab: (tabsOf(said).find((t) => itemsOf(said, t.tab).length) ?? tabsOf(said)[0]!).tab })}>
          <Wave />
          {tabsOf(said).map((t, i) => (
            <span key={t.tab} className="contents">
              {i > 0 && <span className="text-base leading-[18px] text-ink-subtle">·</span>}
              <span className="text-base leading-[18px] font-medium">{t.n} {t.label.toLowerCase()}</span>
            </span>
          ))}
          <span className="grow" />
          {recordings != null && (
            <span className="font-mono text-xs/tight text-ink-subtle">{recordings} recording{recordings === 1 ? "" : "s"}</span>
          )}
          <Chev />
        </Row>
      )}
      {evs.length > 0 && (
        <Row onClick={() => setOpen({ k: "sources" })} className="gap-2.5 border-b">
          <span className="text-base leading-[18px] font-medium">Sources</span>
          <span className="grow" />
          <span className="font-mono text-xs/tight text-ink-subtle">{recording} recording</span>
          {stale.length > 0 && (
            <span className="font-mono text-xs/tight text-ink-muted">
              <span className="text-alert">⚠</span> {stale.length} stale
            </span>
          )}
          <Chev />
        </Row>
      )}

      {evs.length > 0 ? (
        <section className="pt-7">
          <div className="flex items-baseline px-4 pb-2.5">
            <h2 className="text-[15px] leading-[18px] font-semibold tracking-[-0.01em]">What happened this day</h2>
            <span className="grow" />
            <span className="font-mono text-2xs leading-[14px] text-ink-subtle">{evs.length} events</span>
          </div>
          <ol className="border-t border-border">
            {rows(evs, end, failed).map((r) => {
              const line = (a: number, b: number, dot: ReactNode, title: ReactNode, dim: boolean) => (
                <span className="flex w-full items-start gap-2 px-4 py-2.5">
                  <span className="w-11 shrink-0 font-mono text-2xs leading-[18px] text-ink-subtle">{wallHm(a, day.tz ?? [])}</span>
                  <span className="flex h-[18px] w-2 shrink-0 items-center">{dot}</span>
                  <span className={`grow basis-0 text-base leading-[18px] ${dim ? "text-ink-subtle" : ""}`}>{title}</span>
                  <span className="w-10 shrink-0 text-right font-mono text-2xs leading-[18px] text-ink-subtle">{b - a}m</span>
                </span>
              );
              return (
                <li key={r.kind === "event" ? `e${r.e.a}` : `${r.kind}${r.a}`} className="border-b border-border">
                  {r.kind === "event" ? (
                    <button type="button" className="w-full text-left active:bg-hover"
                      onClick={() => setOpen({ k: "event", e: r.e })}>
                      {line(r.e.a, r.e.b, <i className="size-2 rounded-[2px]" style={{ background: cat(r.e.cat) }} />, r.e.title, false)}
                    </button>
                  ) : r.kind === "failed" ? (
                    line(r.a, r.b, <i className="hatch size-2 rounded-[2px] bg-sunken" />,
                      "The call for this window failed — retried next run", true)
                  ) : (
                    line(r.a, r.b, <i className="size-2 rounded-[2px] border border-dashed border-border-strong" />,
                      "The model said nothing about this stretch", true)
                  )}
                </li>
              );
            })}
          </ol>
          {models > 1 && (
            <p className="max-w-[330px] px-4 pt-3.5 text-xs leading-[18px] text-ink-subtle">
              {models - 1} more model{models > 2 ? "s" : ""} judged this day, just not drawn.
            </p>
          )}
        </section>
      ) : (
        <div className="border-t border-border px-4 pt-6"><Recorded meta={meta} day={day} end={end} phone /></div>
      )}

      </div>
      <TabBar meta={meta} view="day" date={date} />

      {open?.k === "event" && (
        <Sheet label={open.e.title} onClose={() => setOpen(null)}>
          <div className="px-5 pt-3.5 pb-[calc(34px+env(safe-area-inset-bottom))]">
            <EventDetail meta={meta} date={date} e={open.e} segs={day.tz} n={dayLen(day)}
              onSaved={() => setOpen(null)} />
          </div>
        </Sheet>
      )}
      {open?.k === "said" && said && (
        <SaidDrawer phone said={said} tab={open.tab} date={date}
          setTab={(t) => setOpen({ k: "said", tab: t })}
          sel={saidSel?.tab === open.tab ? saidSel.id : null}
          onSel={(id) => { setSaidSel({ tab: open.tab, id }); setOpen(null); window.scrollTo({ top: 0, behavior: "smooth" }); }}
          onClose={() => setOpen(null)} />
      )}
      {open?.k === "sources" && (
        <DaySources meta={meta} day={day} date={date} evs={evs} end={end} failed={failed}
          recording={recording} onClose={() => setOpen(null)} />
      )}
      {open?.k === "date" && <DatePicker meta={meta} date={date} onClose={() => setOpen(null)} />}
    </main>
  );
}

/** The day's sources — the bar again, then every lane under it. */
function DaySources({ meta, day, date, evs, end, failed, recording, onClose }: {
  meta: Meta; day: Day; date: string; evs: DayEvent[]; end: number;
  failed: { a: number; b: number }[]; recording: number; onClose: () => void;
}) {
  const { stale } = status(meta);
  return (
    <Sheet label="Sources" onClose={onClose}>
      <div className="flex flex-col gap-5 px-4 pt-3.5 pb-[calc(34px+env(safe-area-inset-bottom))]">
        <header className="flex flex-col gap-1">
          <div className="flex items-baseline">
            <h2 className="text-lg leading-6 font-semibold tracking-tight">Sources</h2>
            <span className="grow" />
            <Link to="/sources" className="text-sm/tight font-medium">All sources ›</Link>
          </div>
          <p className="font-mono text-xs/tight text-ink-subtle">
            {phoneDate(date)} · {recording} recording
            {stale.length > 0 && <> · <span className="text-alert">⚠</span> {stale.length} stale</>}
          </p>
        </header>
        <div className="flex flex-col gap-2">
          <h3 className="text-xs/4 text-ink-subtle">What the model made of it</h3>
          <DayBar phone meta={meta} evs={evs} onSelect={() => {}}
            now={date === meta.today ? end : undefined} failed={failed} n={dayLen(day)} segs={day.tz} />
        </div>
        <Lanes meta={meta} day={day} end={end} full />
      </div>
    </Sheet>
  );
}
