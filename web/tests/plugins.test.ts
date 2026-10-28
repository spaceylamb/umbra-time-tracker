// lib/plugins: what Settings › Plugins says about sending — where things go,
// in words, and which plugins have a second switch at all.
import { describe, expect, test } from "bun:test";
import type { SettingsPlugin } from "../src/api/types";
import { alsoSend, sendsTo } from "../src/lib/plugins";

const p = (extra: Partial<SettingsPlugin>): SettingsPlugin =>
  ({ name: "x", title: "X", what: "", bundled: true, enabled: true, sink: null, sending: false,
     can_send: false, sink_answers: null, send_what: null, send_only: false, destination: null,
     blocked: null, ...extra }) as SettingsPlugin;

describe("a plugin that only sends", () => {
  test("says where it sends, in words", () => {
    expect(sendsTo(p({ send_only: true, sink: "butler", destination: "Butler (your inbox)" })))
      .toBe("Sends to Butler (your inbox)");
  });
  test("falls back to the sink's name when it cannot be described", () => {
    expect(sendsTo(p({ send_only: true, sink: "notes" }))).toBe("Sends to notes");
  });
  test("says nothing while blocked or off — the blocked line says what is missing", () => {
    expect(sendsTo(p({ send_only: true, sink: "butler", blocked: "Needs a sink" }))).toBeNull();
    expect(sendsTo(p({ send_only: true, sink: "butler", enabled: false }))).toBeNull();
  });
  test("has no second switch", () => {
    expect(alsoSend(p({ send_only: true, sink: "butler" }))).toBeNull();
  });
});

describe("a plugin with a second switch", () => {
  test("names what it copies out, and where", () => {
    expect(alsoSend(p({ can_send: true, send_what: "each to-do", sink: "butler",
                        destination: "Butler (your inbox)" })))
      .toEqual({ title: "Also send each to-do to Butler (your inbox)",
                 desc: "They always show in the day's drawer; this sends a copy out too.", ready: true });
  });
  test("without a sink, says how to get one", () => {
    expect(alsoSend(p({ can_send: true, send_what: "the day's results" })))
      .toEqual({ title: "Also send the day's results", desc: "Name a sink in config.toml first.", ready: false });
  });
  test("a plugin that cannot send has neither", () => {
    expect(alsoSend(p({}))).toBeNull();
    expect(sendsTo(p({}))).toBeNull();
  });
});
