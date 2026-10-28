"""What the Settings dialog shows, and the few things it can do.

Read side: one payload with a section per page of the dialog. Everything here
is derived from config, the environment and the files on disk — never a copy
kept somewhere else, so the dialog cannot disagree with the program.

Write side is two kinds, both narrow:
  · settings — configedit's allowlist, one line of config.toml each
  · actions  — an allowlist of named, argument-free things to do on this Mac:
    open config.toml in an editor, reveal a folder, reinstall the scheduled
    job, start a run now. Nothing here takes a path or a command from the
    request; the name picks one of these and that is all it can do.
"""

import os
import subprocess
import sys
import time

from . import (__version__, config, configedit, db, ops, paths, providers,
               registry, sink, taxonomy, viz)


def _tz() -> str:
    """IANA name when the system says it, else the abbreviation."""
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return os.environ.get("TZ") or time.tzname[0]


def _size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _plugins(conf) -> list:
    from . import plugins as bundled_dir
    found, _ = registry.plugins()
    out = []

    def bundled(m) -> bool:
        return os.path.abspath(getattr(m, "__file__", "") or "").startswith(bundled_dir.DIR)

    # The dialog's words for each are the plugin's own: TITLE and
    # WHAT, falling back to its name and docstring.
    for name in sorted(found, key=lambda n: (getattr(found[n], "ORDER", 99), n)):
        # A bundled Sink-only plugin (butler) is a destination, chosen by
        # naming it as a sink — not a switch on this page.
        if bundled(found[name]) and not registry.has_round(found[name]):
            continue
        title = getattr(found[name], "TITLE", None) or name
        what = getattr(found[name], "WHAT", None) or getattr(found[name], "__doc__", "") or ""
        spec = ((conf.get("plugins") or {}).get(name)) or {}
        out.append({"name": name, "title": title, "what": what.strip().split("\n")[0],
                    "bundled": bundled(found[name]),
                    "enabled": registry.enabled(name, conf),
                    "sink": spec.get("sink"),
                    "sink_answers": (sink.answers(spec["sink"], conf)
                                     if spec.get("sink") else None),
                    "sending": sink.enabled_for(name, conf) is not None,
                    # A second switch: also send a copy of what it makes here.
                    "can_send": bool(getattr(found[name], "SENDS", False)),
                    "send_what": getattr(found[name], "SEND_WHAT", None),
                    # What it makes is only ever sent: on means sending.
                    "send_only": bool(getattr(found[name], "SEND_ONLY", False)),
                    # Where it goes, in words rather than a config name.
                    "destination": (sink.describe(spec["sink"], conf)
                                    if spec.get("sink") else None),
                    # Why it cannot run, if it cannot (blocked), and anything
                    # else its own block on this page needs (settings_row).
                    "blocked": (found[name].blocked(conf)
                                if callable(getattr(found[name], "blocked", None)) else None),
                    **(found[name].settings_row(conf)
                       if callable(getattr(found[name], "settings_row", None)) else {})})
    return out


def _providers(conf) -> list:
    rows = []
    for name, spec in (conf.get("providers") or {}).items():
        env = spec.get("api_key_env")
        rows.append({"name": name, "kind": spec.get("kind") or "?",
                     "models": list(spec.get("models") or []), "builtin": False,
                     "key_set": bool(os.environ.get(env)) if env else None})
    rows.append({"name": "gemini", "kind": "gemini", "models": ["gemini*"],
                 "builtin": True, "key_set": bool(os.environ.get("GEMINI_API_KEY"))})
    return rows


def _categories(conf) -> dict:
    own = {k for k in (conf.get("categories") or {})}
    fams: dict = {}
    for c in taxonomy.known(conf):
        fams.setdefault(c.get("family") or "", []).append(
            {"key": c["key"], "en": c.get("en") or c["key"], "zh": c.get("zh") or c["key"],
             "own": c["key"] in own})
    order = [*taxonomy.families(), ""]
    return {"families": [{"family": f, "cats": fams[f]} for f in order if f in fams],
            "own": sorted(own),
            "groups": [{"key": g["key"], "en": g.get("en") or g["key"], "cats": g["cats"]}
                       for g in taxonomy.groups(conf).values()]}


def get() -> dict:
    conf = config.load()
    con = db.connect()
    try:
        runs = [f["last_run"] for f in db.freshness(con) if f["last_run"] and not f["live"]]
    finally:
        con.close()
    plist = os.path.expanduser(f"~/Library/LaunchAgents/{ops.LABEL_SYNC}.plist")
    from . import infer
    return {
        "config": configedit.current(),
        "general": {"tz": _tz(), "tz_abbr": time.strftime("%Z")},
        "model": {"status": providers.status(infer.MODEL, conf),
                  "providers": _providers(conf),
                  "rules": len(conf.get("rules") or [])},
        "schedule": {"installed": os.path.exists(plist) if sys.platform == "darwin" else None,
                     "platform": sys.platform,
                     "last_run": max(runs) if runs else None,
                     "stale_min": ops.stale_min()},
        "plugins": _plugins(conf),
        "categories": _categories(conf),
        "publishing": {"page": viz.OUT, "page_exists": os.path.exists(viz.OUT),
                       "deploy": bool((conf.get("deploy") or {}).get("command"))},
        "about": {"version": __version__, "license": "Apache-2.0",
                  "paths": [
                      {"key": "data", "label": "Data", "path": db.DB_DIR, "size": None},
                      {"key": "database", "label": "Database", "path": db.DB_PATH, "size": _size(db.DB_PATH)},
                      {"key": "palette", "label": "Palette", "path": taxonomy.assignment_path(),
                       "size": _size(taxonomy.assignment_path())},
                      {"key": "config", "label": "Config", "path": config.CONFIG_DIR, "size": None},
                      {"key": "logs", "label": "Logs", "path": paths.log_dir(), "size": None}]},
    }


# ── Actions ───────────────────────────────────────────────────

def _open(args: list) -> None:
    opener = ["open"] if sys.platform == "darwin" else ["xdg-open"]
    subprocess.Popen(opener + args, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _umbra(*argv: str) -> None:
    ops.spawn(list(argv), log=f"settings-{argv[0]}")


def _reveal(key: str):
    def go():
        p = {r["key"]: r["path"] for r in get()["about"]["paths"]}[key]
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p} does not exist yet")
        _open(["-R", p] if sys.platform == "darwin" and os.path.isfile(p) else [p])
    return go


def _open_config():
    p = configedit.path()
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Umbra — see config.example.toml in the repo for every key.\n")
    _open(["-t", p] if sys.platform == "darwin" else [p])


def _open_page():
    if not os.path.exists(viz.OUT):
        raise FileNotFoundError("the page has not been built yet — it is after the next run")
    _open([viz.OUT])


ACTIONS = {
    "open-config": _open_config,
    "open-page": _open_page,
    "schedule-install": lambda: _umbra("schedule", "install"),
    "run-now": lambda: _umbra("sync"),
    **{f"reveal-{k}": _reveal(k) for k in ("data", "database", "palette", "config", "logs")},
}


def act(name: str) -> dict:
    if name not in ACTIONS:
        raise KeyError(f"no such action: {name}")
    ACTIONS[name]()
    return {"done": name}
