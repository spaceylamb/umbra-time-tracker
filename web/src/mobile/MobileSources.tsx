// Sources on a phone: the same four groups as the desktop,
// each a column instead of a table. Needs-you cards stack their action under
// them; recording sources put their lane under the name, full width.
import type { Freshness, Meta } from "../api/types";
import { READ_ONLY_NOTE, STATIC } from "../api/client";
import { ago } from "../lib/time";
import { Button } from "../ui/Button";
import { NeedsCard, type Src } from "../sources/SourcesPage";
import { MarkRow, TabBar, TAB_PAD } from "./Shell";

function Head({ n, title, right }: { n: string; title: string; right?: string }) {
  return (
    <div className="flex items-baseline gap-2.5 px-4 pb-3">
      <span className="font-mono text-2xs leading-[14px] text-ink-subtle">{n}</span>
      <h2 className="text-[15px] leading-[18px] font-semibold tracking-[-0.01em]">{title}</h2>
      <span className="grow" />
      {right && <span className="font-mono text-2xs leading-[14px] text-ink-subtle">{right}</span>}
    </div>
  );
}

export function MobileSources({
  meta, srcs, needs, recording, unknown, unset, absent, last, running, onImport, counts, runsOf, origin, label, bySource, len = 1440,
}: {
  /** The shown day's length in minutes; the lanes are to its scale. */
  len?: number;
  meta: Meta; srcs: Src[]; needs: Freshness[]; recording: Freshness[]; unknown: Src[]; unset: Src[]; absent: Src[];
  last: number | null; running: boolean; onImport: (only?: string) => void;
  counts: Record<string, number | undefined>; runsOf: (src: string) => [number, number, string][];
  origin: (src: string) => string; label: (src: string) => string; bySource: Map<string, Src>;
}) {
  let n = 0;
  const num = () => `0${++n}`;
  return (
    <main className={`mx-auto w-full max-w-[640px] ${TAB_PAD}`}>
      <div className="px-4 pt-[10px]"><MarkRow /></div>
      <div className="page-in">
      <header className="flex flex-col gap-1.5 px-4 pt-3 pb-7">
        <div className="flex items-center">
          <h1 className="text-[26px] leading-8 font-semibold tracking-tight">Sources</h1>
          <span className="grow" />
          {!STATIC && (
            <Button variant="primary" size="default" disabled={running} onClick={() => onImport()}>
              {running ? "Importing" : "Import now"}
            </Button>
          )}
        </div>
        <p className="flex items-center gap-1.5 font-mono text-2xs leading-[14px] text-ink-subtle">
          {needs.length > 0 && <>
            <i className="size-[5px] rounded-full bg-alert" />
            <span className="text-ink-muted">{needs.length} source{needs.length === 1 ? "" : "s"} need{needs.length === 1 ? "s" : ""} you</span>
            <span>·</span>
          </>}
          {last ? `last pass ${ago(last)}` : "never imported"}
        </p>
        {STATIC && <p className="pt-1 text-xs/4 text-ink-subtle">{READ_ONLY_NOTE}</p>}
      </header>

      {needs.length > 0 && (
        <section className="flex flex-col pb-7">
          <Head n={num()} title="Needs you" right={`${needs.length} of ${srcs.length}`} />
          <div className="flex flex-col gap-2.5 px-4">
            {needs.map((f) => <NeedsCard phone key={f.source} f={f} s={bySource.get(f.source)} retry={(s) => onImport(s)} />)}
          </div>
        </section>
      )}

      <section className="flex flex-col pb-7">
        <Head n={num()} title="Recording" right={`${recording.length} of ${srcs.length}`} />
        <div className="flex flex-col gap-3.5 px-4">
          {recording.map((f) => (
            <div key={f.source} className="flex flex-col gap-1.5">
              <div className="flex items-baseline gap-2">
                <i className="size-[5px] shrink-0 -translate-y-[3px] rounded-full bg-live" />
                <span className="shrink-0 text-sm/5 font-medium">{label(f.source)}</span>
                <span className="min-w-0 truncate font-mono text-2xs text-ink-subtle">{origin(f.source)}{f.live ? " · live" : ""}</span>
                <span className="grow" />
                <span className="shrink-0 font-mono text-2xs text-ink-muted">
                  {counts[f.source] ?? 0} · {f.live ? "(live)" : f.last_run ? ago(f.last_run) : "—"}
                </span>
              </div>
              <span className="relative ml-[13px] h-1.5 rounded-[2px] bg-sunken">
                {runsOf(f.source).map(([a, b], i) => (
                  <span key={i} className="absolute inset-y-0 rounded-[1px] bg-neutral-500"
                    style={{ left: `${(a / len) * 100}%`, width: `max(${((b - a) / len) * 100}%, 2px)` }} />
                ))}
              </span>
            </div>
          ))}
        </div>
      </section>

      {unknown.length > 0 && (
        <section className="flex flex-col pb-7">
          <Head n={num()} title="Cannot be checked" right={`${unknown.length} source${unknown.length === 1 ? "" : "s"}`} />
          <div className="mx-4 flex flex-col gap-2 rounded-[10px] bg-surface p-4">
            {unknown.map((s) => (
              <span key={s.name} className="flex items-baseline gap-3 text-sm/5">
                <span className="text-ink-subtle">—</span>
                <span className="font-medium">{s.label}</span>
                <span className="font-mono text-xs text-ink-subtle">{s.name}</span>
              </span>
            ))}
            <span className="pt-1 font-mono text-2xs leading-4 text-ink-subtle">
              needs Full Disk Access<br />System Settings → Privacy &amp; Security
            </span>
            <p className="text-[13px]/5 text-ink-muted">
              Full Disk Access has no reliable probe. The only way to know is to read a protected file, which is exactly
              what the import does — so this page reports what the last import managed, and shows an em dash where a
              tick would be a guess.
            </p>
          </div>
        </section>
      )}

      {unset.length > 0 && (
        <section className="flex flex-col pb-7">
          <Head n={num()} title="Not set up" right="absent, not a gap" />
          <div className="flex flex-col px-4">
            {unset.map((s) => (
              <div key={s.name} className="flex flex-col gap-0.5 border-b border-border py-2.5">
                <span className="text-sm/5 font-medium">{s.label}</span>
                <span className="text-[13px]/5 text-ink-muted">{s.what}</span>
                {s.needs[0] && <span className="font-mono text-2xs leading-4 text-ink-subtle">{s.needs[0].replace(/ \(.*\)$/, "")}</span>}
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="flex flex-col">
        <Head n={num()} title="Not on this machine" right={absent.length ? undefined : "nothing, today"} />
        {absent.length ? (
          <div className="flex flex-col px-4">
            {absent.map((s) => (
              <div key={s.name} className="flex flex-col gap-0.5 border-b border-border py-2.5 text-ink-subtle">
                <span className="text-sm/5">{s.label}</span>
                <span className="text-[13px]/5">{s.needs[0]}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="px-4 text-[13px]/5 text-ink-muted">
            Every source that exists can run here. On another system, the ones whose data only exists on macOS would sit
            in this section — listed and greyed, never dropped from the page, because a source you cannot see is one you
            will not remember you are missing.
          </p>
        )}
      </section>

      </div>
      <TabBar meta={meta} view="sources" />
    </main>
  );
}
