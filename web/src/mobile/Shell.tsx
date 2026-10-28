// The phone's frame: the mark row on top, the tab bar below.
//
// The mark row scrolls away with the page; the gear sits at its right end on
// every tab (the only way into Settings on a phone). The tab bar is the
// desktop's Day | Week | Sources segmented control, moved to where a thumb is.
import { Link, type LinkProps } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import type { Meta } from "../api/types";
import { Gear, Mark } from "../day/Topbar";
import { useSettings } from "../settings/SettingsDialog";
import { isoWeek } from "../lib/week";
import { status } from "../lib/fresh";

export function MarkRow() {
  const settings = useSettings();
  return (
    <div className="relative flex h-5 w-full shrink-0 items-center justify-center">
      <Mark size={20} />
      <button type="button" aria-label="Settings" onClick={() => settings()}
        className="absolute -top-3 -right-3 flex size-11 items-center justify-center text-ink-muted">
        <Gear size={20} />
      </button>
    </div>
  );
}

export function TabBar({ meta, view, date }: { meta: Meta; view: "day" | "week" | "sources"; date?: string }) {
  const focus = date ?? meta.order.at(-1) ?? meta.today;
  const stale = status(meta).stale.length;
  const tab = (on: boolean) =>
    `flex h-[34px] grow basis-0 items-center justify-center gap-[5px] rounded-md text-sm/tight
     ${on ? "bg-surface font-medium text-ink" : "text-ink-subtle"}`;
  return (
    <nav aria-label="View"
      className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-canvas pb-[env(safe-area-inset-bottom)]">
      <div className="flex h-14 items-center gap-1.5 px-3">
        <Link to="/day/$date" params={{ date: focus }} className={tab(view === "day")}
          aria-current={view === "day" ? "page" : undefined}>Day</Link>
        <Link to="/week/$week" params={{ week: isoWeek(focus) }} className={tab(view === "week")}
          aria-current={view === "week" ? "page" : undefined}>Week</Link>
        <Link to="/sources" className={tab(view === "sources")}
          aria-current={view === "sources" ? "page" : undefined}>
          Sources
          {stale > 0 && <i className="size-[5px] rounded-full bg-alert" title={`${stale} need you`} />}
        </Link>
      </div>
    </nav>
  );
}

/** Room under the page for the fixed tab bar. */
export const TAB_PAD = "pb-[calc(88px+env(safe-area-inset-bottom))]";

function useScrolled(px: number) {
  const [y, setY] = useState(false);
  useEffect(() => {
    const f = () => setY(window.scrollY > px);
    f();
    window.addEventListener("scroll", f, { passive: true });
    return () => window.removeEventListener("scroll", f);
  }, [px]);
  return y;
}

type Step = { to: LinkProps["to"]; params: Record<string, string>; label: string } | undefined;

/** The date (or week) row: ‹ title ▾ ›, and a status line under it. It sticks
 * when the mark row above has scrolled away; the title opens the
 * picker. */
export function PhoneNav({ title, onPick, prev, next, status, live }: {
  title: string; onPick: () => void; prev: Step; next: Step; status: string; live: boolean;
}) {
  const stuck = useScrolled(30);
  const step = (s: Step, glyph: string, label: string) =>
    s ? (
      <Link to={s.to} params={s.params as never} aria-label={s.label} resetScroll={false}
        className="flex size-11 shrink-0 items-center justify-center text-[22px]/[26px] text-ink-muted">{glyph}</Link>
    ) : (
      <span aria-label={label} className="flex size-11 shrink-0 items-center justify-center text-[22px]/[26px] text-ink-subtle/40">{glyph}</span>
    );
  return (
    <div className={`sticky top-0 z-10 flex flex-col items-center gap-[2px] bg-canvas px-4 pb-[14px]
      ${stuck ? "shadow-[0_1px_0_var(--color-border)]" : ""}`}>
      <div className="flex w-full items-center justify-center">
        {step(prev, "‹", "No earlier records")}
        <button type="button" onClick={onPick} aria-haspopup="dialog"
          className="flex h-11 w-[200px] shrink-0 items-center justify-center gap-1.5">
          <span className="text-lg leading-6 font-semibold tracking-tight">{title}</span>
          <span aria-hidden className="text-xs leading-6 text-ink-muted">▾</span>
        </button>
        {step(next, "›", "Nothing later yet")}
      </div>
      <div className="flex items-center gap-[5px] font-mono text-2xs leading-[14px] text-ink-subtle">
        <i className={`size-1 rounded-full ${live ? "bg-live" : "bg-ink-subtle"}`} />
        {status}
      </div>
    </div>
  );
}

/** The headline numbers: the configured groups, summed. */
export function Metrics({ meta, mins, judged }: { meta: Meta; mins: Record<string, number>; judged: boolean }) {
  if (!meta.groups.length) return null;
  return (
    <dl className="flex w-full">
      {meta.groups.map((g) => {
        const m = g.cats.reduce((n, k) => n + (mins[k] ?? 0), 0);
        return (
          <div key={g.key} className="flex grow basis-0 flex-col gap-px">
            <dt className="text-2xs leading-[14px] text-ink-subtle">{g.name}</dt>
            <dd className={`text-xl leading-8 font-semibold tracking-tight ${judged ? "" : "text-ink-subtle"}`}>
              {judged ? `${(m / 60).toFixed(1)}h` : "—"}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
