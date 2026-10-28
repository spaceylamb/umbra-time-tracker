// Bottom of the week: where the time went, and against the same days last week.
// The comparison is like for like — three days against three, never a
// part-week against a whole one, which would show every category falling.
import type { Meta } from "../api/types";
import { leftNote, type Left } from "../lib/clock";
import { cat, catName } from "../lib/day";

const h1 = (m: number) => `${(m / 60).toFixed(1)}h`;
const NUM = ["no", "one", "two", "three", "four", "five", "six", "seven"];

export function Where({ meta, mins, loading = false }: { meta: Meta; mins: Record<string, number>; loading?: boolean }) {
  const rows = Object.entries(mins).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = rows.reduce((n, [, v]) => n + v, 0);
  const max = rows[0]?.[1] ?? 1;
  return (
    <div className="flex grow basis-0 flex-col gap-[18px]">
      <div className="flex items-center gap-[14px]">
        <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">Where the time went</h2>
        <div className="h-px grow bg-border" />
        <span className="shrink-0 font-mono text-xs/4 text-ink-subtle">{h1(total)} judged</span>
      </div>
      <div className="flex flex-col gap-[9px]">
        {rows.map(([c, v]) => (
          <div key={c} className="flex items-center gap-[14px]">
            <span className="w-[84px] shrink-0 text-sm/tight text-ink-muted">{catName(meta, c)}</span>
            <div className="h-2.5 grow basis-0 rounded-xs bg-sunken">
              <div className="h-2.5 rounded-xs" style={{ width: `${(v / max) * 100}%`, background: cat(c) }} />
            </div>
            <span className="w-11 shrink-0 text-right font-mono text-xs/4 text-ink-muted">{h1(v)}</span>
            <span className="w-9 shrink-0 text-right font-mono text-xs/4 text-ink-subtle">{Math.round((v / total) * 100)}%</span>
          </div>
        ))}
        {!rows.length && <p className={`text-sm/tight text-ink-subtle ${loading ? "pending" : ""}`}>
          {loading ? "Loading the week…" : "No day this week has been judged yet."}</p>}
      </div>
    </div>
  );
}

export function Versus({ meta, cur, prev, n, left = [] }: {
  meta: Meta; cur: Record<string, number>; prev: Record<string, number>; n: number;
  /** Days left out of the comparison — travel days, this week or last. */
  left?: Left[];
}) {
  const keys = [...new Set([...Object.keys(cur), ...Object.keys(prev)])]
    .sort((a, b) => (cur[b] ?? 0) - (cur[a] ?? 0));
  const diff = (k: string) => (cur[k] ?? 0) - (prev[k] ?? 0);
  const max = Math.max(1, ...keys.map((k) => Math.abs(diff(k))));
  const hasPrev = Object.keys(prev).length > 0;
  return (
    <div className="flex grow basis-0 flex-col gap-[18px]">
      <div className="flex items-center gap-[14px]">
        <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">
          {n >= 7 ? "Against last week" : `Against the same ${NUM[n]} day${n === 1 ? "" : "s"} last week`}
        </h2>
        <div className="h-px grow bg-border" />
      </div>
      {hasPrev ? (
        <div className="flex flex-col gap-[9px]">
          {keys.map((k) => {
            const d = diff(k);
            const flat = Math.abs(d) < 3;
            return (
              <div key={k} className="flex items-center gap-[14px]">
                <span className={`w-[84px] shrink-0 text-sm/tight ${flat ? "text-ink-subtle" : "text-ink-muted"}`}>{catName(meta, k)}</span>
                <div className="flex h-2.5 grow basis-0 justify-end">
                  {d < 0 && !flat && <div className="h-2.5 rounded-xs" style={{ width: `${(-d / max) * 100}%`, background: cat(k) }} />}
                </div>
                <div className="h-4 w-px shrink-0 bg-border-strong" />
                <div className="flex h-2.5 grow basis-0">
                  {d > 0 && !flat && <div className="h-2.5 rounded-xs" style={{ width: `${(d / max) * 100}%`, background: cat(k) }} />}
                </div>
                <span className={`w-[46px] shrink-0 text-right font-mono text-xs/4 ${flat ? "text-ink-subtle" : "text-ink-muted"}`}>
                  {flat ? "0.0h" : `${d > 0 ? "+" : "−"}${(Math.abs(d) / 60).toFixed(1)}h`}
                </span>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-sm/tight text-ink-subtle">Nothing was judged in those days last week, so there is nothing to compare.</p>
      )}
      {hasPrev && left.length > 0 && (
        <p className="pt-0.5 text-xs/[19px] text-ink-subtle">
          {`${NUM[n]![0]!.toUpperCase()}${NUM[n]!.slice(1)} day${n === 1 ? "" : "s"} against ${NUM[n]}.`} {leftNote(left)}
        </p>
      )}
      {hasPrev && n < 7 && !left.length && (
        <p className="pt-0.5 text-xs/[19px] text-ink-subtle">
          {`${NUM[n]![0]!.toUpperCase()}${NUM[n]!.slice(1)} day${n === 1 ? "" : "s"} against ${NUM[n]}, not ${NUM[n]} against seven`} —
          comparing a part-week to a whole one would show every category falling.
        </p>
      )}
    </div>
  );
}
