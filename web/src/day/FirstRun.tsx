// Nothing has been imported yet, and the import running.
//
// The bar is plain ground here, not the hatch: the hatch means "recorded,
// not judged", and nothing has been recorded. While the import runs, the bar
// stays unread until every source is in — half an import drawn as a day
// would look like a quiet day, not an unfinished one.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { Meta, Setup } from "../api/types";
import { READ_ONLY_NOTE, STATIC, importQuery, metaQuery, setupQuery, startImport } from "../api/client";
import { Button } from "../ui/Button";
import { nowMinute } from "../lib/time";
import { Topbar } from "./Topbar";
import { DayNav } from "./DayNav";
import { DayBar } from "./DayBar";
import { Notice } from "./Notice";
import { useSettings } from "../settings/SettingsDialog";

type Src = Setup["sources"][number];

function Spinner() {
  return (
    <svg className="size-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="8" stroke="currentColor" strokeOpacity="0.3" strokeWidth="2.5" />
      <path d="M12 4a8 8 0 0 1 8 8" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

function Group({ title, rows, right, status, note }: {
  title: string; rows: Src[]; right?: React.ReactNode;
  status: (s: Src) => { text: string; tone: "ink" | "muted" | "subtle" }; note?: string;
}) {
  if (!rows.length) return null;
  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-2 pb-2">
        <span className="text-[13px]/4 text-ink-subtle">{title}</span>
        <span className="font-mono text-xs/4 text-ink-subtle">{rows.length}</span>
        <span className="grow" />
        {right}
      </div>
      {rows.map((s, i) => {
        const st = status(s);
        return (
          <div key={s.name} className={`flex h-11 items-center gap-6 ${i ? "border-t border-border" : ""}`}>
            <div className="flex w-[300px] shrink-0 items-baseline gap-2">
              <span className="text-sm/[18px] font-medium">{s.label}</span>
              <span className="font-mono text-xs/4 text-ink-subtle">{s.name}</span>
            </div>
            <span className="grow text-[13px]/[18px] text-ink-muted">{s.what}</span>
            <span className={`shrink-0 font-mono text-xs/4 ${st.tone === "ink" ? "text-ink" : st.tone === "muted" ? "text-ink-muted" : "text-ink-subtle"}`}>
              {st.text}
            </span>
          </div>
        );
      })}
      {note && <p className="max-w-[760px] pt-3 text-xs/[18px] text-ink-subtle">{note}</p>}
    </div>
  );
}

export function FirstRun({ meta }: { meta: Meta }) {
  const setup = useQuery(setupQuery);
  const imp = useQuery(importQuery);
  const qc = useQueryClient();
  const settings = useSettings();
  const go = useMutation({
    mutationFn: startImport,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["import"] }),
  });

  const run = imp.data?.run;
  const running = !!run?.running || go.isPending;
  // The run finishing is what makes days exist; re-read meta then, and the
  // index route sends the page to the newest day.
  const finished = !!run && !run.running && run.sources.some((s) => s.state === "done");
  useEffect(() => {
    if (finished) void qc.invalidateQueries({ queryKey: metaQuery.queryKey });
  }, [finished, qc]);

  const srcs = setup.data?.sources ?? [];
  const ready = srcs.filter((s) => s.state === "ready");
  const perm = srcs.filter((s) => s.state === "unknown");
  const keys = srcs.filter((s) => s.state === "missing");
  const prog = new Map((run?.sources ?? []).map((s) => [s.source, s]));
  const done = ready.filter((s) => prog.get(s.name)?.state === "done").length;

  const readyStatus = (s: Src) => {
    const p = running ? prog.get(s.name) : undefined;
    if (!p) return { text: "ready", tone: "ink" as const };
    if (p.state === "done") return { text: `${(p.rows ?? 0).toLocaleString()} records`, tone: "muted" as const };
    if (p.state === "reading") return { text: "reading…", tone: "ink" as const };
    if (p.state === "failed") return { text: "failed", tone: "ink" as const };
    return { text: "next", tone: "subtle" as const };
  };

  const today = meta.today;
  const now = nowMinute();
  const reading = ready.find((s) => prog.get(s.name)?.state === "reading");

  return (
    <main className="mx-auto w-full max-w-[1440px] px-10 pb-16">
      <Topbar meta={meta} />
      <DayNav meta={meta} date={today} evs={[]} />
      <div className="flex w-full items-end gap-5 pb-[30px]">
        <div className="flex flex-col gap-2">
          <div className="flex items-end gap-1">
            {Array.from({ length: 21 }, (_, i) => (
              <div key={i} className={`h-9 w-[14px] rounded-[2px] bg-sunken ${i === 20 ? "shadow-[0_0_0_1px_var(--color-ink)]" : ""}`} />
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-[3px] pb-5 text-sm/tight">
          <p className="text-ink-muted">Three weeks fill in here, one column per day, as days are read.</p>
          <p className="text-ink-subtle">
            {running ? "Columns appear once the import finishes, not source by source." : "Nothing has been imported yet, so there is nothing to compare."}
          </p>
        </div>
      </div>
      <DayBar meta={meta} evs={[]} onSelect={() => {}} now={now} unread />
      <div className="pb-8" />

      {running ? (
        <Notice
          title={`Reading what is on this Mac — ${done} of ${ready.length} sources in`}
          action={<Button variant="primary" size="default" disabled className="!opacity-100 pl-3"><Spinner />Importing</Button>}
          sub="a first run reads all history"
        >
          {reading ? `${reading.label} is being read now. ` : ""}
          The bar above stays unread until every source is in — half an import drawn as a day would look like a quiet
          day, not an unfinished one.
        </Notice>
      ) : (
        <Notice
          title="Nothing has been read yet"
          action={STATIC ? undefined : <Button variant="primary" size="default" onClick={() => go.mutate()}>Import now</Button>}
          sub={`${ready.length} sources · on this Mac, no network`}
        >
          Umbra builds each day from records already on this Mac, and it has not opened any of them. The bar above is
          unread, not empty — nothing on this page is a finding yet.
          {go.isError && <span className="block pt-2 text-ink">{String(go.error.message)}</span>}
        </Notice>
      )}

      <section className="flex flex-col gap-8">
        <div className="flex w-full items-center gap-[14px]">
          <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">What can be read</h2>
          <div className="h-px grow bg-border" />
          <span className="shrink-0 text-sm/tight text-ink-subtle">
            {srcs.length} sources · {ready.length} ready on this Mac right now
          </span>
        </div>
        <Group title="Ready now" rows={ready} status={readyStatus} />
        <Group title="One permission away" rows={perm} status={() => ({ text: "needs Full Disk Access", tone: "subtle" })}
          note="One Full Disk Access grant covers all of these. Give it to the Python the background job runs, not the one in your terminal — they are different files. umbra doctor names the right one." />
        <Group title="Needs a key or a login" rows={keys}
          status={(s) => ({ text: s.needs[0]?.replace(/ \(.*\)$/, "") ?? "needs setup", tone: "subtle" })}
          note="Skip any you don't use — a source that was never set up is simply absent, it is not a gap in your day." />

        {meta.model.key_set === false && (
          <div className="flex items-start gap-6 rounded-[10px] bg-surface px-5 py-4">
            <div className="grow">
              <p className="text-sm/5 font-medium">No model key yet — and the import does not need one</p>
              <p className="text-sm/5 text-ink-muted">
                Without a model you get grey lanes of what was recorded. Colour appears on a day only once a model has
                read it; until then the bar stays hatched, never guessed.
              </p>
            </div>
            <Button onClick={() => settings("model")}>Add a model key</Button>
          </div>
        )}
      </section>
    </main>
  );
}
