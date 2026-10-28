"""The payloads `umbra serve` returns, as plain functions.

No HTTP here: serve.py maps paths onto these, and the contract tests call them
directly against a seeded database. Every shape is defined in
schema/api.schema.json and is the viz payload cut along viz.RESIDENT /
viz.ONDEMAND — this module adds no data of its own.
"""

import base64
import gzip
import json

from . import analyze, apps, config, db, providers, sources, taxonomy, tz, viz


class NotFound(LookupError):
    pass


def _lang(conf) -> str:
    return (conf.get("ui") or {}).get("lang", "en")


def meta(con=None) -> dict:
    con = con or db.connect()
    conf = config.load()
    lang = _lang(conf)
    from . import infer
    return {
        "model": providers.status(infer.MODEL, conf),
        "theme": (conf.get("ui") or {}).get("theme", "system"),
        "order": viz.day_list(con),
        "since": None,
        "lang": lang,
        **viz.page_fields(con),
        "labels": {
            "source": viz.LABEL,
            "origin": {k: list(v) for k, v in viz.ORIGIN.items()},
            # lane key -> the source it is drawn from, so a lane can show its
            # source's record count from Day.counts.
            "lane_source": {k: src for k, src, _ in viz.LANES},
            "block": viz.BNAME,
            # letter -> category: the page decodes bcat with this.
            "bcode": {v: k for k, v in viz.BCODE.items()},
            "app": apps.labels(lang),
            "category": taxonomy.labels(lang, conf),
        },
        "palette": {t: taxonomy.colors(conf, t) for t in ("light", "dark")},
        "groups": [{"key": g["key"], "name": g.get(lang) or g["key"],
                    "cats": g["cats"]} for g in taxonomy.groups(conf).values()],
    }


def _full(con, date: str) -> dict:
    try:
        analyze.day_bounds(date, con)
    except ValueError:
        raise NotFound(f"not a date: {date}") from None
    d = viz.day_cached(con, date, want_raw=True)
    if d is None:
        raise NotFound(f"no events on {date}")
    return d


def _unpack(blob: str):
    return json.loads(gzip.decompress(base64.b64decode(blob)))


def day(date: str, con=None) -> dict:
    con = con or db.connect()
    d = _full(con, date)
    # end and minutes are counted here, at read time, not in the cached day:
    # how much of today has passed changes every minute. The events are the
    # drawn model's, the same list the page draws.
    end = tz.elapsed_minutes(con, date)
    out = {"date": date, **{k: d[k] for k in viz.RESIDENT if k in d}, "end": end}
    evs = (d.get("ev") or {}).get(d.get("evuse")) or next(iter((d.get("ev") or {}).values()), [])
    if evs:
        out["minutes"] = viz.minutes_of(evs, (d.get("failed") or {}).get("windows", []), end)
    return out


def detail(date: str, con=None) -> dict:
    d = _full(con or db.connect(), date)
    out = {"date": date, **{k: d[k] for k in ("lanes", "bruns", "ai") if k in d}}
    if "dg" in d:
        out["dg"] = _unpack(d["dg"])
    return out


def raw(date: str, con=None) -> dict:
    d = _full(con or db.connect(), date)
    return {"date": date, "records": _unpack(d["raw"]) if "raw" in d else []}


# What each built-in source reads, in the words the first-run screen uses. A source module
# can say it itself with DESCRIPTION (user sources should); this is the
# fallback for the built-ins, which predate the attribute.
_WHAT = {
    "chrome": "pages, titles, how long each stayed open",
    "claude": "what you asked an agent — the problem you were on",
    "zsh": "commands, and the directory they ran in",
    "git": "commits, with the repo they landed in",
    "knowledgec": "which app was in front, minute by minute",
    "iphone": "what the phone was doing while the Mac slept",
    "media": "what you listened to",
    "intent": "that you talked, not what was said",
    "whoop": "sleep, workouts, recovery",
    "plaud": "what you said out loud, with times",
    "weread": "reading sessions and highlights",
    "location": "stays and trips — never raw coordinates",
    "butler": "your agents' task history",
}


def setup() -> dict:
    """GET /api/setup — every source and whether it can run here, now.

    Three-valued like sources.probe: "unknown" is Full Disk Access, which
    cannot be probed without reading a protected file; showing ready or
    missing there would be inventing an answer.
    """
    found, errors = sources.discover()
    out = []
    for name in sorted(found):
        mod = found[name]
        src = getattr(mod, "SOURCE", None) or name
        row = {"name": name, "source": src,
               "label": viz.LABEL.get(src) or viz.LABEL.get(name) or name,
               "what": getattr(mod, "DESCRIPTION", None) or _WHAT.get(name, ""),
               "needs": []}
        why = sources.skip_reason(mod)
        if why:
            row.update(state="unsupported", needs=[why])
        elif not sources.enabled(name):
            # Off by choice reads like not set up: absent, not a gap.
            row.update(state="missing", needs=["turned off in config.toml"])
        else:
            st = sources.status(mod)
            missing = [hint or req for req, ok, hint in st if ok is False]
            unknown = [hint or req for req, ok, hint in st if ok is None]
            if missing:
                row.update(state="missing", needs=missing)
            elif unknown:
                row.update(state="unknown", needs=unknown)
            else:
                row["state"] = "ready"
        out.append(row)
    for name, err in sorted(errors.items()):
        out.append({"name": name, "source": name, "label": name, "what": "", "state": "broken",
                    "needs": [str(err)[:200]]})
    return {"sources": out}


def import_progress(con=None) -> dict:
    """GET /api/import — the running import, or the last one."""
    return {"run": db.progress_latest(con or db.connect())}


def start_import(con=None, only: str | None = None) -> dict:
    """POST /api/import — run `umbra import` in the background, unless one is
    already running. The page then polls /api/import.

    only: one source's module name, for retrying the one that failed (the
    Sources page). It must be a source that exists — it goes onto a command line."""
    argv = ["import"]
    if only is not None:
        found, _ = sources.discover()
        if only not in found:
            raise NotFound(f"no such source: {only}")
        argv += ["--only", only]
    p = db.progress_latest(con or db.connect())
    if p and p["running"]:
        return {"started": False}
    from . import ops
    ops.spawn(argv, log="import-now")
    return {"started": True}


def said(date: str, con=None) -> dict:
    """GET /api/day/{date}/said — what was said aloud that day.

    Whatever the enabled plugins put in the drawer: each that declares
    `said(con, date, conf)` adds its part (the tasks lane its tasks, the claims
    lane its claims). A switched-off plugin adds nothing, so its tab is gone.
    The core names none of them.
    """
    con = con or db.connect()
    try:
        analyze.day_bounds(date, con)
    except ValueError:
        raise NotFound(f"not a date: {date}") from None
    from . import registry
    conf = config.load()
    out = {"date": date}
    found, _ = registry.plugins()
    for name, mod in sorted(found.items()):
        fn = getattr(mod, "said", None)
        if callable(fn) and registry.enabled(name, conf):
            out.update(fn(con, date, conf))
    return out


def said_mark(kind: str, item_id: int, body: dict, con=None) -> dict:
    """POST /api/said/{kind}/{id} — the page's ✓, ✕ or Seen on one item.

    Goes to the plugin whose SAID_KIND it is ("task", "claim"); that plugin
    reads the body and says whether it took. NotFound for a kind nobody owns or
    an item the plugin does not have."""
    from . import registry
    found, _ = registry.plugins()
    mod = next((m for m in found.values() if getattr(m, "SAID_KIND", None) == kind
                and callable(getattr(m, "said_mark", None))), None)
    if mod is None:
        raise NotFound(f"nothing here keeps {kind}s")
    try:
        return mod.said_mark(con or db.connect(), item_id, body)
    except LookupError as e:
        raise NotFound(str(e)) from None


def correct(date: str, body: dict, con=None) -> dict:
    """POST /api/day/{date}/correct — {"span": "18:00-19:00" | "a", "b",
    "cat", "text"?}, or {"undo": said_at} to take one back. Your word for a stretch of the day (see correct.py): the
    page draws it at once, and the next round re-asks the model about the
    windows it covers. ValueError for one that does not read — nothing is
    stored."""
    from . import correct as C
    con = con or db.connect()
    try:
        analyze.day_bounds(date, con)
    except ValueError:
        raise NotFound(f"not a date: {date}") from None
    if body.get("undo") is not None:
        if not C.withdraw(con, date, int(body["undo"])):
            raise NotFound(f"no correction said at {body['undo']} on {date}")
        return {"ok": True}
    if body.get("span"):
        a, b = C.parse_span(body["span"])
    elif "a" in body and "b" in body:
        a, b = body["a"], body["b"]
    else:
        raise ValueError("say which stretch: span \"18:00-19:00\", or a and b in minutes")
    uid = C.save(con, date, a, b, str(body.get("cat") or ""), str(body.get("text") or ""))
    return {"ok": True, "uid": uid}


def config_get() -> dict:
    """GET /api/config — the settings the page may change, and their values."""
    from . import configedit
    return configedit.current()


def config_set(changes: dict) -> dict:
    """POST /api/config — {"section.key": value}; None resets to the default."""
    from . import configedit
    return configedit.update(changes)


_WEEK = __import__("re").compile(r"^\d{4}-W\d{2}$")


def week(wk: str, con=None) -> dict:
    """GET /api/week/{2026-W37} — the week's days, its priorities, and how
    much time the model credited to each.

    Day payloads are not repeated here: the page already fetches days by
    date and caches them, and the week is seven of those. What only exists
    per week — the priorities and their attribution — comes from here.

    done lists the days whose attribution is current for these priorities.
    A day not in it is unasked, not zero: "0.0 h" must not read as an answer
    to a question nobody has asked yet (goals.done_days).
    """
    import datetime as dt

    from . import goals as G, infer
    if not _WEEK.match(wk):
        raise NotFound(f"not an ISO week: {wk}")
    try:
        mon = dt.date.fromisocalendar(int(wk[:4]), int(wk[6:]), 1)
    except ValueError:
        raise NotFound(f"not an ISO week: {wk}") from None
    con = con or db.connect()
    days = [(mon + dt.timedelta(i)).isoformat() for i in range(7)]
    texts = G.load_goals(con, wk)
    gdig = G.gdigest(texts)
    model = infer.MODEL
    attrib = {d: G.load_attrib(con, d, model, gdig) for d in days} if texts else {}
    targets = G.load_targets(con, wk)
    per_goal = []
    for i in range(1, len(texts) + 1):
        rows = [r for d in days for r in attrib.get(d, []) if r["g"] == i]
        by_day = {}
        for d in days:
            got = [r for r in attrib.get(d, []) if r["g"] == i]
            if got:
                by_day[d] = G.sessions(got)
        t = targets[i - 1] if i - 1 < len(targets) else None
        per_goal.append({"text": texts[i - 1],
                         "target": t[0] if t else None,
                         "unit": t[1] if t else None,
                         "minutes": sum(r["b"] - r["a"] for r in rows),
                         "sessions": sum(by_day.values()),
                         "session_days": sorted(by_day)})
    return {"week": wk, "days": days, "goals": texts, "gdig": gdig,
            "model": model, "priorities": per_goal,
            "attrib": {d: rows for d, rows in attrib.items() if rows},
            "totals": {str(k): v for k, v in G.totals(con, days, model, gdig).items()}
                      if texts else {},
            "done": sorted(G.done_days(con, days, model, gdig)) if texts else [],
            "judging": judging(wk),
            "judge_failed": wk in _JUDGE_FAILED,
            "judge_error": judge_error(wk)}


MAX_GOALS = 3

# Weeks being judged right now, by the process judging them. Held here rather
# than in a file because this server is the only thing that starts them, and
# keeping the Popen is what lets poll() reap a finished child: a pid checked
# with os.kill(pid, 0) still answers for a zombie nobody has waited on, and the
# page would show "judging" forever.
_JUDGING: dict = {}
# Weeks whose last judging run ended badly — most often no model key, or the
# provider refusing it. Without this the page would go from "judging" to "not
# yet judged" and nothing would say the attempt had happened and failed.
_JUDGE_FAILED: set = set()


def judging(wk: str) -> bool:
    p = _JUDGING.get(wk)
    if p is None:
        return False
    code = p.poll()
    if code is not None:
        del _JUDGING[wk]
        if code:
            _JUDGE_FAILED.add(wk)
        return False
    return True


def start_judging(wk: str) -> bool:
    """Judge one week's days against its priorities now, in the background.

    This is the reason attribution once ran on Cloudflare: pressing Save had to
    answer in seconds, not at the next scheduled round, hours away. A local
    server makes that possible without the edge. The page polls /api/week
    while `judging` is true.

    Nothing happens when the goals plugin is switched off — the person turned
    judging off, and saving a priority is not asking for it back.
    """
    from . import registry
    if judging(wk) or not registry.enabled("goals"):
        return False
    from . import ops
    _JUDGE_FAILED.discard(wk)
    _JUDGING[wk] = ops.spawn(["plugins", "run", "goals", "--week", wk],
                             log=f"judge-{wk}")
    return True


def judge_error(wk: str) -> str | None:
    """Why the last judging run for this week failed, from its own output —
    not a guess. A missing key, a refused one, a model name that does not
    exist and a bug all used to read "the model could not be reached"."""
    if wk not in _JUDGE_FAILED:
        return None
    from . import ops
    return ops.last_line(f"judge-{wk}") or "it exited without saying why"


def set_goals(wk: str, texts, con=None) -> dict:
    """POST /api/week/{week}/goals — {"goals": [...]}, at most three.

    Each item is a string, or {"text", "target", "unit"}. The time is still
    what the model credits (decided 2026-09-28); a target only says how much
    of it the week is aiming for.

    Three is a limit, not a suggestion (cmd_goals): a fourth "most important
    thing" means none of them is. An empty list clears the week.

    New wording starts judging the week at once (start_judging) rather than at
    the next scheduled round.
    """
    import time

    from . import goals as G
    if not _WEEK.match(wk):
        raise NotFound(f"not an ISO week: {wk}")
    if not isinstance(texts, list):
        raise ValueError("goals must be a list")
    items = []
    for t in texts:
        if isinstance(t, str):
            t = {"text": t}
        if not isinstance(t, dict) or not isinstance(t.get("text"), str):
            raise ValueError("each priority needs a text")
        if not t["text"].strip():
            continue
        target, unit = t.get("target"), t.get("unit")
        if target is not None:
            if isinstance(target, bool) or not isinstance(target, (int, float)) or target <= 0:
                raise ValueError("a target must be a positive number")
            if unit not in G.UNITS:
                raise ValueError(f"unit must be one of {list(G.UNITS)}")
        items.append((t["text"].strip(), (float(target), unit) if target is not None else None))
    if len(items) > MAX_GOALS:
        raise ValueError(f"at most {MAX_GOALS} priorities a week — you gave {len(items)}")
    con = con or db.connect()
    before = G.gdigest(G.load_goals(con, wk))
    G.save_goals(con, wk, [x for x, _ in items], now_ms=int(time.time() * 1000))
    G.set_targets(con, wk, [t for _, t in items])
    # Only new wording is a new question. A changed target re-judges nothing —
    # it sits outside the goal digest on purpose — so there is nothing to start.
    if items and G.gdigest([x for x, _ in items]) != before:
        start_judging(wk)
    return week(wk, con)
