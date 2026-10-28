// Your own correction, in the event panel (umbra/correct.py): one button, an
// editor in place, and — on a block of yours — what the model makes of it and
// what it said before. Short words on purpose: the panel is read at a glance.
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { DayEvent, Meta } from "../api/types";
import { READ_ONLY_NOTE, STATIC, correctDay } from "../api/client";
import { CATS, hhmm, minutes, modelSays, spanError } from "../lib/correct";
import { cat, catName } from "../lib/day";
import { wallRange, type Seg } from "../lib/clock";

const ghost = "rounded-md bg-sunken px-3 py-1.5 text-[13px]/4 font-medium text-ink hover:bg-fill-hover disabled:opacity-50";
const plain = "px-2 py-1.5 text-[13px]/4 text-ink-muted hover:text-ink disabled:opacity-50";

function useSave(date: string, onSaved?: (a: number, b: number) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => correctDay(date, body),
    onSuccess: (_d, body) => {
      void qc.invalidateQueries({ queryKey: ["day", date] });
      onSaved?.(Number(body.a ?? -1), Number(body.b ?? -1));
    },
  });
}

function Editor({ meta, date, e, n, onDone, onSaved }: {
  meta: Meta; date: string; e: DayEvent; n: number; onDone: () => void;
  onSaved?: (a: number, b: number) => void;
}) {
  const [from, setFrom] = useState(hhmm(e.a));
  const [to, setTo] = useState(hhmm(e.b));
  const [c, setC] = useState<string | null>(e.mine ? e.cat : null);
  const [note, setNote] = useState(e.mine ? e.title : "");
  const save = useSave(date, (a, b) => { onDone(); onSaved?.(a, b); });
  const a = minutes(from), b = minutes(to);
  const bad = spanError(a, b, n) ?? (c ? null : "Pick a category.");
  const time = "w-[72px] rounded-md px-2.5 py-1.5 font-mono text-[13px]/4 shadow-[0_0_0_1px_var(--color-border-strong)] bg-surface";
  return (
    <form className="flex flex-col gap-3.5 rounded-xl bg-surface p-5 shadow-(--shadow-popover)"
      onSubmit={(ev) => { ev.preventDefault(); if (!bad) save.mutate({ a, b, cat: c, text: note.trim() }); }}>
      <span className="text-[15px]/5 font-semibold">What were you doing?</span>
      <label className="flex flex-col gap-1.5">
        <span className="text-xs text-ink-subtle">When</span>
        <span className="flex items-center gap-2 font-mono text-[13px]">
          <input aria-label="From" className={time} value={from} onChange={(x) => setFrom(x.target.value)} />–
          <input aria-label="To" className={time} value={to} onChange={(x) => setTo(x.target.value)} />
        </span>
      </label>
      <div className="flex flex-col gap-1.5">
        <span className="text-xs text-ink-subtle">Category</span>
        <div role="radiogroup" aria-label="Category" className="flex flex-wrap gap-1.5">
          {CATS.map((k) => (
            <button key={k} type="button" role="radio" aria-checked={c === k} onClick={() => setC(k)}
              className={`inline-flex items-center gap-1.5 rounded-md py-1.5 pr-2.5 pl-2 text-[13px]/4
                ${c === k ? "bg-surface font-medium text-ink shadow-[0_0_0_1.5px_var(--color-ink)]" : "bg-canvas text-ink-muted hover:text-ink"}`}>
              <i className="size-2 rounded-[2px]" style={{ background: cat(k) }} />{catName(meta, k)}
            </button>
          ))}
        </div>
      </div>
      <label className="flex flex-col gap-1.5">
        <span className="text-xs text-ink-subtle">Note (optional)</span>
        <input className="rounded-md px-2.5 py-2 text-[13px]/4 shadow-[0_0_0_1px_var(--color-border-strong)] bg-surface"
          value={note} onChange={(x) => setNote(x.target.value)} />
      </label>
      <span className="flex items-center gap-2">
        <button type="submit" disabled={!!bad || save.isPending}
          className="rounded-md bg-ink px-3.5 py-1.5 text-[13px]/4 font-medium text-canvas disabled:opacity-40">Save</button>
        <button type="button" className={plain} onClick={onDone}>Cancel</button>
        {(save.error || (bad && c)) && (
          <span className="text-xs text-ink-muted">{(save.error as Error | null)?.message ?? bad}</span>
        )}
      </span>
    </form>
  );
}

/** The button under the title, and the editor it opens. A stretch the model
 * said nothing about (gap) opens straight into the editor. */
export function CorrectBar({ meta, date, e, n, open: opened = false, onSaved }: {
  meta: Meta; date: string; e: DayEvent; n: number; open?: boolean;
  onSaved?: (a: number, b: number) => void;
}) {
  const [open, setOpen] = useState(opened);
  const undo = useSave(date, onSaved);
  if (open) return <Editor meta={meta} date={date} e={e} n={n} onDone={() => setOpen(false)} onSaved={onSaved} />;
  // The exported page is read-only: the buttons show, and say why they do nothing.
  const ro = !!STATIC;
  const title = ro ? READ_ONLY_NOTE : undefined;
  return e.mine ? (
    <span className="flex items-center gap-1">
      <button type="button" className={ghost} disabled={ro} title={title} onClick={() => setOpen(true)}>Edit</button>
      <button type="button" className={plain} disabled={ro || undo.isPending} title={title}
        onClick={() => undo.mutate({ undo: e.said_at })}>Undo</button>
    </span>
  ) : (
    <button type="button" className={`${ghost} self-start`} disabled={ro} title={title} onClick={() => setOpen(true)}>✎ Correct</button>
  );
}

function Rule({ children }: { children: string }) {
  return (
    <div className="flex w-full items-center gap-[14px]">
      <h3 className="shrink-0 text-sm/tight font-medium text-ink-muted">{children}</h3>
      <div className="h-px grow bg-border" />
    </div>
  );
}

const TONE = { pending: "text-ink-muted", nosay: "text-ink-muted", agree: "text-ink", soft: "text-ink-muted", vs: "text-ink" };

/** On a block of yours: what the model makes of it, and what it said before. */
export function Yours({ meta, e, segs = [] }: { meta: Meta; e: DayEvent; segs?: Seg[] }) {
  const says = modelSays(meta, e);
  return (
    <>
      <section className="flex flex-col gap-3">
        <Rule>Model</Rule>
        <p className={`text-base/[22px] ${TONE[says.tone]}`}>
          {says.tone === "vs" ? <><span className="font-semibold text-alert">⚠</span> {says.text}</> : says.text}
        </p>
      </section>
      {(e.was?.length ?? 0) > 0 && (
        <section className="flex flex-col gap-2.5">
          <Rule>Before</Rule>
          {e.was!.map((w) => (
            <span key={`${w.a}`} className="flex items-center gap-2.5 text-[13px]/4 text-ink-muted">
              <span className="w-[96px] font-mono text-xs text-ink-subtle">{wallRange(w.a!, w.b!, segs)}</span>
              <i className="size-2 rounded-[2px]" style={{ background: cat(w.cat ?? "unknown") }} />{w.title}
            </span>
          ))}
        </section>
      )}
    </>
  );
}
