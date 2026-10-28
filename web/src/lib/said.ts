// The drawer's tabs, read off the payload. Each lane (plugin) that is on puts
// its part in /said — tasks, claims — and one that is off leaves it out, so a
// tab is there exactly when its lane is on, whether or not it heard anything.
import type { Said, SaidClaim, SaidTask } from "../api/types";

export type Tab = "todo" | "check";

// A task's state, said in words, lives in the drawer; whether it still needs
// someone is here, because the counts need it too. Closed ones — run, or
// dismissed here, at the destination or with `umbra tasks dismiss` — stay in
// the list, faded and last: hiding them would make "you dealt with it" look
// like "never heard". A seen claim is treated the same way.
const CLOSED = new Set(["approved", "dismissed", "duplicate"]);
export const isOpen = (t: SaidTask) => !CLOSED.has(t.status);

export function itemsOf(said: Said, tab: "todo"): SaidTask[];
export function itemsOf(said: Said, tab: "check"): SaidClaim[];
export function itemsOf(said: Said, tab: Tab): (SaidTask | SaidClaim)[];
export function itemsOf(said: Said, tab: Tab) {
  return (tab === "todo" ? said.tasks : said.claims) ?? [];
}

/** The tabs this day's drawer has, in order, with what is still open in each. */
export function tabsOf(said: Said): { tab: Tab; label: string; n: number }[] {
  const out: { tab: Tab; label: string; n: number }[] = [];
  if (said.tasks) out.push({ tab: "todo", label: "To do", n: said.tasks.filter(isOpen).length });
  if (said.claims) out.push({ tab: "check", label: "To check", n: said.claims.filter((c) => !c.seen).length });
  return out;
}

/** Whether there is a drawer to open at all: some lane heard something. */
export const anything = (said: Said) => (said.tasks?.length ?? 0) + (said.claims?.length ?? 0) > 0;

/** A source as the site it is on: "https://www.postgresql.org/docs/…" → "postgresql.org". */
export function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** The note at the foot of To check: whether a verdict is a check or a memory,
 * who checks, and where the claims went. */
export function lookupNote(said: Said): string {
  const by = said.lookup_by ?? "";
  const sink = said.lookup_sink ?? "";
  if (!said.lookup)
    return "Nothing looks these up yet, so the first guess is the model’s memory, not a check — when these were looked up, a third of the dated or numeric ones it called fine turned out not to be. Add a key for Gemini, OpenAI or Claude to the key file.";
  if (sink && by === sink && !said.lookup_answers)
    return `Claims the model doubts, or that carry a number or a date, are sent to ${sink}. What it finds does not come back here, so the first guess is still the model’s memory.`;
  if (sink && by === sink)
    return `Looked up by ${sink}: claims the model doubts, or that carry a number or a date, are checked against sources and come back here.`;
  return `Looked up by ${by}, with a web search behind every verdict — ${said.looked_today ?? 0} today. Claims the model doubts, or that carry a number or a date, are checked; settled facts are not.`;
}

/** Sources as sites, one link per site: two pages on postgresql.org read as
 * "postgresql.org" once, linking the first. */
export function sites(sources: { url: string; title: string }[]) {
  const seen = new Set<string>();
  return sources.flatMap((s) => {
    const site = host(s.url);
    if (seen.has(site)) return [];
    seen.add(site);
    return [{ ...s, site }];
  });
}
