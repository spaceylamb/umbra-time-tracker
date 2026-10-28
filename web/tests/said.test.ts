// lib/said: which tabs the drawer has, and what is in each. A tab is there when
// its plugin is on (its part of the payload is present), not because it has
// something in it — and gone when the plugin is off.
import { describe, expect, test } from "bun:test";
import type { Said, SaidClaim, SaidTask } from "../src/api/types";
import { anything, host, itemsOf, lookupNote, sites, tabsOf } from "../src/lib/said";

const task = (id: number, status = "proposed") => ({ id, status }) as SaidTask;
const claim = (id: number, seen = false) => ({ id, seen }) as SaidClaim;
const said = (extra: Partial<Said>): Said => ({ date: "2026-10-23", ...extra });

describe("tabsOf", () => {
  test("both lanes on: both tabs, counting what is still open", () => {
    const s = said({ tasks: [task(1), task(2, "dismissed")], claims: [claim(3), claim(4, true)] });
    expect(tabsOf(s)).toEqual([
      { tab: "todo", label: "To do", n: 1 },
      { tab: "check", label: "To check", n: 1 },
    ]);
  });
  test("only the claims lane on: only its tab", () => {
    expect(tabsOf(said({ claims: [claim(1)] })).map((t) => t.tab)).toEqual(["check"]);
  });
  test("a lane that is on but heard nothing keeps its tab", () => {
    expect(tabsOf(said({ tasks: [] })).map((t) => t.tab)).toEqual(["todo"]);
  });
  test("nothing on: no tabs", () => expect(tabsOf(said({}))).toEqual([]));
});

describe("anything / itemsOf", () => {
  test("the drawer opens only when some lane heard something", () => {
    expect(anything(said({ tasks: [], claims: [] }))).toBe(false);
    expect(anything(said({ claims: [claim(1, true)] }))).toBe(true);
  });
  test("a lane that is off has no items", () => {
    expect(itemsOf(said({}), "check")).toEqual([]);
    expect(itemsOf(said({ tasks: [task(5)] }), "todo").map((x) => x.id)).toEqual([5]);
  });
});

describe("host", () => {
  test("a source reads as its site", () => {
    expect(host("https://www.postgresql.org/docs/15/sql-merge.html")).toBe("postgresql.org");
    expect(host("https://sqlite.org/isolation.html")).toBe("sqlite.org");
  });
  test("something that is not a URL is shown as it is", () => expect(host("not a url")).toBe("not a url"));
});

describe("lookupNote", () => {
  const base = { lookup: true, lookup_by: "gemini-3.8-flash", lookup_sink: "", lookup_answers: true, looked_today: 4 };
  test("a model looks them up: which, and how many today", () => {
    const n = lookupNote(said(base));
    expect(n).toContain("Looked up by gemini-3.8-flash");
    expect(n).toContain("4 today");
  });
  test("one today is said in the singular", () => {
    expect(lookupNote(said({ ...base, looked_today: 1 }))).toContain("1 today");
  });
  test("a sink that reports back", () => {
    expect(lookupNote(said({ ...base, lookup_by: "butler", lookup_sink: "butler" })))
      .toContain("Looked up by butler");
  });
  test("a sink that only receives: nothing comes back", () => {
    const n = lookupNote(said({ ...base, lookup_by: "notes", lookup_sink: "notes", lookup_answers: false }));
    expect(n).toContain("sent to notes");
    expect(n).toContain("does not come back");
  });
  test("nothing can look them up: say what is missing", () => {
    const n = lookupNote(said({ ...base, lookup: false, lookup_by: "" }));
    expect(n).toContain("first guess is the model’s memory");
    expect(n).toContain("key");
  });
});

describe("sites", () => {
  test("two pages on one site read as that site once, the first page kept", () => {
    const s = sites([
      { url: "https://www.postgresql.org/docs/15/sql-merge.html", title: "" },
      { url: "https://postgresql.org/docs/release/15.0/", title: "" },
      { url: "https://www.crunchydata.com/blog/merge", title: "" },
    ]);
    expect(s.map((x) => [x.site, x.url])).toEqual([
      ["postgresql.org", "https://www.postgresql.org/docs/15/sql-merge.html"],
      ["crunchydata.com", "https://www.crunchydata.com/blog/merge"],
    ]);
  });
});
