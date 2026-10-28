// A day is not always 1440 minutes: 1380 / 1500 when the clocks change, and
// anything on a travel day (a ten-hour day flying out, a thirty-nine-hour one
// flying back). The server says how long each day is and which clocks it was
// read on (Day.n_min, Day.tz); everything here turns that into what the page
// draws, so no component hard-codes 1440.
import type { Day } from "../api/types";

export type Seg = Day["tz"][number];
export type Tick = { m: number; label: string };

/** The day's length; 1440 for a payload that predates n_min. */
export const dayLen = (day: Day | undefined) => day?.n_min ?? 1440;

const hm = (wall: number) => {
  const w = ((wall % 1440) + 1440) % 1440;
  return `${String(Math.floor(w / 60)).padStart(2, "0")}:${String(w % 60).padStart(2, "0")}`;
};

/** Ticks every few hours **of the clock on the wall**: after the clocks go
 * back, 03:00 is 240 minutes in, not 180. A long day is ticked every six
 * hours so its labels still fit; no tick sits just after a clock change,
 * where the change's own label goes. A day that did not begin at midnight
 * (the day after flying east begins when the last one ended) is labelled
 * with the time it did begin. */
export function clockTicks(segs: Seg[], n: number, every = n > 1800 ? 6 : 3): Tick[] {
  const step = every * 60, out: Tick[] = [];
  segs.forEach((s, i) => {
    const lo = s.at, hi = segs[i + 1]?.at ?? n;
    const shift = s.wall - s.at;                    // wall clock = minute + shift
    if (i === 0 && s.wall % step) out.push({ m: 0, label: hm(s.wall) });
    for (let wall = Math.ceil((lo + shift) / step) * step; wall < hi + shift; wall += step) {
      const m = wall - shift;
      if (m - lo < 50 && (i > 0 || s.wall % step)) continue;
      out.push({ m, label: hm(wall) });
    }
  });
  return out;
}

/** Each moment the clock changed: where, and both clocks' names. */
export const switchesOf = (segs: Seg[]) => segs.slice(1).map((s) => ({
  at: s.at,
  label: `${segs[0]!.abbr} → ${s.abbr} · ${hm(s.wall)}`,
}));

/** "10", "23½", "23h 15m" — a day's length in words. */
const hoursOf = (n: number) =>
  n % 60 === 0 ? String(n / 60) : n % 60 === 30 ? `${Math.floor(n / 60)}½` : `${Math.floor(n / 60)}h ${n % 60}m`;

/** The line beside the date: the clock(s), the length if it is not 24
 * hours, whether it is a travel day, whether it is today. */
export function tzLine(day: Day, today: boolean): string {
  if (!day.tz?.length) return today ? "today" : "";
  const abbrs = day.tz.map((s) => s.abbr).filter((a, i, xs) => a !== xs[i - 1]);
  const n = dayLen(day);
  return [abbrs.join(" → "),
    n !== 1440 && `a ${hoursOf(n)}${n % 60 === 0 || n % 60 === 30 ? "-hour" : ""} day`,
    day.travel && "travel",
    today && "today"].filter(Boolean).join(" · ");
}

export type Left = { date: string; lastWeek: boolean; n_min: number };

/** Like for like: a travel day — this week or the same weekday last week —
 * leaves that weekday out of both sides. A ten-hour day and a thirty-nine-
 * hour one do not belong in a column of 24-hour days. */
export function likeForLike(cur: string[], last: string[], data: Record<string, Day | undefined>) {
  const out = { cur: [] as string[], prev: [] as string[], left: [] as Left[] };
  cur.forEach((d, i) => {
    const p = last[i];
    const a = data[d], b = p ? data[p] : undefined;
    if (a?.travel) out.left.push({ date: d, lastWeek: false, n_min: dayLen(a) });
    else if (b?.travel) out.left.push({ date: p!, lastWeek: true, n_min: dayLen(b) });
    else { out.cur.push(d); if (p) out.prev.push(p); }
  });
  return out;
}

const WEEKDAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const named = (iso: string) => {
  const d = new Date(`${iso}T00:00:00Z`);
  return { wd: WEEKDAY[d.getUTCDay()]!, dom: d.getUTCDate() };
};

const and = (xs: string[]) => xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`;

/** Says which days the comparison left out and why, in one sentence for this
 * week's and one for last week's. Leaving a day out without saying so would
 * be a quiet truncation (design.md principle 04). A travel day includes the
 * days either side of the change (tz.TRAVEL_HALO_MS) — hence "around". */
export function leftNote(left: Left[]): string {
  const parts: string[] = [];
  const here = left.filter((l) => !l.lastWeek);
  if (here.length) {
    const one = here.length === 1;
    parts.push(`${and(here.map((l) => { const { wd, dom } = named(l.date); return `${wd} ${dom}`; }))} ${one ? "is" : "are"} left out: `
      + `${one ? "a travel day" : "travel days"}, ${and(here.map((l) => hoursOf(l.n_min)))} hours long — `
      + "around a change of timezone, a day does not belong in a column of ordinary ones. "
      + `${one ? "Its" : "Their"} time is still in “Where the time went”.`);
  }
  const before = left.filter((l) => l.lastWeek);
  if (before.length) {
    const one = before.length === 1, ns = before.map((l) => named(l.date));
    parts.push(`${and(ns.map((x) => x.wd))} ${one ? "is" : "are"} left out of both weeks: `
      + `${and(ns.map((x) => `${x.wd} ${x.dom}`))} last week ${one ? "was a travel day" : "were travel days"}, `
      + `${and(before.map((l) => hoursOf(l.n_min)))} hours long.`);
  }
  return parts.join(" ");
}

const segAt = (m: number, segs: Seg[]) => segs.reduce((cur, s) => (s.at <= m ? s : cur), segs[0]!);

/** A minute of the day as the clock on the wall read it. Minutes count from
 * the day's start, so on an ordinary day the two are the same; after the
 * clocks change, or on a travel day, they are not — minute 1400 of the day
 * you flew back is 08:20 in California, not "23:20". */
export const wallHm = (m: number, segs: Seg[]) => {
  if (!segs.length) return hm(m);
  const s = segAt(m, segs);
  return hm(s.wall + (m - s.at));
};

/** A stretch on the wall clock. Its end at a midnight reads 24:00, as the
 * day's last minute always has. */
export const wallRange = (a: number, b: number, segs: Seg[]) => {
  if (!segs.length) return `${hm(a)}–${b === 1440 ? "24:00" : hm(b)}`;
  const s = segAt(Math.max(a, b - 1), segs);
  const wb = s.wall + (b - s.at);
  return `${wallHm(a, segs)}–${b > a && wb % 1440 === 0 ? "24:00" : hm(wb)}`;
};
