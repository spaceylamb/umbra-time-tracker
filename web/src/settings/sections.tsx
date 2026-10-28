// One component per page of the Settings dialog.
import type { ReactNode } from "react";
import type { Settings } from "../api/types";
import { Button } from "../ui/Button";
import { STATIC } from "../api/client";
import { alsoSend, sendsTo } from "../lib/plugins";
import { ago } from "../lib/time";
import { Mono, Row, Segmented, Stepper, Switch, tilde } from "./controls";

export type Put = (key: string | Record<string, unknown>, value?: unknown) => void;
export type Act = (name: string) => void;

const val = <T,>(s: Settings, key: string): T => {
  const k = s.config.keys.find((x) => x.key === key)!;
  return (k.value ?? k.default) as T;
};

const cat = (k: string) => `var(--cat-${k}, var(--color-ink-subtle))`;

export function General({ s, put }: { s: Settings; put: Put }) {
  return (
    <>
      <Row first title="Language" desc="The interface, and the names of categories.">
        <Segmented label="Language" value={val<string>(s, "ui.lang")} onChange={(v) => put("ui.lang", v)}
          options={[{ value: "en", label: "English" }, { value: "zh", label: "中文" }]} />
      </Row>
      <Row title="Theme" desc={STATIC ? "Follows this device unless the Mac picks one." : "Follows this Mac unless you pick one."}>
        <Segmented label="Theme" value={val<string>(s, "ui.theme")} onChange={(v) => put("ui.theme", v)}
          options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} />
      </Row>
      <Row title="Time zone" desc={STATIC ? "Read from the Mac. A day runs from its midnight to the next — wherever you are reading this." : "Read from this Mac. A day runs from its midnight to the next."}>
        <Mono>{s.general.tz} · {s.general.tz_abbr}</Mono>
      </Row>
    </>
  );
}

export function Model({ s, act }: { s: Settings; act: Act }) {
  const m = s.model.status;
  return (
    <>
      <Row first title="Model" desc="Reads each day. Routed to a provider by name prefix; set it with UMBRA_INFER_MODEL.">
        <Mono>{m.name}</Mono>
      </Row>
      <Row title="Key file" desc="Loaded at the start of every command. Keys already in the environment win.">
        <Mono>{m.env_file ? tilde(m.env_file) : "—"}</Mono>
      </Row>
      <Row title="Personal rules" desc="What a signal means in your life — a hint the model weighs, never a hard label.">
        <Mono>{s.model.rules} rule{s.model.rules === 1 ? "" : "s"}</Mono>
        <Button onClick={() => act("open-config")}>Add in editor</Button>
      </Row>
      <div className="flex items-center pt-4 pb-2">
        <span className="text-[13px]/4 text-ink-muted">Providers</span>
        <span className="grow" />
        <span className="font-mono text-2xs text-ink-subtle">read-only here</span>
      </div>
      {s.model.providers.map((p) => (
        <div key={p.name} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border py-2.5 text-[13px]/4">
          <span className="w-24 font-medium">{p.name}</span>
          <span className="w-28 font-mono text-xs text-ink-subtle">{p.builtin ? "built in" : p.kind}</span>
          <span className="grow font-mono text-xs text-ink-muted">{p.models.join(", ")}</span>
          <span className="flex items-center gap-1.5 text-xs text-ink-muted">
            {p.key_set === null ? "no key needed" : (
              <><i className={`size-[5px] rounded-full ${p.key_set ? "bg-live" : "bg-ink-subtle"}`} />
                {p.key_set ? "key found" : `no key — add ${m.key_env ?? "it"} to the key file`}</>
            )}
          </span>
        </div>
      ))}
      <p className="flex items-center gap-2 pt-3 text-xs/4 text-ink-subtle">
        <i className="size-3 rounded-[3px] border border-dashed border-border-strong" />
        Ollama, vLLM, llama.cpp or any OpenAI-compatible endpoint — add a [providers.*] table.
      </p>
    </>
  );
}

export function Schedule({ s, put, act }: { s: Settings; put: Put; act: Act }) {
  const every = val<number>(s, "schedule.interval_min");
  const job = s.schedule;
  return (
    <>
      <Row first title="Run every" desc={`Import, read, build the page. A run 1.5× late raises the alarm — ${Math.round(every * 1.5 / 60)}h at this setting.`}>
        <Segmented label="Run every" value={every} onChange={(v) => put("schedule.interval_min", v)}
          options={[180, 360, 720, 1440].map((m) => ({ value: m, label: `${m / 60}h` }))} />
      </Row>
      <Row title="Re-read recent days" desc="Days whose records did not change cost no call.">
        <Stepper label="Days" value={val<number>(s, "sync.infer_days")} min={1} max={14}
          unit={val<number>(s, "sync.infer_days") === 1 ? "day" : "days"} onChange={(v) => put("sync.infer_days", v)} />
      </Row>
      <Row title="Background job" desc={job.platform === "darwin"
        ? "launchd. Reinstall after changing how often it runs, Node, or moving folders — PATH is frozen at install."
        : "Print the systemd unit with umbra schedule install and install it yourself."}>
        {job.installed != null && (
          <span className="flex items-center gap-1.5 whitespace-nowrap text-xs text-ink-muted">
            <i className={`size-[5px] rounded-full ${job.installed ? "bg-live" : "bg-ink-subtle"}`} />
            {job.installed ? "installed" : "not installed"}
          </span>
        )}
        <Button onClick={() => act("schedule-install")}>{job.installed ? "Reinstall" : "Install"}</Button>
      </Row>
      <Row title="Last run" desc="Today is read once it is over, or when you ask for it.">
        <Mono>{job.last_run ? ago(job.last_run) : "never"}</Mono>
        <Button variant="primary" onClick={() => act("run-now")}>Run now</Button>
      </Row>
    </>
  );
}

// A setting that belongs to the plugin above it.
function Child({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-3 border-t border-border py-2.5 pl-6 text-[13px]/4">
      <span className="text-ink-subtle">└</span>
      <span className="flex grow flex-col gap-[3px] text-ink-muted">
        {title}
        {desc && <span className="text-xs text-ink-subtle">{desc}</span>}
      </span>
      {children}
    </div>
  );
}

type PluginRow = Settings["plugins"][number];

// Fact-check: which model looks claims up — each provider's own, or a sink
// that reports back — and how many a day at most.
function Lookup({ p, put }: { p: PluginRow; put: Put }) {
  const lk = p.lookup!;
  const cur = lk.choices.find((c) => c.value === lk.using);
  const pick = (v: string) => {
    const c = lk.choices.find((x) => x.value === v)!;
    put(v === "sink" ? { "plugins.claims.lookup": "sink" }
      : { "plugins.claims.lookup": "model", "plugins.claims.lookup_model": c.model });
  };
  return (
    <>
      <Child title="Using" desc={
        <span className="flex flex-col gap-[3px]">
          <span className={cur?.model || lk.using === "custom" ? "font-mono" : ""}>
            {cur?.model ? `${cur.model} · ${cur.note}` : lk.using === "custom" ? lk.model : cur?.note}
          </span>
          {cur?.key_set === false && (
            <span className="text-ink-muted"><i className="mr-1.5 inline-block size-[5px] rounded-full bg-ink-subtle align-middle" />
              no key — add {cur.key_env} to the key file</span>
          )}
        </span>}>
        <Segmented label="Look claims up with" value={lk.using} onChange={pick}
          options={lk.choices.map((c) => ({ value: c.value, label: c.label }))} />
      </Child>
      <Child title="Up to" desc="About 7 a day need one; this caps a busy day.">
        <Stepper label="Lookups a day" value={lk.cap} min={1} max={200} unit="a day"
          onChange={(v) => put("plugins.claims.max_lookups", v)} />
      </Child>
    </>
  );
}

export function Plugins({ s, put }: { s: Settings; put: Put }) {
  return (
    <>
      {s.plugins.map((p, i) => (
        <div key={p.name}>
          {/* A plugin that cannot run says what it is missing, and cannot be
              switched on until it has it — never on and doing half. */}
          <Row first={i === 0} title={p.title} desc={p.blocked || sendsTo(p) ? (
            <span className="flex flex-col gap-1">
              {p.what}
              <span className="text-ink-muted"><i className="mr-1.5 inline-block size-[5px] rounded-full bg-ink-subtle align-middle" />{p.blocked ?? sendsTo(p)}</span>
            </span>
          ) : p.what}>
            <Switch label={p.title} on={p.enabled} disabled={!!p.blocked && !p.enabled}
              onChange={(v) => put(`plugins.${p.name}.enabled`, v)} />
          </Row>
          {p.lookup && p.enabled && !p.blocked && <Lookup p={p} put={put} />}
          {(() => {
            // Only for a plugin useful here alone; one that only sends has
            // no second switch — being on is sending (sendsTo above).
            const also = p.enabled && !p.blocked ? alsoSend(p) : null;
            return also && (
              <Child title={also.title} desc={also.desc}>
                <Switch label={`${p.title} sending`} on={p.sending} disabled={!also.ready}
                  onChange={(v) => put(`plugins.${p.name}.send`, v)} />
              </Child>
            );
          })()}
        </div>
      ))}
      <div className="flex items-center border-t border-border pt-4 pb-1">
        <span className="text-[13px]/4 text-ink-muted">Yours</span>
        <span className="grow" />
        <span className="font-mono text-2xs text-ink-subtle">~/.config/umbra/plugins/</span>
      </div>
      <p className="text-xs/4 text-ink-subtle">
        {s.plugins.some((p) => !p.bundled)
          ? s.plugins.filter((p) => !p.bundled).map((p) => p.name).join(", ")
          : "None yet. A file here with the same name as a bundled plugin replaces it."}
      </p>
    </>
  );
}

export function Categories({ s, lang }: { s: Settings; lang: string }) {
  const nm = (c: { en: string; zh: string }) => (lang === "zh" ? c.zh : c.en);
  const byKey = Object.fromEntries(s.categories.families.flatMap((f) => f.cats).map((c) => [c.key, c]));
  const fams = s.categories.families;
  return (
    <>
      <div className="flex items-center pb-2 max-sm:pt-3.5">
        <span className="text-[13px]/4 text-ink-muted">Built in</span>
        <span className="grow" />
        <span className="font-mono text-2xs text-ink-subtle">
          {fams.filter((f) => f.family).length} families · {fams.reduce((n, f) => n + f.cats.length, 0)} categories
        </span>
      </div>
      <div className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
        {fams.map((f) => (
          <div key={f.family || "none"} className="flex items-center gap-3 border-b border-border py-2">
            <span className="w-16 shrink-0 font-mono text-2xs text-ink-subtle">{f.family || "no family"}</span>
            {f.cats.map((c) => (
              <span key={c.key} className="flex items-center gap-1.5 text-[13px]/4">
                <i className="size-2 rounded-full" style={{ background: cat(c.key) }} />{nm(c)}
              </span>
            ))}
            {f.family && f.cats.length < 2 && (
              <span className="flex items-center gap-1.5 text-xs text-ink-subtle">
                <i className="size-2 rounded-full border border-dashed border-border-strong" />free slot
              </span>
            )}
          </div>
        ))}
      </div>
      <p className="pt-4 text-[13px]/4 text-ink-muted">Yours</p>
      <p className="pt-1 text-xs/[18px] text-ink-subtle">
        {s.categories.own.length ? s.categories.own.join(", ") :
          "None yet. One you add in config.toml gets its colour by the same rule — name a family with a free slot and it sits with its kin."}
      </p>
      <div className="flex items-center pt-5 pb-1">
        <span className="text-[13px]/4 text-ink-muted">Headline numbers</span>
        <span className="grow" />
        <span className="font-mono text-2xs text-ink-subtle">summed, never asked of a model</span>
      </div>
      {s.categories.groups.length ? s.categories.groups.map((g) => (
        <div key={g.key} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-border py-2.5">
          <span className="w-28 text-[13px]/4 font-medium max-sm:w-full">{g.en}</span>
          <span className="text-ink-subtle max-sm:hidden">=</span>
          {g.cats.map((k) => (
            <span key={k} className="flex items-center gap-1.5 text-[13px]/4">
              <i className="size-2 rounded-full" style={{ background: cat(k) }} />{byKey[k] ? nm(byKey[k]!) : k}
            </span>
          ))}
        </div>
      )) : <p className="text-xs/4 text-ink-subtle">None — the headline row is hidden until a [groups.*] table defines one.</p>}
    </>
  );
}

export function Publishing({ s, put, act }: { s: Settings; put: Put; act: Act }) {
  const raw = val<number>(s, "viz.raw_days");
  return (
    <>
      <Row first title="Page" desc="Rebuilt at the end of every run.">
        <Mono>…/{s.publishing.page.split("/").slice(-2).join("/")}</Mono>
        <Button onClick={() => act("open-page")} disabled={!s.publishing.page_exists}>Open</Button>
      </Row>
      <Row title="Raw records in the page" desc="The last N days carry every record. 0 carries all of them — the file gets large.">
        <Stepper label="Raw days" value={raw} min={0} max={3650} unit="days" onChange={(v) => put("viz.raw_days", v)} />
      </Row>
      <Row title="Deploy" desc="A command that pushes the page to a host you own — set in config.toml.">
        <Mono>{s.publishing.deploy ? "set" : "not set"}</Mono>
      </Row>
      <div className="mt-4 flex gap-3 rounded-lg bg-sunken px-4 py-3">
        <span className="text-alert" aria-hidden>⚠</span>
        <div className="flex flex-col gap-1">
          <span className="text-[13px]/4 font-medium">The page carries your raw records</span>
          <span className="text-xs/[18px] text-ink-muted">
            Browsing, clipboard and agent transcripts, verbatim. Put auth at the edge first (Cloudflare Access, Netlify
            Identity) so the file never downloads without a login.
          </span>
        </div>
      </div>
    </>
  );
}

const mb = (n: number | null) => (n == null ? "" : ` · ${n > 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.round(n / 1e3)} KB`}`);

export function About({ s, act }: { s: Settings; act: Act }) {
  return (
    <>
      <div className="flex items-center gap-4 pb-4">
        <svg width="40" height="40" viewBox="0 0 24 24" aria-hidden>
          <circle cx="12" cy="12" r="10" fill="var(--color-ink)" />
          <path d="M12 2 A7 7 0 1 0 22 12 A10 10 0 0 0 12 2 Z" fill="var(--color-surface)" />
          <circle cx="12" cy="12" r="10" fill="none" stroke="var(--color-ink)" strokeWidth="2" />
        </svg>
        <div className="flex grow flex-col">
          <span className="text-lg/6 font-semibold">Umbra</span>
          <span className="font-mono text-xs text-ink-subtle">{s.about.version} · {s.about.license}</span>
        </div>
      </div>
      <div className="flex items-center border-t border-border pt-4 pb-1">
        <span className="text-[13px]/4 text-ink-muted">Where things live</span>
        <span className="grow" />
        <span className="font-mono text-2xs text-ink-subtle">UMBRA_*_DIR overrides each</span>
      </div>
      {s.about.paths.map((p) => (
        <div key={p.key} className="flex items-center gap-4 border-b border-border py-3 text-[13px]/4">
          <span className="w-24 font-medium">{p.label}</span>
          <span className="grow truncate font-mono text-xs text-ink-muted">{tilde(p.path)}{mb(p.size)}</span>
          <Button variant="ghost" onClick={() => act(`reveal-${p.key}`)}>Reveal</Button>
        </div>
      ))}
    </>
  );
}
