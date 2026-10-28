"""Claude Code session importer.

Source: ~/.claude/projects/<project dir>/<sessionId>.jsonl
One JSON object per line. Only non-sidechain messages with type == "user" are
kept: they are what the person actually typed, the most direct evidence of
what problem they were working on.

Fields: timestamp (ISO 8601, Z) / cwd / gitBranch / sessionId / uuid

⚠️ It can run to thousands of jsonl files and more than a gigabyte, many of
   them copies inside agent worktrees. Deduplicated by uuid (the uid does it for free); sidechains are
   skipped (a sub-agent's messages are not yours).

⚠️ type == "user" is not "a person typed this". Three kinds of noise, measured:
   1. isMeta=True         → documentation injected by a skill
   2. isCompactSummary    → a context-compaction summary
   3. orchestration instructions dressed as user messages (worktree preambles,
      reducer-agent instructions, "Continue from where you left off") — clean at
      the field level, recognisable only by pattern.
   Left in, they drown the channel with the best signal in system noise.
"""

import datetime as dt
import glob
import json
import os
import re

from .. import prompts

SOURCE = "claude"

PROJECTS = os.path.expanduser("~/.claude/projects")
MAX_TEXT = 2000

# Worktree directory names are uuids; the project name is further up the path.
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_WORKTREE_MARKERS = {".butler-worktrees", "worktrees", ".superset"}


def project_of(cwd: str) -> str:
    """The real project name, from a cwd.

    /home/me/code/myapp/.butler-worktrees/<uuid>   →  myapp
    /home/me/.superset/worktrees/notes-helper        →  notes-helper
    """
    parts = [p for p in cwd.split("/") if p]
    if not parts:
        return "?"
    for i, part in enumerate(parts):
        if part in _WORKTREE_MARKERS and i > 0:
            # The part before the marker is the project, unless it is itself
            # the worktree container.
            return parts[i - 1] if not _UUID.match(parts[i - 1]) else parts[-1]
    last = parts[-1]
    return parts[-2] if _UUID.match(last) and len(parts) > 1 else last


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def collect(since_ms: int | None = None) -> list[tuple]:
    rows = []
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if since_ms and os.path.getmtime(path) * 1000 < since_ms:
            continue
        try:
            fh = open(path, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if d.get("type") != "user" or d.get("isSidechain"):
                    continue
                if d.get("isMeta") or d.get("isCompactSummary"):
                    continue
                ts, uuid = d.get("timestamp"), d.get("uuid")
                if not ts or not uuid:
                    continue
                # Injected templates often wrap a real request; dropping them
                # whole loses data. prompts.clean strips the wrapper and returns
                # None only for text that is entirely machine-generated.
                text = prompts.clean(_text_of(d.get("message", {}).get("content")))
                if not text:
                    continue
                ts_ms = int(dt.datetime.fromisoformat(
                    ts.replace("Z", "+00:00")).timestamp() * 1000)
                cwd = d.get("cwd") or ""
                rows.append((
                    f"claude:{uuid}", ts_ms, None, "claude", "prompt",
                    {"text": text[:MAX_TEXT],
                     "project": project_of(cwd),
                     "cwd": cwd,
                     "branch": d.get("gitBranch") or ""},
                ))
    return rows


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "claude", "kind": None,
          "origin": ("~/.claude", "Claude Code session logs")}]


def render(e) -> str | None:
    """Dictated instructions to an AI.

    The prompt tells the model that "to AI" lines are work rather than chat, so
    this prefix has to match butler's exactly — a different one would put this
    source outside that instruction.
    """
    t = (e.get("text") or e.get("title") or "").strip().replace("\n", " ")
    return f"💬 to AI: {t}" if t else None


def classify(e) -> str | None:
    return "code"
