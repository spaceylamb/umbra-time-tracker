"""Butler, as a reference Sink.

Butler is a personal agent-task service each user deploys themselves. Umbra
shipped tied to it: two of its three lanes named it in their tables, their config
keys and their error messages, which is why neither could go out in a release —
nobody else has one.

Nothing here is special. It is one implementation of the Sink interface, sitting
beside `command` and `webhook`, and a Slack or email sink is the same amount of
work. It ships because a reference implementation that actually runs in
production is worth more than a fictional example, and because the protocol
details below are the kind of thing you only learn by being bitten.

## What goes out

An item carries what any destination needs, in terms no destination owns:

    title        what it is called
    body         the content
    key          the idempotency key
    visible      should a person see this, or is it a background job
    interactive  should it wait for a person to act on it

Butler maps those to its own flags. A Slack sink would map `visible` to a
channel or ignore it. That mapping is the sink's business, which is the point —
the plugin producing the item does not know what Butler is.

## Three traps, all found in production

**A duplicate is a fact, not a verdict.** Butler deduplicates on
externalEventKey and replays the *old* taskId with `duplicate: true`. It is
raised as sink.Duplicate carrying that receipt, and the caller decides, because
the two bundled lanes want opposite things from it. Tasks key on the proposal id,
so a duplicate means "already sent". Claims put the attempt count in the key, so
a duplicate means the key is being built wrong and the replayed task may be one
that already failed — on 2026-08-25 fifteen claims sat in that loop, with no
error, every log line green, and status bouncing between new and checking,
never converging.

**A failed send must not look sent.** Same rule the interface states, learned
here first: marking the item sent means it never goes out and nothing says so.

**Some failures end the batch.** A destination that was asked to hold an item
for review and did not is misconfigured, and every remaining item will land the
same way — each one running unattended. That is raised as sink.Fatal so the
caller stops rather than losing a day's worth of items one at a time.

**A failed task is not a finished one.** Polling has to distinguish "still
working" from "done" from "failed", because the third means the items go back in
the queue. Collapsing failed into done files an empty report as the answer.
"""

import os

# Butler dedupes on this key. The version segment is not decoration: the card
# format changed twice, and reusing a key across a format change makes Butler
# replay the old task and the new format never ships.
KEY_VERSION = "v2"


def _creds() -> tuple[str, str]:
    base = os.environ.get("UMBRA_BUTLER_URL", "").rstrip("/")
    pat = os.environ.get("UMBRA_BUTLER_PAT")
    if not base or not pat:
        # Fatal rather than per-item: with no credentials every remaining item
        # fails identically, and a loop that logs the same line per proposal
        # buries the one thing that needs doing.
        from .. import sink as S
        raise S.Fatal(
            "UMBRA_BUTLER_URL / UMBRA_BUTLER_PAT are not set. They belong in "
            "your env file, which umbra loads itself — see [env] in "
            "config.example.toml. Butler is a service you deploy yourself; "
            "there is no public default, because pointing at a stranger's "
            "server is the worst possible one.")
    return base, pat


#: How the page names this destination (sink.describe).
DESTINATION = "Butler (your inbox)"


def send(item: dict) -> str:
    """Dispatch one item. Returns the destination's id for it.

    Raises on anything short of a fresh dispatch, including a duplicate. The
    caller decides what a failure means for its own state — this only refuses to
    report a non-delivery as a delivery.
    """
    from .. import net
    base, pat = _creds()
    visible = bool(item.get("visible"))
    body = {
        "prompt": item.get("body") or "",
        "title": item.get("title") or "umbra",
        "sourceApp": "umbra",
        "externalEventKey": item["key"],
    }
    if visible:
        # Visible and not self-starting: source decides whether it is seen,
        # status decides whether it runs. A card is something to read, not a
        # task that should begin on its own.
        body["reviewGated"] = True
        body["proposed"] = True
    else:
        # A background worker: readonly means it never merges anything, and it
        # sits in a source hidden from the inbox.
        body["readonly"] = True

    data = net.post_json(f"{base}/api/external/dispatch", body,
                         {"Authorization": f"Bearer {pat}"}) or {}
    tid = data.get("taskId")
    if not tid:
        raise RuntimeError(f"Butler returned no taskId: {str(data)[:120]}")
    # An interactive item must arrive *held*. An older Butler ignored the flag
    # without complaining and let its daemon claim the task at once, so getting
    # the upgrade order wrong meant a day's worth of extracted items running
    # unattended. The first one is already gone by the time this is read — the
    # receipt only exists because it was accepted — but raising immediately
    # limits the damage to that one. Silence is not agreement: the older version
    # had no status field at all.
    if item.get("interactive") and not data.get("duplicate") \
            and data.get("status") != "proposed":
        from .. import sink as S
        raise S.Fatal(
            f"the destination did not hold this item as proposed for review "
            f"(it reported "
            f"{data.get('status') or 'no status at all'}), so it has most "
            f"likely already started. Receipt: {tid}")
    if data.get("duplicate"):
        from .. import sink as S
        raise S.Duplicate(
            f"the destination had already seen this key and replayed {tid}", tid)
    return tid


def poll(receipt: str):
    """What became of a dispatched task.

    None while it is still working — the interface's "ask again next round".
    A dict once it has settled, which distinguishes done from failed, because
    the caller has to requeue on the second and must not file an empty report as
    an answer.
    """
    from .. import net
    base, pat = _creds()
    d = net.get_json(f"{base}/api/external/tasks/{receipt}",
                     {"Authorization": f"Bearer {pat}"}) or {}
    st = d.get("status")
    if st == "done":
        return {"state": "done", "report": d.get("finalText") or d.get("text") or ""}
    if st in ("failed", "cancelled"):
        return {"state": "failed", "why": st}
    return None


def render(result: dict) -> str:
    """What came back, as text.

    Butler's answer is already prose written for a person, so there is nothing
    to reformat. A sink whose destination returns structured data would do the
    work here instead.
    """
    if not isinstance(result, dict):
        return str(result)
    if result.get("state") == "failed":
        return f"(the task {result.get('why', 'did not finish')})"
    return result.get("report") or ""


PAGE = 500
MAX_PAGES = 20          # a backstop against a cursor bug paging forever


def feedback():
    """Everything a person has acted on, in one request.

    Butler has no per-item endpoint for this, and it would be the wrong shape if
    it did: what is wanted is "what did somebody tap", not "what happened to task
    X" asked N times. sourceApp is a bandwidth filter only — a destination that
    ignored it and returned everything would still be correct here, because
    receipts we never issued are skipped.
    """
    from .. import net
    base, pat = _creds()
    headers = {"Authorization": f"Bearer {pat}"}
    out, cursor, pages = [], None, 0
    while pages < MAX_PAGES:
        q = f"sourceApp=umbra&limit={PAGE}" + (f"&cursor={cursor}" if cursor else "")
        data = net.get_json(f"{base}/api/export/tasks?{q}", headers) or {}
        pages += 1
        for t in data.get("tasks") or []:
            out.append({"receipt": t.get("id"), "result": t})
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return out
