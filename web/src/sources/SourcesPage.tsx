// Every source, grouped by what it needs from you.
//
// Nothing is hidden: a source that cannot run is listed and says why; one
// whose permission cannot be probed (Full Disk Access) says so rather than
// showing a tick it cannot back up. The only part that asks for action is
// "Needs you"; the rest is a list.
import { dayLen } from "../lib/clock";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pending } from "../ui/Pending";
import { READ_ONLY_NOTE, STATIC, dayQuery, detailQuery, importQuery, metaQuery, setupQuery, startImport } from "../api/client";
import type { Freshness, Meta, Setup } from "../api/types";
import { useTheme } from "../theme";
import { isStale, plainError } from "../lib/fresh";
import { ago, hm, shortDate } from "../lib/time";
import { Topbar } from "../day/Topbar";
import { Button } from "../ui/Button";
import { useMobile } from "../lib/mobile";
import { MobileSources } from "../mobile/MobileSources";

export type Src = Setup["sources"][number];

function Head({ n, title, right }: { n: string; title: string; right?: string }) {
  return (
    <div className="flex w-full items-center gap-[14px] pb-4">
      <span className="font-mono text-xs/4 text-ink-subtle">{n}</span>
      <h2 className="shrink-0 text-[15px] font-semibold leading-[18px] tracking-[-0.01em]">{title}</h2>
      <div className="h-px grow bg-border" />
      {right && <span className="shrink-0 text-sm/tight text-ink-subtle">{right}</span>}
    </div>
  );
}

const NUM = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen"];
const word = (n: number) => NUM[n] ?? String(n);

export function clock(ms: number | null | undefined, day: string) {
  if (!ms) return "—";
  const d = new Date(ms);
  const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return iso === day ? hm(d.getHours() * 60 + d.getMinutes()) : `${hm(d.getHours() * 60 + d.getMinutes())} ${shortDate(iso)}`;
}

export function NeedsCard({ f, s, retry, phone = false }: {
  f: Freshness; s?: Src; retry: (name: string) => void; phone?: boolean;
}) {
  const now = Date.now();
  const lines: [string, string][] = [];
  let why = "";
  if (f.live) {
    const silent = f.last_event ? Math.round((now - f.last_event) / 60000) : null;
    lines.push(["⚠", silent != null
      ? `no data for ${silent} minutes, and this source has no import step`
      : "it has never written anything"]);
    why = "A live source cannot be late — it either writes or it has stopped. Restart the collector with umbra schedule install --collector.";
  } else if (f.bad) {
    const err = f.error ? ` — ${plainError(f.error)}` : "";
    lines.push(["✕", f.last_ok == null
      ? `has never imported${err}`
      : `${f.fails} consecutive failures${err}`]);
    why = f.last_ok
      ? `Last succeeded ${shortDate(new Date(f.last_ok).toISOString().slice(0, 10))}. What it recorded before then is in the store; nothing since has arrived.`
      : "Nothing from it is in the store yet.";
  } else {
    lines.push(["⚠", `last import ${f.check_ts ? ago(f.check_ts) : "never"} — later than a missed run explains`]);
    why = "The scheduled job may not be running. Settings › Schedule shows whether it is installed.";
  }
  if (phone) {
    // On a phone, stacked — glyph and name, origin, what went wrong, why, and
    // the one action full width under it (not on the exported copy).
    return (
      <div className="flex flex-col gap-1.5 rounded-[10px] bg-surface p-4">
        <div className="flex items-baseline gap-2">
          <span className="text-alert" aria-hidden>{lines[0]![0]}</span>
          <span className="text-[15px]/5 font-semibold">{s?.label ?? f.source}</span>
        </div>
        <span className="font-mono text-xs/4 text-ink-subtle">{s?.name ?? f.source}{f.live ? " · live" : ""}</span>
        {lines.map(([, t], i) => <span key={i} className="font-mono text-xs/[18px] text-ink">{t}</span>)}
        <span className="text-[13px]/5 text-ink-muted">{why}</span>
        {!f.live && s && !STATIC && (
          <Button size="default" className="mt-2.5 w-full" onClick={() => retry(s.name)}>Try again</Button>
        )}
      </div>
    );
  }
  return (
    <div className="flex gap-4 rounded-[10px] bg-surface px-5 py-4">
      <span className="pt-px text-alert" aria-hidden>{lines[0]![0]}</span>
      <div className="flex grow flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[14px]/5 font-medium">{s?.label ?? f.source}</span>
          <span className="font-mono text-xs/4 text-ink-subtle">{s?.name ?? f.source}{f.live ? " · live" : ""}</span>
        </div>
        {lines.map(([g, t], i) => (
          <span key={i} className="flex gap-1.5 font-mono text-xs/[18px] text-ink">
            <span className="text-alert" aria-hidden>{g}</span>{t}
          </span>
        ))}
        <span className="max-w-[760px] text-[13px]/5 text-ink-muted">{why}</span>
      </div>
      {!f.live && s && !STATIC && <Button onClick={() => retry(s.name)}>Try again</Button>}
    </div>
  );
}

export function SourcesPage() {
  const qc = useQueryClient();
  const phone = useMobile();
  const meta = useQuery(metaQuery);
  const setup = useQuery(setupQuery);
  const imp = useQuery(importQuery);
  useTheme(meta.data);
  const day = meta.data?.order.at(-1);
  const dayData = useQuery({ ...dayQuery(day ?? ""), enabled: !!day });
  const detail = useQuery({ ...detailQuery(day ?? ""), enabled: !!day });
  const go = useMutation({
    mutationFn: (only?: string) => startImport(only),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["import"] }),
  });

  if (!meta.data || !setup.data) return <Pending error={meta.error ?? setup.error} />;
  const m: Meta = meta.data;
  const srcs = setup.data.sources;
  const bySource = new Map(srcs.map((s) => [s.source, s]));
  const fresh = new Map(m.fresh.map((f) => [f.source, f]));
  const needs = m.fresh.filter((f) => isStale(f));
  const needSet = new Set(needs.map((f) => f.source));
  const recording = m.fresh.filter((f) => !needSet.has(f.source) && f.count > 0);
  const unknown = srcs.filter((s) => s.state === "unknown" && !recording.some((f) => f.source === s.source));
  const unset = srcs.filter((s) => s.state === "missing" && !fresh.get(s.source)?.count && !needSet.has(s.source));
  const absent = srcs.filter((s) => s.state === "unsupported" || s.state === "broken");
  const runs = m.fresh.filter((f) => !f.live && f.last_run).map((f) => f.last_run!);
  const last = runs.length ? Math.max(...runs) : null;
  const lanes = detail.data?.lanes ?? {};
  const laneKeys = (src: string) => Object.keys(m.labels.lane_source).filter((k) => m.labels.lane_source[k] === src);
  const origin = (src: string) => {
    const k = laneKeys(src)[0];
    return (k && m.labels.origin[k]?.[0]) || bySource.get(src)?.name || src;
  };
  const running = !!imp.data?.run?.running || go.isPending;
  const isToday = day === m.today;

  if (phone) {
    return (
      <MobileSources meta={m} srcs={srcs} needs={needs} recording={recording} unknown={unknown} unset={unset}
        absent={absent} last={last} running={running} onImport={(only) => go.mutate(only)}
        counts={dayData.data?.counts ?? {}} len={dayLen(dayData.data)} runsOf={(src) => laneKeys(src).flatMap((k) => (lanes[k] as [number, number, string][] | undefined) ?? [])}
        origin={origin} label={(src) => bySource.get(src)?.label ?? m.labels.source[src] ?? src} bySource={bySource} />
    );
  }

  return (
    <main className="mx-auto w-full max-w-[1440px] px-10 pb-16">
      <Topbar meta={m} view="sources" />
      <div className="page-in">
      <div className="flex items-start gap-10 pb-12">
        <div className="flex max-w-[680px] flex-col gap-2">
          <h1 className="text-[34px] font-semibold leading-10 tracking-tight">Sources</h1>
          <p className="text-[15px]/[22px] text-ink-muted">
            {`${word(srcs.length)[0]!.toUpperCase()}${word(srcs.length).slice(1)}`} of them, and the day is only ever as
            good as these. A source that cannot run is listed here, never hidden — and one that cannot be checked says so
            rather than showing a tick.
          </p>
        </div>
        <div className="grow" />
        <div className="flex shrink-0 flex-col items-end gap-2">
          {STATIC ? <span className="max-w-[260px] text-right text-xs/4 text-ink-subtle">{READ_ONLY_NOTE}</span> : (
            <Button variant="primary" size="default" disabled={running} onClick={() => go.mutate(undefined)}>
              {running ? "Importing" : "Import now"}
            </Button>
          )}
          <span className="font-mono text-xs/4 text-ink-subtle">
            {last ? `last pass ${clock(last, m.today)} · ${ago(last)}` : "never imported"}
          </span>
        </div>
      </div>

      {needs.length > 0 && (
        <section className="flex flex-col gap-2.5 pb-12">
          <Head n="01" title="Needs you" right="the only part of this page that is not a list" />
          {needs.map((f) => <NeedsCard key={f.source} f={f} s={bySource.get(f.source)} retry={(n) => go.mutate(n)} />)}
        </section>
      )}

      <section className="flex flex-col pb-12">
        <Head n="02" title="Recording" right={`${word(recording.length)} of ${word(srcs.length)}, and what each one put into ${isToday ? "today" : shortDate(day ?? m.today)}`} />
        <div className="flex h-8 items-center gap-6 border-b border-border font-mono text-2xs text-ink-subtle">
          <span className="w-[180px] pl-[22px]">source</span>
          <span className="w-[220px]">where it comes from</span>
          <span className="grow">{isToday ? "today" : day ? shortDate(day) : ""}</span>
          <span className="w-16 text-right">events</span>
          <span className="w-24 text-right">last event</span>
          <span className="w-24 text-right">last import</span>
        </div>
        {recording.map((f) => {
          const runsOf = laneKeys(f.source).flatMap((k) => (lanes[k] as [number, number, string][] | undefined) ?? []);
          return (
            <div key={f.source} className="flex h-11 items-center gap-6 border-b border-border">
              <span className="flex w-[180px] items-center gap-3">
                <i className="size-[5px] rounded-full bg-live" />
                <span className="text-sm/5 font-medium">{bySource.get(f.source)?.label ?? m.labels.source[f.source] ?? f.source}</span>
              </span>
              <span className="w-[220px] truncate font-mono text-xs text-ink-subtle">{origin(f.source)}{f.live ? " · live" : ""}</span>
              <span className="relative h-1.5 grow">
                <span className="absolute inset-y-0 left-0 w-[300px] rounded-[2px] bg-sunken" />
                {runsOf.map(([a, b], i) => (
                  <span key={i} className="absolute inset-y-0 rounded-[1px] bg-neutral-500"
                    style={{ left: (a / dayLen(dayData.data)) * 300, width: Math.max(((b - a) / dayLen(dayData.data)) * 300, 2) }} />
                ))}
              </span>
              <span className="w-16 text-right font-mono text-xs text-ink-muted">{dayData.data?.counts[f.source] ?? 0}</span>
              <span className="w-24 text-right font-mono text-xs text-ink-muted">{clock(f.last_event, day ?? m.today)}</span>
              <span className="w-24 text-right font-mono text-xs text-ink-muted">{f.live ? "(live)" : f.last_run ? ago(f.last_run) : "—"}</span>
            </div>
          );
        })}
      </section>

      {unknown.length > 0 && (
        <section className="flex flex-col pb-12">
          <Head n="03" title="Cannot be checked" right={`${word(unknown.length)} source${unknown.length === 1 ? "" : "s"}, one honest answer`} />
          <div className="flex gap-12 rounded-[10px] bg-surface px-6 py-5">
            <div className="flex w-[330px] shrink-0 flex-col gap-2">
              {unknown.map((s) => (
                <span key={s.name} className="flex items-baseline gap-3 text-sm/5">
                  <span className="text-ink-subtle">—</span>
                  <span className="font-medium">{s.label}</span>
                  <span className="font-mono text-xs text-ink-subtle">{s.name}</span>
                </span>
              ))}
              <span className="pt-2 font-mono text-2xs leading-4 text-ink-subtle">
                needs Full Disk Access<br />System Settings → Privacy &amp; Security
              </span>
            </div>
            <p className="max-w-[800px] text-[13px]/[21px] text-ink-muted">
              Full Disk Access has no reliable probe. The only way to know is to read a protected file, which is exactly
              what the import does — so this page reports what the last import managed, and shows an em dash where a tick
              would be a guess. Grant it to the Python the background job runs, not the one in your terminal: they are
              different files, and umbra doctor names the right one.
            </p>
          </div>
        </section>
      )}

      {unset.length > 0 && (
        <section className="flex flex-col pb-12">
          <Head n="04" title="Not set up" right="absent, not a gap in your day" />
          {unset.map((s) => (
            <div key={s.name} className="flex h-11 items-center gap-6 border-b border-border">
              <span className="w-[180px] pl-[22px] text-sm/5 font-medium">{s.label}</span>
              <span className="grow text-[13px] text-ink-muted">{s.what}</span>
              <span className="font-mono text-xs text-ink-subtle">{s.needs[0]?.replace(/ \(.*\)$/, "")}</span>
            </div>
          ))}
        </section>
      )}

      <section className="flex flex-col">
        <Head n={unset.length ? "05" : "04"} title="Not on this machine" right={absent.length ? undefined : "nothing, today"} />
        {absent.length ? absent.map((s) => (
          <div key={s.name} className="flex h-11 items-center gap-6 border-b border-border text-ink-subtle">
            <span className="w-[180px] pl-[22px] text-sm/5">{s.label}</span>
            <span className="grow text-[13px]">{s.needs[0]}</span>
          </div>
        )) : (
          <p className="max-w-[860px] text-sm/[21px] text-ink-muted">
            Every source that exists can run here. On another system, the ones whose data only exists on macOS would sit
            in this section — listed and greyed, never dropped from the page, because a source you cannot see is one you
            will not remember you are missing.
          </p>
        )}
      </section>
      </div>
    </main>
  );
}
