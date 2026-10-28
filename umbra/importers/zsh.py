"""zsh history importer.

Source: ~/.zsh_history
EXTENDED_HISTORY format:  : <unix_ts>:<duration>;<command>

⚠️ Timestamps are only there if EXTENDED_HISTORY is on. Many setups turn it on
   implicitly (a framework, or a default); set it explicitly in .zshrc, or a
   change of configuration silently drops the timestamps.
⚠️ Multi-line commands continue with a backslash and are joined here.
"""

import os
import re

REQUIRES = ["file:~/.zsh_history"]

HISTFILE = os.path.expanduser("~/.zsh_history")
ENTRY = re.compile(r"^: (\d+):(\d+);(.*)$")


def collect(since_ms: int | None = None) -> list[tuple]:
    # since_ms is accepted and ignored: this source reads a local file, and a
    # re-read takes milliseconds — there is no API quota to save. It stays so
    # every importer has the same signature and one loop can pass it by keyword.
    if not os.path.exists(HISTFILE):
        return []
    raw = open(HISTFILE, "rb").read().decode("utf-8", "replace")

    rows, seen = [], set()
    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        m = ENTRY.match(lines[i])
        if not m:
            i += 1
            continue
        ts, dur, cmd = int(m.group(1)), int(m.group(2)), m.group(3)
        while cmd.endswith("\\") and i + 1 < len(lines):   # continuation
            i += 1
            cmd = cmd[:-1] + "\n" + lines[i]
        # Several commands can share a second; a sequence number in the uid
        # keeps them from overwriting each other.
        n = 0
        while (key := f"zsh:{ts}:{n}") in seen:
            n += 1
        seen.add(key)
        rows.append((key, ts * 1000, dur * 1000 or None, "zsh", "command",
                     {"cmd": cmd[:500]}))
        i += 1
    return rows


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "zsh", "kind": None,
          "origin": ("~/.zsh_history", "terminal commands")}]


def render(e) -> str | None:
    return f"$ {e.get('cmd') or ''}"


def classify(e) -> str | None:
    return "code"
