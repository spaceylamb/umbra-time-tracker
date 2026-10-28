"""Run inference: digest -> model -> spans -> calendar events.

This used to be lab/run.py plus lab/events.py, run once and left in a scratch
directory that was cleared on 2026-08-18. Moving it into the repo separated two
things: **calling the model** is I/O, **handling what the model said** is
deterministic. The second is testable, so it must be tested — every mistake at
this layer is of the silent kind.

Two passes:

    first   48 half-hour windows, each with an hour of context either side
            -> activity spans
    second  all of a day's spans -> calendar events (no window needed; the input
            is already two orders of magnitude smaller)

## The digest is written to disk before the model is called

When a call fails, the record of what went in must survive. The other order
(store it only on success) leaves nothing at all in the database for the day
that crashed — and the entire reason the digests table exists is to be able to
look up what was fed in, which is most needed precisely for the run that failed.

## What the model says is clamped back into the window

It can see an hour either side, so it returns spans reaching into the context
(window 09:00-09:30, answer 08:30-09:45). That half hour **is already the
responsibility of the 08:30 window**, and accepting it makes two windows fight,
producing two judgements for the same stretch of time.
"""

import concurrent.futures
import json
import os
import re
import time

from . import (analyze, analyzer, config, db, digest, inference as INF,
               providers, taxonomy, tz)

MODEL = os.environ.get("UMBRA_INFER_MODEL", "gemini-3.7-flash")
WORKERS = analyzer.WORKERS   # one number, owned by analyzer.py

def lang() -> str:
    """Which language the prompt is written in. config [infer].lang,
    defaulting to en.

    Affects **the instruction text** and the category names only. The user's own
    data (the source lines inside the digest) stays in whatever language it was
    in, and the model reads both. Category names following the language is
    harmless: taxonomy.norm recognises either, and everything normalises to a key
    before it is stored.
    """
    return (config.load().get("infer") or {}).get("lang", "en")


def cats(lg: str | None = None) -> str:
    # The category list is generated from taxonomy — that is the single source,
    # and a hand-copied duplicate here would eventually drift
    return taxonomy.prompt_cats(lg or lang())

PROMPT_ZH = """从记录还原一个人的活动。记录来源：语音逐字稿、对 AI 的口述
指令、浏览、剪贴板、前台应用、位置、手环。

只判断 {a}–{b}。{wa}–{wb} 的记录是上下文，不要为它们输出区间。

判读
· Speaker 1/2/… 的编号只在同一条录音内有效，跨录音不通用。谁是本人从
  内容判断，不要假设某个编号就是他。
· 语音行开头的 [xxxx] 是录音编号。同一段里出现两个不同的编号，说明这段
  被两条录音同时录下了（随身设备 + 电脑端各录一份）—— 同一句话会出现
  两遍，措辞可能略有出入。那是**一次发言**，不是说了两次，也不是两个人
  在复述对方。只有其中一条录到的内容（通常是会议里对方的声音）照常算数。
· 「对AI说」是口述指令，属于工作，不是聊天。
· 浏览记录条数最多，但条数不代表主次 —— 开会时开着网页是常态。看内容，
  不要数条数。
· 没有依据就写「不确定」，不要编。
{rules}
切分
· 活动没变 → 一个区间，覆盖 {a}–{b}。
· 活动变了 → 在变化点切开，区间首尾相接，合起来仍覆盖 {a}–{b}。
· 变化的信号：前台应用换了、说话对象换了、话题换了、工作转为休息。
· 只要有记录支持，短到 1 分钟的区间也可以。

输出 JSON 数组，每个元素：
{{"start":"HH:MM","end":"HH:MM","cat":"<选一个：{cats}>",
  "activity":"一句话：在做什么",
  "confidence":"high|medium|low",
  "with":"独自|家人|同事|朋友|对AI说话|不确定",
  "also":["同时进行的次要活动"],
  "evidence":["原样抄录支撑判断的记录，2–6 条"]}}

只输出 JSON。

{text}"""

MERGE_PROMPT_ZH = """把 {day} 的逐段判断合成日历事件。

输入是按时间排列的活动区间，每段几分钟到半小时。输出是你会写进日历的
条目：一件事可以横跨很多段（整夜睡眠、一下午写代码、一小时购物）。
粒度取中等 —— 不要碎到每个动作，也不要粗到「上午」「下午」。

规则
· 覆盖 00:00–24:00，不留缝、不重叠。
· 相邻的同一件事合并；中间被打断的算两件。
· 标题写成日历条目（「买潜水服」「和爷爷奶奶视频」「调试 Shopify 跳转」），
  不要写「使用电脑」这类空话。
· 没有依据的时段写「不确定在做什么」，不要编。

输出 JSON 数组，每个元素：
{{"start":"HH:MM","end":"HH:MM","cat":"<选一个：{cats}>",
  "title":"日历条目标题","detail":"一两句展开",
  "from":["09:00–09:30","09:30–10:00"]}}

`from` 原样列出这件事由哪几段合成 —— 没有它就无法核对。

只输出 JSON。

{lines}"""

PROMPT_EN = """Reconstruct what a person was doing, from records: voice
transcripts, instructions dictated to AI assistants, browsing, clipboard,
foreground apps, location, fitness tracker.

Judge only {a}–{b}. Records from {wa}–{wb} are context — do not emit spans
for them.

Reading the records
- Speaker 1/2/… numbering is valid only within one recording, never across
  recordings. Work out who the user is from content; never assume a fixed
  number is them.
- A [xxxx] prefix on a voice line is the recording id. Two different codes
  in one slot mean two recordings captured the same scene at once (a worn
  device plus the computer): each sentence appears twice, with slightly
  different wording. That is ONE utterance — not something said twice, and
  not two people echoing each other. Lines that appear in only one of the
  two recordings (typically the other party on a call) still count.
- "To AI" lines are dictated instructions. That is work, not chat.
- Browsing has the most rows, but row count is not importance — pages left
  open during a meeting are normal. Read content, don't count rows.
- If there is no evidence, write "Unknown". Never invent.
{rules}
Splitting the slot
- Activity unchanged → one span covering {a}–{b}.
- Activity changed → split at the change points into adjacent spans that
  together still cover {a}–{b}.
- Signals of a change: the foreground app switches, who is being addressed
  switches, the topic switches, work turns into rest.
- Spans as short as 1 minute are fine when records support them.

Output a JSON array; each element:
{{"start":"HH:MM","end":"HH:MM","cat":"<one of: {cats}>",
  "activity":"one sentence: what they were doing",
  "confidence":"high|medium|low",
  "with":"alone|family|colleagues|friends|to AI|unknown",
  "also":["secondary things happening at the same time"],
  "evidence":["verbatim source records backing this judgement, 2-6"]}}

Output only JSON.

{text}"""

MERGE_PROMPT_EN = """Merge the per-slot judgements for {day} into calendar
events.

Input is activity spans in time order, each a few minutes to half an hour.
Output is what you would actually write in a calendar: one event may span
many slots (a full night's sleep, an afternoon of coding, an hour of
shopping). Aim for medium granularity — not every micro-action, not just
"morning/afternoon".

Rules
- Cover 00:00–24:00 with no gaps and no overlaps.
- Merge adjacent slots of the same thing; an interrupted thing is two events.
- Titles read like calendar entries ("Buy a wetsuit", "Video call with
  grandparents", "Debug the Shopify redirect"), never "Using computer".
- For stretches with no evidence write "Unknown". Never invent.

Output a JSON array; each element:
{{"start":"HH:MM","end":"HH:MM","cat":"<one of: {cats}>",
  "title":"calendar entry title","detail":"one or two sentences",
  "from":["09:00–09:30","09:30–10:00"]}}

`from` lists verbatim the slots this event was built from — without it the
event cannot be audited.

Output only JSON.

{lines}"""

def prompt_tpl(lg: str | None = None) -> str:
    return PROMPT_ZH if (lg or lang()) == "zh" else PROMPT_EN


def merge_tpl(lg: str | None = None) -> str:
    return MERGE_PROMPT_ZH if (lg or lang()) == "zh" else MERGE_PROMPT_EN


def gemini(prompt: str, model: str | None = None) -> str:
    """The old name, kept because scripts and docs still use it. The actual
    routing lives in providers.resolve."""
    return providers.resolve(model or MODEL)(prompt)


# The one implementation lives in analyzer.parse_json. Kept as a module-level
# alias rather than deleted: `infer._json` was reached from two other modules,
# and an alias makes that call site keep working while the private name stops
# being the thing they depend on.
_json = analyzer.parse_json


def parse(txt: str, a: int, b: int):
    """The model's reply -> a list of spans clamped into [a, b).

    Clamped rather than discarded: "they had started coding by 08:30" is
    probably correct, it is simply that the 08:30 window owns that half hour.
    Only spans lying entirely outside the window are dropped.
    """
    out = []
    for s in _json(txt):
        if not isinstance(s, dict):
            continue
        try:
            x, y = INF._min(s["start"]), INF._min(s["end"])
        except (KeyError, ValueError, AttributeError, TypeError):
            continue          # skip a malformed time; do not take the batch down
        x, y = max(x, a), min(y, b)
        if x >= y:
            continue                       # entirely outside the window
        out.append({**s, "start": f"{x // 60:02d}:{x % 60:02d}",
                    "end": f"{y // 60:02d}:{y % 60:02d}"})
    return out


def _hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def TARGET_HAS_CONTENT(text: str) -> bool:
    """Does the target stretch contain any records — context alone does not count.

    With nothing after the separator, all the model can see is the hour either
    side, and asking it to judge the half hour in between is asking it to guess.
    An empty window is better left empty: inference.expand leaves uncovered slots
    as None, and "there is no answer yet" beats an invented one.
    """
    i = text.find(digest.TARGET_MARK)
    return i >= 0 and bool(text[i + len(digest.TARGET_MARK):].strip())


def _redact(err: str) -> str:
    """Errors can carry the request URL, and the Gemini URL carries the key."""
    return re.sub(r"(key=)[^&\s'\"]+", r"\1…", err)


def run_day(con, day: str, model: str | None = None, call=None, log=None,
            fresh: bool = False):
    """The first pass. Returns {"spans": [...], "failed": n, "windows": n,
    "fresh_calls": how many calls were actually made,
    "reused": how many windows were carried over}.

    ## Incremental: the digest is the cache key

    The digest is written to disk first anyway (so that what was fed in can be
    looked up afterwards), and now it does a second job: when this round's text
    for a window is **byte-for-byte identical** to last round's, carry over last
    round's spans for that window and do not call the model. Backfilling data
    changes the input of one or two windows, so re-running a day drops from 48
    calls to a handful. fresh=True bypasses the cache and re-calls everything.

    "Did last round have an answer" is decided by "are there spans landing inside
    this window" — a window that failed last round has no spans and is retried
    automatically. The side effect is that a window where the model genuinely
    answered "nothing happened in this half hour" is also re-called. Since "no
    answer" and "an empty answer" cannot be told apart, better to pay for one
    extra call than to cache a failure as a success.
    """
    model = model or MODEL
    log = log or (lambda *_: None)
    lo, hi = analyze.day_bounds(day, con)
    n_min = (hi - lo + 1) // 60000
    events = db.load_range(con, lo, hi)

    prev_text = {}
    prev_spans = []
    if not fresh:
        prev_text = {d["a"]: d["text"]
                     for d in INF.load_digests(con, day).get(model, [])}
        prev_spans = INF.load(con, day).get(model, [])

    def _prev_in(a, b):
        return [s for s in prev_spans
                if INF._min(s["start"]) < b and INF._min(s["end"]) > a]

    jobs, reused, all_recs = [], [], []
    for a, b in digest.windows(n_min=n_min):
        text = digest.render(events, a, b, lo_ms=lo)
        n = sum(1 for e in events
                if lo + (a - digest.CONTEXT_MIN) * 60000
                <= e["ts"] < lo + (b + digest.CONTEXT_MIN) * 60000)
        rec = {"start": a, "end": b,
               "win": (max(0, a - digest.CONTEXT_MIN),
                       min(n_min, b + digest.CONTEXT_MIN)),
               "n": n, "text": text}
        all_recs.append(rec)
        # An empty target means no model call: pure waste, and it would
        # certainly invent something. The digest is stored regardless — empty
        # text and "never stored" are different facts.
        if not (text.strip() and TARGET_HAS_CONTENT(text)):
            continue
        kept = _prev_in(a, b)
        if text == prev_text.get(a) and kept:
            reused.extend(kept)
            continue
        jobs.append(rec)

    # Digests are written **first**, and for **every** window — save_digests
    # deletes then writes, so storing only the windows about to be called would
    # delete the digests of the carried-over ones, leaving them without a cache
    # key next round and degrading the incremental path into a full one. The
    # record of what went in must also survive a failed call.
    INF.save_digests(con, day, model, all_recs)

    spans, failed, failures, lost = list(reused), 0, [], []
    if jobs:
        # Resolved here, not at the top: a missing key raises once for the
        # day, and a day with nothing new to ask needs no key at all.
        call = call or providers.resolve(model)
        with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
            tpl, cs = prompt_tpl(), cats()
            rules = taxonomy.prompt_rules(lang())
            futs = {pool.submit(call, tpl.format(
                        a=_hm(r["start"]), b=_hm(r["end"]),
                        wa=_hm(r["win"][0]), wb=_hm(r["win"][1]),
                        cats=cs, rules=rules, text=r["text"])): r for r in jobs}
            for f in concurrent.futures.as_completed(futs):
                r = futs[f]
                try:
                    got = parse(f.result(), r["start"], r["end"])
                except Exception as e:                    # noqa: BLE001
                    failed += 1
                    failures.append({"a": r["start"], "b": r["end"],
                                     "at": int(time.time() * 1000),
                                     "reason": _redact(str(e))})
                    log(f"  ✗ {_hm(r['start'])} {e}")
                    lost.append(r)
                    # A failed window keeps last round's answer. save deletes
                    # the whole day and writes it again, so without this one
                    # network blip blanks these windows — the page then shows
                    # the day "done up to" the first failure.
                    spans.extend(_prev_in(r["start"], r["end"]))
                    continue
                spans.extend(got)
                log(f"  ✓ {_hm(r['start'])} {len(got)} spans")
    if lost:
        # But the cache key must not record the new input: a digest is "the
        # input this answer was computed from". A failed window's answer still
        # comes from the old input; recorded as new, the next round would see
        # "input unchanged + an answer" and reuse the old answer as the new one
        # for good. Restore last round's text; with none, store nothing, and the
        # next round calls again.
        keep = {r["start"]: prev_text.get(r["start"]) for r in lost}
        recs = [dict(d, text=keep[d["start"]]) if d["start"] in keep else d
                for d in all_recs]
        INF.save_digests(con, day, model,
                         [d for d in recs if d["text"] is not None])
    # One old answer may span two failed windows and be added twice.
    spans = list({(s.get("start"), s.get("end"), s.get("cat"),
                   s.get("activity")): s for s in spans}.values())
    spans.sort(key=lambda s: INF._min(s["start"]))
    if spans:
        INF.save(con, day, model, spans)
    # Every window this run needed was tried, so this is the whole truth about
    # which failed — including "none did", which clears last round's.
    INF.save_failures(con, day, model, failures)
    return {"spans": spans, "failed": failed, "windows": len(jobs),
            "fresh_calls": len(jobs) - failed, "reused": len(reused)}


# How a span's companions and parallel activities read in the merge prompt, per
# prompt language. They were Chinese only, so an English prompt carried Chinese
# annotations on every line. The zh wording is unchanged.
_WITH = {"zh": ("（跟 {}）", "（同时：{}）"), "en": (" (with {})", " (also: {})")}


def merge_prompt(day: str, spans) -> str:
    w, a = _WITH["zh" if lang() == "zh" else "en"]
    lines = "\n".join(
        f"{s['start']}–{s['end']} [{s.get('cat','?')}] {s.get('activity','')}"
        + (w.format(s["with"]) if s.get("with") else "")
        + (a.format("/".join(s["also"])) if s.get("also") else "")
        for s in spans)
    return merge_tpl().format(day=day, cats=cats(), lines=lines)


def merge(con, day: str, spans, model: str | None = None, call=None,
          dirty: bool = True):
    """The second pass: spans -> calendar events. No spans means no model call.

    dirty=False means the first pass was entirely carried over and made no calls
    — the input did not change, so the output will not either, and the previous
    events are returned as they are. Saving this one call is the bulk of the
    win when a scheduled job re-runs the same day repeatedly: with nothing
    changed all day, the whole chain makes zero calls.
    """
    if not spans:
        return []
    model = model or MODEL
    if not dirty and con is not None:
        old = INF.load_events(con, day).get(model)
        if old:
            return old
    call = call or providers.resolve(model)
    # The upper bound is **this day's** length. With 1440 hard-coded, the last
    # fifteen hours of a 39-hour day would be clamped away as out of range, and
    # once clamped everything looks normal. For today it is now: asked to
    # compose the whole day, the model fills the hours that have not happened
    # with an "Unknown" event, and those then count as covered.
    end = tz.elapsed_minutes(con, day) if con is not None else 1440
    evs = parse(call(merge_prompt(day, spans)), 0, end)
    if evs and con is not None:
        INF.save_events(con, day, model, evs)
    return evs
