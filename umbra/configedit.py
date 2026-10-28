"""Edit config.toml from the page without losing a single comment.

config.toml is a file people read, annotate and keep in dotfiles repos. A
settings screen that round-trips it through a TOML writer would drop every
comment and reorder every table the first time anyone changes the language —
so this edits **lines**, not a parsed document:

  · the key exists in its table          → only that line's value changes
  · the table exists, the key does not   → one line added at the table's end
  · the table does not exist             → the table is appended to the file

Then the result is parsed with tomllib and every value read back. If anything
does not come back as written — an inline table, a dotted key, a layout this
line editor does not understand — nothing is written and the caller gets the
reason. The file is replaced atomically.

Only the keys editable() lists can be set. Everything else — providers, categories,
groups, deploy — stays "Open in editor": those are structures, and
a form for them would be a worse editor than the one the person already has.
"""

import json
import os
import re
import tempfile
import tomllib

from . import config

# key -> (kind, default, constraint, what it changes)
EDITABLE = {
    "ui.lang": ("enum", "en", ["en", "zh"], "language of the page and of the prompts"),
    "ui.theme": ("enum", "system", ["system", "light", "dark"], "page theme"),
    "schedule.interval_min": ("enum", 360, [180, 360, 720, 1440],
                              "how often the background job runs"),
    "sync.infer_days": ("int", 2, (1, 14), "recent days a scheduled run re-reads"),
    "viz.raw_days": ("int", 30, (0, 3650), "days whose raw records the page embeds"),
}


def editable() -> dict:
    """EDITABLE plus every installed plugin's keys.

    Each plugin can be switched on and off. Running one stays on this Mac;
    sending is a second switch, for a plugin that SENDS — and it only sends once
    a sink is also named, which stays a config-file decision (a destination is a
    structure, not a toggle). A plugin's own SETTINGS are editable under its
    name. All of it is read off the plugins, so the core names none of them.
    """
    from . import registry
    keys = dict(EDITABLE)
    found, _ = registry.plugins()
    for name, mod in sorted(found.items()):
        what = (getattr(mod, "WHAT", None) or getattr(mod, "__doc__", None) or name)
        keys[f"plugins.{name}.enabled"] = (
            "bool", bool(getattr(mod, "ENABLED_BY_DEFAULT", False)), None,
            what.strip().split("\n")[0])
        if getattr(mod, "SENDS", False):
            keys[f"plugins.{name}.send"] = (
                "bool", False, None,
                f"also send {getattr(mod, 'SEND_WHAT', None) or 'it'} to the named sink")
        for k, decl in (getattr(mod, "SETTINGS", None) or {}).items():
            keys[f"plugins.{name}.{k}"] = tuple(decl)
    return keys

# Changing these does not take effect until something is reinstalled. The
# page says so rather than letting a setting look applied when it is not.
NEEDS = {"schedule.interval_min": "umbra schedule install"}


class Refused(ValueError):
    pass


def path() -> str:
    return os.path.join(config.CONFIG_DIR, "config.toml")


def _get(conf: dict, key: str):
    node = conf
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def current() -> dict:
    conf = config.load()
    return {
        "path": path(),
        "keys": [{"key": k, "kind": kind, "default": default,
                  "options": opt if kind == "enum" else None,
                  "min": opt[0] if kind == "int" else None,
                  "max": opt[1] if kind == "int" else None,
                  "value": _get(conf, k), "what": what,
                  "needs": NEEDS.get(k)}
                 for k, (kind, default, opt, what) in editable().items()],
    }


def _check(key: str, value):
    keys = editable()
    if key not in keys:
        raise Refused(f"{key} is not editable here — open config.toml")
    kind, _, opt, _ = keys[key]
    if value is None:
        return None            # None = remove the key, fall back to default
    if kind == "enum" and value not in opt:
        raise Refused(f"{key} must be one of {opt}")
    if kind == "bool" and not isinstance(value, bool):
        raise Refused(f"{key} must be true or false")
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise Refused(f"{key} must be a whole number")
        if not opt[0] <= value <= opt[1]:
            raise Refused(f"{key} must be between {opt[0]} and {opt[1]}")
    return value


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False)     # a valid TOML basic string


_HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")


def _set_line(lines: list[str], table: str, key: str, value) -> list[str]:
    start = end = None
    for i, ln in enumerate(lines):
        m = _HEADER.match(ln)
        if m:
            if start is not None and end is None:
                end = i
            if m.group(1).strip() == table:
                start, end = i, None
    kv = re.compile(rf"^(\s*){re.escape(key)}\s*=")
    if start is not None:
        end = len(lines) if end is None else end
        for i in range(start + 1, end):
            m = kv.match(lines[i])
            if m:
                if value is None:
                    return lines[:i] + lines[i + 1:]
                indent = m.group(1)
                return lines[:i] + [f"{indent}{key} = {_fmt(value)}\n"] + lines[i + 1:]
        if value is None:
            return lines
        last = start
        for i in range(start + 1, end):
            if lines[i].strip() and not lines[i].lstrip().startswith("#"):
                last = i
        return lines[:last + 1] + [f"{key} = {_fmt(value)}\n"] + lines[last + 1:]
    if value is None:
        return lines
    tail = [] if not lines or lines[-1].endswith("\n") else ["\n"]
    return lines + tail + [f"\n[{table}]\n", f"{key} = {_fmt(value)}\n"]


def update(changes: dict) -> dict:
    """Apply {"section.key": value} (None removes the key). All or nothing."""
    changes = {k: _check(k, v) for k, v in changes.items()}
    p = path()
    text = ""
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            text = f.read()
    lines = text.splitlines(keepends=True)
    for key, value in changes.items():
        table, _, name = key.rpartition(".")
        lines = _set_line(lines, table, name, value)
    out = "".join(lines)
    try:
        parsed = tomllib.loads(out)
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"the edit would not parse ({e}); config.toml left as it was") from None
    for key, value in changes.items():
        if _get(parsed, key) != value:
            raise Refused(f"{key} did not read back as written — config.toml "
                          "keeps it in a shape this editor does not handle; "
                          "change it in the file")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".config.", suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(out)
    if os.path.exists(p):
        os.chmod(tmp, os.stat(p).st_mode & 0o777)
    os.replace(tmp, p)
    return current()
