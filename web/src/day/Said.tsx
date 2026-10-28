// What was said aloud — a trigger by the date, and a drawer.
//
// The drawer is non-modal: the bar stays visible and the picked item's
// 30-minute window is outlined on it, which is the point of opening it here
// rather than on its own page. No title: the trigger that opened it already
// said what it is, and the tabs are the head.
//
// Verdicts on claims are ranked by ink weight only, never hue — hue means a
// category, and the alert colour is for ⚠.
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { READ_ONLY_NOTE, STATIC, setClaimSeen, setTaskVerdict } from "../api/client";
import type { Said, SaidClaim, SaidTask } from "../api/types";
import { Button } from "../ui/Button";
import { Sheet } from "../ui/Sheet";
import { useFluidHover } from "../ui/fluid";
import { shortDate } from "../lib/time";
import { anything, isOpen, itemsOf, lookupNote, sites, tabsOf, type Tab } from "../lib/said";

export { isOpen, type Tab };

// A task's state, said in words (see lib/said for which count as closed).
const state = (t: SaidTask) =>
  t.status === "dispatched" ? "sent"
  : t.status === "approved" ? "ran"
  : t.status === "duplicate" ? "said again — kept once"
  : t.status === "dismissed"
    ? t.verdict === "not_a_task" ? "not a task" : t.verdict === "already_done" ? "done" : "dismissed"
  : null;
const openFirst = <T,>(xs: T[], open: (x: T) => boolean) => [...xs.filter(open), ...xs.filter((x) => !open(x))];

/** Small text actions under a row — outside the row's own button, which
 * selects it. In the exported copy they show, disabled, and say why. */
function Acts({ children }: { children: React.ReactNode }) {
  return <div className="flex gap-1 pb-2.5 pl-[68px]">{children}</div>;
}
function Act({ label, onClick, busy }: { label: string; onClick: () => void; busy: boolean }) {
  return (
    <Button variant="ghost" className="h-6 px-2 text-2xs" disabled={!!STATIC || busy}
      title={STATIC ? READ_ONLY_NOTE : undefined} onClick={onClick}>
      {label}
    </Button>
  );
}

const Wave = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" aria-hidden className="text-ink-muted">
    <path d="M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3" />
  </svg>
);

export function SaidTrigger({ said, open, onOpen }: {
  said: Said; open: Tab | null; onOpen: (t: Tab) => void;
}) {
  if (!anything(said)) return null;
  const tabs = tabsOf(said);
  const half = (t: Tab, n: number, label: string) => (
    <span role="button" tabIndex={-1}
      onClick={(e) => { e.stopPropagation(); onOpen(t); }}
      className={open && open !== t ? "text-ink-subtle" : "text-ink"}>
      {n} {label}
    </span>
  );
  return (
    <Button open={!!open} className="pl-2" aria-label="Said aloud"
      onClick={() => onOpen(open ?? (tabs.find((t) => itemsOf(said, t.tab).length) ?? tabs[0]!).tab)}>
      <Wave />
      {tabs.map((t, i) => (
        <span key={t.tab} className="contents">
          {i > 0 && <span className="text-ink-subtle">·</span>}
          {half(t.tab, t.n, t.label.toLowerCase())}
        </span>
      ))}
    </Button>
  );
}

const VERDICT: Record<string, string> = {
  likely_wrong: "text-ink font-semibold",
  unsure: "text-ink-muted font-medium",
  confident_right: "text-ink-subtle",
};

function Row({ time, under, selected, faded = false, onClick, acts, children }: {
  time: string; under?: string; selected: boolean; faded?: boolean; onClick: () => void;
  acts?: React.ReactNode; children: React.ReactNode;
}) {
  // The wrapper carries the divider: a border on the rounded button itself
  // would curve up at both ends.
  return (
    <div data-fluid className="relative">
    <button type="button" onClick={onClick}
      className={`flex w-full gap-3 rounded-lg px-3 py-3.5 text-left transition-colors duration-[80ms]
        ${selected ? "bg-sunken" : ""} ${faded && !selected ? "opacity-50" : ""}`}>
      <span className="flex w-12 shrink-0 flex-col gap-0.5 pt-0.5 font-mono">
        <span className={`text-xs/4 ${selected ? "text-ink-muted" : "text-ink-subtle"}`}>{time}</span>
        {under && <span className="text-2xs leading-[14px] text-ink-subtle">{under}</span>}
      </span>
      <span className="flex grow flex-col gap-1">{children}</span>
    </button>
    {acts}
    </div>
  );
}

const Quote = ({ q }: { q: string }) => <span className="text-[13px]/5 text-ink-muted">“{q}”</span>;

// What a lookup found, ranked by ink weight like the first guess: wrong is the
// heaviest, right the lightest — never by hue, which means a category.
const FOUND: Record<string, string> = {
  wrong: "text-ink font-semibold",
  inaccurate: "text-ink font-medium",
  unsettled: "text-ink-muted font-medium",
  right: "text-ink-subtle",
};

function Found({ c }: { c: SaidClaim }) {
  const f = c.found!;
  return (
    <span className="flex flex-col gap-1 pt-1">
      <span className="flex items-baseline gap-1.5 font-mono text-2xs leading-[14px]">
        <span className={FOUND[f.verdict] ?? "text-ink-muted"}>{f.verdict}</span>
        {f.misheard && <span className="text-ink-subtle">· probably misheard</span>}
      </span>
      {f.actual && <span className="text-[13px]/5 text-ink">{f.actual}</span>}
      {f.sources.length > 0 && (
        <span className="flex flex-wrap gap-x-2.5 text-xs/4">
          {sites(f.sources).map((s) => (
            <a key={s.url} href={s.url} target="_blank" rel="noreferrer" title={s.title || s.url}
              onClick={(e) => e.stopPropagation()}
              className="text-ink-muted underline decoration-border-strong underline-offset-[3px] hover:text-ink">
              {s.site}
            </a>
          ))}
        </span>
      )}
      <span className="font-mono text-2xs leading-[14px] text-ink-subtle">
        first guess {c.verdict.replace("_", " ")}
        {c.status === "duplicate" && <span className="text-ink-muted"> · said again — kept once</span>}
      </span>
    </span>
  );
}

/** phone: the same content in a bottom sheet, no ×, 32px tabs;
 * picking an item closes the sheet so its window can be seen on the bar. */
export function SaidDrawer({ said, tab, setTab, sel, onSel, onClose, date, phone = false }: {
  said: Said; tab: Tab; setTab: (t: Tab) => void;
  sel: number | null; onSel: (id: number) => void; onClose: () => void; date: string;
  phone?: boolean;
}) {
  // A tab whose lane was switched off is gone; land on one that is there.
  const tabs = tabsOf(said);
  if (!tabs.some((t) => t.tab === tab) && tabs[0]) tab = tabs[0].tab;
  // Escape, or a press anywhere outside the drawer, closes it — except on a
  // said-aloud trigger, which switches tabs or closes it itself.
  const aside = useRef<HTMLElement>(null);
  // It slides in from the edge it lives on and back out to it (FF: a drawer
  // is the slow tier, its exit one step quicker); the parent unmounts it
  // once it is out.
  const [leaving, setLeaving] = useState(false);
  const close = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? onClose() : setLeaving(true));
  useEffect(() => {
    if (phone) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && close();
    const down = (e: PointerEvent) => {
      const t = e.target as Element;
      if (aside.current?.contains(t) || t.closest?.('[aria-label="Said aloud"]')) return;
      close();
    };
    window.addEventListener("keydown", k);
    window.addEventListener("pointerdown", down);
    return () => { window.removeEventListener("keydown", k); window.removeEventListener("pointerdown", down); };
  }, [phone]);

  const fluid = useFluidHover<HTMLDivElement>();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["day", date, "said"] });
  const verdict = useMutation({
    mutationFn: (v: { id: number; verdict: "already_done" | "not_a_task" | null }) => setTaskVerdict(v.id, v.verdict),
    onSettled: refresh,
  });
  const seen = useMutation({
    mutationFn: (v: { id: number; seen: boolean }) => setClaimSeen(v.id, v.seen),
    onSettled: refresh,
  });
  const failed = (verdict.error ?? seen.error) as Error | null;
  const tabBtn = (t: Tab, label: string, n: number) => (
    <button type="button" onClick={() => setTab(t)} aria-pressed={tab === t}
      className={`flex ${phone ? "h-8" : "h-7"} grow basis-0 items-center justify-center gap-1.5 rounded-[5px] text-[13px]/4 font-medium
        ${tab === t ? "bg-surface text-ink shadow-[0_1px_2px_#0000001a]" : "text-ink-muted hover:text-ink"}`}>
      {label}<span className="font-mono text-xs/4 text-ink-subtle">{n}</span>
    </button>
  );

  const body = (
    <>
      <div className="flex items-center gap-2">
        <div className="flex grow rounded-[7px] bg-sunken p-0.5">
          {tabs.map((t) => <span key={t.tab} className="contents">{tabBtn(t.tab, t.label, t.n)}</span>)}
        </div>
        {!phone && <Button variant="ghost" icon aria-label="Close" onClick={close}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden>
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </Button>}
      </div>
      <p className="px-1 pb-1 text-xs/[18px] text-ink-subtle">
        {tab === "todo"
          ? `${shortDate(date)} · read in 30-minute windows — ${phone ? "tap one to see its window on the bar" : "the one you pick lights up on the bar"}. Recordings land ~40 hours late, so this day may still grow.`
          : `${shortDate(date)} · only windows with two or more people talking — ${phone ? "tap one to see its window on the bar" : "the one you pick lights up on the bar"}.`}
      </p>

      <div ref={fluid.ref} {...fluid.handlers} className="relative flex flex-col [&>[data-fluid]~[data-fluid]]:border-t [&>[data-fluid]~[data-fluid]]:border-border">
        {fluid.highlight("rounded-lg bg-hover")}
        {tab === "todo"
          ? openFirst(itemsOf(said, "todo"), isOpen).map((t: SaidTask) => (
              <Row key={t.id} time={t.t} selected={sel === t.id} faded={!isOpen(t)} onClick={() => onSel(t.id)}
                acts={
                  isOpen(t) ? (
                    <Acts>
                      <Act label="✓ Done" busy={verdict.isPending} onClick={() => verdict.mutate({ id: t.id, verdict: "already_done" })} />
                      <Act label="✕ Not a task" busy={verdict.isPending} onClick={() => verdict.mutate({ id: t.id, verdict: "not_a_task" })} />
                    </Acts>
                  ) : t.status === "dismissed" || t.status === "duplicate" ? (
                    <Acts>
                      <Act label="Undo" busy={verdict.isPending} onClick={() => verdict.mutate({ id: t.id, verdict: null })} />
                    </Acts>
                  ) : undefined
                }>
                <span className={`text-sm/5 font-medium ${isOpen(t) ? "" : "line-through decoration-ink-subtle"}`}>{t.task}</span>
                <Quote q={t.quote} />
                <span className="pt-1 font-mono text-2xs leading-[14px] text-ink-subtle">
                  {t.type} · {t.domain}{t.marked ? " · marked" : ""}
                  {state(t) && <span className="text-ink-muted"> · {state(t)}</span>}
                </span>
              </Row>
            ))
          : openFirst(itemsOf(said, "check"), (c) => !c.seen).map((c: SaidClaim) => (
              <Row key={c.id} time={c.t} under={c.speaker.replace("speaker ", "spk ")}
                selected={sel === c.id} faded={c.seen} onClick={() => onSel(c.id)}
                acts={c.status === "duplicate" ? undefined : (
                  <Acts>
                    <Act label={c.seen ? "Undo" : "✓ Seen"} busy={seen.isPending}
                      onClick={() => seen.mutate({ id: c.id, seen: !c.seen })} />
                  </Acts>
                )}>
                <span className="text-sm/5 font-medium">{c.claim}</span>
                <Quote q={c.quote} />
                {c.found ? <Found c={c} /> : <span className="flex flex-col gap-0.5 pt-1">
                  <span className="flex items-baseline gap-1.5 font-mono text-2xs leading-[14px]">
                    <span className="text-ink-subtle">first guess</span>
                    <span className={VERDICT[c.verdict] ?? "text-ink-muted"}>{c.verdict.replace("_", " ")}</span>
                    {c.status === "duplicate" && <span className="text-ink-muted">· said again — kept once</span>}
                    {c.status === "sent" && <span className="text-ink-muted">· sent to {said.lookup_sink}</span>}
                  </span>
                  <span className="text-xs/4 text-ink-subtle">
                    {c.waiting ? <span className="text-ink-muted">looked up in the next round</span>
                      : c.would_check ? "would be looked up" : "not worth a lookup"}{c.why ? ` — ${c.why}` : ""}
                  </span>
                </span>}
              </Row>
            ))}
        {failed && (
          <p className="px-3 pt-3.5 pl-[72px] text-xs/[18px] text-ink">Not saved: {failed.message}</p>
        )}
        {tab === "todo" && (said.held ?? 0) > 0 && (
          <p className="px-3 pt-3.5 pl-[72px] text-xs/[18px] text-ink-subtle">
            Also heard, not listed: {said.held} things you were dictating to an agent as you said them — saying
            them was already doing them.
          </p>
        )}
      </div>

      <p className="rounded-lg bg-sunken px-3.5 py-3 text-xs/[18px] text-ink-muted">
        {tab === "todo"
          ? said.sending
            ? "Sending is on: each of these goes out as a proposal you still have to tap before anything runs."
            : "Sending is off, so nothing here has left this Mac. Turn it on in Settings › Plugins and each one goes out as a proposal you still have to tap before anything runs."
          : lookupNote(said)}
      </p>
    </>
  );

  if (phone) {
    return (
      <Sheet label="Said aloud" onClose={onClose}>
        <div className="flex flex-col gap-3.5 px-5 pt-3.5 pb-[calc(24px+env(safe-area-inset-bottom))]">{body}</div>
      </Sheet>
    );
  }
  return (
    <aside ref={aside} aria-label="Said aloud drawer"
      onTransitionEnd={(e) => { if (leaving && e.target === e.currentTarget) onClose(); }}
      className={`fixed inset-y-0 right-0 z-30 flex w-[520px] flex-col gap-3.5 overflow-y-auto bg-surface px-5 pt-5 pb-6
        shadow-(--shadow-drawer) transition-[translate] starting:translate-x-full motion-reduce:transition-none
        ${leaving ? "translate-x-full duration-(--dur-slow-out) ease-out" : "translate-x-0 duration-(--dur-slow) ease-(--ease-spring)"}`}>
      {body}
    </aside>
  );
}
