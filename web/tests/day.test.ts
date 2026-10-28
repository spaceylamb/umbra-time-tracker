// lib/day: what every view draws from a Day payload.
import { describe, expect, test } from "bun:test";
import type { Day } from "../src/api/types";
import { eventsOf } from "../src/lib/day";

const ev = (a: number, b: number, title = "x") => ({ a, b, cat: "code", title }) as never;
const day = (end: number, evs: unknown[]): Day => ({
  date: "2026-10-24", bcat: "", cov: { A: 0, B: 0, off: 0, C: 0, away: 0 }, pct: 0, counts: {},
  n_min: 1440, end, travel: false, tz: [], ev: { m: evs as never }, evuse: "m",
} as Day);

describe("eventsOf", () => {
  test("today: nothing after now, and what crosses now stops there", () => {
    // Composed before merge stopped at now, the stored day can still hold an
    // "Unknown" running to midnight — the list would show it, with a negative
    // length once now is before its start.
    const got = eventsOf(day(508, [ev(0, 420), ev(480, 660), ev(720, 840), ev(840, 1440, "Unknown")]));
    expect(got.map((e) => [e.a, e.b])).toEqual([[0, 420], [480, 508]]);
  });
  test("a past day is untouched", () => {
    const evs = [ev(0, 420), ev(840, 1440)];
    expect(eventsOf(day(1440, evs))).toEqual(evs as never);
  });
  test("a payload without end is untouched", () => {
    const d = day(0, [ev(0, 420)]);
    delete (d as Partial<Day>).end;
    expect(eventsOf(d).length).toBe(1);
  });
});
