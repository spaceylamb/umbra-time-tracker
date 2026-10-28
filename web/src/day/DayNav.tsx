import { Link } from "@tanstack/react-router";
import type { Day, DayEvent, Meta } from "../api/types";
import { tzLine } from "../lib/clock";
import { Button } from "../ui/Button";
import { hours, longDate } from "../lib/time";
import { minutesByCat, todayTarget } from "../lib/day";

const Chev = ({ d }: { d: string }) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={d} />
  </svg>
);

/** ‹ Today › — the day stepper (macOS Calendar's shape). "Today" sits
 * between the arrows because it is the same kind of move, just a long one;
 * it is a real button, filled like the arrows, and stays in place (disabled)
 * when you are already there, so the arrows never shift. Where nothing was
 * recorded today it reads "Latest" and goes to the newest day — the same
 * place "/" goes. quiet: the pinned header's ghost version. */
export function DayStepper({ meta, date, quiet = false }: { meta: Meta; date: string; quiet?: boolean }) {
  const i = meta.order.indexOf(date);
  const prev = i > 0 ? meta.order[i - 1] : undefined;
  const next = i >= 0 && i < meta.order.length - 1 ? meta.order[i + 1] : undefined;
  const target = todayTarget(meta);
  const variant = quiet ? "ghost" : "secondary";
  const item = (to: string | undefined, label: string, child: React.ReactNode, icon: boolean) => {
    const btn = (
      <Button variant={variant} icon={icon} aria-label={label} disabled={!to}
        className={icon ? "text-ink-muted" : "font-medium text-ink-muted"}>
        {child}
      </Button>
    );
    // Stepping keeps your place on the page: comparing two days is reading
    // the same part of each, and a jump to the top would lose it.
    return to ? <Link key={label} to="/day/$date" params={{ date: to }} resetScroll={false} tabIndex={-1}>{btn}</Link> : <span key={label}>{btn}</span>;
  };
  return (
    <div className="flex gap-[2px]">
      {item(prev, "Previous day", <Chev d="M15 18l-6-6 6-6" />, true)}
      {target && item(target === date ? undefined : target, target === meta.today ? "Today" : "Latest",
        target === meta.today ? "Today" : "Latest", false)}
      {item(next, "Next day", <Chev d="M9 18l6-6-6-6" />, true)}
    </div>
  );
}

export function DayNav({ meta, date, evs, day, children }: {
  meta: Meta; date: string; evs: DayEvent[]; day?: Day; children?: React.ReactNode;
}) {
  const mins = minutesByCat(evs);
  return (
    <div className="flex w-full items-center gap-5 pb-9">
      <div data-flip="day-stepper"><DayStepper meta={meta} date={date} /></div>
      <div className="flex items-baseline gap-3">
        <h1 data-flip="day-title" className="text-[26px] font-semibold leading-8 tracking-tight">{longDate(date)}</h1>
        <span data-flip="day-tz" className="font-mono text-xs/tight text-ink-subtle">
          {day ? tzLine(day, date === meta.today) : `${meta.tz}${date === meta.today ? " · today" : ""}`}
        </span>
      </div>
      {children && <div data-flip="day-said">{children}</div>}
      <div className="grow" />
      {/* Unconfigured groups mean no row at all, not a row of zeros. */}
      {meta.groups.length > 0 && (
        <dl className="flex items-center gap-6">
          {meta.groups.map((g) => {
            const m = g.cats.reduce((n, c) => n + (mins[c] ?? 0), 0);
            return (
              <div key={g.key} className="flex flex-col gap-px">
                <dt data-flip={`day-l-${g.key}`} className="text-xs/tight text-ink-subtle">{g.name}</dt>
                <dd data-flip={`day-v-${g.key}`} className={`text-[22px] font-semibold leading-7 tracking-tight ${evs.length ? "" : "text-ink-subtle"}`}>
                  {evs.length ? hours(m) : "—"}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </div>
  );
}
