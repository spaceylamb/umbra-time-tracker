// The Settings dialog.
//
// Modal, centred, over a scrim — unlike the Said-aloud drawer, nothing on the
// page needs to stay in view while settings change. Every control writes one
// line of config.toml (umbra/configedit.py) and shows what the file now
// says; structures stay "Open in editor".
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { READ_ONLY_NOTE, STATIC, metaQuery, runAction, setConfig, settingsQuery } from "../api/client";
import type { Settings } from "../api/types";
import { Button } from "../ui/Button";
import { About, Categories, General, Model, Plugins, Publishing, Schedule } from "./sections";
import type { Put } from "./sections";
import { tilde } from "./controls";
import { useMobile } from "../lib/mobile";
import { Sheet } from "../ui/Sheet";
import { useFluidHover } from "../ui/fluid";

export type Section = "general" | "model" | "schedule" | "plugins" | "categories" | "publishing" | "about";

const Ctx = createContext<(s?: Section) => void>(() => {});
/** Open Settings, optionally on one page: `useSettings()("model")`. */
export const useSettings = () => useContext(Ctx);

const PAGES: Record<Section, { title: string; sub: string; icon: string }> = {
  general: { title: "General", sub: "Language, theme and how a day is cut.", icon: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4" },
  model: { title: "Model", sub: "Which model reads your days. Records go to it one day at a time.", icon: "M9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2M6 6h12v12H6z" },
  schedule: { title: "Schedule", sub: "Umbra runs itself in the background on this Mac.", icon: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" },
  plugins: { title: "Plugins", sub: "Each runs on this Mac. Anything that goes out of Umbra says where it goes.", icon: "M4 4h7v7H4zM13 13h7v7h-7zM13 4h3a4 4 0 0 1 4 4v3M4 13v3a4 4 0 0 0 4 4h3" },
  categories: { title: "Categories", sub: "What the model may call a stretch of time. Colours are assigned by rule, never picked.", icon: "M7 5a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM17 5a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM7 15a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM17 15a2 2 0 1 1 0 4 2 2 0 0 1 0-4z" },
  publishing: { title: "Publishing", sub: "The page Umbra builds, and where it goes after.", icon: "M12 16V4M7 9l5-5 5 5M4 20h16" },
  about: { title: "About", sub: "What this build is, and where everything it keeps lives.", icon: "M12 11v6M12 7.5v.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" },
};

function NavItem({ id, on, go }: { id: Section; on: boolean; go: (s: Section) => void }) {
  return (
    <button type="button" onClick={() => go(id)} aria-current={on ? "page" : undefined} data-fluid
      className={`relative flex h-8 w-full items-center gap-2.5 rounded-[6px] px-2.5 text-[13px] font-medium transition-colors duration-[80ms]
        ${on ? "bg-surface text-ink shadow-[0_1px_2px_#0000000f]" : "text-ink-muted hover:text-ink"}`}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={PAGES[id].icon} /></svg>
      {PAGES[id].title}
    </button>
  );
}

/** The two kinds of write the dialog makes (a config key, a named action),
 * and the one-line note that says how the last one went. */
function useWrites(section: Section | null) {
  const qc = useQueryClient();
  const s = useQuery(settingsQuery);
  const meta = useQuery(metaQuery);
  const [note, setNote] = useState<string | null>(null);
  const put = useMutation({
    mutationFn: (changes: Record<string, unknown>) => setConfig(changes),
    onMutate: (changes) => {
      // Optimistic: the control moves now; a refusal puts it back and says why.
      const prev = qc.getQueryData<Settings>(settingsQuery.queryKey);
      if (prev) qc.setQueryData(settingsQuery.queryKey, {
        ...prev, config: { ...prev.config, keys: prev.config.keys.map((k) => (k.key in changes ? { ...k, value: changes[k.key] } : k)) } as Settings["config"],
      });
      return { prev };
    },
    onError: (e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(settingsQuery.queryKey, ctx.prev); setNote(e.message); },
    onSuccess: (_d, changes) => {
      const needs = s.data?.config.keys.find((k) => k.key in changes)?.needs;
      setNote(needs ? `Saved. Takes effect after: ${needs}` : null);
      void qc.invalidateQueries({ queryKey: settingsQuery.queryKey });
      void qc.invalidateQueries({ queryKey: metaQuery.queryKey });
      if (Object.keys(changes).some((k) => k.startsWith("plugins."))) void qc.invalidateQueries({ queryKey: ["day"] });
    },
  });
  const act = useMutation({
    mutationFn: runAction,
    onSuccess: (r) => setNote(r.done === "run-now" ? "A run has started in the background." :
      r.done === "schedule-install" ? "Reinstalling the background job." : null),
    onError: (e) => setNote(e.message),
  });

  useEffect(() => setNote(null), [section]);
  // One key, or several that only make sense together (a choice that is two
  // keys in config.toml goes in one write, so the page never sees half of it).
  const doPut: Put = (key, value) => put.mutate(typeof key === "string" ? { [key]: value } : key);
  const doAct = (name: string) => act.mutate(name);
  const page = (id: Section) => !s.data ? <p className="text-sm text-ink-subtle">…</p> : {
    general: <General s={s.data} put={doPut} />,
    model: <Model s={s.data} act={doAct} />,
    schedule: <Schedule s={s.data} put={doPut} act={doAct} />,
    plugins: <Plugins s={s.data} put={doPut} />,
    categories: <Categories s={s.data} lang={meta.data?.lang ?? "en"} />,
    publishing: <Publishing s={s.data} put={doPut} act={doAct} />,
    about: <About s={s.data} act={doAct} />,
  }[id];
  return { s, note, doAct, page };
}

function Dialog({ section, go, close }: { section: Section; go: (s: Section) => void; close: () => void }) {
  const { s, note, doAct, page } = useWrites(section);
  const fluid = useFluidHover<HTMLElement>();
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [close]);

  const p = PAGES[section];

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      <div className="absolute inset-0 bg-scrim transition-opacity duration-(--dur-slow) ease-(--ease-spring) starting:opacity-0" onClick={close} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label="Settings"
        className="relative flex h-[640px] w-[880px] overflow-clip rounded-xl bg-surface shadow-(--shadow-overlay)
          transition-[opacity,scale] duration-(--dur-slow) ease-(--ease-spring) starting:scale-[0.98] starting:opacity-0">
        <nav ref={fluid.ref} {...fluid.handlers} className="relative flex w-52 shrink-0 flex-col gap-0.5 bg-canvas px-2.5 pt-5 pb-3">
          {fluid.highlight()}
          <h2 className="px-2.5 pb-3 text-[15px] font-semibold">Settings</h2>
          <NavItem id="general" on={section === "general"} go={go} />
          <span className="px-2.5 pt-4 pb-1.5 text-xs text-ink-subtle">Pipeline</span>
          {(["model", "schedule", "plugins"] as const).map((id) => <NavItem key={id} id={id} on={section === id} go={go} />)}
          <span className="px-2.5 pt-4 pb-1.5 text-xs text-ink-subtle">Output</span>
          {(["categories", "publishing"] as const).map((id) => <NavItem key={id} id={id} on={section === id} go={go} />)}
          <span className="grow" />
          <NavItem id="about" on={section === "about"} go={go} />
        </nav>
        <section className="flex min-w-0 grow flex-col px-7 pt-6 pb-5">
          <header className="flex items-start gap-4 pb-4">
            <div className="flex grow flex-col gap-1">
              <h3 className="text-lg/6 font-semibold">{p.title}</h3>
              <p className="text-[13px]/5 text-ink-subtle">{p.sub}</p>
            </div>
            <Button variant="ghost" icon aria-label="Close" onClick={close}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden>
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </Button>
          </header>
          {/* Exported: shown, never actionable — a disabled fieldset turns off
              every control inside it at once. */}
          <fieldset disabled={!!STATIC} className="-mx-1 min-h-0 grow overflow-y-auto px-1">
            {page(section)}
          </fieldset>
          {section !== "about" && (
            <footer className="flex items-center gap-3 border-t border-border pt-4">
              <span className="min-w-0 grow truncate text-xs text-ink-subtle">
                {STATIC ? READ_ONLY_NOTE : note ?? <>Saved to <span className="font-mono">{s.data ? tilde(s.data.config.path) : "config.toml"}</span></>}
              </span>
              {!STATIC && <Button onClick={() => doAct("open-config")}>Open in editor</Button>}
            </footer>
          )}
        </section>
      </div>
    </div>
  );
}

/** What each row of the phone's list says on its right. */
function summary(id: Section, s: Settings): string {
  const v = (key: string) => { const k = s.config.keys.find((x) => x.key === key); return k?.value ?? k?.default; };
  switch (id) {
    case "general": return `${v("ui.lang") === "zh" ? "中文" : "English"} · ${String(v("ui.theme"))[0]!.toUpperCase()}${String(v("ui.theme")).slice(1)}`;
    case "model": return s.model.status.name;
    case "schedule": return `every ${Number(v("schedule.interval_min")) / 60}h`;
    case "plugins": {
      const on = s.plugins.filter((p) => p.enabled).length;
      const send = s.plugins.filter((p) => p.sending).length;
      return `${on} on · ${send ? `${send} send${send === 1 ? "s" : ""}` : "none send"}`;
    }
    case "categories": return String(s.categories.families.reduce((n, f) => n + f.cats.length, 0));
    case "publishing": return s.publishing.deploy ? "Deploying" : "Off";
    default: return "";
  }
}

/** Settings on a phone — a sheet with the list of pages,
 * each page pushed into the same sheet with ‹ Settings to come back. On the
 * exported copy every page still opens (to read what the Mac has set); the
 * controls are disabled and the foot says where changes are made. */
function PhoneSettings({ page, go, close }: { page: Section | null; go: (s: Section | "home") => void; close: () => void }) {
  const { s, note, page: body } = useWrites(page);
  const mono = (id: Section) => id === "model" || id === "schedule" || id === "categories";
  const row = (id: Section) => (
    <button key={id} type="button" onClick={() => go(id)}
      className="flex h-[52px] w-full items-center gap-3.5 px-4 text-left active:bg-hover">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0 text-ink-muted"><path d={PAGES[id].icon} /></svg>
      <span className="text-md/5">{PAGES[id].title}</span>
      <span className="grow" />
      {s.data && <span className={`truncate text-ink-subtle ${mono(id) ? "font-mono text-xs" : "text-xs"}`}>{summary(id, s.data)}</span>}
      <span aria-hidden className="text-md leading-5 text-ink-subtle">›</span>
    </button>
  );
  const group = (label: string, ids: Section[]) => (
    <>
      <span className="px-4 pt-3 pb-1.5 text-xs text-ink-subtle">{label}</span>
      <div className="flex flex-col divide-y divide-border [&>*]:mx-0">{ids.map(row)}</div>
    </>
  );
  const foot = STATIC ? (
    <div className="flex flex-col gap-2.5 px-4 pt-[22px]">
      <div className="flex flex-col gap-1 rounded-lg bg-sunken px-3.5 py-3">
        <span className="text-[13px]/[18px] font-medium">This is the exported copy</span>
        <span className="text-[13px]/[19px] text-ink-muted">
          {page ? "Shown, not changeable here. " : "Every page opens so you can read what the Mac has set. "}
          Changes are made in Umbra on your Mac; the next export carries them here.
        </span>
      </div>
      {s.data && <span className="truncate font-mono text-2xs leading-[14px] text-ink-subtle">from {tilde(s.data.config.path)}</span>}
    </div>
  ) : (
    <div className="mx-4 mt-[22px] flex min-w-0 items-center gap-1.5 border-t border-border-strong pt-3.5 text-xs/tight">
      {note ? <span className="text-ink-muted">{note}</span> : <>
        <span className="shrink-0 text-ink-subtle">Saved to</span>
        <span className="truncate font-mono text-ink-muted">{s.data ? tilde(s.data.config.path) : "config.toml"}</span>
      </>}
    </div>
  );
  return (
    <Sheet label="Settings" onClose={close}>
      <div className="flex min-h-[calc(100dvh-116px)] flex-col pb-[calc(34px+env(safe-area-inset-bottom))]">
        <div className="sticky top-0 z-10 flex h-11 items-center bg-surface px-4">
          <span className="w-24">
            {page && (
              <button type="button" onClick={() => go("home")} className="flex items-center gap-1 text-md/5 text-ink-muted">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                  strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M15 18l-6-6 6-6" /></svg>
                Settings
              </button>
            )}
          </span>
          <h2 className="grow text-center text-md/5 font-semibold">{page ? PAGES[page].title : "Settings"}</h2>
          <button type="button" onClick={close} className="w-24 text-right text-md/5 font-semibold">Done</button>
        </div>
        {page ? (
          <div className="flex flex-col px-4 pt-2">
            <p className="pb-3.5 text-[13px]/[19px] text-ink-muted">{PAGES[page].sub}</p>
            <fieldset disabled={!!STATIC} className="border-t border-border">{body(page)}</fieldset>
          </div>
        ) : (
          <div className="flex flex-col pt-3.5">
            {row("general")}
            {group("Pipeline", ["model", "schedule", "plugins"])}
            {group("Output", ["categories", "publishing"])}
            <div className="pt-3.5">{row("about")}</div>
          </div>
        )}
        {page !== "about" && foot}
      </div>
    </Sheet>
  );
}

/** Wraps the app; ?settings=<page> opens it on load (links, screenshots). */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const initial = new URLSearchParams(location.search).get("settings");
  // "home": opened without naming a page — the desktop shows General, the
  // phone its list of pages.
  const [open, setOpen] = useState<Section | "home" | null>(
    initial === "home" ? "home" : initial && initial in PAGES ? (initial as Section) : null);
  const show = useCallback((s?: Section) => setOpen(s ?? "home"), []);
  const phone = useMobile();
  return (
    <Ctx.Provider value={show}>
      {children}
      {open && (phone
        ? <PhoneSettings page={open === "home" ? null : open} go={setOpen} close={() => setOpen(null)} />
        : <Dialog section={open === "home" ? "general" : open} go={setOpen} close={() => setOpen(null)} />)}
    </Ctx.Provider>
  );
}
