// The public demo (python -m umbra.devseed --site): its clock stands still at
// the moment it was built, so made-up days never age into "stopped".
import { afterEach, describe, expect, test } from "bun:test";
import type { Freshness } from "../src/api/types";
import { clock, demo } from "../src/lib/demo";
import { isStale, status } from "../src/lib/fresh";
import { ago } from "../src/lib/time";

const g = globalThis as { __UMBRA_STATIC__?: unknown };
const BUILT = Date.UTC(2026, 8, 8, 23, 0);
const fresh = (o: Partial<Freshness> = {}): Freshness =>
  ({ source: "chrome", count: 1, live: false, bad: false, last_run: BUILT - 60000,
     check_ts: BUILT - 60000, stale_min: 540, ...o }) as Freshness;

afterEach(() => { delete g.__UMBRA_STATIC__; });

describe("in the demo", () => {
  test("the clock stands at the moment it was built", () => {
    g.__UMBRA_STATIC__ = { demo: { now: BUILT, link: "https://example.invalid" } };
    expect(demo()?.link).toBe("https://example.invalid");
    expect(clock()).toBe(BUILT);
    expect(ago(BUILT - 5 * 60000)).toBe("5m ago");
  });
  test("a source imported when it was built never goes stale", () => {
    g.__UMBRA_STATIC__ = { demo: { now: BUILT, link: "x" } };
    expect(isStale(fresh())).toBe(false);
    expect(status({ fresh: [fresh()] } as never).stale).toEqual([]);
  });
});

describe("in an ordinary export", () => {
  test("the clock is the reader's, so the same source has gone stale", () => {
    g.__UMBRA_STATIC__ = {};
    expect(demo()).toBeUndefined();
    expect(Math.abs(clock() - Date.now())).toBeLessThan(1000);
    expect(isStale(fresh())).toBe(true);
  });
});
