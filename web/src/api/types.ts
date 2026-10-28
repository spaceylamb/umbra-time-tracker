/* Generated from umbra/schema/api.schema.json by scripts/gen-types.mjs. Do not edit. */

/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "IsoDate".
 */
export type IsoDate = string;

/**
 * The one contract between umbra serve (Python) and the web frontend (TypeScript). TS types are generated from this file; Python validates against it in tests with umbra/schema/validate.py. Shapes are the viz payload, cut where viz.py cuts RESIDENT from ONDEMAND.
 */
export interface Api {
  Meta?: Meta;
  Freshness?: Freshness;
  Labels?: Labels;
  Day?: Day;
  DayEvent?: DayEvent;
  DayDetail?: DayDetail;
  Run?: Run;
  Ai?: Ai;
  Span?: Span;
  Digest?: Digest;
  Raw?: Raw;
  ApiError?: ApiError;
  IsoDate?: IsoDate;
  StringMap?: StringMap;
  ColourMap?: ColourMap;
  ModelStatus?: ModelStatus;
  Failed?: Failed;
  Setup?: Setup;
  ImportProgress?: ImportProgress;
  Started?: Started;
  Said?: Said;
  SaidTask?: SaidTask;
  SaidClaim?: SaidClaim;
  Config?: Config;
  Week?: Week;
  Settings?: Settings;
  Done?: Done;
  SettingsPlugin?: SettingsPlugin;
}
/**
 * GET /api/meta — everything that is per page, not per day. today is computed on the server: the browser's clock and timezone are not trusted to mark today.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Meta".
 */
export interface Meta {
  /**
   * Every day that has events, oldest first.
   */
  order: IsoDate[];
  since?: string | null;
  today: IsoDate;
  tz: string;
  tzOffset: string;
  generated: string;
  built_ms: number;
  page_stale_min: number;
  lang: string;
  fresh: Freshness[];
  labels: Labels;
  /**
   * Category colours for both themes, computed by umbra/palette.py. Never tokens (ADR 0003).
   */
  palette: {
    light: ColourMap;
    dark: ColourMap;
  };
  groups: {
    key: string;
    name: string;
    cats: string[];
  }[];
  model: ModelStatus;
  theme: "system" | "light" | "dark";
}
/**
 * One source. The page does exactly one subtraction: bad || (check_ts != null && now - check_ts > stale_min*60000). The rule lives in viz.stale_fields only.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Freshness".
 */
export interface Freshness {
  source: string;
  count: number;
  last_event?: number | null;
  last_run?: number | null;
  last_ok?: number | null;
  ok?: boolean | null;
  error?: string | null;
  fails?: number;
  live: boolean;
  bad: boolean;
  check_ts: number | null;
  stale_min: number;
  [k: string]: unknown | undefined;
}
/**
 * source = viz.LABEL, origin = viz.ORIGIN (lane -> [short, long]), block = viz.BNAME, bcode = bcat letter -> category key, app = apps.labels(lang), category = taxonomy.labels(lang).
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Labels".
 */
export interface Labels {
  source: StringMap;
  /**
   * viz.ORIGIN: lane key -> [short, long].
   */
  origin: {
    /**
     * @minItems 2
     * @maxItems 2
     */
    [k: string]: [string, string] | undefined;
  };
  block: StringMap;
  bcode: StringMap;
  app: StringMap;
  category: StringMap;
  lane_source: StringMap;
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "StringMap".
 */
export interface StringMap {
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "ColourMap".
 */
export interface ColourMap {
  [k: string]: string | undefined;
}
/**
 * The model the next run will use, and whether it can be called. Never carries the key itself. key_set is null for a provider that needs no key (a local server).
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "ModelStatus".
 */
export interface ModelStatus {
  name: string;
  kind: string | null;
  key_env: string | null;
  key_set: boolean | null;
  local: boolean;
  env_file: string | null;
}
/**
 * GET /api/day/{date} — the resident layer: what the page needs without opening anything.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Day".
 */
export interface Day {
  date: IsoDate;
  /**
   * One character per 5-minute slot of this day — n_min / 5 of them (288 on an ordinary day); '.' is empty. Decode with labels.block.
   */
  bcat: string;
  cov: {
    A: number;
    B: number;
    off: number;
    C: number;
    away: number;
    /**
     * The day's length in minutes; the denominator of pct.
     */
    n_min?: number;
  };
  pct: number;
  counts: {
    [k: string]: number | undefined;
  };
  /**
   * Absent when no model has composed events for this day — the page draws the hatch then, never an empty bar.
   */
  ev?: {
    [k: string]: DayEvent[] | undefined;
  };
  /**
   * Which model's events are drawn.
   */
  evuse?: string;
  /**
   * Raw records exist but were not kept for this day — not the same as having none.
   */
  raw_omitted?: boolean;
  failed?: Failed;
  /**
   * How many minutes this day has: 1440 usually, 1380 / 1500 on daylight-saving days, anything when travelling. Every axis of the day is 0..n_min.
   */
  n_min: number;
  /**
   * How much of the day has happened, in minutes: n_min once it is over, the current minute today. Counted at read time. Nothing is drawn past it.
   */
  end: number;
  /**
   * The zone changed on or around this day. Its records are all there; comparisons across days leave it out.
   */
  travel: boolean;
  /**
   * The clocks this day was read on, from the minute each began. One usually; two across a daylight-saving change or a flight.
   *
   * @minItems 1
   */
  tz: [
    {
      at: number;
      /**
       * The clock on the wall when this segment began, minutes after midnight. Not always 0 for the first: the day after flying east begins when the last one ended.
       */
      wall: number;
      /**
       * IANA name, e.g. America/Los_Angeles.
       */
      zone: string;
      /**
       * What the clock was called (PDT), or the offset (+08) when no name fits.
       */
      abbr: string;
      /**
       * UTC offset in seconds.
       */
      off_s: number;
    },
    ...{
      at: number;
      /**
       * The clock on the wall when this segment began, minutes after midnight. Not always 0 for the first: the day after flying east begins when the last one ended.
       */
      wall: number;
      /**
       * IANA name, e.g. America/Los_Angeles.
       */
      zone: string;
      /**
       * What the clock was called (PDT), or the offset (+08) when no name fits.
       */
      abbr: string;
      /**
       * UTC offset in seconds.
       */
      off_s: number;
    }[]
  ];
  /**
   * Each minute of 0..end counted once. Absent when no model has composed events. covered + failed + silent = end.
   */
  minutes?: {
    end: number;
    /**
     * Inside an event.
     */
    covered: number;
    /**
     * Not inside an event, inside a window whose call failed (kind call).
     */
    failed: number;
    /**
     * Neither: the model read it and said nothing.
     */
    silent: number;
  };
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "DayEvent".
 */
export interface DayEvent {
  /**
   * Minute of day.
   */
  a: number;
  b: number;
  cat: string;
  title: string;
  start: string;
  end: string;
  detail?: string;
  from?: string[];
  /**
   * Indices into detail.ai.spans[evuse].
   */
  sp?: number[];
  /**
   * What the first pass actually read: the spans' union plus an hour either side.
   *
   * @minItems 2
   * @maxItems 2
   */
  win?: [number, number];
  /**
   * Indices into detail.dg.
   */
  dg?: number[];
  /**
   * your own correction (correct.py), drawn over the model's
   */
  mine?: boolean;
  /**
   * when you said it, ms; what Undo names
   */
  said_at?: number;
  /**
   * the model's events this correction covers
   */
  was?: {
    a?: number;
    b?: number;
    cat?: string;
    title?: string;
    start?: string;
    end?: string;
    detail?: string;
  }[];
  /**
   * the model, having read it, holds to something else
   */
  vs?: {
    cat: string;
    soft: boolean;
    title: string;
    detail?: string;
  };
  /**
   * the model said nothing about this stretch
   */
  nosay?: boolean;
  /**
   * the model has not been asked since you said it
   */
  pending?: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Windows whose model call failed, for one model. Absent when none did. kind auth = a person has to act (key rejected or missing); call = retried next run.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Failed".
 */
export interface Failed {
  model: string;
  windows: {
    a: number;
    b: number;
    at: number;
    kind: "auth" | "call";
    reason: string;
  }[];
}
/**
 * GET /api/day/{date}/detail — the on-demand layer, minus raw records.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "DayDetail".
 */
export interface DayDetail {
  date: IsoDate;
  lanes?: {
    [k: string]: [unknown, unknown, unknown][] | undefined;
  };
  bruns?: Run[];
  ai?: Ai;
  dg?: Digest[];
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Run".
 */
export interface Run {
  t: string;
  end: string;
  min: number;
  cat: string | null;
  label: string;
  cf?: boolean;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Ai".
 */
export interface Ai {
  models: string[];
  spans: {
    [k: string]: Span[] | undefined;
  };
  ag: {}[];
  use?: string;
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Span".
 */
export interface Span {
  a: number;
  b: number;
  a0?: string;
  b0?: string;
  cat: string;
  act: string;
  conf: string;
  who?: string;
  ev: string[];
  also?: unknown[];
  [k: string]: unknown | undefined;
}
/**
 * The text actually sent to the model for one window.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Digest".
 */
export interface Digest {
  a: number;
  b: number;
  start: string;
  end: string;
  wstart: string;
  wend: string;
  n: number;
  chars: number;
  text: string;
}
/**
 * GET /api/day/{date}/raw — every record verbatim. ts is seconds into the day.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Raw".
 */
export interface Raw {
  date: IsoDate;
  records: {
    ts: number;
    source: string;
    kind: string;
    [k: string]: unknown | undefined;
  }[];
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "ApiError".
 */
export interface ApiError {
  error: string;
}
/**
 * GET /api/setup — every source and whether it can run here, now. unknown = cannot be probed (Full Disk Access), never guessed.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Setup".
 */
export interface Setup {
  sources: {
    name: string;
    label: string;
    what: string;
    state: "ready" | "missing" | "unknown" | "unsupported" | "broken";
    needs: string[];
    source: string;
  }[];
}
/**
 * GET /api/import — the running import, or the last one; run is null when none has ever run.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "ImportProgress".
 */
export interface ImportProgress {
  run: {
    run: number;
    running: boolean;
    updated: number;
    sources: {
      source: string;
      state: "next" | "reading" | "done" | "failed" | "aborted";
      rows: number | null;
      error: string | null;
    }[];
  } | null;
}
/**
 * POST /api/import — whether this call started a run (false: one was already running).
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Started".
 */
export interface Started {
  started: boolean;
}
/**
 * GET /api/day/{date}/said — tasks and claims said aloud. held counts echoes: found, but said while dictating to an agent, so not listed. sending: whether tasks go anywhere; lookup: whether claims are checked or only the model's memory. Every field but date comes from an enabled plugin's said(); a switched-off plugin's fields are absent, and so is its tab.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Said".
 */
export interface Said {
  date: IsoDate;
  held?: number;
  sending?: boolean;
  lookup?: boolean;
  /**
   * The sink claims go to; empty when sending is off.
   */
  lookup_sink?: string;
  /**
   * Whether that sink reports back. False: claims are sent and nothing returns.
   */
  lookup_answers?: boolean;
  tasks?: SaidTask[];
  claims?: SaidClaim[];
  /**
   * the model, or the sink, that looks claims up; empty when nothing can
   */
  lookup_by?: string;
  /**
   * claims looked up today
   */
  looked_today?: number;
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "SaidTask".
 */
export interface SaidTask {
  id: number;
  t: string;
  at: number;
  /**
   * @minItems 2
   * @maxItems 2
   */
  win: [number, number];
  task: string;
  quote: string;
  type: string;
  domain: string;
  confidence: string;
  status: string;
  marked: boolean;
  speakers: number | null;
  /**
   * How a closed task was closed: already_done or not_a_task (here or at the destination), ran (approved). Null while open.
   */
  verdict: string | null;
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "SaidClaim".
 */
export interface SaidClaim {
  id: number;
  t: string;
  at: number;
  /**
   * @minItems 2
   * @maxItems 2
   */
  win: [number, number];
  speaker: string;
  claim: string;
  quote: string;
  kind: string;
  verdict: string;
  absolute: boolean;
  why: string;
  status: string;
  card: string | null;
  would_check: boolean;
  /**
   * Marked as looked at on the page (claim_seen).
   */
  seen: boolean;
  /**
   * What the lookup found; null until a claim is looked up (or for one checked by a sink as part of a day's report).
   */
  found: {
    verdict: "right" | "inaccurate" | "wrong" | "unsettled";
    /**
     * what is actually so, in a sentence or two
     */
    actual: string;
    /**
     * the words read like a transcription error
     */
    misheard: boolean;
    sources: {
      url: string;
      title: string;
    }[];
    model: string;
  } | null;
  /**
   * worth a lookup and not looked up yet; the next round will
   */
  waiting: boolean;
}
/**
 * GET/POST /api/config — the settings the page may change. Everything else is 'Open in editor'. needs: what must be re-run before a change takes effect.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Config".
 */
export interface Config {
  path: string;
  keys: {
    key: string;
    kind: "enum" | "int" | "bool";
    default: string | number | boolean;
    options: unknown[] | null;
    min: number | null;
    max: number | null;
    value: string | number | boolean | null;
    what: string;
    needs: string | null;
  }[];
}
/**
 * GET /api/week/{week} and POST /api/week/{week}/goals. Day payloads are fetched per day; this carries only what exists per week. done: days whose attribution is current for these priorities — any other day is unasked, not zero. totals: minutes per priority index (1-based, as a string key); a stretch credited to two priorities counts fully for both. judging: a judgement started by saving these priorities is still running.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Week".
 */
export interface Week {
  week: string;
  /**
   * @minItems 7
   * @maxItems 7
   */
  days: [IsoDate, IsoDate, IsoDate, IsoDate, IsoDate, IsoDate, IsoDate];
  /**
   * @maxItems 3
   */
  goals: [] | [string] | [string, string] | [string, string, string];
  gdig: string;
  model: string;
  attrib: {
    [k: string]:
      | {
          a: number;
          b: number;
          g: number;
          why: string;
        }[]
      | undefined;
  };
  totals: {
    [k: string]: number | undefined;
  };
  done: IsoDate[];
  /**
   * @maxItems 3
   */
  priorities:
    | []
    | [
        {
          text: string;
          target: number | null;
          unit: "hours" | "sessions" | null;
          minutes: number;
          sessions: number;
          session_days: IsoDate[];
        }
      ]
    | [
        {
          text: string;
          target: number | null;
          unit: "hours" | "sessions" | null;
          minutes: number;
          sessions: number;
          session_days: IsoDate[];
        },
        {
          text: string;
          target: number | null;
          unit: "hours" | "sessions" | null;
          minutes: number;
          sessions: number;
          session_days: IsoDate[];
        }
      ]
    | [
        {
          text: string;
          target: number | null;
          unit: "hours" | "sessions" | null;
          minutes: number;
          sessions: number;
          session_days: IsoDate[];
        },
        {
          text: string;
          target: number | null;
          unit: "hours" | "sessions" | null;
          minutes: number;
          sessions: number;
          session_days: IsoDate[];
        },
        {
          text: string;
          target: number | null;
          unit: "hours" | "sessions" | null;
          minutes: number;
          sessions: number;
          session_days: IsoDate[];
        }
      ];
  /**
   * This week's days are being judged against its priorities right now — started by saving new wording. Poll until false; done then says which days are current.
   */
  judging: boolean;
  /**
   * The judging run started by the last save ended in failure — most often no model key. The days it did not reach stay unjudged and are asked again on the next run.
   */
  judge_failed: boolean;
  /**
   * When judge_failed: the run's own last warning, in one line — why it failed, not a guess.
   */
  judge_error: string | null;
}
/**
 * GET /api/settings — one section per page of the Settings dialog, all derived from config, environment and disk.
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Settings".
 */
export interface Settings {
  config: Config;
  general: {
    tz: string;
    tz_abbr: string;
  };
  model: {
    status: ModelStatus;
    rules: number;
    providers: {
      name: string;
      kind: string;
      models: string[];
      builtin: boolean;
      key_set: boolean | null;
    }[];
  };
  schedule: {
    installed: boolean | null;
    platform: string;
    last_run: number | null;
    stale_min: number;
  };
  plugins: SettingsPlugin[];
  categories: {
    families: {
      family: string;
      cats: {
        key: string;
        en: string;
        zh: string;
        own: boolean;
      }[];
    }[];
    own: string[];
    groups: {
      key: string;
      en: string;
      cats: string[];
    }[];
  };
  publishing: {
    page: string;
    page_exists: boolean;
    deploy: boolean;
  };
  about: {
    version: string;
    license: string;
    paths: {
      key: string;
      label: string;
      path: string;
      size: number | null;
    }[];
  };
}
/**
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "SettingsPlugin".
 */
export interface SettingsPlugin {
  name: string;
  title: string;
  what: string;
  bundled: boolean;
  enabled: boolean;
  sink: string | null;
  sending: boolean;
  can_send: boolean;
  /**
   * whether the named sink can report back on one item (poll); null with no sink, or one that does not resolve
   */
  sink_answers: boolean | null;
  /**
   * what the second switch also sends a copy of, in words ("each to-do"); null without one
   */
  send_what: string | null;
  /**
   * what this plugin makes is only ever sent (an alert, a monthly list): on means sending, and there is no second switch
   */
  send_only: boolean;
  /**
   * where the named sink actually sends, in words ("Butler (your inbox)", "the command …", "the webhook at host"); null with no sink, or one that does not resolve
   */
  destination: string | null;
  /**
   * Why this plugin cannot run, in words; null when it can. The switch is disabled while it cannot.
   */
  blocked: string | null;
  /**
   * Fact-check only: who looks claims up, and the choices Settings offers.
   */
  lookup?: {
    /**
     * the chosen choice's value: gemini, gpt, claude, sink, or custom for a lookup_model named in config.toml
     */
    using: string;
    /**
     * the model that looks claims up; null when a sink does
     */
    model: string | null;
    /**
     * lookups a day at most
     */
    cap: number;
    choices: {
      value: string;
      label: string;
      model: string | null;
      note: string;
      key_env: string | null;
      key_set: boolean | null;
    }[];
  };
}
/**
 * POST /api/said/task/{id} and /api/said/claim/{id} — ok is false when the item was not in a state the change applies to (already closed, already open).
 *
 * This interface was referenced by `Api`'s JSON-Schema
 * via the `definition` "Done".
 */
export interface Done {
  ok: boolean;
}
