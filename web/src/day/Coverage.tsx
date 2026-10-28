import type { Day, DayEvent } from "../api/types";
import { minutesOf } from "../lib/day";

export function Coverage({ day, evs, end, today = false }: { day: Day; evs: DayEvent[]; end: number; today?: boolean }) {
  if (!evs.length) {
    const n = Object.values(day.counts).reduce<number>((a, b) => a + (b ?? 0), 0);
    return (
      <p className="pb-[44px] text-sm/tight text-ink-muted">
        No model has looked at this day yet — {n.toLocaleString()} records are here, unread.
      </p>
    );
  }
  const m = minutesOf(day, evs, end);
  const models = day.ev ? Object.keys(day.ev).length : 0;
  return (
    <p className="flex gap-1.5 pb-[44px] text-sm/tight">
      <span className="text-ink">
        covered {m.covered.toLocaleString()} of {m.end.toLocaleString()} min{today ? " so far" : ""}
      </span>
      <span className="text-ink-subtle">
        · {m.silent.toLocaleString()} min the model said nothing about
        {m.failed > 0 && ` · ${m.failed} min where the call failed`} · {evs.length} events · {day.evuse}
        {models > 1 ? ` · ${models - 1} more model${models > 2 ? "s" : ""} not drawn` : ""}
      </span>
    </p>
  );
}
