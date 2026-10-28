// "This week's priorities". Up to three.
//
// The time under each is what the goals model credited to it (decided
// 2026-09-28: the model reads the day and judges which stretches moved a
// priority forward — it understands "sleep before midnight", which no list
// of categories could). A target only says how much the week is aiming for;
// changing it never re-asks the model. One stretch can count toward two.
// A day not yet judged against these priorities is unasked, not zero.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { Week } from "../api/types";
import { STATIC, saveGoals, type GoalInput } from "../api/client";
import { Button } from "../ui/Button";
import { WEEKDAY } from "../lib/week";

const h1 = (m: number) => `${(m / 60).toFixed(1)}h`;
const NUM = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
const fmtN = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

function Head({ right }: { right: React.ReactNode }) {
  return (
    <div className="flex w-full items-center gap-[14px]">
      <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">This week’s priorities</h2>
      <div className="h-px grow bg-border" />
      {right}
    </div>
  );
}

function list(days: string[], week: Week) {
  const names = days.map((d) => WEEKDAY[week.days.indexOf(d)]);
  return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

function Card({ p, i, week }: { p: Week["priorities"][number]; i: number; week: Week }) {
  const isSessions = p.unit === "sessions";
  const value = isSessions ? p.sessions : p.minutes / 60;
  const frac = p.target ? Math.min(1, value / p.target) : null;
  const left = p.target ? Math.max(0, p.target - value) : null;
  return (
    <>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-xs/4 text-ink-subtle">0{i + 1}</span>
        <span className="text-[15px]/[22px] font-medium">{p.text}</span>
      </div>
      <div className="flex items-baseline gap-2.5">
        <span className="text-[30px] font-semibold leading-9 tracking-tight">
          {isSessions ? p.sessions : h1(p.minutes)}
        </span>
        <span className="text-sm/tight text-ink-subtle">
          {p.target == null ? "credited so far"
            : isSessions ? `of ${fmtN(p.target)} sessions · ${h1(p.minutes)}`
            : `of a ${fmtN(p.target)}h target`}
        </span>
      </div>
      {frac != null && (
        <div className="h-[3px] w-full overflow-clip rounded-[2px] bg-sunken">
          <div className="h-full bg-ink" style={{ width: `${frac * 100}%` }} />
        </div>
      )}
      {left != null && (
        <span className="text-sm/tight text-ink-muted">
          {isSessions
            ? `${p.session_days.length ? `${list(p.session_days, week)}. ` : ""}${left === 0 ? "Done." : `${NUM[Math.ceil(left)] ?? fmtN(left)} to go.`}`
            : left === 0 ? "Reached." : `${h1(left * 60)} left`}
        </span>
      )}
    </>
  );
}

/** The phone's three lines per priority: the big number sits at
 * the right of the title, and everything else collapses into one mono line. */
export function PhonePriorities({ week, soFar }: { week: Week; soFar: string[] }) {
  const unjudged = soFar.filter((d) => !week.done.includes(d));
  return (
    <section className="flex flex-col gap-[18px]">
      <h2 className="px-4 text-[15px] leading-[18px] font-semibold tracking-[-0.01em]">This week’s priorities</h2>
      {week.priorities.length ? week.priorities.map((p, i) => {
        const isSessions = p.unit === "sessions";
        const value = isSessions ? p.sessions : p.minutes / 60;
        const frac = p.target ? Math.min(1, value / p.target) : null;
        const left = p.target ? Math.max(0, p.target - value) : null;
        const line = p.target == null ? "credited so far · no target"
          : isSessions
            ? `of ${fmtN(p.target)} sessions · ${h1(p.minutes)}${p.session_days.length ? ` · ${list(p.session_days, week)}` : ""}, ${
              left === 0 ? "done" : `${(NUM[Math.ceil(left!)] ?? fmtN(left!)).toLowerCase()} to go`}`
            : `of a ${fmtN(p.target)}h target · ${left === 0 ? "reached" : `${h1(left! * 60)} left`}`;
        return (
          <div key={i} className="flex flex-col gap-1.5 px-4">
            <div className="flex items-baseline gap-2">
              <span className="w-[18px] shrink-0 font-mono text-2xs leading-[18px] text-ink-subtle">0{i + 1}</span>
              <span className="grow basis-0 text-base leading-[18px] font-medium">{p.text}</span>
              <span className="shrink-0 text-[15px] leading-[18px] font-semibold tracking-[-0.01em]">
                {isSessions ? p.sessions : h1(p.minutes)}
              </span>
            </div>
            {frac != null && (
              <div className="h-1 w-full overflow-clip rounded-[2px] bg-sunken">
                <div className="h-full bg-ink" style={{ width: `${frac * 100}%` }} />
              </div>
            )}
            <span className="font-mono text-2xs leading-[14px] text-ink-subtle">{line}</span>
          </div>
        );
      }) : (
        <p className="px-4 text-sm/5 text-ink-muted">
          No priorities for this week. They are set in Umbra on your Mac, and each run credits the time that moved them forward.
        </p>
      )}
      {week.priorities.length > 0 && (
        <p className="px-4 text-xs leading-[18px] text-ink-subtle">
          Time may count toward more than one.
          {week.judging
            ? " Judging the week against these now…"
            : week.judge_failed ? ` Judging failed: ${week.judge_error}. The next run asks again.`
            : unjudged.length > 0 && ` Not yet judged against these: ${unjudged.map((d) => WEEKDAY[week.days.indexOf(d)]).join(", ")} — unasked, not zero.`}
        </p>
      )}
    </section>
  );
}

const field = "h-9 rounded-[6px] bg-surface px-3 text-sm shadow-[0_0_0_1px_var(--color-border-strong)] outline-none focus:shadow-[0_0_0_1px_var(--color-ink)]";

export function Priorities({ week, soFar }: { week: Week; soFar: string[] }) {
  const qc = useQueryClient();
  const toInputs = (): GoalInput[] => week.priorities.length
    ? week.priorities.map((p) => ({ text: p.text, target: p.target, unit: (p.unit ?? null) as GoalInput["unit"] }))
    : [{ text: "", target: null, unit: null }];
  // ?edit opens this section in edit mode — for links and screenshots.
  const [edit, setEdit] = useState<GoalInput[] | null>(
    () => (new URLSearchParams(location.search).has("edit") ? toInputs() : null));
  const save = useMutation({
    mutationFn: (g: GoalInput[]) => saveGoals(week.week, g),
    onSuccess: (w) => { qc.setQueryData(["week", week.week], w); setEdit(null); },
  });
  const unjudged = soFar.filter((d) => !week.done.includes(d));
  const set = (i: number, patch: Partial<GoalInput>) =>
    setEdit((e) => e!.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  if (edit) {
    return (
      <section className="flex flex-col gap-6 pb-11">
        <Head right={<>
          <span className="shrink-0 text-sm/tight text-ink-subtle">editing · new wording is judged when you save; a new target is not</span>
          <Button variant="ghost" onClick={() => setEdit(null)}>Cancel</Button>
          <Button variant="primary" onClick={() => save.mutate(edit)} disabled={save.isPending}>Save</Button>
        </>} />
        <div className="flex w-full">
          {edit.map((g, i) => (
            <div key={i} className={`flex grow basis-0 flex-col gap-3 ${i ? "border-l border-border pl-12" : ""} ${i < edit.length - 1 ? "pr-12" : ""}`}>
              <div className="flex items-center font-mono text-xs/4 text-ink-subtle">
                0{i + 1}<span className="grow" />
                <button type="button" className="font-sans text-xs text-ink-subtle hover:text-ink"
                  onClick={() => setEdit(edit.filter((_, j) => j !== i))}>Remove</button>
              </div>
              <input value={g.text} autoFocus={i === 0} aria-label={`Priority ${i + 1}`}
                onChange={(e) => set(i, { text: e.target.value })}
                className={`${field} h-10 text-[15px]`} />
              <span className="pt-1 text-xs/4 text-ink-subtle">Target</span>
              <div className="flex items-center gap-2">
                <input type="number" min="0" step="1" inputMode="decimal" aria-label="Target"
                  value={g.target ?? ""} placeholder="—"
                  onChange={(e) => set(i, { target: e.target.value === "" ? null : Number(e.target.value),
                                             unit: g.unit ?? "hours" })}
                  className={`${field} w-16 tabular-nums`} />
                <span className="relative">
                  <select value={g.unit ?? "hours"} aria-label="Unit"
                    onChange={(e) => set(i, { unit: e.target.value as GoalInput["unit"] })}
                    className={`${field} appearance-none pr-8`}>
                    <option value="hours">hours</option>
                    <option value="sessions">sessions</option>
                  </select>
                  <svg className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-ink-subtle"
                    width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </span>
                <span className="text-[13px] text-ink-subtle">
                  {week.priorities[i] && week.priorities[i]!.text === g.text
                    ? (g.unit === "sessions" ? `${week.priorities[i]!.sessions} sessions so far` : `${h1(week.priorities[i]!.minutes)} so far this week`)
                    : "counted once judged"}
                </span>
              </div>
            </div>
          ))}
        </div>
        <div className="flex items-start gap-4">
          {edit.length < 3 && (
            <Button size="default" onClick={() => setEdit([...edit, { text: "", target: null, unit: null }])}>+ Add a priority</Button>
          )}
          <p className="max-w-[620px] text-sm/[21px] text-ink-subtle">
            A priority is a question asked of the week, not a timer. The model reads each day and decides which stretches
            moved it forward, so one hour can count toward two. Leave the target empty to track a priority without a
            number.
          </p>
        </div>
        {save.isError && <p className="text-sm text-ink">{save.error.message}</p>}
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-6 pb-11">
      <Head right={<>
        <span className="shrink-0 text-sm/tight text-ink-subtle">time may count toward more than one</span>
        {!STATIC && (
          <Button onClick={() => setEdit(toInputs())}>
            {week.priorities.length ? "Edit" : "Set priorities"}
          </Button>
        )}
      </>} />
      {week.priorities.length ? (
        <div className="flex w-full">
          {week.priorities.map((p, i) => (
            <div key={i} className={`flex grow basis-0 flex-col gap-[14px] ${i ? "border-l border-border pl-12" : ""} ${i < week.priorities.length - 1 ? "pr-12" : ""}`}>
              <Card p={p} i={i} week={week} />
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm/5 text-ink-muted">
          No priorities for this week. Set up to three, and each run credits the time that moved them forward.
        </p>
      )}
      {week.priorities.length > 0 && week.judging && (
        <p className="text-xs/4 text-ink-subtle">Judging the week against these priorities now…</p>
      )}
      {week.priorities.length > 0 && !week.judging && week.judge_failed && (
        <p className="text-xs/4 text-ink">
          Judging failed: {week.judge_error}. The next run asks again.
        </p>
      )}
      {week.priorities.length > 0 && !week.judging && unjudged.length > 0 && (
        <p className="text-xs/4 text-ink-subtle">
          Not yet judged against these: {unjudged.map((d) => WEEKDAY[week.days.indexOf(d)]).join(", ")} — their time
          is unasked, not zero.
        </p>
      )}
    </section>
  );
}
