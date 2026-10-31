import { useQuery } from "@tanstack/react-query";
import type { Meta } from "../api/types";
import { importQuery } from "../api/client";
import { Link } from "@tanstack/react-router";
import { isoWeek } from "../lib/week";
import { useSettings } from "../settings/SettingsDialog";
import { Button } from "../ui/Button";
import { ago } from "../lib/time";
import { status } from "../lib/fresh";
import { useStuck } from "../lib/stuck";
import { demo } from "../lib/demo";

/** Height of the pinned topbar — what other sticky parts sit under. */
export const TOPBAR_H = 84;   // pt-7 + the 32px nav + pb-6

export function Mark({ size = 19 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r="10" fill="var(--color-ink)" />
      <path d="M12 2 A7 7 0 1 0 22 12 A10 10 0 0 0 12 2 Z" fill="var(--color-canvas)" />
      <circle cx="12" cy="12" r="10" fill="none" stroke="var(--color-ink)" strokeWidth="2" />
    </svg>
  );
}

export const Gear = ({ size = 14 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" />
  </svg>
);

/** view: which of Day / Week is showing. date: the day in focus — Day links
 * to it, Week to the week it falls in, so switching keeps your place. */
export function Topbar({ meta, view = "day", date, pinned }: {
  meta: Meta; view?: "day" | "week" | "sources"; date?: string;
  /** The view's condensed header (ui/Pinned). Rendered inside the topbar so
   * the two are one element to the browser: an overscroll bounce moves them
   * together, and nothing can show through a seam between them. */
  pinned?: React.ReactNode;
}) {
  const focus = date ?? meta.order.at(-1) ?? meta.today;
  const settings = useSettings();
  const { last, stale } = status(meta);
  const run = useQuery(importQuery).data?.run;
  const importing = run?.running
    ? `importing · ${run.sources.filter((s) => s.state === "done").length} of ${run.sources.length}`
    : null;
  const [sentinel, stuck] = useStuck<HTMLDivElement>();
  return (
    <>
    <div ref={sentinel} aria-hidden className="h-px -mb-px" />
    {/* Pinned on every view: where you are and how to get elsewhere should
        never scroll away. The hairline appears only once it is pinned. */}
    <header className={`sticky top-0 z-20 -mx-10 flex items-center gap-6 bg-canvas px-10 pt-7 pb-6 transition-shadow duration-150
      ${stuck ? "shadow-[0_1px_0_var(--color-border)]" : ""}`}>
      <div className="flex items-center gap-[9px]">
        <Mark />
        <span className="text-[17px] font-semibold leading-[22px] tracking-[-0.01em]">Umbra</span>
      </div>
      <nav className="flex rounded-[7px] bg-sunken p-[3px] text-sm/tight font-medium" aria-label="View">
        <Link to="/day/$date" params={{ date: focus }}
          className={`rounded-sm px-[14px] py-[5px] ${view === "day" ? "bg-surface text-ink" : "text-ink-subtle hover:text-ink"}`}
          aria-current={view === "day" ? "page" : undefined}>Day</Link>
        <Link to="/week/$week" params={{ week: isoWeek(focus) }}
          className={`rounded-sm px-[14px] py-[5px] ${view === "week" ? "bg-surface text-ink" : "text-ink-subtle hover:text-ink"}`}
          aria-current={view === "week" ? "page" : undefined}>Week</Link>
        <Link to="/sources"
          className={`flex items-center gap-1.5 rounded-sm px-[14px] py-[5px] ${view === "sources" ? "bg-surface text-ink" : "text-ink-subtle hover:text-ink"}`}
          aria-current={view === "sources" ? "page" : undefined}>
          Sources
          {stale.length > 0 && (
            <i className="size-[5px] rounded-full bg-alert" title={`${stale.length} need you`} />
          )}
        </Link>
      </nav>
      <div className="grow" />
      {!demo() && <div className="flex items-center gap-2 text-sm/tight text-ink-subtle">
        {view === "sources" && stale.length > 0 && !importing ? (
          <><i className="size-[5px] rounded-full bg-alert" />
            <span className="text-ink">{stale.length} source{stale.length === 1 ? "" : "s"} need{stale.length === 1 ? "s" : ""} you</span></>
        ) : (
          <><i className={`size-[5px] rounded-full ${last || importing ? "bg-live" : "bg-ink-subtle"}`} />
            {importing ?? (last ? `updated ${ago(last)}` : "never updated")}</>
        )}
      </div>}
      <Button icon aria-label="Settings" onClick={() => settings()} className="text-ink-muted">
        <Gear />
      </Button>
      {pinned}
    </header>
    </>
  );
}
