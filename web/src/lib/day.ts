// Everything derived from a Day payload, in one place, so the bar, the list
// and the numbers cannot count the same minutes differently.
import type { Day, DayEvent, Meta } from "../api/types";

export const cat = (c: string) => `var(--cat-${c}, var(--color-ink-subtle))`;

export const catName = (meta: Meta | undefined, c: string) =>
  meta?.labels.category[c] ?? c;

/** The events of the model this day is drawn with; [] when none has looked.
 * Nothing after the day's end (Day.end, now for today): a day composed before
 * merge stopped at now can still hold an "Unknown" running to midnight. */
export function eventsOf(day: Day | undefined): DayEvent[] {
  if (!day?.ev) return [];
  const who = day.evuse ?? Object.keys(day.ev)[0];
  const evs = (who && day.ev[who]) || [];
  const end = day.end;
  if (end == null || evs.every((e) => e.b <= end)) return evs;
  return evs.filter((e) => e.a < end).map((e) => (e.b > end ? { ...e, b: end } : e));
}

export type Row =
  | { kind: "event"; e: DayEvent }
  | { kind: "gap"; a: number; b: number }
  | { kind: "failed"; a: number; b: number };

/** Gaps the model said nothing about, long enough to be worth a row. Short
 * seams between events (a 5-minute walk between two blocks) stay seams. */
export const GAP_ROW_MIN = 15;

/** A gap is split around failed windows: "the model said nothing" and "the
 * call failed" are different facts and get different rows (17c). */
function gapRows(a: number, b: number, failed: { a: number; b: number }[]): Row[] {
  const out: Row[] = [];
  let t = a;
  for (const w of failed) {
    if (w.b <= a || w.a >= b) continue;
    const fa = Math.max(w.a, a), fb = Math.min(w.b, b);
    if (fa - t >= GAP_ROW_MIN) out.push({ kind: "gap", a: t, b: fa });
    const last = out.at(-1);
    if (last?.kind === "failed" && last.b === fa) last.b = fb;
    else out.push({ kind: "failed", a: fa, b: fb });
    t = fb;
  }
  if (b - t >= GAP_ROW_MIN) out.push({ kind: "gap", a: t, b });
  return out;
}

export function rows(evs: DayEvent[], end = 1440, failed: { a: number; b: number }[] = []): Row[] {
  const out: Row[] = [];
  const fs = [...failed].sort((x, y) => x.a - y.a);
  let t = 0;
  for (const e of evs) {
    if (e.a > t) out.push(...gapRows(t, e.a, fs));
    out.push({ kind: "event", e });
    t = Math.max(t, e.b);
  }
  if (end > t) out.push(...gapRows(t, end, fs));
  return out;
}

export function covered(evs: DayEvent[]) {
  return evs.reduce((n, e) => n + (e.b - e.a), 0);
}

/** Every minute of the day so far, counted once (Day.minutes, from the
 * server): inside an event, inside a failed call's window, or neither. The
 * page used to subtract summed event lengths and failed windows from the end,
 * so an event over a failed window was taken away twice and the remainder
 * went negative. The fallback is only for a payload that predates it. */
export function minutesOf(day: Day, evs: DayEvent[], end: number) {
  if (day.minutes) return day.minutes;
  const c = covered(evs);
  const failed = (day.failed?.windows ?? []).filter((w) => w.kind === "call")
    .reduce((n, w) => n + Math.max(0, Math.min(w.b, end) - w.a), 0);
  return { end, covered: c, failed, silent: Math.max(0, end - c - failed) };
}

/** Minutes per category — the only input the headline numbers have. They are
 * sums over the model's categories, never a judgement of their own. */
export function minutesByCat(evs: DayEvent[]) {
  const m: Record<string, number> = {};
  for (const e of evs) m[e.cat] = (m[e.cat] ?? 0) + (e.b - e.a);
  return m;
}

/** The event a day opens on: the longest one that is not sleep. */
export function defaultEvent(evs: DayEvent[]) {
  const pool = evs.filter((e) => e.cat !== "sleep");
  return (pool.length ? pool : evs).reduce<DayEvent | undefined>(
    (best, e) => (!best || e.b - e.a > best.b - best.a ? e : best),
    undefined,
  );
}

/** Where "Today" goes: today if anything was recorded today, else the newest
 * day there is — "/" means the same thing (router.tsx). */
export const todayTarget = (meta: Meta) =>
  meta.order.includes(meta.today) ? meta.today : meta.order.at(-1);
