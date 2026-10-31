// The page does exactly one subtraction; the rule is Python's (viz.stale_fields).
import type { Freshness, Meta } from "../api/types";
import { clock } from "./demo";

export const isStale = (f: Freshness, now = clock()) =>
  f.bad || (f.check_ts != null && now - f.check_ts > f.stale_min * 60000);

export function status(meta: Meta, now = clock()) {
  const runs = meta.fresh.filter((f) => !f.live && f.last_run != null);
  const last = runs.length ? Math.max(...runs.map((f) => f.last_run!)) : null;
  return {
    last,
    stale: meta.fresh.filter((f) => isStale(f, now)),
  };
}

/** An import error, as a person reads it. The raw text stays in
 * `umbra freshness` and the logs; the page drops what only a debugger needs —
 * urllib's wrapper, errno numbers, exception class names — and writes home
 * paths as ~/…, the same way the export does. */
export function plainError(e: string): string {
  return e
    .replace(/^<urlopen error (.*)>$/s, "$1")
    .replace(/\[SSL: [A-Z_]+\] /g, "")
    .replace(/EOF occurred in violation of protocol \(_ssl\.c:\d+\)/, "the connection dropped mid-request")
    .replace(/\[Errno -?\d+\] /g, "")
    .replace(/^nodename nor servname provided, or not known$|^Name or service not known$/, "could not reach the server — no network?")
    .replace(/^No such file or directory: /, "not found: ")
    .replace(/^[A-Z][A-Za-z]*(Error|Exception): /, "")
    .replace(/(['"]?)\/(Users|home)\/[^/'"\s]+\//g, "$1~/")
    .trim();
}
