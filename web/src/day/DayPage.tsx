import { useQuery } from "@tanstack/react-query";
import { Pending } from "../ui/Pending";
import { useEffect, useRef, useState } from "react";
import { dayRoute } from "../router";
import { dayQuery, metaQuery, saidQuery } from "../api/client";
import type { DayEvent } from "../api/types";
import { useTheme } from "../theme";
import { defaultEvent, eventsOf } from "../lib/day";
import { eventAt } from "../lib/correct";
import { dayLen, tzLine } from "../lib/clock";
import { nowMinute } from "../lib/time";
import { Topbar, TOPBAR_H } from "./Topbar";
import { useStuck } from "../lib/stuck";
import { hours, longDate } from "../lib/time";
import { minutesByCat } from "../lib/day";
import { Pinned, InlineNumbers, stageOf, PIN_HEAD_H } from "../ui/Pinned";
import { useFlip, type Pair } from "../lib/flip";

/** Room above the pinned slim bar (its readout chip rises into it). */
const BAR_ROW_PT = 20;

import { DayNav, DayStepper } from "./DayNav";
import { DayStrip } from "./DayStrip";
import { DayBar } from "./DayBar";
import { Coverage } from "./Coverage";
import { EventList } from "./EventList";
import { EventDetail } from "./EventDetail";
import { Notice } from "./Notice";
import { Recorded } from "./Recorded";
import { Button } from "../ui/Button";
import { SaidDrawer, SaidTrigger, type Tab } from "./Said";
import { itemsOf } from "../lib/said";
import { useSettings } from "../settings/SettingsDialog";
import { useMobile } from "../lib/mobile";
import { MobileDay } from "../mobile/MobileDay";

export function DayPage() {
  const { date } = dayRoute.useParams();
  const meta = useQuery(metaQuery);
  const day = useQuery(dayQuery(date));
  useTheme(meta.data);
  const evs = eventsOf(day.data);
  const [sel, setSel] = useState<DayEvent | undefined>();
  const settings = useSettings();
  const phone = useMobile();
  // Two steps for the pinned header, each as its original reaches the pinned
  // surface: the date row reaching the topbar, then the bar reaching the
  // line where its copy sits (head + the copy's top padding) — so the morph
  // is a flattening in place, never a move against the scroll (when it fired
  // earlier the copy travelled down while the page went up: it read as a
  // bounce). The copy swaps in and morphs out of the original.
  const [navSentinel, navGone] = useStuck<HTMLDivElement>(TOPBAR_H);
  const [barSentinel, barGone] = useStuck<HTMLDivElement>(TOPBAR_H + PIN_HEAD_H + BAR_ROW_PT);
  const $ = (sel: string) => () => document.querySelector(sel);
  // Everything in the date row has an original, so everything morphs: the
  // stepper, the title, the zone, the said-aloud button, each number.
  useFlip(navGone, () => ["day-stepper", "day-title", "day-tz", "day-said",
    ...(meta.data?.groups ?? []).flatMap((g) => [`day-l-${g.key}`, `day-v-${g.key}`])]
    .map((k) => [$(`[data-flip="${k}"]`), $(`[data-flip-to="${k}"]`)] as Pair));
  // The bar and its hours move as one: the hours ride along under the
  // flattening bar instead of being overlapped by it and jumping up after.
  useFlip(barGone, () => [[$('[data-flip="day-bar"]'), $('[data-flip="day-bar-to"]'), "stretch"],
    [$('[data-flip="day-bar-ticks"]'), $('[data-flip="day-bar-to-ticks"]'), "stretch"]]);
  const said = useQuery(saidQuery(date));
  // ?said=todo|check&pick=<id> opens the drawer on load — a link to one
  // thing that was said, and how screenshots reach this state.
  const q = new URLSearchParams(location.search);
  const [saidTab, setSaidTab] = useState<Tab | null>(
    q.get("said") === "todo" || q.get("said") === "check" ? (q.get("said") as Tab) : null);
  const [saidSel, setSaidSel] = useState<number | null>(q.get("pick") ? Number(q.get("pick")) : null);

  // A new day opens on its own default event, not the last day's selection —
  // except right after a correction, when the panel stays on what you changed.
  const kept = useRef<number | null>(null);
  const [gap, setGap] = useState(false);
  useEffect(() => {
    const at = kept.current;
    kept.current = null;
    setGap(false);
    setSel((at != null && eventAt(evs, at)) || defaultEvent(evs));
  }, [date, day.data]); // eslint-disable-line
  const correctGap = (a: number, b: number) => {
    setSel({ a, b, cat: "unknown", title: "The model said nothing about this stretch",
      start: "", end: "" } as DayEvent);
    setGap(true);
  };

  if (meta.isError || day.isError) {
    return <p className="p-10 text-sm text-ink-muted">{String((meta.error ?? day.error)?.message)}</p>;
  }
  if (!meta.data || !day.data) return <Pending />;

  const isToday = date === meta.data.today;
  const mins = minutesByCat(evs);
  // The day's own length, and how much of it has happened — both from the
  // server, which knows the day's clock; the browser's clock may not match it.
  const n = dayLen(day.data);
  const end = day.data.end ?? (isToday ? Math.min(nowMinute(), n) : n);
  const segs = day.data.tz;
  const m = meta.data.model;
  const failed = day.data.failed?.windows ?? [];
  const auth = failed.filter((w) => w.kind === "auth");
  const call = failed.filter((w) => w.kind === "call");

  const picked = said.data && saidSel != null
    ? itemsOf(said.data, saidTab ?? "todo").find((x) => x.id === saidSel)
    : undefined;
  const saidMark = picked ? { a: picked.win[0]!, b: picked.win[1]!, label: `said at ${picked.t}` } : undefined;

  // Which kind of "not judged" this is — they must not look alike (the four
  // kinds of nothing, docs/design.md). Order matters: a missing key explains a
  // refused call, so it wins.
  const keyAction = (label: string) => (
    <Button variant="primary" size="default" onClick={() => settings("model")}>{label}</Button>
  );
  const unjudged = evs.length === 0 && (
    m.key_set === false ? (
      <Notice title="Recorded, not yet read" action={keyAction("Add a model key")} sub="or a local model · Settings › Model">
        No model key is set, so nothing has been judged and nothing is drawn as judged — the bar is hatched, the lanes
        below are grey. With a key, the next run reads today and yesterday; older days wait until you ask for them.
      </Notice>
    ) : auth.length > 0 ? (
      <Notice alert title="The model refused the key" action={keyAction("Check the key")} sub="then this day is retried on the next run">
        {day.data.failed!.model} answered at {new Date(auth[0]!.at).toTimeString().slice(0, 5)} with “{auth[0]!.reason}”.
        Every record is here and unread — nothing was judged, so nothing is drawn as judged.
      </Notice>
    ) : (
      <Notice title="No model has looked at this day yet">
        Every record is here and unread. The bar is hatched rather than empty: this is not a quiet day, it is an
        unread one.
      </Notice>
    )
  );

  if (phone) {
    return <MobileDay meta={meta.data} day={day.data} date={date} evs={evs} end={end} failed={call} unjudged={unjudged} />;
  }

  // The condensed header: the date row once the real one starts under the
  // topbar, then the bar once the real bar starts under that. The title and
  // the bar morph out of their originals (lib/flip).
  const pinned = (
      <Pinned snapMore stage={stageOf(navGone, barGone)}
        head={
          <div className="flex h-full items-center gap-3 pt-1">
            <span data-flip-to="day-stepper"><DayStepper quiet meta={meta.data} date={date} /></span>
            <span data-flip-to="day-title" className="text-[15px]/5 font-semibold tracking-[-0.01em]">{longDate(date)}</span>
            <span data-flip-to="day-tz" className="font-mono text-xs/tight text-ink-subtle">{tzLine(day.data, isToday)}</span>
            {said.data && (
              <span data-flip-to="day-said"><SaidTrigger said={said.data} open={saidTab}
                onOpen={(t) => { setSaidTab((cur) => (cur === t ? null : t)); setSaidSel(null); }} /></span>
            )}
            <span className="grow" />
            {evs.length > 0 && <span><InlineNumbers flip="day" items={meta.data.groups.map((g) => ({
              key: g.key, name: g.name, value: hours(g.cats.reduce((n, c) => n + (mins[c] ?? 0), 0)) }))} /></span>}
          </div>
        }
        more={
          <div className="pb-3" style={{ paddingTop: BAR_ROW_PT }}>
            <DayBar slim flip="day-bar-to" meta={meta.data} evs={evs} selected={sel} onSelect={setSel} now={isToday ? end : undefined} n={n} segs={segs}
              failed={call} mark={saidMark} />
          </div>
        } />
  );

  return (
    <main className="mx-auto w-full max-w-[1440px] px-10 pb-16">
      <Topbar meta={meta.data} view="day" date={date} pinned={pinned} />
      <div className="page-in">
      <div ref={navSentinel} aria-hidden className="h-px -mb-px" />
      {/* Each original swaps with its pinned copy in one frame (lib/flip). */}
      <div className={navGone ? "opacity-0" : ""}>
        <DayNav meta={meta.data} date={date} evs={evs} day={day.data}>
          {said.data && (
            <SaidTrigger said={said.data} open={saidTab}
              onOpen={(t) => { setSaidTab((cur) => (cur === t ? null : t)); setSaidSel(null); }} />
          )}
        </DayNav>
      </div>
      <DayStrip meta={meta.data} date={date} />
      <div className={`pt-[38px] ${barGone ? "opacity-0" : ""}`}>
        <div ref={barSentinel} aria-hidden className="h-px -mb-px" />
        <DayBar flip="day-bar" meta={meta.data} evs={evs} selected={sel} onSelect={setSel} now={isToday ? end : undefined} n={n} segs={segs}
          failed={call} mark={saidMark} />
      </div>
      {evs.length > 0 ? <Coverage day={day.data} evs={evs} end={end} today={isToday} /> : <div className="pb-8" />}
      {unjudged}
      {evs.length === 0 && <Recorded meta={meta.data} day={day.data} end={end} />}

      {evs.length > 0 && (
        <section className="flex flex-col gap-[22px]">
          <div className="flex w-full items-center gap-[14px]">
            <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">What happened this day</h2>
            <div className="h-px grow bg-border" />
          </div>
          <div className="flex items-start gap-10">
            <EventList evs={evs} selected={sel} onSelect={(e) => { setGap(false); setSel(e); }} end={end}
              failed={call} segs={segs} onCorrect={correctGap} />
            {sel && <EventDetail meta={meta.data} date={date} e={sel} segs={segs} n={n} gap={gap}
              onSaved={(a) => { kept.current = a >= 0 ? a : sel.a; }} />}
          </div>
        </section>
      )}
      {said.data && saidTab && (
        <SaidDrawer said={said.data} tab={saidTab} date={date}
          setTab={(t) => { setSaidTab(t); setSaidSel(null); }}
          sel={saidSel} onSel={setSaidSel} onClose={() => { setSaidTab(null); setSaidSel(null); }} />
      )}
      </div>
    </main>
  );
}
