"""A source that has stopped: one card per outage, sent through a sink.

Location once stopped arriving for over three weeks before anyone noticed. The
log said the import was fine every round — pulled 42, added 42 — because the
same old stretch was being rewritten. The page's freshness column would have
shown it, to someone who opened the page and looked at that column.

So the test is the **newest record itself** (max(ts) in events), never whether
the import succeeded: "succeeded, nothing new" is precisely what a stopped
source looks like, and the imports table shows it green the whole time.

## Which sources

Only those that declare they should have something every normal day:
`STALE_DAYS` on the source says how many quiet days mean "stopped", and
`STALE_HINT` says where to look first — almost every stop is at the far end (a
phone app, a sign-in, a permission), not in Umbra. A reading app, git or the
shell is quiet for a week for no reason at all; alerting on those teaches
people to ignore the card. `[plugins.staleness] days = {source = N}` changes
one, `0` stops watching it, and a source that never declared can be added.

A source switched off is not watched. A source that never had a single record
is not "stopped" either — it was never connected, and Settings › Sources says
what it needs.

## One card per outage

The card's key is the source and the time of its last record. While the source
stays quiet that does not move, so a round every few hours sends nothing new;
once data returns and stops again, the last record has moved and it is a new
outage. The sink ledger (sink_dispatch) remembers what went out, so this holds
for a destination that does not dedupe by key too.
"""

import time

from .. import config, registry, sources

# What the core reads off this plugin (see docs/writing-a-plugin.md).
TITLE = "Missing data alerts"
WHAT = "Alerts you when a daily source, like location or WHOOP, stops sending data."
ORDER = 4
SEND_ONLY = True              # the alert is the whole point: on means sending it

PLUGIN = "staleness"
KEY_PREFIX = "umbra:stale:v1:"
DAY_MS = 86_400_000


def _conf() -> dict:
    return config.load()


def _now() -> int:
    return int(time.time() * 1000)


def _spec(conf: dict) -> dict:
    return ((conf.get("plugins") or {}).get(PLUGIN)) or {}


def limits(conf: dict) -> dict:
    """Source name (as written into events) -> quiet days that mean stopped."""
    out = {}
    for src, mod in registry.specs().items():
        days = getattr(mod, "STALE_DAYS", None)
        if days:
            out[src] = float(days)
    for src, days in (_spec(conf).get("days") or {}).items():
        if days:
            out[src] = float(days)
        else:
            out.pop(src, None)
    off, _unset = sources.aside()
    return {s: d for s, d in out.items() if s not in off}


def _hint(src: str) -> str:
    mod = registry.spec(src)
    return (getattr(mod, "STALE_HINT", None) if mod is not None else None) or \
        "the far end: the device, the sign-in, the permission it reads with."


def _last(con, srcs) -> dict:
    if not srcs:
        return {}
    return dict(con.execute(
        "SELECT source, max(ts) FROM events WHERE source IN (%s) GROUP BY source"
        % ",".join("?" * len(srcs)), list(srcs)))


def watched(con, now_ms: int | None = None, conf: dict | None = None) -> list[dict]:
    """Every watched source: its last record (None if it never had one), how
    many days ago, its limit, and whether that makes it stopped."""
    now = _now() if now_ms is None else now_ms
    conf = _conf() if conf is None else conf
    lim = limits(conf)
    last = _last(con, lim)
    out = []
    for src in sorted(lim):
        ts = last.get(src)
        age = None if ts is None else (now - ts) / DAY_MS
        out.append({"source": src, "last_ms": ts, "limit": int(lim[src]),
                    "days": None if age is None else int(age),
                    "stopped": age is not None and age >= lim[src],
                    "hint": _hint(src)})
    return out


def stale(con, now_ms: int | None = None, conf: dict | None = None) -> list[dict]:
    """The watched sources that have stopped."""
    return [s for s in watched(con, now_ms, conf) if s["stopped"]]


def item(s: dict) -> dict:
    """The card for one outage, in the shape every sink takes."""
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_ms"] / 1000))
    return {
        "key": f"{KEY_PREFIX}{s['source']}:{s['last_ms']}",
        "title": f"Umbra: {s['source']} has had nothing new for {s['days']} days",
        "body": (f"The newest record from **{s['source']}** is from {since}, "
                 f"{s['days']} days ago. Its import may well keep saying it "
                 f"succeeded — succeeding with nothing new is what a stopped "
                 f"source looks like.\n\n"
                 f"Look first at: {s['hint']}\n\n"
                 f"Once it is back this card can be closed; the same outage "
                 f"is not sent twice."),
        "visible": True,
        "source": s["source"],
        "last_ms": s["last_ms"],
        "days": s["days"],
    }


def blocked(conf: dict | None = None) -> str | None:
    """Why this cannot run, or None. A stopped-source alert that goes nowhere is
    a log line nobody reads — the problem it exists to solve."""
    from .. import sink
    conf = _conf() if conf is None else conf
    name = _spec(conf).get("sink")
    if not name:
        return ('Needs somewhere to send alerts: name a sink in config.toml '
                '([plugins.staleness] sink = "butler", a command or a webhook).')
    if sink.answers(name, conf) is None:
        return f'The sink "{name}" is not set up — add [sinks.{name}] to config.toml.'
    return None


def sync(con, days, log=print):
    """One round: find the stopped sources, send each outage once."""
    from .. import sink as S
    conf = _conf()
    why = blocked(conf)
    if why:
        log(f"  staleness: not running — {why}")
        return
    found = stale(con, now_ms=_now(), conf=conf)
    if not found:
        return
    name = S.enabled_for(PLUGIN, conf)
    for s in found:
        it = item(s)
        item_id = f"{s['source']}:{s['last_ms']}"
        st = S.state(con, PLUGIN, item_id, name)
        if st and st["status"] in (S.SENT, S.ANSWERED):
            continue
        try:
            S.send(con, PLUGIN, item_id, it, name, now_ms=_now())
            log(f"  ⚠ staleness: {s['source']} nothing new for {s['days']} days → {name}")
        except RuntimeError as e:
            if isinstance(e.__cause__, S.Duplicate):
                # The destination already has this outage: that is sent.
                S.record(con, PLUGIN, item_id, name, S.ANSWERED,
                         external_id=e.__cause__.receipt, now_ms=_now())
            else:
                log(f"  ✗ staleness: card for {s['source']} not sent, next round "
                    f"tries again: {e.__cause__ or e}")
        con.commit()


# ── The command line (umbra stale) ───────────────────────────

CLI_NAME = "stale"
CLI_HELP = "which every-day sources have stopped sending anything new"


def show(con) -> None:
    rows = watched(con, now_ms=_now(), conf=_conf())
    if not rows:
        print("  no source is watched.")
    for r in rows:
        if r["last_ms"] is None:
            print(f"  ·  {r['source']:10s} never had data")
            continue
        since = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_ms"] / 1000))
        mark = "⚠" if r["stopped"] else "✓"
        print(f"  {mark}  {r['source']:10s} last {since} · {r['days']}d ago "
              f"· stopped after {r['limit']}d")
        if r["stopped"]:
            print(f"       look at: {r['hint']}")


def cli(args):
    from .. import db
    con = db.connect()
    try:
        show(con)
    finally:
        con.close()
