// The block under the bar that says what state this day is in, when that is
// not "judged" — every such state shares its shape: a title, one
// paragraph that says what is true and what is not, and at most one action.
import type { ReactNode } from "react";

export function Notice({
  title, alert = false, children, action, sub,
}: {
  title: string;
  /** Prefix the ⚠ glyph — the only place the alert colour is allowed. */
  alert?: boolean;
  children: ReactNode;
  action?: ReactNode;
  sub?: ReactNode;
}) {
  return (
    <section className="flex w-full flex-col items-stretch gap-5 pb-8 sm:flex-row sm:items-start sm:gap-10 sm:pb-12">
      <div className="flex max-w-[720px] flex-col gap-1.5">
        <h2 className="flex items-center gap-2 text-base/[22px] font-semibold">
          {alert && <span className="text-alert" aria-hidden>⚠</span>}
          {title}
        </h2>
        <div className="text-[15px]/[22px] text-ink-muted">{children}</div>
      </div>
      <div className="hidden grow sm:block" />
      {(action || sub) && (
        <div className="flex shrink-0 flex-col items-center gap-2 sm:items-end">
          {action}
          {sub && <div className="font-mono text-xs/tight text-ink-subtle">{sub}</div>}
        </div>
      )}
    </section>
  );
}
