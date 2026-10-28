// ISO weeks, in the user's local calendar — the same weeks goals.week_of
// produces on the server, so a week the page asks for is the week it gets.

const parse = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!);
};
const fmt = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const addDays = (iso: string, n: number) => {
  const d = parse(iso);
  d.setDate(d.getDate() + n);
  return fmt(d);
};

export function isoWeek(iso: string): string {
  const d = parse(iso);
  const day = (d.getDay() + 6) % 7;                // Monday = 0
  d.setDate(d.getDate() - day + 3);                 // Thursday of this week
  const year = d.getFullYear();
  const jan4 = new Date(year, 0, 4);
  const week = 1 + Math.round(((d.getTime() - jan4.getTime()) / 86400000 - 3 + ((jan4.getDay() + 6) % 7)) / 7);
  return `${year}-W${String(week).padStart(2, "0")}`;
}

export function mondayOf(week: string): string {
  const [y, w] = [Number(week.slice(0, 4)), Number(week.slice(6))];
  const jan4 = new Date(y, 0, 4);
  const mon = new Date(jan4);
  mon.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7) + (w - 1) * 7);
  return fmt(mon);
}

export const shiftWeek = (week: string, n: number) => isoWeek(addDays(mondayOf(week), 7 * n));

const MONTH = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

/** "7 – 13 September", or "31 August – 6 September" across a month. */
export function weekTitle(days: string[]) {
  const a = parse(days[0]!), b = parse(days[6]!);
  return a.getMonth() === b.getMonth()
    ? `${a.getDate()} – ${b.getDate()} ${MONTH[b.getMonth()]}`
    : `${a.getDate()} ${MONTH[a.getMonth()]} – ${b.getDate()} ${MONTH[b.getMonth()]}`;
}

export const WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const dayOfMonth = (iso: string) => parse(iso).getDate();
