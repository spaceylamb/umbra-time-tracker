import { clock } from "./demo";

export const hm = (m: number) =>
  `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;

export const range = (a: number, b: number) => `${hm(a)}–${hm(b)}`;

const WEEKDAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTH = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];
const MON = MONTH.map((m) => m.slice(0, 3));

// Dates are ISO days in the user's local calendar; never let the browser's
// timezone reinterpret them.
const parse = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!);
};

export const longDate = (iso: string) => {
  const d = parse(iso);
  return `${WEEKDAY[d.getDay()]} ${d.getDate()} ${MONTH[d.getMonth()]}`;
};

/** The phone's date title: "Tue 8 September". */
export const phoneDate = (iso: string) => {
  const d = parse(iso);
  return `${WEEKDAY[d.getDay()]!.slice(0, 3)} ${d.getDate()} ${MONTH[d.getMonth()]}`;
};

export const monthName = (m: number) => MONTH[m]!;

export const shortDate = (iso: string) => {
  const d = parse(iso);
  return `${d.getDate()} ${MON[d.getMonth()]}`;
};

export const hours = (min: number) => `${(min / 60).toFixed(1)}h`;

export function ago(ms: number, now = clock()) {
  const m = Math.max(0, Math.round((now - ms) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

export const nowMinute = () => {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
};
