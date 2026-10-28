// The page's 1440, made per day. Run with `bun test` from web/.
import { describe, expect, test } from "bun:test";
import type { Day } from "../src/api/types";
import { clockTicks, dayLen, leftNote, likeForLike, switchesOf, tzLine, wallHm, wallRange } from "../src/lib/clock";
import { hm, range } from "../src/lib/time";

const PDT = { at: 0, wall: 0, zone: "America/Los_Angeles", abbr: "PDT", off_s: -25200 };
const day = (o: Partial<Day>): Day => ({
  date: "2026-10-05", bcat: "", cov: { A: 0, B: 0, off: 0, C: 0, away: 0 }, pct: 0, counts: {},
  n_min: 1440, end: 1440, travel: false, tz: [PDT], ...o,
} as Day);

describe("clockTicks", () => {
  test("an ordinary day: every three hours of the wall clock", () => {
    expect(clockTicks([PDT], 1440).map((t) => t.label))
      .toEqual(["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]);
  });

  test("a ten-hour day stops where it ends", () => {
    expect(clockTicks([PDT], 600).map((t) => t.m)).toEqual([0, 180, 360, 540]);
  });

  test("after the clocks go back, ticks follow the new clock", () => {
    const t = clockTicks([PDT, { ...PDT, at: 120, wall: 60, abbr: "PST", off_s: -28800 }], 1500);
    // 03:00 PST is 240 minutes after midnight: 01:00–02:00 happened twice.
    expect(t.find((x) => x.label === "03:00")!.m).toBe(240);
    expect(t.at(-1)).toEqual({ m: 1320, label: "21:00" });
  });

  test("a long day is ticked every six hours so the labels fit", () => {
    const segs = [{ at: 0, wall: 0, zone: "Asia/Shanghai", abbr: "CST", off_s: 28800 }, { ...PDT, at: 960, wall: 60 }];
    const t = clockTicks(segs, 2340);
    expect(t.map((x) => x.label)).toEqual(["00:00", "06:00", "12:00", "06:00", "12:00", "18:00"]);
  });

  test("a day that began at 01:00 says so at its start", () => {
    // The day after flying east begins when the last one ended.
    const t = clockTicks([{ ...PDT, wall: 60, abbr: "CST" }], 1380);
    expect(t.slice(0, 3)).toEqual([{ m: 0, label: "01:00" }, { m: 120, label: "03:00" }, { m: 300, label: "06:00" }]);
  });

  test("no tick crowds the label of a clock change", () => {
    const t = clockTicks([PDT, { ...PDT, at: 120, wall: 60, abbr: "PST", off_s: -28800 }], 1500);
    expect(t.some((x) => x.m > 120 && x.m < 170)).toBe(false);
  });
});

describe("switchesOf", () => {
  test("names both clocks and the time on the new one", () => {
    expect(switchesOf([PDT, { ...PDT, at: 120, wall: 60, abbr: "PST", off_s: -28800 }]))
      .toEqual([{ at: 120, label: "PDT → PST · 01:00" }]);
  });
  test("one clock, no switch", () => expect(switchesOf([PDT])).toEqual([]));
});

describe("tzLine", () => {
  test("an ordinary day is just its clock", () => expect(tzLine(day({}), false)).toBe("PDT"));
  test("today says so", () => expect(tzLine(day({}), true)).toBe("PDT · today"));
  test("a travel day: clocks, length, travel", () => {
    expect(tzLine(day({ n_min: 600, travel: true }), false)).toBe("PDT · a 10-hour day · travel");
  });
  test("the clocks going back is two clocks and 25 hours, not travel", () => {
    expect(tzLine(day({ n_min: 1500, tz: [PDT, { ...PDT, at: 120, wall: 60, abbr: "PST", off_s: -28800 }] }), false))
      .toBe("PDT → PST · a 25-hour day");
  });
  test("a length that is not whole hours keeps its minutes", () => {
    expect(tzLine(day({ n_min: 1410, travel: true }), false)).toBe("PDT · a 23½-hour day · travel");
  });
  test("an old payload without the fields still reads", () => {
    expect(tzLine({ date: "2026-10-05" } as Day, false)).toBe("");
  });
});

describe("dayLen", () => {
  test("defaults to 1440 when a payload predates n_min", () => {
    expect(dayLen(undefined)).toBe(1440);
    expect(dayLen(day({ n_min: 600 }))).toBe(600);
  });
});

describe("likeForLike", () => {
  const cur = ["2026-10-19", "2026-10-20", "2026-10-21"];
  const last = ["2026-10-12", "2026-10-13", "2026-10-14"];
  test("a travel day this week leaves that weekday out of both weeks", () => {
    const data = { "2026-10-20": day({ travel: true, n_min: 2340 }) };
    const r = likeForLike(cur, last, data);
    expect(r.cur).toEqual(["2026-10-19", "2026-10-21"]);
    expect(r.prev).toEqual(["2026-10-12", "2026-10-14"]);
    expect(r.left).toEqual([{ date: "2026-10-20", lastWeek: false, n_min: 2340 }]);
  });
  test("so does one last week", () => {
    const data = { "2026-10-12": day({ travel: true, n_min: 600 }) };
    const r = likeForLike(cur, last, data);
    expect(r.cur).toEqual(["2026-10-20", "2026-10-21"]);
    expect(r.left).toEqual([{ date: "2026-10-12", lastWeek: true, n_min: 600 }]);
  });
  test("no travel, nothing left out", () => {
    expect(likeForLike(cur, last, {}).left).toEqual([]);
  });
});

describe("leftNote", () => {
  test("names the day, why, and where its time still is", () => {
    expect(leftNote([{ date: "2026-10-20", lastWeek: false, n_min: 2340 }]))
      .toBe("Tuesday 20 is left out: a travel day, 39 hours long — around a change of timezone, a day does not belong in a column of ordinary ones. Its time is still in “Where the time went”.");
  });
  test("a travel day last week leaves the weekday out of both", () => {
    expect(leftNote([{ date: "2026-10-12", lastWeek: true, n_min: 600 }]))
      .toBe("Monday is left out of both weeks: Monday 12 last week was a travel day, 10 hours long.");
  });
  test("several days are named once, in one sentence", () => {
    expect(leftNote([
      { date: "2026-08-31", lastWeek: false, n_min: 1440 },
      { date: "2026-09-01", lastWeek: false, n_min: 600 },
      { date: "2026-09-03", lastWeek: false, n_min: 2340 }]))
      .toBe("Monday 31, Tuesday 1 and Thursday 3 are left out: travel days, 24, 10 and 39 hours long — around a change of timezone, a day does not belong in a column of ordinary ones. Their time is still in “Where the time went”.");
  });
  test("several last week, one sentence", () => {
    expect(leftNote([
      { date: "2026-08-31", lastWeek: true, n_min: 1440 },
      { date: "2026-09-01", lastWeek: true, n_min: 600 }]))
      .toBe("Monday and Tuesday are left out of both weeks: Monday 31 and Tuesday 1 last week were travel days, 24 and 10 hours long.");
  });
  test("nothing left out says nothing", () => expect(leftNote([])).toBe(""));
});

describe("wall-clock labels", () => {
  const back = [{ at: 0, wall: 0, zone: "Asia/Shanghai", abbr: "CST", off_s: 28800 }, { ...PDT, at: 960, wall: 60 }];
  test("an ordinary day reads exactly as before", () => {
    for (const [a, b] of [[0, 430], [430, 1440], [605, 1222]] as const) {
      expect(wallRange(a, b, [PDT])).toBe(range(a, b));
      expect(wallHm(a, [PDT])).toBe(hm(a));
    }
  });
  test("past a clock change, minutes read on the new clock", () => {
    // 23:20–26:00 after the day began is 08:20–11:00 in California.
    expect(wallRange(1400, 1560, back)).toBe("08:20–11:00");
  });
  test("the day's last minute is 24:00, not 00:00", () => {
    expect(wallRange(1700, 2340, back)).toBe("13:20–24:00");
  });
  test("a day that began at 01:00 reads from 01:00", () => {
    expect(wallHm(0, [{ ...PDT, wall: 60 }])).toBe("01:00");
  });
});
