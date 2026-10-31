"""Fact-checking: claims said aloud in conversation → verified online → a daily card.

The second voice lane, beside actions (tasks). It reads the same recordings in
the same 30-minute windows, and **shares nothing else** — its own findings, its
own prompt, its own calls. The reason is the one that kept task extraction from
riding on inference: one call doing "find claims" and "find tasks" does both
worse, and neither half can then be cached on its own.

## Two stages, because checking 27 claims a day online is not affordable

    ①  extract claims, and have the model judge each from memory first
       cheap, local: about 27 a day
    ②  send only what the filter kept, for verification with a live lookup
       expensive: about 7 a day, $3-4

## The filter was measured, not guessed

A week of claims, about half of them actually verified online:

  · Claims with a threshold number, an effective date, an exception clause or a
    changing price (absolute) that the offline model called fine were **wrong
    33% of the time** (44% counting "right conclusion, stale explanation"), and
    10 of the 12 errors were in this group.
  · Definitions, physical constants, astronomy and settled law ("a kilowatt-hour
    is a unit of energy", "New Zealand drives on the left") were **100% right**;
    checking them is waste.

So the question is not "trust the model or not" but **trust it by category**.

It fails the other way too: the offline model treated anything from the weeks
before a recording as invented — a 22% false-positive rate, including three
real acquisitions and a product launch. The most telling case: it worked out
that an anniversary "should fall in 2026" without knowing that it was 2026. So
the prompt carries today's date and reminds the model its knowledge has a
cutoff, so that "I haven't heard of this" lands on unsure, not likely_wrong.

## Multi-speaker windows only

A stretch of one person dictating to an agent contains no claims made to
anyone — measured, those windows yield nothing but travel plans.
"""

import time

from ... import analyzer, config
from .prompts import AFTER_MARK, CARD_PROMPT_EN, CARD_PROMPT_ZH, CHECK_TAIL_EN, CHECK_TAIL_ZH, LOOKUP_PROMPT_EN, LOOKUP_PROMPT_ZH, PROMPT_EN, PROMPT_ZH, TARGET_MARK, VERDICT_ZH, _prompt, card_prompt, lookup_prompt  # noqa: F401  (re-exported)
from .repeats import DUP_CLAIM, DUP_MIN_CHARS, DUP_NEAR_MIN, DUP_QUOTE, _PUNCT, _SUSPICION, _claim_sim, _minute, _quote_sim, near_dup  # noqa: F401  (re-exported)
from .store import DIGEST_TABLE, PLUGIN, _LEGACY_COLS, _LIFECYCLE, _deliver, _write, load_digests, records  # noqa: F401  (re-exported)
from .judge import FOUND, KINDS, VERDICTS, _found, _norm, _unlinked, owns_quote  # noqa: F401  (re-exported)

# What the core reads off this plugin (see docs/writing-a-plugin.md).
TITLE = "Fact-check"
WHAT = "Checks facts said in your recordings against the web."
ORDER = 3
SENDS = True                  # off until a sink is named and sending switched on
SEND_WHAT = "the day's results"
LEGACY_SECTION = True         # a [claims] section predates plugins
SAID_KIND = "claim"           # what the drawer's Seen acts on (said_mark)

MIN_SPEAKERS = 2          # multi-speaker windows only
WORKERS = analyzer.WORKERS   # one number, owned by analyzer.py
CONTEXT_MIN = 0           # a claim must stand on its own, with no context outside the window
BATCH = 60                # at most this many claims in one verification job
ONE_WAY_DAYS = 7          # how far back a one-way send reaches (see forward)
LOOKUP_DAYS = 7           # how far back a lookup reaches (see lookup)
MAX_LOOKUPS = 20          # lookups a day, unless [plugins.claims] max_lookups says otherwise
MAX_ATTEMPTS = 3          # failed lookups before a claim is set aside
MAX_SOURCES = 3           # sources kept per claim
# The model each provider looks claims up with unless [plugins.claims]
# lookup_model names another — the cheapest of each that searches, measured
# 2026-10-24 (gemini and gpt ten of ten on claims with known answers).
LOOKUP_MODELS = {"gemini": "gemini-3.8-flash", "gpt": "gpt-6-luna",
                 "claude": "claude-sonnet-5-5"}
# How each provider's searches are billed (2026-10-24), for Settings to say.
SEARCH_PRICE = {"gemini": "searches free for 5,000 a month",
                "gpt": "$10 per 1,000 searches", "claude": "$10 per 1,000 searches"}

# What Settings may change, as plugins.claims.<key> (see configedit.editable).
SETTINGS = {
    "lookup": ("enum", "model", ["model", "sink"],
               "who looks claims up: a model that searches, or a sink that reports back"),
    "lookup_model": ("enum", LOOKUP_MODELS["gemini"], list(LOOKUP_MODELS.values()),
                     "the model that looks claims up"),
    "max_lookups": ("int", MAX_LOOKUPS, (1, 200), "lookups a day at most"),
}


def _conf() -> dict:
    return config.load()


def _sink_name() -> str | None:
    """Which destination this lane sends to, or None when sending is off.

    Both halves of the gate live in sink.enabled_for: a destination has to be
    named *and* sending switched on. Naming one to see what would go out must
    not start it going out.

    The old [claims] check = "butler" key is still honoured, because someone
    upgrading should not silently stop verifying. It names one destination in
    config, which is what this whole change is undoing, so it is the compatible
    path rather than the documented one.
    """
    from ... import sink as S
    name = S.enabled_for("claims", _conf())
    if name:
        return name
    legacy = ((_conf().get("claims") or {}).get("check") or "").strip()
    return legacy or None


def lookup_mode() -> str:
    """Who looks claims up: "model" (this lane asks a model that searches) or
    "sink" (a destination with an agent behind it does, and reports back).

    There is no "off". The plugin is the feature, and it is off until switched
    on; a list of the model's guesses from memory is not what anyone turns a
    fact-checker on for. `[plugins.claims] lookup = "sink"` asks for the sink.
    Without it, a named sink that reports back means "sink" — that is how
    verification worked before the lane could look things up itself, and
    upgrading must not change it. A sink that only receives (a command, a
    webhook) cannot look anything up: the model does, and it gets the card.
    """
    from ... import sink as S
    spec = ((_conf().get("plugins") or {}).get(PLUGIN)) or {}
    if spec.get("lookup") in ("model", "sink"):
        return spec["lookup"]
    name = _sink_name()
    return "sink" if name and S.answers(name, _conf()) else "model"


def lookup_model() -> str | None:
    """The model that looks claims up: the one `lookup_model` names, else the
    first of LOOKUP_MODELS whose key is in the environment. None when no model
    that searches can be called."""
    from ... import providers
    conf = _conf()
    named = (((conf.get("plugins") or {}).get(PLUGIN)) or {}).get("lookup_model")
    if named:
        return named
    for m in LOOKUP_MODELS.values():
        if providers.status(m, conf).get("key_set"):
            return m
    return None


def blocked(conf: dict | None = None) -> str | None:
    """Why this plugin cannot run, or None. Either it works or it says what it
    is missing — a fact-checker that cannot check does not half-run."""
    if lookup_mode() == "sink":
        return None if _sink_name() else \
            'lookup = "sink" needs a sink that reports back, named in config.toml'
    if lookup_model() is None:
        return ("Needs a model that can search — add GEMINI_API_KEY, "
                "OPENAI_API_KEY or ANTHROPIC_API_KEY to the key file.")
    return None


def settings_row(conf: dict) -> dict:
    """This plugin's block in Settings › Plugins: who looks claims up, each
    provider with its key status, and the daily cap. A named sink that reports
    back joins the choices; a lookup_model named in config.toml that is none of
    LOOKUP_MODELS shows as "custom" rather than being overwritten."""
    from ... import providers
    from ... import sink as S
    choices = []
    for value, model in LOOKUP_MODELS.items():
        st = providers.status(model, conf)
        choices.append({"value": value, "label": {"gemini": "Gemini", "gpt": "OpenAI",
                                                  "claude": "Claude"}[value],
                        "model": model, "note": SEARCH_PRICE[value],
                        "key_env": st.get("key_env"), "key_set": st.get("key_set")})
    name = _sink_name()
    if name and S.answers(name, conf):
        choices.append({"value": "sink", "label": name, "model": None,
                        "note": f"{name} looks them up and reports back",
                        "key_env": None, "key_set": None})
    spec = ((conf.get("plugins") or {}).get(PLUGIN)) or {}
    if lookup_mode() == "sink":
        using, model = "sink", None
    else:
        model = lookup_model()
        using = next((c["value"] for c in choices if c["model"] == model),
                     "custom" if model else "gemini")
    return {"lookup": {"using": using, "model": model, "choices": choices,
                       "cap": int(spec.get("max_lookups", MAX_LOOKUPS))}}


def check_enabled() -> bool:
    """Can claims be looked up? Running the plugin means looking them up; this
    is False only when it cannot (see blocked)."""
    return blocked() is None


def _self():
    import sys
    return sys.modules[__name__]


# ── Scanning a day ────────────────────────────────────────────

def _bridge_cache(con, day: str, model: str) -> int:
    """Carry this lane's old cache (claim_digests) over to the core's.

    Where the old cache answered exactly the text a window renders to now, the
    window is recorded as answered. Asking again would pay to extract the same
    claims, worded a little differently, and keep both.
    """
    old = load_digests(con, day, model)
    if not old:
        return 0
    n = 0
    for w in analyzer.windows(con, _self(), day, model):
        prev = old.get(w["a"])
        if not prev or prev[0] != w["text"] or not prev[1]:
            continue
        if con.execute("SELECT 1 FROM analyzer_runs WHERE plugin=? AND day=? "
                       "AND model=? AND win_a=?",
                       (PLUGIN, day, model, w["a"])).fetchone():
            continue
        analyzer.mark_ran(con, PLUGIN, day, model, w["a"], w["digest"])
        n += 1
    return n


def scan(con, day: str, model: str | None = None, call=None, log=None,
         fresh: bool = False) -> dict:
    """Scan one day's multi-speaker stretches. Returns {"fresh", "windows",
    "fresh_calls", "reused", "failed"} — the shape this lane always returned."""
    from ... import db
    model = model or analyzer.model_for(PLUGIN)
    db.items_after_legacy(con)
    if not fresh:
        _bridge_cache(con, day, model)
    r = analyzer.run(con, PLUGIN, _self(), day, model, call=call, log=log,
                     fresh=fresh)
    ids = set(r.get("ids") or [])
    new = [x for x in records(con, day=day) if x["src"] == "item" and x["id"] in ids]
    dup = _mark_repeats(con, day, new)
    new = [x for x in new if x["id"] not in dup]
    return {"fresh": new, "windows": r["called"],
            "fresh_calls": r["called"] - r["failed"], "reused": r["reused"],
            "failed": r["failed"], "dup": len(dup)}


def _mark_repeats(con, day: str, new: list[dict]) -> set:
    """Hold this round's repeats of earlier rounds as `duplicate`; return their ids.

    Compared **only with earlier rounds**: two items the model split in one round
    are split on purpose. A repeat is never deleted — findings accumulate — it
    just leaves the checking queue and the drawer's count.

    Holding back the repeat must not hold back the check: when this round is more
    doubtful and the earlier one is not yet sent, the earlier one's first verdict
    is raised. One already checking or checked is left alone — its report was
    written against the old verdict.
    """
    if not new:
        return set()
    ids = {x["id"] for x in new}
    prev = [r for r in records(con, day=day)
            if not (r["src"] == "item" and r["id"] in ids)
            and r["status"] != "duplicate"]
    dup = set()
    for x in new:
        old = near_dup(prev, x["t"], x["claim"], x["quote"])
        if old is None:
            continue
        dup.add(x["id"])
        _write(con, x, "duplicate", dup_of=old["id"])
        if old["status"] == "new":
            v_old = _norm(old["verdict"], VERDICTS, "unsure")
            v_new = _norm(x["verdict"], VERDICTS, "unsure")
            verdict = max(v_old, v_new, key=_SUSPICION.get)
            absolute = max(old["absolute"] or 0, x["absolute"] or 0)
            if old["src"] == "legacy":
                con.execute("UPDATE claims SET verdict=?, absolute=? WHERE id=?",
                            (verdict, absolute, old["id"]))
            else:
                _write(con, old, verdict=verdict, absolute=absolute)
    con.commit()
    return dup


# ── The filter ────────────────────────────────────────────────

def checkable(con, limit: int | None = None, day: str | None = None) -> list:
    """The claims worth paying to check online. Two rules, both from seven days
    of measurement:

      verdict != confident_right   the model itself is not sure
      absolute                     a threshold, effective date, exception or
                                   changing price — of these, 33% the offline
                                   model called fine were wrong

    The other way round, definitions and constants (absolute=0 and
    confident_right) never once missed, so they are let through.

    `day` filters **before** the limit. Taking the global top N first and then
    keeping one day starved the earliest day once later days held more than N
    absolute claims — dispatch got nothing, pending_day returned the same day
    next round, and the lane stopped with no error.
    """
    rows = [r for r in records(con, day=day, status="new")
            if (r["verdict"] or "unsure") != "confident_right" or r["absolute"]]
    rows.sort(key=lambda r: (-(r["absolute"] or 0), r["day"], r["t"] or "", r["id"]))
    return rows[:int(limit)] if limit else rows


def check_prompt(rows) -> str:
    """The verification job's text, in the configured language. Not part of any
    cache key: it is written at send time, so changing it re-runs nothing."""
    from ... import infer
    zh = infer.lang() == "zh"
    said, first, firm = (("原话", "离线初判", " / 绝对化") if zh
                         else ("Said", "Offline first pass", " / absolute"))
    body = "\n".join(
        f"{i}. [{r['day']} {r['t']} {r['speaker']}] {r['claim']}\n"
        + (f"   {said}：「{r['quote']}」\n" if zh else f"   {said}: \"{r['quote']}\"\n")
        + (f"   {first}：{r['verdict']}" if zh else f"   {first}: {r['verdict']}")
        + (firm if r["absolute"] else "")
        + (f" —— {r['why']}" if r["why"] and zh else f" — {r['why']}" if r["why"] else "")
        for i, r in enumerate(rows, 1))
    head = ("这是从随身录音的多人对话里抽出的断言，请**联网逐条核实**。\n\n" if zh
            else "These claims were extracted from conversations recorded on a "
                 "wearable. Please **verify each one online**.\n\n")
    return head + body + "\n" + (CHECK_TAIL_ZH if zh else CHECK_TAIL_EN)


def pending_day(con) -> str | None:
    """The earliest day with claims still waiting to be checked."""
    days = [r["day"] for r in checkable(con)]
    return min(days) if days else None


def dispatch(con, day: str | None = None, log=print,
             today: str | None = None) -> str | None:
    """Send one day's checkable claims out as a single job.

    One batch, not one job per claim: the answers are compressed into a single
    daily card at the end, and separate jobs would mean N agents each writing a
    section that knows nothing about the others, plus N times the startup cost.

    One day per job for the same reason — a report maps naturally onto a day, so
    the card needs no further splitting. Batching across days would mean cutting
    one report into two cards, and cutting a report is not reliably automatable.

    Where it goes is the sink's business now. What stays here is which claims are
    worth the money, how they are grouped, and what a failure means for their
    state.

    A destination that cannot answer gets them too, through forward: the claims
    go out and nothing waits for them.
    """
    from ... import sink as S
    impl = S.resolve(_sink_name())
    if not callable(impl.get("poll")):
        return forward(con, impl, _sink_name(), today=today, log=log)
    day = day or pending_day(con)
    if not day:
        return None
    rows = checkable(con, limit=BATCH, day=day)
    if not rows:
        return None
    # How many times this day has been sent. **It belongs in the key.** The
    # destination dedupes on the key, so an unchanged key means a resend
    # replays whatever the first attempt produced — including a task that
    # already failed, which then can never be retried.
    att = 1 + max((r["attempt"] or 0) for r in rows)

    try:
        tid = impl["send"]({
            "title": f"Fact check {day} ({len(rows)})",
            "body": check_prompt(rows),
            "key": f"umbra:claims:v2:{day}:{rows[0]['id']}:{att}",
            "visible": False,          # a background worker, not something to read
            "interactive": False,
        })
    except Exception as e:                                # noqa: BLE001
        # **State does not move on failure.** Marking these checking would say
        # they were sent when they were not, and nothing would ever say
        # otherwise.
        log(f"  ✗ dispatch failed; status stays new: {e}")
        return None
    for r in rows:
        _write(con, r, "checking", check_task=tid, attempt=att)
    con.commit()
    S.record(con, PLUGIN, f"check:{day}:{att}", _sink_name(), S.SENT,
             external_id=tid)
    return tid


def forward_text(day: str, rows) -> tuple[str, str]:
    """Title and body of a one-way send: the drawer's rows, as text a person
    reads in a channel or a file."""
    from ... import infer
    zh = infer.lang() == "zh"
    if zh:
        title = f"待核查断言 · {day}（{len(rows)} 条）"
        head = f"{day} 有 {len(rows)} 条断言值得查。结果不会回到 Umbra。"
    else:
        title = f"Claims to look up · {day} ({len(rows)})"
        n = len(rows)
        head = (f"{n} claim{'' if n == 1 else 's'} from {day} worth a lookup. "
                f"Umbra does not hear back.")
    parts = [head]
    for r in sorted(rows, key=lambda r: r["t"] or ""):     # the day, in order
        v = r["verdict"] or "unsure"
        guess = VERDICT_ZH.get(v, v) if zh else v.replace("_", " ")
        why = r["why"] or ""
        parts.append("\n".join([
            f"{r['t'] or ''} · {r['speaker'] or ''}".strip(" ·"),
            r["claim"],
            f"「{r['quote']}」" if zh else f"\"{r['quote']}\"",
            (f"初判：{guess}" + (f" —— {why}" if why else "")) if zh
            else (f"first guess: {guess}" + (f" — {why}" if why else "")),
        ]))
    return title, "\n\n".join(parts) + "\n"


def forward(con, impl: dict, name: str, today: str | None = None,
            log=print) -> None:
    """Send the claims to a destination that cannot answer, and stop there.

    Each day of the last ONE_WAY_DAYS goes as its own item, all in one round:
    there is no lookup to pay for, so no reason to ration. The item carries a
    readable body for a person, the verification prompt and the rows for a
    script that does its own lookup, and nothing comes back from either.

    A sent claim ends at `sent`. Not `checking`: pull looks for an answer to a
    receipt, and from here none will ever come, so a claim left there would sit
    unfinished forever with nothing saying why. Not resent either.

    The week's limit is so that switching sending on does not empty weeks of
    stale claims into a channel. Older ones stay in the drawer.
    """
    import datetime as dt
    from ... import sink as S
    today = today or time.strftime("%Y-%m-%d")
    since = (dt.date.fromisoformat(today)
             - dt.timedelta(days=ONE_WAY_DAYS - 1)).isoformat()
    days = sorted({r["day"] for r in checkable(con) if r["day"] >= since})
    n = sent_days = 0
    for day in days:
        rows = checkable(con, limit=BATCH, day=day)
        att = 1 + max((r["attempt"] or 0) for r in rows)
        title, body = forward_text(day, rows)
        try:
            receipt = impl["send"]({
                "title": title,
                "body": body,
                "day": day,
                "prompt": check_prompt(rows),
                "claims": [{"time": r["t"], "speaker": r["speaker"],
                            "claim": r["claim"], "quote": r["quote"],
                            "first_guess": r["verdict"] or "unsure",
                            "why": r["why"] or "", "absolute": bool(r["absolute"])}
                           for r in rows],
                "key": f"umbra:claims:v2:{day}:{rows[0]['id']}:{att}",
                "visible": True,           # someone reads this; nothing runs it for us
                "interactive": False,
            })
        except S.Fatal as e:
            log(f"  ✗ sending to {name!r} stopped; claims stay new: {e}")
            break
        except Exception as e:                            # noqa: BLE001
            # **State does not move on failure**, the same rule as dispatch.
            log(f"  ✗ sending {day} to {name!r} failed; claims stay new: {e}")
            continue
        for r in rows:
            _write(con, r, "sent", check_task=receipt, attempt=att)
        con.commit()
        S.record(con, PLUGIN, f"check:{day}:{att}", name, S.ANSWERED,
                 external_id=receipt)
        n += len(rows)
        sent_days += 1
    if n:
        log(f"  claims: sent {n} from {sent_days} day(s) to {name!r}; "
            f"nothing comes back")
    return None


def lookup_report(r: dict, found: dict) -> str:
    """One claim's finding as text: what the card is made from, and what a
    one-way destination receives."""
    from ... import infer
    zh = infer.lang() == "zh"
    head = f"[{r['day']} {r.get('t') or ''} {r.get('speaker') or ''}]".replace("  ", " ")
    said = f"原话：「{r['quote']}」" if zh else f'Said: "{r["quote"]}"'
    tx = (("（多半是转写错误）" if zh else " (likely a transcription error)")
          if found["transcription_error"] else "")
    verdict = (f"判定：{found['verdict']}{tx} —— {found['actual']}" if zh
               else f"Verdict: {found['verdict']}{tx} — {found['actual']}")
    lines = [f"{head} {r['claim']}", said, verdict]
    if found["sources"]:
        lines.append("来源：" if zh else "Sources:")
        lines += [f"- {s['title'] or s['url']}: {s['url']}" for s in found["sources"]]
    return "\n".join(lines)


def lookup(con, ask=None, today: str | None = None, log=print) -> int:
    """Look up each claim worth it, one by one, with a model that searches.
    Returns how many were looked up.

    One call per claim rather than one per day: each answer then rests on its
    own searches, a failure costs one claim rather than a day, and the sources
    belong to the claim they support.

    What is kept: the verdict and what is actually so, from the model; the
    sources, **only from the provider's record of what the search returned** —
    a link the model writes into its answer is not a source.

    Bounded three ways, because it costs money: a week back at most, so switching
    it on does not check months of backlog; MAX_LOOKUPS a day (or
    `max_lookups`); and MAX_ATTEMPTS failures before a claim is set aside, so
    one claim nobody can check does not hold its day's card back forever.
    """
    import concurrent.futures
    import datetime as dt
    from ... import providers
    spec = ((_conf().get("plugins") or {}).get(PLUGIN)) or {}
    today = today or time.strftime("%Y-%m-%d")
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=LOOKUP_DAYS - 1)).isoformat()
    rows = [r for r in checkable(con) if r["day"] >= since
            and (r["attempt"] or 0) < MAX_ATTEMPTS]
    if not rows:
        return 0
    # Newest first: under the cap, today's claims matter more than last week's.
    rows.sort(key=lambda r: (r["day"], r["absolute"] or 0, r["t"] or ""), reverse=True)
    cap = int(spec.get("max_lookups", MAX_LOOKUPS))
    done = sum(1 for r in records(con) if (r.get("lookup") or {}).get("on") == today)
    room = max(0, cap - done)
    if len(rows) > room:
        log(f"  claims: daily cap of {cap} lookups reached; "
            f"{len(rows) - room} wait for tomorrow")
        rows = rows[:room]
    if not rows:
        return 0
    model = lookup_model()
    if ask is None:
        try:
            ask = providers.lookup(model)
        except RuntimeError as e:
            log(f"  ✗ claims lookup: {e}")
            return 0
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(ask, lookup_prompt(r, today)): r for r in rows}
        results = []
        for f in concurrent.futures.as_completed(futs):
            r = futs[f]
            try:
                got = f.result()
                if not got.get("searches"):
                    # A verdict from memory is what the offline first pass
                    # already gave; it is not a lookup.
                    raise ValueError("answered without searching")
                found = _found(got.get("text") or "")
            except Exception as e:                        # noqa: BLE001
                results.append((r, None, e))
                continue
            found["sources"] = (got.get("sources") or [])[:MAX_SOURCES]
            found.update(model=model, searches=got.get("searches", 0), on=today)
            results.append((r, found, None))
    n = 0
    for r, found, err in results:
        if found is None:
            att = (r["attempt"] or 0) + 1
            gave_up = att >= MAX_ATTEMPTS
            _write(con, r, "skipped" if gave_up else None, attempt=att)
            log(f"  ✗ claims lookup {r['day']} {r.get('t') or ''}: {err}"
                + ("; set aside after repeated failures" if gave_up else ""))
            continue
        fields = {"report": lookup_report(r, found)}
        if r["src"] == "item":
            fields["lookup"] = found
        _write(con, r, "checked", **fields)
        n += 1
    con.commit()
    return n


def pull(con, log=print) -> int:
    """Collect finished checks. Returns how many claims got an answer.

    **Not so it can be reworked and resent.** The card is the final product —
    opening it is the answer and replying to it is the follow-up, on the
    destination's own thread, with Umbra not involved. Pulling back is for the
    record: so "how often was I wrong this month" is answerable later, and so
    the filter itself can be checked against what verification actually found.

    A failed job puts its claims back in the queue. Marking them checked would
    say they were verified when they were not — the same rule as dispatch, one
    step later.
    """
    from ... import sink as S
    checking = records(con, status="checking")
    tasks = sorted({r["check_task"] for r in checking if r["check_task"]})
    if not tasks:
        return 0
    name = _sink_name()
    if not name:
        return 0
    fn = S.resolve(name).get("poll")
    if not callable(fn):
        # A destination that cannot answer is a legitimate configuration; there
        # is simply nothing to collect from it.
        return 0
    n = 0
    for tid in tasks:
        try:
            got = fn(tid)
        except Exception as e:                            # noqa: BLE001
            log(f"  ✗ could not poll {tid}: {e}")
            continue
        if got is None:
            continue                       # still working; ask again next round
        batch = [r for r in checking if r["check_task"] == tid]
        if got.get("state") == "done":
            for r in batch:
                _write(con, r, "checked", report=got.get("report") or "")
            n += len(batch)
        else:
            # Counted from the batch, not from the connection's change count,
            # which would fold in whatever else this round wrote. A person reads
            # this line to know how many need rechecking.
            for r in batch:
                _write(con, r, "new", check_task=None)
            log(f"  ⚠ {tid} {got.get('why', 'failed')}, "
                f"{len(batch)} claims returned to the queue")
    con.commit()
    return n


def render_cards(con, model: str | None = None, call=None, log=print) -> int:
    """Compress verification reports into card text. Returns how many days were
    rendered.

    Why a separate step: the report is research material (fully sourced, kept on
    record); the card is something to skim on a phone. Measured, one report was
    8,854 characters over 99 lines, 37 of them links — as a card body nobody can
    read it, so the reader opens it and asks an agent to summarise it again,
    three extra steps out of nowhere.

    Separating them has another benefit: a day already sent can be re-rendered
    without checking anything online again.

    **Rendered per day over what is undelivered, not per report.** A day often
    has several reports (a late recording or a rewritten transcript sends
    another batch), and rendering each then joining them end to end put two
    full "summary + wrong + right" sets on one card. So whenever a day has a
    report not yet rendered, all of that day's undelivered reports are
    compressed together again — the one rendered earlier too, which costs one
    more local call. Delivered ones stay out, or a supplement would repeat the
    digest.
    """
    from ... import providers
    model = model or analyzer.model_for(PLUGIN)
    undelivered = [r for r in records(con, status="checked")
                   if r["report"] and not r["delivered"]]
    days = sorted({r["day"] for r in undelivered if r["card"] is None})
    if not days:
        return 0
    call = call or providers.resolve(model)
    n = 0
    for day in days:
        rows = sorted((r for r in undelivered if r["day"] == day),
                      key=lambda r: r["id"])
        reports = list(dict.fromkeys(r["report"] for r in rows))
        if all(r.get("lookup") for r in rows):
            # Looked up one by one: each report is one claim, and together they
            # are one list, not several reports to reconcile.
            report = "\n\n".join(reports)
        else:
            report = reports[0] if len(reports) == 1 else "\n\n".join(
                f"=== Report {i} ===\n\n{r}" for i, r in enumerate(reports, 1))
        try:
            card = call(card_prompt().format(report=report)).strip()
        except Exception as e:                            # noqa: BLE001
            log(f"  ✗ {day} could not render the card: {e}")
            continue
        if not card:
            continue
        for r in rows:
            _write(con, r, card=card)
        n += 1
    con.commit()
    return n


def digest(con, log=print) -> int:
    """Turn each fully checked day into one **visible** digest card. Late content
    goes out separately, as a supplement.

    Returns how many cards were sent. Deliveries are recorded per claim: Plaud
    recordings arrive late, and recording one card per day would let the first
    digest seal that day for good. An old database has no per-claim ledger, so
    the first run rebuilds the original card's coverage from "claim created
    before the digest was sent".

    Why this is a second send rather than the verification itself: the check
    goes out as a background worker, which by design is hidden from anywhere a
    person looks. Verification is verification; what a person reads has to be a
    separate, visible thing.

    Why it must not start itself: it is something to read, not a task. Acting on
    it turns it into a conversation with the report already in context, which is
    where a follow-up question belongs.
    """
    import hashlib

    # Upgrading an old database: claims that existed, with visible card text,
    # before the original digest was sent count as delivered. Late ones were
    # created after the digest and must not be backfilled — that would be
    # exactly the omission this ledger exists to prevent.
    con.execute(
        "INSERT OR IGNORE INTO claim_card_items (claim_id,task,created_ms) "
        "SELECT c.id,cc.task,cc.created_ms FROM claims c "
        "JOIN claim_cards cc ON cc.day=c.day "
        "WHERE c.status='checked' AND c.card IS NOT NULL AND c.card != '' "
        "AND c.created_ms IS NOT NULL AND cc.created_ms IS NOT NULL "
        "AND c.created_ms <= cc.created_ms")

    # A day's digest goes only once everything worth checking that day has
    # been checked. Half a report misleads more than none.
    allrec = records(con)
    by_day: dict = {}
    for r in allrec:
        by_day.setdefault(r["day"], []).append(r)

    def finished(rs):
        if not any(r["status"] == "checked" for r in rs):
            return False
        # Anything still checking is unfinished, unconditionally — it was
        # already judged worth checking.
        if any(r["status"] == "checking" for r in rs):
            return False
        # A new claim counts only if it is worth checking. A missing verdict is
        # unsure: old rows, or a field the model left out, are NULL, and reading
        # that as "fine" would call the day finished and send half a digest.
        if any(r["status"] == "new" and ((r["verdict"] or "unsure") != "confident_right"
                                        or r["absolute"]) for r in rs):
            return False
        # While any checked report that day has no card yet, nothing is sent.
        # Otherwise, with one fast report and one slow, half a card goes first
        # and the slow one arrives next round as a pointless supplement.
        return not any(r["status"] == "checked" and not r["card"] for r in rs)

    days = sorted(d for d, rs in by_day.items() if finished(rs))
    if not days:
        return 0
    from ... import sink as S
    name = _sink_name()
    if not name:
        return 0
    send = S.resolve(name)["send"]

    sent = 0
    for day in days:
        # Only claims not yet in any visible card. The claims of one
        # verification job share one card, so its text is added once; the
        # ledger still records every id in the group.
        rows = sorted((r for r in by_day[day] if r["status"] == "checked"
                       and r["card"] and not r["delivered"]),
                      key=lambda r: r["id"])
        if not rows:
            continue
        cards = {}
        for r in rows:
            cards.setdefault(r["card"], []).append(r["id"])
        body = "\n\n".join(cards)
        ids = [r["id"] for r in rows]
        n = len(ids)
        # A day has had its card if the old ledger says so, or if a digest for
        # it was recorded since the move.
        supplement = (con.execute("SELECT 1 FROM claim_cards WHERE day=?",
                                  (day,)).fetchone() is not None
                      or S.state(con, PLUGIN, f"digest:{day}", name) is not None
                      or any(r["delivered"] for r in by_day[day]))
        fp = hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()[:16]
        try:
            # The body **is** the card, with no preamble wrapped around it —
            # the title already carries the date and the count, and an agent
            # reading it sees the same thing (measured: it read two added
            # paragraphs as noise and summarised them back).
            tid = send({
                "title": (f"{day} fact check, supplement ({n})" if supplement
                          else f"{day} fact check ({n})"),
                "body": body,
                # The version segment is load-bearing. The card format changed
                # twice — report verbatim, then a card, then a plain-text card —
                # and reusing a key across a format change makes the destination
                # replay the old delivery, so the new format never ships.
                "key": (f"umbra:claims:supplement:v1:{day}:{fp}" if supplement
                        else f"umbra:claims:digest:v3:{day}"),
                # Something to read, and it must not start itself: it is a
                # digest, not a task.
                "visible": True,
                "interactive": True,
            })
        except Exception as e:                            # noqa: BLE001
            log(f"  ✗ {day} could not send the digest: {e}")
            continue
        now = int(time.time() * 1000)
        if not supplement:
            S.record(con, PLUGIN, f"digest:{day}", name, S.ANSWERED,
                     external_id=tid, now_ms=now)
        for r in rows:
            _deliver(con, r, tid, now)
        sent += 1
    con.commit()
    return sent


# ── The Analyzer declaration ──────────────────────────────────
#
# What this lane reads, how it slices a day, what it asks, and how to read the
# answer. Everything else — windowing, the cache, concurrency, the model call,
# storage, incrementality — belongs to the core.

INPUT = "events:plaud"
WINDOW = 30
# A claim is identified by the words themselves, within a day — the key the old
# table deduplicated on: dedupe on the transcript, not on the rewording.
KEY = "quote"

# Zero context on purpose: a claim has to stand on its own. With surrounding
# minutes in view the model reaches into them for a subject, and the claim it
# returns is about something nobody said in this window.
# CONTEXT_MIN is already defined above at 0; the runner reads it.


def PROMPT() -> str:
    """A callable, because the wording is chosen from config at run time.

    A module-level string would be fixed at import and would pin whichever
    language was set the first time the process loaded.
    """
    return _prompt()


def render_window(events, a, b, lo_ms, ctx=0):
    """The source's own window rendering, with this lane's two settings.

    The rendering belongs to plaud: which records carry evidence and merging the
    same sentence captured by two devices are both that source's business. What
    is this lane's is the two arguments — its own marker wording, so rewording
    one lane's separator cannot invalidate the other's cached history, and a
    minimum speaker count, because a person dictating alone to an agent is not
    addressing anyone. Measured, those windows yield nothing but travel plans.
    """
    from ...importers import plaud
    return plaud.render_window(events, a, b, lo_ms, ctx=ctx,
                               marks=(TARGET_MARK, AFTER_MARK),
                               min_speakers=MIN_SPEAKERS)


def params(con, day: str, window: dict) -> dict:
    """Quotes the old table already holds for this day. The new store's key
    cannot see them, so without this the first rounds after the move would find
    each claim again and send it to be checked a second time. The old table
    never changes, so this never moves the cache."""
    return {"_known": sorted({q for (q,) in con.execute(
        "SELECT quote FROM claims WHERE day=?", (day,))})}


def parse(items, window):
    """The model's array -> rows, with the two rejections this lane owns.

    A claim with no quote cannot be checked against anything, so it is not kept.
    A quote that does not appear in this window belongs to a neighbour — at zero
    context, "not found here" means exactly that, which is why owns_quote lives
    here rather than in the core: the tasks lane runs at thirty minutes of
    context, where the same rule would drag in every neighbour's output.
    """
    known = set((window.get("params") or {}).get("_known") or [])
    out = []
    for x in items:
        if not isinstance(x, dict) or not x.get("claim") or not x.get("quote"):
            continue
        if x["quote"] in known:
            continue              # already found, before the move
        if not owns_quote(window["text"], x["quote"]):
            continue
        out.append({
            "a": window["a"], "b": window["b"],
            "t": x.get("time"), "speaker": x.get("speaker"),
            "claim": x["claim"], "quote": x["quote"],
            "kind": _norm(x.get("kind"), KINDS, "other"),
            # An unrecognised self-assessment counts as unsure rather than fine:
            # better to pay for one more check than to wave something through
            # because the model mis-spelled a field.
            "verdict": _norm(x.get("verdict"), VERDICTS, "unsure"),
            "absolute": 1 if x.get("absolute") else 0,
            "why": x.get("why"),
        })
    return out


def listing(con) -> list[dict]:
    """Every claim, newest day first, with whether verification would look at
    it (the same gate checkable applies: what the model doubts, or what carries
    a number or date that can go stale)."""
    rows = records(con)
    rows.sort(key=lambda r: (r["day"],), reverse=True)
    return [{"day": r["day"], "t": r["t"], "speaker": r["speaker"],
             "kind": r["kind"], "verdict": r["verdict"],
             "absolute": bool(r["absolute"]), "status": r["status"],
             "claim": r["claim"],
             "would_check": (r["verdict"] or "unsure") != "confident_right"
                            or bool(r["absolute"])}
            for r in rows]


def sync(con, days, log=print):
    """One scheduled round: extract, look up, compress into cards, and send the
    day's digest. Nothing at all when the plugin cannot look claims up."""
    why = blocked()
    if why:
        log(f"  claims: not running — {why}")
        return
    for day in days:
        r = scan(con, day)
        log(f"  claims {day}: reused {r['reused']} / called "
            f"{r['fresh_calls']} windows -> {len(r['fresh'])} new claims")
    if not check_enabled():
        return
    try:
        if lookup_mode() == "model":
            n = lookup(con, log=log)
            if n:
                log(f"  claims: looked up {n}")
            if not _sink_name():
                return              # the drawer shows them; nothing to deliver
        else:
            tid = dispatch(con, log=log)
            if tid:
                log(f"  claims: dispatched for verification {tid}")
            n = pull(con, log=log)
            if n:
                log(f"  claims: pulled back {n} verification results")
        # A report is research material; a card is what gets read on a phone.
        # Compress before sending (see render_cards).
        r = render_cards(con, log=log)
        if r:
            log(f"  claims: rendered cards for {r} days")
        d = digest(con, log=log)
        if d:
            log(f"  claims: sent {d} daily digest cards")
    except Exception as e:                    # noqa: BLE001
        log(f"  ✗ claims check: {e}")


# ── The drawer (api.said / api.said_mark) ─────────────────────

def said(con, date: str, conf: dict) -> dict:
    """This lane's part of the day's drawer: the claims heard that day, and
    whether a verdict is a check or only the model's memory (`lookup`), and
    where the checks go (`lookup_sink`, and whether it reports back)."""
    from ... import sink
    claims = []
    seen = {i for (i,) in con.execute("SELECT claim_id FROM claim_seen")}
    on = check_enabled()
    today = time.strftime("%Y-%m-%d")
    for r in records(con, day=date):
        t, w = r["t"], r["win_a"]
        w = w if w is not None else (analyzer.hm_min(t, 0) // 30) * 30
        verdict = r["verdict"] or "unsure"
        claims.append({"id": r["id"], "t": t or "", "at": analyzer.hm_min(t, w),
                       "win": [w, w + 30], "speaker": r["speaker"] or "",
                       "claim": r["claim"], "quote": r["quote"],
                       "kind": r["kind"] or "other", "verdict": verdict,
                       "absolute": bool(r["absolute"]), "why": r["why"] or "",
                       "status": r["status"], "card": r["card"],
                       # A repeat of an earlier round's claim is closed the way a
                       # task said twice is: out of the count, sunk to the end.
                       "seen": r["id"] in seen or r["status"] == "duplicate",
                       # The lane's own gate (checkable): a lookup is spent on
                       # what the model doubts or what has a number or date in
                       # it that can go stale.
                       "would_check": verdict != "confident_right"
                                      or bool(r["absolute"])})
        f = r.get("lookup")
        claims[-1]["found"] = {
            "verdict": f["verdict"], "actual": f["actual"],
            "misheard": bool(f.get("transcription_error")),
            "sources": f.get("sources") or [], "model": f.get("model") or ""} if f else None
        # Worth a lookup and not looked up yet: the next round will.
        claims[-1]["waiting"] = (on and not f and r["status"] == "new"
                                 and claims[-1]["would_check"])
    # Who looks claims up, and whether an answer comes back here: a model
    # does, and so does a sink that reports back; one that only receives
    # gets them, and nothing it finds reaches this drawer.
    to = _sink_name() or ""
    mode = lookup_mode()
    by = (to if mode == "sink" else lookup_model() or "") if on else ""
    return {"claims": claims, "lookup": on, "lookup_by": by, "lookup_sink": to,
            "lookup_answers": on and (mode == "model" or sink.answers(to) is not False),
            "looked_today": sum(1 for r in records(con)
                                if (r.get("lookup") or {}).get("on") == today)}


def said_mark(con, item_id: int, body: dict) -> dict:
    """The page's Seen: {"seen": true | false}. Looked at on the page; the
    lookup's own state is left alone."""
    if not any(r["id"] == item_id for r in records(con)):
        raise LookupError(f"no claim {item_id}")
    with con:
        if body.get("seen"):
            con.execute("INSERT OR IGNORE INTO claim_seen (claim_id, at_ms) "
                        "VALUES (?, ?)", (item_id, int(time.time() * 1000)))
        else:
            con.execute("DELETE FROM claim_seen WHERE claim_id=?", (item_id,))
    return {"ok": True}


# ── The command line (umbra claims) ──────────────────────────

CLI_NAME = "claims"
CLI_HELP = "fact-check: claims said out loud → verified → a daily digest"


def cli_args(p) -> None:
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "scan", "check", "pull", "render", "digest"])
    p.add_argument("id", nargs="?", help="a date for scan")


def cli(args):
    """Fact-checking. scan extracts claims (local, cheap); check is the only
    step that goes online, and it has to be switched on."""
    from ... import db
    con = db.connect()
    if args.action == "scan":
        import datetime as _dt
        day = args.id or str(_dt.date.today())
        r = scan(con, day, log=print)
        print(f"  {day}: {len(r['fresh'])} new claims "
              f"(called {r['fresh_calls']} / reused {r['reused']} windows"
              + (f", {r['failed']} failed" if r["failed"] else "") + ")")
    elif args.action == "check":
        # A one-way sink returns no receipt to follow up on; it says what it
        # sent in its own line, and that is not "nothing".
        lines = []
        tid = dispatch(con, log=lambda m: (lines.append(m), print(m)))
        if tid:
            print(f"  sent for verification: {tid}")
        elif not lines:
            print("  nothing needs checking")
    elif args.action == "pull":
        print(f"  pulled back {pull(con)} verification results")
    elif args.action == "render":
        print(f"  rendered cards for {render_cards(con)} days")
    elif args.action == "digest":
        print(f"  sent {digest(con)} daily digest cards")
    else:
        rows = listing(con)
        if not rows:
            print("  no claims yet. Scan a day: umbra claims scan 2026-08-22")
        for r in rows:
            flag = "!" if r["would_check"] else " "
            print(f" {flag}[{r['status']:8s}] {r['day']} {r['t'] or '--:--'} "
                  f"{r['speaker'] or '?':10s} [{r['kind']}/{r['verdict']}"
                  f"{'/absolute' if r['absolute'] else ''}] {r['claim'][:52]}")
