// Your own corrections (umbra/correct.py): what the editor offers and checks,
// and what the panel says about the model's view of one. The words are short
// on purpose — the panel is read at a glance.
import type { DayEvent, Meta } from "../api/types";
import { catName } from "./day";

/** The categories one can mean. Not unknown (that is the model abstaining, not
 * an answer), nor app or place, which the bar uses for its own lanes. */
export const CATS = ["read", "meal", "social", "talk", "code", "research", "browse", "listen",
  "fun", "chore", "workout", "transit", "sleep", "other"] as const;

/** "12:30" → 750; null for anything that is not HH:MM. */
export function minutes(s: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(s.trim());
  if (!m) return null;
  const h = Number(m[1]), mm = Number(m[2]);
  return h < 25 && mm < 60 ? h * 60 + mm : null;
}

export const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

/** What the server would refuse, said before sending (correct._norm). */
export function spanError(a: number | null, b: number | null, n: number): string | null {
  if (a == null || b == null) return "Times are HH:MM.";
  if (b <= a) return "The end is before the start.";
  if (a < 0 || b > n) return "Outside this day.";
  return null;
}

/** What the model makes of your correction, in a few words. Four answers kept
 * apart (see correct.disagrees and read_by_model), plus "nothing to compare". */
export function modelSays(meta: Meta | undefined, e: DayEvent): { tone: "pending" | "agree" | "soft" | "vs" | "nosay"; text: string } {
  if (e.pending) return { tone: "pending", text: "Not re-read yet." };
  if (e.nosay) return { tone: "nosay", text: "Nothing to compare yet." };
  if (!e.vs) return { tone: "agree", text: "✓ Agrees." };
  const theirs = catName(meta, e.vs.cat);
  if (e.vs.soft) return { tone: "soft", text: `${theirs}, with ${catName(meta, e.cat).toLowerCase()} noted.` };
  return { tone: "vs", text: `Still says ${theirs}${e.vs.title ? ` — “${e.vs.title}”` : ""}.` };
}

/** The event covering minute m: after a save the day is redrawn, and the panel
 * should stay on the stretch you just corrected rather than jump back. */
export const eventAt = (evs: DayEvent[], m: number) => evs.find((e) => e.a <= m && m < e.b);
