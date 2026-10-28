"""Butler task-history importer.

Source: your own Butler instance, /api/export/tasks (Bearer PAT).

Why the local jsonl files are not enough:
  - `~/.claude/projects/*.jsonl` covers Claude Code only. Tasks run through
    Codex, Kimi or another agent leave **no local trace at all**.
  - The local files record neither success nor duration nor cost.
  - A hand-off between providers (one job passed from Claude to Codex) is two
    unrelated sessions locally; only Butler's conversation_id knows they are one.

Butler is not enough on its own either: a session started directly in a
terminal with claude or codex never reaches it.
The two complement each other; the overlap is deduplicated in segment.collapse().

Environment: UMBRA_BUTLER_PAT (in your env file; see [env] in config.example.toml)
"""

import datetime as dt
import os
import urllib.parse

from .. import net, prompts

REQUIRES = ["env:UMBRA_BUTLER_PAT", "env:UMBRA_BUTLER_URL"]

# No public default: this is a service each person deploys. Hard-coding one
# domain would send someone else's data to a stranger's server, which is the
# worst default there is.
#
# ⚠️ Read inside collect(), not here: modules are imported **before** main()
# loads the env file, and a module-level constant would freeze "not set yet in
# the launchd environment" for good.
def _base() -> str:
    return os.environ.get("UMBRA_BUTLER_URL", "").rstrip("/")
PAGE = 500
MAX_PAGES = 200          # a backstop against a cursor bug paging forever
MAX_TEXT = 2000


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(dt.datetime.fromisoformat(
            iso.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def collect(since_ms: int | None = None) -> list[tuple]:
    # The shared loop passes milliseconds (like events.ts); this API wants ISO
    since = (dt.datetime.fromtimestamp(since_ms / 1000, dt.UTC)
             .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z") if since_ms else None
    if not _base():
        raise RuntimeError("UMBRA_BUTLER_URL is not set (the address of your own Butler instance)")
    pat = os.environ.get("UMBRA_BUTLER_PAT")
    if not pat:
        raise RuntimeError(
            "UMBRA_BUTLER_PAT is not set. Put it in your env file — umbra loads "
            "it itself; see [env] in config.example.toml")

    headers = {"Authorization": f"Bearer {pat}"}
    rows, cursor, pages = [], None, 0

    while pages < MAX_PAGES:
        params = {"limit": str(PAGE)}
        # Cursor first: once paging has started, since must not take part, or
        # it pulls back pages already read.
        if cursor:
            params["cursor"] = cursor
        elif since:
            params["since"] = since
        data = net.get_json(f"{_base()}/api/export/tasks?"
                            f"{urllib.parse.urlencode(params)}", headers)
        pages += 1

        for t in data.get("tasks", []):
            ts = _ts(t.get("createdAt"))
            if ts is None:
                continue
            # tasks.prompt mixes in templates injected by the orchestrator; the
            # person's own sentence can sit behind 2,000 characters of
            # preamble. Strip the wrapper; drop the task only if it is all template.
            prompt = prompts.clean(t.get("prompt") or "")
            if not prompt:
                continue
            rows.append((
                f"butler:task:{t['id']}", ts,
                t.get("durationMs"), "butler", "task",
                {"text": prompt[:MAX_TEXT],
                 # provider can be null (early tasks, or ones another agent
                 # dispatched directly)
                 "provider": t.get("provider") or t.get("source") or "?",
                 "project": t.get("projectId") or "?",
                 "title": t.get("title") or "",
                 "status": t.get("status") or "",
                 "conversation": t.get("conversationId") or "",
                 "cost_usd": t.get("costUsd"),
                 "model": t.get("model") or "",
                 # Who sent it, when a program did (Butler's dispatch API).
                 # provider is the agent that ran it, so without this a
                 # program's weekly job reads as something you type weekly.
                 "source_app": t.get("sourceApp") or ""},
            ))

        # Messages you added while a task was running — written by you too.
        # Missing them would show a long conversation as one opening line.
        for m in data.get("messages", []):
            ts = _ts(m.get("createdAt"))
            text = prompts.clean(m.get("prompt") or "")
            if ts is None or not text:
                continue
            rows.append((
                f"butler:msg:{m['id']}", ts, None, "butler", "followup",
                {"text": text[:MAX_TEXT], "task": m.get("taskId") or ""},
            ))

        cursor = data.get("nextCursor")
        if not cursor:
            break
    return rows


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "butler", "kind": None,
          "origin": ("Butler API", "agent task records")}]


def render(e) -> str | None:
    """Same shape as claude_code: both are the user talking to an AI."""
    t = (e.get("text") or e.get("title") or "").strip().replace("\n", " ")
    return f"💬 to AI: {t}" if t else None


def classify(e) -> str | None:
    """Counts as coding, but note it is excluded from MAC_ACTIVE in blocks.py:
    a butler prompt arriving during idle was sent from a phone, or is an async
    run reporting back — neither is a contradiction on this Mac."""
    return "code"
