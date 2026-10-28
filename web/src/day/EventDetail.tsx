// The detail panel: one event, and how it came to be said.
//
// Four numbered steps, each a real count from the data, never a paraphrase:
//   01 what the composing pass saw (span summaries, no raw data)
//   02 the window the spans were judged against, and what was in it
//   03 what the model itself cited — its picks, not its input
//   04 every raw record in the window, verbatim
import { wallHm, wallRange, type Seg } from "../lib/clock";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import type { DayEvent, Meta, Span } from "../api/types";
import { detailQuery, rawQuery } from "../api/client";
import { cat, catName } from "../lib/day";
import { CorrectBar, Yours } from "./Correct";

function Rule({ children }: { children: ReactNode }) {
  return (
    <div className="flex w-full items-center gap-[14px]">
      <h3 className="shrink-0 text-sm/tight font-medium text-ink-muted">{children}</h3>
      <div className="h-px grow bg-border" />
    </div>
  );
}

function Toggle({ open, onClick, children }: { open: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-expanded={open}
      className="text-sm/tight font-medium text-ink underline decoration-1 underline-offset-2 hover:text-ink-muted">
      {open ? "Hide" : children}
    </button>
  );
}

function Step({ n, children }: { n: string; children: ReactNode }) {
  return (
    <div className="flex gap-[14px] pb-3">
      <span className="w-[18px] shrink-0 pt-[3px] font-mono text-xs/tight text-ink-subtle">{n}</span>
      <div className="flex grow flex-col gap-2.5">{children}</div>
    </div>
  );
}

function summarise(meta: Meta, spans: Span[]) {
  if (!spans.length) return "No first-pass spans could be matched to this event.";
  const cats = new Set(spans.map((s) => s.cat));
  const confs = new Set(spans.map((s) => s.conf));
  const n = `${spans.length} span${spans.length === 1 ? "" : "s"}`;
  if (cats.size === 1) {
    const c = catName(meta, spans[0]!.cat);
    const conf = confs.size === 1 ? ` at ${spans[0]!.conf} confidence` : "";
    return `${n}, ${spans.length === 1 ? "judged" : "every one judged"} ${c}${conf}.`;
  }
  const by: Record<string, number> = {};
  for (const s of spans) by[s.cat] = (by[s.cat] ?? 0) + 1;
  return `${n}: ${Object.entries(by).sort((a, b) => b[1] - a[1])
    .map(([c, k]) => `${k} ${catName(meta, c)}`).join(", ")}.`;
}

/** gap: a stretch the model said nothing about, opened to say what it was —
 * only the editor, since there is nothing of the model's to show. */
export function EventDetail({ meta, date, e, segs = [], n = 1440, gap = false, onSaved }: {
  meta: Meta; date: string; e: DayEvent; segs?: Seg[]; n?: number; gap?: boolean;
  onSaved?: (a: number, b: number) => void;
}) {
  const detail = useQuery(detailQuery(date));
  const [open, setOpen] = useState<"text" | "cited" | "raw" | null>(null);
  const toggle = (k: typeof open) => setOpen((o) => (o === k ? null : k));
  const raw = useQuery({ ...rawQuery(date), enabled: open === "raw" || !!e.win });

  const ai = detail.data?.ai;
  const all = (ai?.use && ai.spans[ai.use]) || [];
  const spans = (e.sp ?? []).map((i) => all[i]).filter((s): s is Span => !!s);
  const dg = (e.dg ?? []).map((i) => detail.data?.dg?.[i]).filter((d) => !!d);
  const cited = spans.flatMap((s) => s.ev.map((ev) => ({ at: s.a, ev })));
  const also = [...new Set(spans.flatMap((s) => (s.also as string[] | undefined) ?? []))];
  const [wa, wb] = e.win ?? [e.a, e.b];
  const inWin = (raw.data?.records ?? []).filter((r) => r.ts / 60 >= wa! && r.ts / 60 < wb!);
  const bySrc: Record<string, number> = {};
  for (const r of inWin) bySrc[r.source] = (bySrc[r.source] ?? 0) + 1;
  const mins = e.b - e.a;

  return (
    <article className="flex min-w-0 grow flex-col gap-6">
      <header className="flex flex-col gap-2.5">
        <div className="flex items-center gap-2.5">
          <i className="size-2 rounded-[2px]" style={{ background: cat(e.cat) }} />
          <span className="text-sm/tight font-medium" style={{ color: cat(e.cat) }}>{catName(meta, e.cat)}</span>
          <span className="font-mono text-xs/tight text-ink-subtle">{wallRange(e.a, e.b, segs)} · {mins} min{e.mine ? " · ✎" : ""}</span>
        </div>
        <h2 className="text-[22px] font-semibold leading-[30px] tracking-tight">{e.title}</h2>
        <CorrectBar key={`${e.a}-${e.b}-${gap}`} meta={meta} date={date} e={e} n={n} open={gap} onSaved={onSaved} />
      </header>
      {e.mine && <Yours meta={meta} e={e} segs={segs} />}
      {!gap && <>

      <section className="flex flex-col gap-3">
        <Rule>How the model judged these {mins} minutes</Rule>
        <div className="flex h-4 w-full gap-[3px]">
          {spans.map((s) => (
            <div key={s.a} className="rounded-xs" title={`${wallRange(s.a, s.b, segs)} ${s.act}`}
              style={{ flexGrow: s.b - s.a, flexBasis: 0, background: cat(s.cat) }} />
          ))}
        </div>
        <p className="flex flex-wrap items-baseline gap-2 text-sm/tight">
          <span className="text-ink-muted">{detail.isPending ? "…" : summarise(meta, spans)}</span>
          {also.length > 0 && <span className="text-ink-subtle">Also noted: {also.join(", ")}.</span>}
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <Rule>Why it says so</Rule>
        <Step n="01">
          <p className="text-base/[22px] text-ink-muted">
            The pass that composed this event saw only the day’s {detail.isPending ? "…" : all.length} span summaries. No raw data at all.
          </p>
        </Step>
        <Step n="02">
          <p className="text-base/[22px] text-ink-muted">
            The spans themselves were judged against {wallRange(wa!, wb!, segs)} — the target stretch plus an hour either
            side — which held {raw.isPending ? "…" : inWin.length} records.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(bySrc).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
              <span key={s} className="rounded-[4px] bg-sunken px-2 py-[3px] font-mono text-2xs leading-[14px] text-ink-muted">
                {meta.labels.source[s] ?? s} {n}
              </span>
            ))}
          </div>
          {dg.length > 0 && (
            <p className="flex flex-wrap items-baseline gap-2 text-sm/tight">
              <span className="text-ink-subtle">
                compressed into {dg.length} chunk{dg.length === 1 ? "" : "s"} / {dg.reduce((n, d) => n + d!.chars, 0)} chars
                — that text, not the {inWin.length} records, is what the model read.
              </span>
              <Toggle open={open === "text"} onClick={() => toggle("text")}>View text</Toggle>
            </p>
          )}
          {open === "text" && (
            <pre className="max-h-80 overflow-auto rounded-md bg-sunken p-3 font-mono text-xs/[18px] whitespace-pre-wrap text-ink-muted">
              {dg.map((d) => `── ${d!.start}–${d!.end} (read ${d!.wstart}–${d!.wend}, ${d!.n} records)\n${d!.text}`).join("\n\n")}
            </pre>
          )}
        </Step>
        <Step n="03">
          <p className="flex flex-wrap items-baseline gap-2">
            <span className="text-base/[22px] text-ink-muted">The model itself cited {detail.isPending ? "…" : cited.length} records as evidence.</span>
            {cited.length > 0 && <Toggle open={open === "cited"} onClick={() => toggle("cited")}>Show</Toggle>}
          </p>
          <p className="flex items-baseline gap-2 text-sm/snug">
            <span className="text-alert" aria-hidden>⚠</span>
            <span className="text-ink-subtle">
              These are the model’s own picks, not its full input, and it rewrites labels as it quotes them.
            </span>
          </p>
          {open === "cited" && (
            <ul className="flex flex-col gap-1 font-mono text-xs/[18px] text-ink-muted">
              {cited.map((c, i) => <li key={i}>{c.ev}</li>)}
            </ul>
          )}
        </Step>
        <Step n="04">
          <p className="flex flex-wrap items-baseline gap-2">
            <span className="text-base/[22px] text-ink-muted">
              Every raw record in this window — all {raw.isPending ? "…" : inWin.length}, verbatim.
            </span>
            <Toggle open={open === "raw"} onClick={() => toggle("raw")}>Show</Toggle>
          </p>
          {open === "raw" && (
            <ul className="flex max-h-96 flex-col gap-1 overflow-auto font-mono text-xs/[18px] text-ink-muted">
              {inWin.map((r, i) => {
                const { ts, source, kind, ...rest } = r;
                return (
                  <li key={i} className="flex gap-3">
                    <span className="shrink-0 text-ink-subtle">{wallHm(Math.floor(ts / 60), segs)}</span>
                    <span className="w-28 shrink-0">{meta.labels.source[source] ?? source}</span>
                    <span className="truncate">{kind} {JSON.stringify(rest)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </Step>
      </section>
      </>}
    </article>
  );
}
