// lib/correct: the editor's choices and checks, and what the panel says about
// the model's view of your correction — the v2 copy, short on purpose.
import { describe, expect, test } from "bun:test";
import type { DayEvent, Meta } from "../src/api/types";
import { CATS, eventAt, hhmm, minutes, modelSays, spanError } from "../src/lib/correct";

const meta = { labels: { category: { read: "Reading", meal: "Meals", social: "Social" } } } as unknown as Meta;
const mine = (extra: Partial<DayEvent>): DayEvent =>
  ({ a: 750, b: 790, cat: "read", title: "reading", start: "12:30", end: "13:10", mine: true, ...extra }) as DayEvent;

describe("the editor", () => {
  test("offers the categories one can mean, not unknown or the internal ones", () => {
    expect(CATS).toContain("read");
    expect(CATS).not.toContain("unknown");
    expect(CATS).not.toContain("app");
  });
  test("reads and writes the clock", () => {
    expect(minutes("12:30")).toBe(750);
    expect(minutes("7:05")).toBe(425);
    expect(minutes("12h30")).toBeNull();
    expect(hhmm(750)).toBe("12:30");
  });
  test("refuses what the server would refuse, before sending it", () => {
    expect(spanError(750, 790, 1440)).toBeNull();
    expect(spanError(790, 750, 1440)).toBe("The end is before the start.");
    expect(spanError(null, 790, 1440)).toBe("Times are HH:MM.");
    expect(spanError(1400, 1500, 1440)).toBe("Outside this day.");
  });
});

describe("what the model makes of it", () => {
  test("not asked since you said it", () => {
    expect(modelSays(meta, mine({ pending: true }))).toEqual({ tone: "pending", text: "Not re-read yet." });
  });
  test("agrees", () => expect(modelSays(meta, mine({}))).toEqual({ tone: "agree", text: "✓ Agrees." }));
  test("the same thing the other way round", () => {
    const s = modelSays(meta, mine({ vs: { cat: "meal", soft: true, title: "dinner" } }));
    expect(s.tone).toBe("soft");
    expect(s.text).toMatch(/with .* noted\.$/);
  });
  test("disagrees, quoting what it holds to", () => {
    const s = modelSays(meta, mine({ vs: { cat: "social", soft: false, title: "coffee with a colleague" } }));
    expect(s.tone).toBe("vs");
    expect(s.text).toMatch(/^Still says .* — “coffee with a colleague”\.$/);
  });
  test("said nothing about it", () => {
    expect(modelSays(meta, mine({ nosay: true }))).toEqual({ tone: "nosay", text: "Nothing to compare yet." });
  });
});

describe("after saving", () => {
  test("the selection stays on the stretch just corrected", () => {
    const evs = [{ a: 700, b: 750 }, { a: 750, b: 790 }, { a: 790, b: 820 }] as DayEvent[];
    expect(eventAt(evs, 750)).toBe(evs[1]);
    expect(eventAt(evs, 760)).toBe(evs[1]);
    expect(eventAt(evs, 900)).toBeUndefined();
  });
});
