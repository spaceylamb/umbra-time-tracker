"""Git reflog importer.

Parses .git/logs/HEAD directly instead of spawning git — much faster across a
few dozen repositories.

Format: <old_sha> <new_sha> <Name> <email> <unix_ts> <tz>\t<action>: <message>

The reflog rather than the commit log, because the reflog records checkouts,
rebases and merges too: it shows the real rhythm of moving between branches,
where the commit log keeps only the end result.
"""

import glob
import os
import re

from .. import config

LINE = re.compile(r"^([0-9a-f]{40}) ([0-9a-f]{40}) (.+?) <(.*?)> (\d+) ([+-]\d{4})\t(.*)$")


def roots() -> list[str]:
    """The folders your repositories live in: `[sources.git] roots` in
    config.toml.

    No default. Guessing (~, ~/code, ~/Desktop) either finds nothing — and
    then the source says "ready" while importing zero rows, which is how this
    was first seen — or walks folders macOS guards with a permission prompt
    that a scheduled job has nobody to answer."""
    conf = (config.load().get("sources") or {}).get("git") or {}
    return [os.path.expanduser(r) for r in conf.get("roots") or []]


def check():
    rs = roots()
    if not rs:
        yield ("roots", False,
               'no folders to scan — set [sources.git] roots = ["~/code"] in config.toml')
        return
    ok = bool(_repos(rs))
    yield ("repos", ok,
           "" if ok else f"no git repositories under {', '.join(rs)} (two levels deep)")


def _repos(rs: list[str] | None = None) -> list[str]:
    found = []
    for root in roots() if rs is None else rs:
        # Two levels only: <root>/<project>/.git and <project>/<sub>/.git
        found += glob.glob(os.path.join(root, "*", ".git"))
        found += glob.glob(os.path.join(root, "*", "*", ".git"))
    return [p for p in found if os.path.isdir(p)]


def collect(since_ms: int | None = None) -> list[tuple]:
    # since_ms is accepted and ignored: this source reads a local file, and a
    # re-read takes milliseconds — there is no API quota to save. It stays so
    # every importer has the same signature and one loop can pass it by keyword.
    rows = []
    for gitdir in _repos():
        project = os.path.basename(os.path.dirname(gitdir))
        head = os.path.join(gitdir, "logs", "HEAD")
        if not os.path.exists(head):
            continue
        try:
            content = open(head, errors="replace").read()
        except OSError:
            continue
        for n, line in enumerate(content.splitlines()):
            m = LINE.match(line)
            if not m:
                continue
            new_sha, ts, action = m.group(2), int(m.group(5)), m.group(7)
            rows.append((
                f"git:{project}:{new_sha}:{n}", ts * 1000, None, "git", "reflog",
                {"project": project, "action": action[:200], "sha": new_sha[:8]},
            ))
    return rows


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "git", "kind": None,
          "origin": ("git log", "commits across local repos")}]


def render(e) -> str | None:
    return f"⎇ git {e.get('project') or ''}: {(e.get('action') or '')[:80]}"


def classify(e) -> str | None:
    return "code"
