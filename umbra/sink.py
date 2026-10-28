"""Where Umbra's output goes, and what may come back.

A Sink is the only interface with an effect outside this machine. Everything
else reads local data, calls a model, and writes rows; a Sink creates a card in
someone's inbox, a message in a channel, a line in a file.

The contract is three functions, one required:

    send(item)        -> receipt | None    what leaves Umbra
    poll(receipt)     -> result | None     optional: what came of one item
    feedback()        -> [{receipt, result}]  optional: everything that changed
    render(result)    -> str               optional: how it reads

`poll` asks about one item; `feedback` asks what changed at all. A destination
usually offers one or the other, and which it offers is a fact about it rather
than a choice — a task queue can answer "what happened to task X", while
somewhere a person taps buttons reports in bulk and has no per-item endpoint at
all. Polling that one item by item is N requests for something it will hand over
in one.

A destination that cannot answer simply does not implement `poll`. That is the
whole difference between a Slack webhook and a backend with an agent behind it —
not two interfaces, one interface with an optional half. An earlier cut of this
split the interface by whether the destination had agent capability, which put
the implementer's business in the contract; what a destination can do is theirs
to decide, and all this layer needs to describe is what goes out and what may
return.

## Two gates, not one

Analysis is local, costs cents, and has no side effects. Sending has effects in
somebody else's system: verification runs to a few dollars a day, and task
dispatch creates roughly twenty cards. Enabling the first must never enable the
second — installing something and having it immediately start writing into your
inbox is a decision made on your behalf before you have seen one result.

## Failure leaves state alone

A send that raises does not mark the item sent. Marking it means it never goes
out and nothing says so — the quiet failure this codebase keeps finding. The
attempt is counted so a destination that rejects the same item forever is
visible rather than retried invisibly.
"""

import json
import os

SCHEMA = """
-- What was sent where, and what came back.
--
-- Deliberately not in analyzer_rows. Analyzer output is written once; this has
-- a lifecycle and is updated repeatedly. The existing `claims` table mixes the
-- two -- claim, quote, kind, verdict on one side and status, check_task,
-- report, attempt on the other -- and that mixture is exactly what stops it
-- being generalizable as it stands.
CREATE TABLE IF NOT EXISTS sink_dispatch (
    plugin      TEXT NOT NULL,
    item_id     TEXT NOT NULL,   -- the plugin's own id for the thing it sent
    sink        TEXT NOT NULL,
    external_id TEXT,            -- the receipt, when the destination gave one
    status      TEXT NOT NULL,   -- pending | sent | answered | failed
    result      TEXT,            -- what came back, when anything did
    attempt     INTEGER NOT NULL DEFAULT 0,
    at_ms       INTEGER NOT NULL,
    PRIMARY KEY (plugin, item_id, sink)
);
CREATE INDEX IF NOT EXISTS idx_sink_status ON sink_dispatch(plugin, status);
"""

PENDING, SENT, ANSWERED, FAILED = "pending", "sent", "answered", "failed"


class Duplicate(RuntimeError):
    """The destination had already seen this key, and replayed its old receipt.

    A fact, not a verdict. What it means depends on how the sender builds its
    key, and the two bundled lanes want opposite things from it: tasks key on the
    proposal id, so a duplicate means "already sent, nothing to do"; claims put
    the attempt count in the key, so a duplicate means the key logic is broken
    and the replayed task may be one that already failed. The sink reports; the
    caller decides.
    """

    def __init__(self, message, receipt=None):
        super().__init__(message)
        self.receipt = receipt


class Fatal(RuntimeError):
    """This send failed in a way that makes the next one pointless.

    A network blip is per-item: skip it and the rest of the batch still goes. A
    misconfigured destination is not — every remaining item will land the same
    way, and for a destination that was supposed to hold items for review and
    did not, each one that lands is something running unattended. Callers let
    this one out of the loop.
    """


# ── Built-in sinks ────────────────────────────────────────────

def _command(spec):
    """Run a shell command, with the item in the environment.

    **The item is never interpolated into the command string.** It is model
    output and a transcript quote; putting either on a command line hands a
    shell whatever the model happened to say. Environment variables carry no
    such risk, and the config example shows the quoting.
    """
    cmd = spec.get("command")
    if not cmd:
        raise RuntimeError(
            'sink kind "command" needs a command: '
            '[sinks.<name>] kind = "command", command = "..."')

    def send(item):
        import subprocess
        env = {**os.environ, "UMBRA_ITEM": json.dumps(item, ensure_ascii=False)}
        for k, v in item.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                env[f"UMBRA_{k.upper()}"] = "" if v is None else str(v)
        r = subprocess.run(["/bin/sh", "-c", cmd], env=env,
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(
                f"sink command exited {r.returncode}: {r.stderr.strip()[:200]}")
        return (r.stdout.strip() or None) and r.stdout.strip()[:400]
    return {"send": send}


def _webhook(spec):
    url = spec.get("url")
    if not url:
        raise RuntimeError(
            'sink kind "webhook" needs a url: '
            '[sinks.<name>] kind = "webhook", url = "https://..."')

    def send(item):
        from . import net
        net.post_json(url, item)
        return None                # a webhook acknowledges; it does not answer
    return {"send": send}


KINDS = {"command": _command, "webhook": _webhook}


# ── Resolution ────────────────────────────────────────────────

def resolve(name: str, conf: dict | None = None) -> dict:
    """Sink name -> {"send": fn, "poll": fn?, "feedback": fn?, "render": fn?}.

    A name resolves to a built-in kind configured under [sinks.<name>], or to a
    plugin module that declares send(). Plugins win, on the same rule that lets a
    user source override a built-in: whoever wrote the file in your own directory
    meant it.
    """
    from . import config, registry
    conf = config.load() if conf is None else conf

    found, _errors = registry.plugins()
    mod = found.get(name)
    if mod is not None and callable(getattr(mod, "send", None)):
        return {k: getattr(mod, k)
                for k in ("send", "poll", "feedback", "render")
                if callable(getattr(mod, k, None))}

    spec = (conf.get("sinks") or {}).get(name)
    if not spec:
        raise RuntimeError(
            f"no sink named {name!r}. Add one to ~/.config/umbra/config.toml:\n"
            f"  [sinks.{name}]\n"
            '  kind = "command"          # or "webhook"\n'
            '  command = "cat >> ~/umbra-out.txt"\n'
            "or drop a module declaring send() into ~/.config/umbra/plugins/.")
    make = KINDS.get(spec.get("kind"))
    if make is None:
        raise RuntimeError(
            f"sink {name!r} has unknown kind {spec.get('kind')!r}; "
            f"available: {', '.join(sorted(KINDS))}")
    return make(spec)


def answers(name: str, conf: dict | None = None) -> bool | None:
    """Can this sink say what came of one item (poll)? None when the name does
    not resolve.

    The question fact-checking turns on: verification is something the
    destination does and reports back, so a sink that only receives — a
    command, a webhook — takes the claims and nothing ever returns. Settings
    has to say that before the switch is on, not after a week of silence.
    """
    try:
        impl = resolve(name, conf)
    except Exception:                                   # noqa: BLE001
        return None
    return callable(impl.get("poll"))


def enabled_for(plugin: str, conf: dict | None = None) -> str | None:
    """Which sink this plugin sends to, or None when sending is off.

    Off unless both a sink is named and sending is switched on. Two keys rather
    than one because naming a destination and authorising delivery to it are
    different decisions — configuring a sink to see what would go out should not
    start it going out.

    A plugin whose only output is what it sends (SEND_ONLY: an alert, a monthly
    list) has one switch: being on is the decision to send, and it cannot be on
    without a sink (its blocked() says so). A second switch under it would let
    "on" mean "on and doing nothing you can see".
    """
    from . import config, registry
    conf = config.load() if conf is None else conf
    spec = ((conf.get("plugins") or {}).get(plugin)) or {}
    mod = registry.plugins()[0].get(plugin)
    if not spec.get("send") and not getattr(mod, "SEND_ONLY", False):
        return None
    return spec.get("sink") or None


def describe(name: str, conf: dict | None = None) -> str | None:
    """Where a sink actually sends, in words, for the page. A sink's name is
    whatever someone typed in config.toml — "to notes" says nothing. None when
    the name does not resolve.

    A webhook is named by its host only: the rest of the URL is often the secret.
    """
    from urllib.parse import urlparse

    from . import config, registry
    conf = config.load() if conf is None else conf
    mod = registry.plugins()[0].get(name)
    if mod is not None and callable(getattr(mod, "send", None)):
        return getattr(mod, "DESTINATION", None) or name
    spec = (conf.get("sinks") or {}).get(name) or {}
    if spec.get("kind") == "command" and spec.get("command"):
        cmd = " ".join(str(spec["command"]).split())
        return "the command " + (cmd if len(cmd) <= 68 else cmd[:67] + "…")
    if spec.get("kind") == "webhook" and spec.get("url"):
        return f"the webhook at {urlparse(spec['url']).hostname or spec['url']}"
    return None


# ── Dispatch state ────────────────────────────────────────────

def _now():
    import time
    return int(time.time() * 1000)


def record(con, plugin: str, item_id: str, name: str, status: str,
           external_id=None, result=None, now_ms=None) -> None:
    """Write one dispatch's state, counting the attempt."""
    con.execute(
        "INSERT INTO sink_dispatch "
        "(plugin, item_id, sink, external_id, status, result, attempt, at_ms) "
        "VALUES (?,?,?,?,?,?,1,?) "
        "ON CONFLICT(plugin, item_id, sink) DO UPDATE SET "
        "  external_id = COALESCE(excluded.external_id, sink_dispatch.external_id),"
        "  status      = excluded.status,"
        "  result      = COALESCE(excluded.result, sink_dispatch.result),"
        "  attempt     = sink_dispatch.attempt + 1,"
        "  at_ms       = excluded.at_ms",
        (plugin, item_id, name, external_id, status,
         None if result is None else json.dumps(result, ensure_ascii=False),
         _now() if now_ms is None else now_ms))
    con.commit()


def state(con, plugin: str, item_id: str, name: str) -> dict | None:
    row = con.execute(
        "SELECT external_id, status, result, attempt FROM sink_dispatch "
        "WHERE plugin=? AND item_id=? AND sink=?",
        (plugin, item_id, name)).fetchone()
    if not row:
        return None
    ext, status, result, attempt = row
    return {"external_id": ext, "status": status, "attempt": attempt,
            "result": None if result is None else json.loads(result)}


def pending(con, plugin: str, status: str = SENT) -> list[dict]:
    """Dispatches waiting on an answer, for a caller about to poll."""
    return [{"item_id": i, "sink": s, "external_id": e, "attempt": a}
            for i, s, e, a in con.execute(
                "SELECT item_id, sink, external_id, attempt FROM sink_dispatch "
                "WHERE plugin=? AND status=? ORDER BY at_ms", (plugin, status))]


def send(con, plugin: str, item_id: str, item: dict, name: str,
         impl: dict | None = None, now_ms=None) -> bool:
    """Send one item. Returns whether it went.

    A raise leaves the row at `failed` with the attempt counted, never at
    `sent` — marking a failed send as sent means the item never goes out and
    nothing says so.

    A sink with no `poll` lands at `answered`: nothing is coming, so leaving it
    at `sent` would grow a queue of things waiting forever on a destination that
    was never going to reply.
    """
    impl = impl or resolve(name)
    try:
        receipt = impl["send"](item)
    except Exception as e:                                  # noqa: BLE001
        record(con, plugin, item_id, name, FAILED, now_ms=now_ms)
        raise RuntimeError(f"sink {name!r} failed to send {item_id}: {e}") from e
    terminal = SENT if callable(impl.get("poll")) else ANSWERED
    record(con, plugin, item_id, name, terminal,
           external_id=None if receipt is None else str(receipt), now_ms=now_ms)
    return True


def collect(con, plugin: str, name: str, impl: dict | None = None,
            now_ms=None) -> int:
    """Take a destination's bulk report of what changed. Returns how many landed.

    The counterpart to poll, for a destination that reports in bulk. Receipts it
    names that we never sent are skipped silently: it is reporting on everything
    it knows, and most of that is not ours.
    """
    impl = impl or resolve(name)
    fn = impl.get("feedback")
    if not callable(fn):
        return 0
    known = {r["external_id"]: r["item_id"] for r in pending(con, plugin)
             if r["sink"] == name and r["external_id"]}
    got = 0
    for entry in fn() or []:
        item_id = known.get(entry.get("receipt"))
        if item_id is None:
            continue
        record(con, plugin, item_id, name, ANSWERED,
               result=entry.get("result"), now_ms=now_ms)
        got += 1
    return got


def poll(con, plugin: str, name: str, impl: dict | None = None,
         now_ms=None) -> int:
    """Collect answers for everything still waiting. Returns how many arrived.

    A sink with no `poll` has nothing to collect and returns zero rather than
    raising: asking a webhook for its answer is a reasonable thing for a caller
    to do blindly, and the honest reply is "none".
    """
    impl = impl or resolve(name)
    fn = impl.get("poll")
    if not callable(fn):
        return 0
    got = 0
    for row in pending(con, plugin):
        if row["sink"] != name:
            continue
        answer = fn(row["external_id"])
        if answer is None:
            continue                       # still working; ask again next round
        record(con, plugin, row["item_id"], name, ANSWERED, result=answer,
               now_ms=now_ms)
        got += 1
    return got
