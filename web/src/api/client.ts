// The endpoints of umbra serve, typed by the generated schema types.
//
// Two ways to reach them. Served: HTTP to umbra serve. Exported (umbra
// export, the page the phone reads): every GET was written out as a file at
// its own path, so "/api/day/2026-09-08" is "./api/day/2026-09-08.json" —
// or, in the single-file export, an entry in an inlined map. Writes do not
// exist there; they fail with a sentence that says where to do them.
import { queryOptions } from "@tanstack/react-query";
import type { Config, Day, DayDetail, ImportProgress, Meta, Raw, Said, Settings, Setup, Started, Week } from "./types";

type Static = { inline?: Record<string, unknown>; demo?: { now: number; link: string } };
export const STATIC: Static | undefined = (window as unknown as { __UMBRA_STATIC__?: Static }).__UMBRA_STATIC__;
export const READ_ONLY_NOTE = STATIC?.demo
  ? "This is a demo — nothing here can be changed."
  : "This is the exported copy — changes are made in Umbra on your Mac.";

async function get<T>(path: string): Promise<T> {
  if (STATIC) {
    if (STATIC.inline) {
      if (!(path in STATIC.inline)) throw new Error(`not in this export: ${path}`);
      return STATIC.inline[path] as T;
    }
    const r = await fetch(`.${path}.json`);
    if (!r.ok) throw new Error(r.status === 404 ? `not in this export: ${path}` : `${r.status} ${path}`);
    return (await r.json()) as T;
  }
  const r = await fetch(path);
  const body = await r.json();
  if (!r.ok) throw new Error(body?.error ?? `${r.status} ${path}`);
  return body as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  if (STATIC) throw new Error(READ_ONLY_NOTE);
  // X-Umbra + JSON: what umbra serve requires before it will act, so that
  // other sites open in the browser cannot (serve.do_POST).
  const r = await fetch(path, {
    method: "POST",
    headers: { "X-Umbra": "1", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const out = await r.json();
  if (!r.ok) throw new Error(out?.error ?? `${r.status}`);
  return out as T;
}

// Days do not change under the page while it is open, except today; the
// server's no-store is about shared caches, not about refetching every render.
const stale = 5 * 60_000;

export const metaQuery = queryOptions({
  queryKey: ["meta"],
  queryFn: () => get<Meta>("/api/meta"),
  staleTime: 60_000,
});

export const dayQuery = (date: string) =>
  queryOptions({
    queryKey: ["day", date],
    queryFn: () => get<Day>(`/api/day/${date}`),
    staleTime: stale,
  });

export const detailQuery = (date: string) =>
  queryOptions({
    queryKey: ["day", date, "detail"],
    queryFn: () => get<DayDetail>(`/api/day/${date}/detail`),
    staleTime: stale,
  });

export const rawQuery = (date: string) =>
  queryOptions({
    queryKey: ["day", date, "raw"],
    queryFn: () => get<Raw>(`/api/day/${date}/raw`),
    staleTime: Infinity,
  });

export const setupQuery = queryOptions({
  queryKey: ["setup"],
  queryFn: () => get<Setup>("/api/setup"),
  staleTime: 30_000,
});

/** Polls once a second while an import is running, and not at all otherwise. */
export const importQuery = queryOptions({
  queryKey: ["import"],
  queryFn: () => get<ImportProgress>("/api/import"),
  refetchInterval: (q) => (q.state.data?.run?.running ? 1000 : false),
});

export const startImport = (only?: string) => post<Started>("/api/import", only ? { only } : {});

export const saidQuery = (date: string) =>
  queryOptions({
    queryKey: ["day", date, "said"],
    queryFn: () => get<Said>(`/api/day/${date}/said`),
    staleTime: stale,
  });

/** ✓ / ✕ on a task (null reopens it); "looked at" on a claim. The caller
 * refetches the day's said — the server decides what state it lands in. */
export const setTaskVerdict = (id: number, verdict: "already_done" | "not_a_task" | null) =>
  post<{ ok: boolean }>(`/api/said/task/${id}`, { verdict });
/** Your word for a stretch of the day, or {undo: said_at} to take one back. */
export const correctDay = (date: string, body: Record<string, unknown>) =>
  post<{ ok: boolean }>(`/api/day/${date}/correct`, body);
export const setClaimSeen = (id: number, seen: boolean) =>
  post<{ ok: boolean }>(`/api/said/claim/${id}`, { seen });

export const weekQuery = (week: string) =>
  queryOptions({
    queryKey: ["week", week],
    queryFn: () => get<Week>(`/api/week/${week}`),
    staleTime: stale,
    // Saving new wording starts judging on the server; ask again until it is
    // done, so the hours arrive without a reload.
    refetchInterval: (q) => (q.state.data?.judging ? 2000 : false),
  });

export type GoalInput = { text: string; target: number | null; unit: "hours" | "sessions" | null };

export const saveGoals = (week: string, goals: GoalInput[]) => post<Week>(`/api/week/${week}/goals`, { goals });

export const settingsQuery = queryOptions({
  queryKey: ["settings"],
  queryFn: () => get<Settings>("/api/settings"),
  staleTime: 10_000,
});


export const setConfig = (changes: Record<string, unknown>) => post<Config>("/api/config", changes);
export const runAction = (name: string) => post<{ done: string }>("/api/action", { name });
