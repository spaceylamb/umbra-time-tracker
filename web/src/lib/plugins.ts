// What Settings › Plugins says about sending. A sink's name is whatever was
// typed in config.toml, so the page says where things actually go
// (`destination`), falling back to the name only when it cannot be described.
import type { SettingsPlugin } from "../api/types";

const where = (p: SettingsPlugin) => p.destination ?? p.sink;

/** The line under a plugin whose only output is what it sends: on means sending. */
export function sendsTo(p: SettingsPlugin): string | null {
  if (!p.send_only || !p.enabled || p.blocked || !p.sink) return null;
  return `Sends to ${where(p)}`;
}

/** The second switch, for a plugin that is useful here and can also send a copy out. */
export function alsoSend(p: SettingsPlugin): { title: string; desc: string; ready: boolean } | null {
  if (!p.can_send || p.send_only) return null;
  const what = p.send_what ?? "it";
  return p.sink
    ? { title: `Also send ${what} to ${where(p)}`,
        desc: "They always show in the day's drawer; this sends a copy out too.", ready: true }
    : { title: `Also send ${what}`, desc: "Name a sink in config.toml first.", ready: false };
}
