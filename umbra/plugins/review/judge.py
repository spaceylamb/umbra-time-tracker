"""Candidates -> "does this still need you", and the verdict you give.

The layers below are counted; this is the **only** one that calls a model. The
line is drawn here on purpose: what is counted can be rerun and reconciled for
free, while "does this need a person" is a judgement, and only something that
reads the content can make it.

Its rules come from the first batch of voice-dispatched tasks, where 18 of 22
verdicts were "already done" — proposals not anchored in evidence, with no gate
to refuse them:

1. **No inventing.** An akey the model returns that was not in the input is
   dropped whole.
2. **Asked once.** A suggestion is keyed on its deterministic akey, never on
   the model's title (which changes wording every round). An akey that was
   asked about is never sent again: not when you have not ruled on it yet, and
   not when the model silently skipped it — 519 were sent once and 457 came
   back, and without a record of what was asked the other 62 would be paid for
   again every month.
3. **Same input, no call.**
4. **A failed call writes nothing.** One network blip must not quietly erase
   last round's judgement.
"""

import concurrent.futures
import hashlib
import json
import time

from ... import analyzer

TABLES = """
-- One suggestion per akey, ever: `period` is when it was first made. Still open
-- (no verdict) means it belongs in this month's card too.
CREATE TABLE IF NOT EXISTS review_suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period      TEXT NOT NULL,      -- first made in, '2026-M08'
    akey        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,      -- the model's one sentence
    lever       TEXT,
    needs_you   INTEGER,            -- the model's call: can this go on without you
    why         TEXT,
    cadence     TEXT, n INTEGER, median_gap REAL, jitter REAL,
    last_day    TEXT, next_day TEXT,
    evidence    TEXT NOT NULL,      -- JSON, back to the raw records
    model       TEXT, created_ms INTEGER,
    verdict     TEXT,               -- useful | already | not_worth | wrong
    verdict_note TEXT,
    card        TEXT                -- the period of the card it went out in
);
-- What was asked, apart from what was answered: the difference is what the
-- model skipped, and it must not be paid for twice.
CREATE TABLE IF NOT EXISTS review_asked (
    akey  TEXT PRIMARY KEY,
    at_ms INTEGER NOT NULL,
    model TEXT
);
-- "This period's candidates were judged." Apart from the suggestions, because
-- a period can yield none, and no rows would then read as never judged.
CREATE TABLE IF NOT EXISTS review_runs (
    period TEXT NOT NULL,
    model  TEXT NOT NULL,
    cdig   TEXT NOT NULL,
    at_ms  INTEGER NOT NULL,
    PRIMARY KEY (period, model, cdig)
);
-- One card per period, and where it went.
CREATE TABLE IF NOT EXISTS review_cards (
    period     TEXT PRIMARY KEY,
    sink       TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    items      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS review_state (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

#: Candidates per call. 519 in one prompt ran two minutes without an answer, and
#: a truncated long answer **looks normal** — a valid array, 400 short. Per
#: batch, what is missing can be counted.
BATCH = 40
WORKERS = analyzer.WORKERS
VERDICTS = ("useful", "already", "not_worth", "wrong")

PROMPT_EN = """Below are repeated actions **counted** from four months of one person's
digital trail, each on a steady rhythm, with how often, the gaps in days, and
evidence lines.

Your one job: decide whether **this still needs the person**, and say so in one
sentence.

Rules:
- Answer only for the akeys listed below. **Add no akey** — what you were not
  shown does not exist.
- When unsure, needs_you = true. Better to miss a chance to automate than to
  hand away something that needs their judgement.
- Write the title as what they keep doing, not as a slogan.
- Choose lever from: {levers}
    unsubscribe  unsubscribe or mute        schedule  run it on a schedule
    push         make it come to them       session   make the sign-in last longer
    route        let an agent connect both ends        drop  just stop doing it
    recall       next time, lay out last time's sites and order (one-off, cannot be automated)
    delegate     hand it off, or fold it into one trip (offline: cannot be pushed or scheduled)

Output only a JSON array, each item:
{{"akey": "...", "title": "...", "lever": "...", "needs_you": true/false, "why": "one sentence"}}

Candidates:
{items}
"""

PROMPT_ZH = """下面是从一个人四个月的数字轨迹里**数出来**的、有固定节奏的重复动作。
每一条都带着出现次数、间隔天数和证据行。

你的任务只有一件：判断**这件事还需不需要他本人**，并用一句中文说清楚。

规矩：
- 只能对下面列出的 akey 作答。**不许新增任何 akey** —— 你没看到的东西不存在。
- 拿不准的就 needs_you=true。宁可漏掉一个自动化机会，也不要让他去做一件
  其实需要他判断的事。
- title 写成「他在重复做什么」，不要写成建议口号。
- lever 从这几个里选：{levers}
    unsubscribe 退订/关通知      schedule 定时跑          push 改成推给他
    session 延长会话有效期        route 让 agent 接上两头   drop 干脆别做了
    recall 下次同类项目把上次这批站点和顺序摊开（一次性的事，没法自动化）
    delegate 找人代办 / 并到一次出门（线下的事，没法推送也没法定时）

只输出 JSON 数组，每项：
{{"akey": "...", "title": "...", "lever": "...", "needs_you": true/false, "why": "一句话"}}

候选：
{items}
"""


def ensure(con) -> None:
    analyzer.ensure_tables(con, TABLES)


def _prompt() -> str:
    from ... import infer
    return PROMPT_ZH if infer.lang() == "zh" else PROMPT_EN


def _digest(cands) -> str:
    h = hashlib.sha256()
    for c in sorted(cands, key=lambda c: c["akey"]):
        h.update(f"{c['akey']}|{c['n']}|{c['last']}\x00".encode())
    return h.hexdigest()[:16]


def _asked(con) -> set:
    return {r[0] for r in con.execute("SELECT akey FROM review_asked")}


def _render(cands) -> str:
    lines = []
    for c in cands:
        ev = " / ".join(e["text"][:40] for e in c["evidence"][:2])
        lines.append(f"- akey={c['akey']} [{c['kind']}] ×{c['n']} {c['name']} "
                     f"gap {c['median_gap']:.0f}±{c['jitter']:.0f}d last {c['last']} "
                     f"evidence: {ev}")
    return "\n".join(lines)


def _one_batch(call, batch):
    from . import detect
    return analyzer.parse_json(call(_prompt().format(levers=" ".join(detect.LEVERS),
                                                      items=_render(batch))))


def run(con, cands, period: str, model: str, call=None,
        now_ms: int | None = None, workers: int = WORKERS,
        with_failed: bool = False, log=None):
    """Judge what has not been asked about yet. Returns the suggestions still
    open — not only this round's: a month with nothing new must not read as
    "no suggestions"."""
    log = log or (lambda *_: None)
    ensure(con)
    asked = _asked(con)
    fresh = [c for c in cands if c["akey"] not in asked]

    def done(failed=0):
        got = load(con)
        return (got, failed) if with_failed else got

    if not fresh:
        return done()
    cdig = _digest(fresh)
    if con.execute("SELECT 1 FROM review_runs WHERE period=? AND model=? AND cdig=?",
                   (period, model, cdig)).fetchone():
        return done()
    if call is None:
        from ... import providers
        call = providers.resolve(model)
    batches = [fresh[i:i + BATCH] for i in range(0, len(fresh), BATCH)]

    answers, ok = [], []
    with concurrent.futures.ThreadPoolExecutor(max(1, min(workers, len(batches)))) as pool:
        futs = {pool.submit(_one_batch, call, b): b for b in batches}
        for f in concurrent.futures.as_completed(futs):
            try:
                answers += f.result()
                ok.append(futs[f])
            except Exception as e:                              # noqa: BLE001
                # Kept, not swallowed: "the reply changed shape" and "the
                # network is down" must not both read as "N batches failed".
                log(f"  ✗ review: one batch of {len(futs[f])} not judged: {e}")
    failed = len(batches) - len(ok)
    # One batch failing is retried next round. All of them failing is not
    # "the model found nothing" and must not pass for it.
    if failed == len(batches):
        raise RuntimeError(f"review: all {failed} batches failed; nothing was judged")

    by_key = {c["akey"]: c for c in fresh}
    now = now_ms or int(time.time() * 1000)
    with con:
        # Asked means a batch that came back, answered or not.
        for b in ok:
            for c in b:
                con.execute("INSERT OR IGNORE INTO review_asked (akey, at_ms, model) "
                            "VALUES (?,?,?)", (c["akey"], now, model))
        for it in answers:
            c = by_key.get(it.get("akey"))
            if not c:                      # invented: dropped whole
                continue
            con.execute(
                "INSERT OR IGNORE INTO review_suggestions "
                "(period, akey, kind, title, lever, needs_you, why, cadence, n, "
                " median_gap, jitter, last_day, next_day, evidence, model, created_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (period, c["akey"], c["kind"], str(it.get("title") or "")[:200],
                 it.get("lever"), int(bool(it.get("needs_you"))),
                 str(it.get("why") or "")[:300], c["name"], c["n"], c["median_gap"],
                 c["jitter"], str(c["last"]),
                 None if c.get("next_expected") is None else str(c["next_expected"]),
                 json.dumps(c["evidence"], ensure_ascii=False), model, now))
        # A round with a failed batch is not "this period was judged", or the
        # failed candidates would hit the cache and never be judged.
        if not failed:
            con.execute("INSERT OR REPLACE INTO review_runs (period, model, cdig, at_ms) "
                        "VALUES (?,?,?,?)", (period, model, cdig, now))
    return done(failed)


def _rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load(con) -> list[dict]:
    """The suggestions still open (no verdict), across periods."""
    ensure(con)
    return _rows(con, "SELECT * FROM review_suggestions WHERE verdict IS NULL "
                      "ORDER BY needs_you, n DESC")


def by_id(con, sid: int) -> dict | None:
    ensure(con)
    got = _rows(con, "SELECT * FROM review_suggestions WHERE id=?", (sid,))
    return got[0] if got else None


def verdict(con, sid: int, v: str, note: str = "") -> None:
    """What you said about one suggestion. An unknown value is refused: stored,
    it would be a state nobody recognises."""
    if v not in VERDICTS:
        raise ValueError(f"a verdict is one of {'/'.join(VERDICTS)}, not {v!r}")
    if by_id(con, sid) is None:
        raise LookupError(f"no suggestion #{sid}")
    with con:
        con.execute("UPDATE review_suggestions SET verdict=?, verdict_note=? WHERE id=?",
                    (v, note, sid))


def omitted(con) -> int:
    """Asked about, and not a word back. "The model says leave it" and "the
    model never mentioned it" must not look alike."""
    ensure(con)
    return con.execute("SELECT COUNT(*) FROM review_asked WHERE akey NOT IN "
                       "(SELECT akey FROM review_suggestions)").fetchone()[0]


def hit_rate(con) -> dict:
    """Verdicts so far: in a few months, whether this deserves to live."""
    ensure(con)
    return dict(con.execute("SELECT verdict, COUNT(*) FROM review_suggestions "
                            "WHERE verdict IS NOT NULL GROUP BY 1").fetchall())
