"""Tasks heard in what you said: a recording → proposals → a destination → your verdict.

    scan      one day's Plaud transcript, judged **in 30-minute windows** →
              proposals, kept in the core's findings store
    dispatch  proposals → the destination you chose, held for a person
    you       ▶ run it / ✕ not a task / ✓ already done   ← the gate is there
    verdicts  what you tapped comes back on a later round and is recorded

Not only "remind me to X". **Four kinds**: a question not yet worked out
(question), something to do (errand), a promise to someone else (followup), an
idea not yet decided (idea). The kind exists only on Umbra's side — a destination
need not care, and all four arrive as one kind of item.

**The gate is at the destination, not here.** The reference sink sends each
proposal held for review (Butler's `proposed` status, which its daemon never
claims), so nothing starts until you act on it. Umbra adds no second gate of its
own — two gates only make you say yes to one thing twice. A proposal must carry
the words and the minute they were said: a proposal with no quote cannot be
checked, the same discipline as "on what grounds" in the inference chain.

## Why it does not ride on inference

Inference already slices a day into windows, renders them and caches by the
rendered text, so asking "any tasks in here?" in the same call looks free. It
is not, for three reasons:

  1. Inference's cache is keyed by model. Sharing it would tie task freshness to
     whichever model inference happened to use; switch models and every task
     answer is thrown away.
  2. The two questions fail differently. One combined prompt means rewording
     inference **quietly changes task recall**, with nothing to report it — the
     pair this project guards against most: "the model said nothing" versus "we
     never asked".
  3. Measured on one day: every source rendered line by line came to 235,173
     characters, speech only 52%. The rest — browser titles, window titles,
     foreground apps — says nothing about what someone said they need to do, and
     feeding it in doubles the cost.

So this lane reads speech only, keeps its own findings and cache, and runs as
its own step in each round with its own fault isolation. What it shares with
inference is the 30-minute grid: window numbers have to line up, or a person
cannot cross-check one against the other.

## Not real-time

The floor is Plaud's upload delay (median 40 hours, measured). This catches
"things to deal with later", not "right now".
"""

import json
import os
import re
import subprocess

from ... import analyzer, config

# Only config can be imported at module level: this module is loaded through
# the plugin registry, which can happen while db / analyze / digest are still
# initialising, and importing them at the top would be a circular import.

# What the core reads off this plugin (see docs/writing-a-plugin.md).
TITLE = "To-dos from recordings"
WHAT = "Finds things you said you'd do in your recordings."
ORDER = 2
SENDS = True                  # off until a sink is named and sending switched on
SEND_WHAT = "each to-do"
LEGACY_SECTION = True         # an [actions] section predates plugins
SAID_KIND = "task"            # what the drawer's ✓ / ✕ act on (said_mark)

WORKERS = analyzer.WORKERS   # one number, owned by analyzer.py
CONTEXT_MIN = 30          # minutes of speech shown either side of the window

# The four kinds. **A destination need not read this**; it exists on Umbra's
# side so you can look back at what was sent. A value outside these four is not
# guessed at — it is recorded as unknown.
TYPES = ("question", "errand", "followup", "idea")

# Work / personal. **Used only to filter at send time, never in extraction** —
# "no reminders for work" holds today and may not tomorrow, and written into the
# prompt it could not be undone with one line of config.
DOMAINS = ("work", "personal")

# The separators are this lane's own, not digest's constants: each lane must be
# able to reword its own without touching the other — the reason they are
# separate — and one shared string would be a hidden wire between them. They are
# also part of the cache key: rewording one re-runs every cached day of this lane.
TARGET_MARK = "───── the stretch you are judging starts here ─────"
AFTER_MARK = "───── context after that stretch ─────"

# ── Echoes ────────────────────────────────────────────────────
# Some people dictate tasks to an agent by voice. The moment it is said aloud the
# work **is already under way** — extracting it and pushing it back as a to-do
# turns the day's finished work into cards. Measured: on one day, 31 of 32
# proposals were that.
#
# The parameters were measured (two days), not guessed:
#   delay   median 1 minute, max 18          → window ±20
#   score   bimodal both days, split at 36%↔1% and 45%↔29% → threshold 0.35
ECHO_WINDOW_MIN = 20
ECHO_THRESHOLD = 0.35
ECHO_GRAM = 3
# A quote shorter than this is never judged an echo: a few characters make few
# grams, and any long text may cover them by chance. Better to miss an echo
# than to kill a real task.
ECHO_MIN_CHARS = 8

# ── Context on the card ───────────────────────────────────────
# Given one sentence, the reader cannot recognise what they were talking about —
# worse, cannot tell "I did say that" from "the transcriber misheard".
#
# Excerpt radius, measured on 79 real proposals:
#   ±5 min   median 1,320 chars / max 4,038
#   ±10      median 2,494 / max 9,165       <- default
#   ±15      median 3,275 / max 13,649      too long on a phone
CONTEXT_WIN_MIN = 10
# Lines kept either side. **Lines are the unit a person reads** — ten minutes
# alone may be three sentences, ten minutes with four people setting up a tent
# forty sentences and 2,500 characters (measured). Cut by time alone, the one
# sentence is buried in the middle, which is no context at all. The time window
# becomes an outer guard: a sentence after half an hour of silence is not
# context.
CONTEXT_MAX_LINES = 8
QUOTE_MARK_ZH = "  ⟵ 就是这句"
QUOTE_MARK_EN = "  ⟵ this one"

# ── Two recordings of one sentence ────────────────────────────
# With the wearable and Plaud Desktop both running in a meeting, one sentence
# has two transcripts. The cost is not looks: it is half of context_max_lines'
# eight-line budget. The measurements behind the threshold are in
# importers/plaud.py, beside the code that does the merging.
DEDUPE_THRESHOLD = 0.9
DEDUPE_MIN_CHARS = 8  # shorter than this (whitespace removed) never merges
# The time gate is in **seconds**: measured, the two tracks align within 1.4 s,
# and ±5 s leaves 3.5× margin (see importers/plaud.py).
DEDUPE_NEAR_MS = 5000

# ── One task, said twice ──────────────────────────────────────
# A thing that matters comes up more than once — "I need to book the car in",
# and forty minutes later "remind me, the car service". Each window owns its
# own quote, so each finds it, and the drawer listed it twice.
#
# Text similarity cannot tell these apart, measured on titles: a restatement
# scored 0.43–0.48 while "send Maya the numbers" / "send Sam the numbers" — two
# tasks — scored 0.83. So the model judges, once per day and only when the
# round found something new: one short call over the day's titles, not one per
# window. The later one is held as `duplicate`, shown faded, and Undo brings
# it back — a wrong merge is visible and reversible, not lost.

# ── Hardware marks ────────────────────────────────────────────
# The button pressed mid-recording. It is **the only direct human input** in the
# whole chain — confidence is the model grading itself, speakers is inferred
# from the room, echo_score is a text comparison: all guesses. So a mark
# overrides those gates rather than sitting beside them.
#
# But it means "probably something here, look twice", not "certainly something
# here", and certainly not "only here" — scanning only marked windows would
# reduce the system to a manual notepad.
MARK_NEAR_MIN = 5     # how close a proposal must be to a mark to count as marked.
                      # The whole window would dilute "this moment" into a
                      # 30-minute label.


PROMPT_ZH = """下面是一个人 {day} 随身录音的逐字稿片段。**只判断分隔线之后
那一段**，它前后的内容只是上下文，帮你听懂在说什么，不要从里面抽任务。

找出他在这段话里露出来的、之后要处理的事，分四类：

  question  他撞上一个没弄清的问题。判据是**他真的不知道，而且这个答案
            查得到**。「这个接口为什么返回 403，我也不知道」算；
            「他怎么能这么想呢」不算 —— 那是感慨，不是真在问。
  errand    要去办的具体事情：订、买、交、约、改、发、取消。
  followup  对**别人**许下的承诺：「我回头把那个发给你」。
  idea      想做但还没定下来的事：「也许该做个 X」。**明确要求记下来的
            也算**：「这个是个不错的 idea，帮我记一下」—— 值不值得做他
            已经替你判断过了，你只要把它记准。

录音会录到身边所有人。**只抽戴录音笔的这个人自己要处理的事**：他自己说
要做的，或者别人当面让他做的。旁边的人在说他们自己的安排、电视或视频里
的人在说话，都不是他的任务。分不清是谁的事，就别输出。

不确定就别输出。**宁可漏，不要编** —— 每一条都会变成一件推到他面前、
要他伸手处理的事，编出来的那条比漏掉的那条贵得多。

quote 必须从分隔线之后的原文**逐字照抄**，一个字都不能改、不能顺。
改过的引文核对不了，那条提案也就没用了。

输出 JSON 数组，没有就输出 []：
{{"time":"HH:MM","type":"question|errand|followup|idea",
  "domain":"work|personal",
  "task":"一句话说清要做什么，用中文写","quote":"逐字原文","confidence":"high|medium|low"}}

task 一律用中文写，不管原话是什么语言；quote 照抄原文，不翻译。

只输出 JSON，不要别的。
{marks}
{text}"""

PROMPT_EN = """Below is an excerpt from a person's wearable recorder for {day}.
**Judge only the part after the divider** — what surrounds it is context to
help you follow the conversation; never take a task from it.

Find the things they revealed they'll need to deal with later, in four kinds:

  question  they hit something they haven't figured out. The test is that
            they genuinely don't know AND the answer is findable. "Why does
            that endpoint return 403, I have no idea" counts; "how could he
            possibly think that" does not — that's a sigh, not a question.
  errand    a concrete thing to do: book, buy, file, schedule, change, send.
  followup  a promise made to **someone else**: "I'll send you that later".
  idea      something they want to do but haven't decided on. **An explicit
            "note this down for me" counts too** — "that's a good idea, note
            this down": they have already judged it worth keeping; your job is
            only to record it accurately.

The recorder hears everyone nearby. **Only take what the wearer has to deal
with**: what they said they would do, or what someone asked them to do. People
nearby talking about their own plans, or someone on a TV or video, are not the
wearer's tasks. If you cannot tell whose it is, output nothing.

If unsure, output nothing. **Prefer missing one over inventing one** — every
item becomes a thing pushed in front of them to act on, and an invented one
costs far more than a missed one.

`quote` must be copied **verbatim** from the text after the divider — not a
word changed or tidied. A reworded quote can't be checked against anything.

Output a JSON array, or [] if there are none:
{{"time":"HH:MM","type":"question|errand|followup|idea",
  "domain":"work|personal",
  "task":"one sentence saying what needs doing, in English","quote":"verbatim","confidence":"high|medium|low"}}

Write `task` in English whatever language was spoken; `quote` stays exactly as
said, untranslated.

Output only JSON.
{marks}
{text}"""


def _prompt() -> str:
    from ... import infer
    return PROMPT_ZH if infer.lang() == "zh" else PROMPT_EN


def _conf() -> dict:
    return config.load()


# ── Window rendering (the old lane's) ─────────────────────────

def render(events, a: int, b: int, lo_ms: int, ctx: int = CONTEXT_MIN) -> str:
    """The speech transcript of window [a, b), with ctx minutes of context either
    side — the old lane's own rendering.

    Kept because its output is what the old cache (task_digests) recorded, and
    the cache bridge and its test compare against it byte for byte. The lane
    itself renders through render_window below.

    **No speech in the target stretch returns an empty string** — there is
    nothing to judge, a call would be waste, and the model would dutifully
    extract something from the context.
    """
    from ... import render as R
    rows = []
    for e in events:
        if e.get("source") != "plaud":
            continue
        text = (e.get("text") or "").strip()
        if not text:
            continue                    # a segment with no text is no evidence
        m = (e["ts"] - lo_ms) // 60000
        if m < a - ctx or m >= b + ctx:
            continue
        rows.append({"ts": e["ts"], "m": m, "text": text,
                     "speaker": e.get("speaker") or "?",
                     "file_id": e.get("file_id")})

    # One sentence captured by two tracks is fed once — otherwise it becomes two
    # proposals, and since the dedupe key is the quote and two transcriptions
    # differ, the key cannot stop it. It happened: one sentence about contacting
    # two people became two proposals, each needing a tap. A single-track window
    # is unchanged to the byte — the cache key is this text.
    rows, _ = dedupe_recordings(rows)

    before, inside, after = [], [], []
    for r in rows:
        row = f"{R.hm(r['m'])} [{r['speaker']}] {r['text']}"
        m = r["m"]
        (inside if a <= m < b else (before if m < a else after)).append((m, row))

    if not inside:
        return ""

    def fmt(rows):
        return [r for _, r in sorted(rows, key=lambda x: x[0])]

    out = fmt(before) + [TARGET_MARK] + fmt(inside)
    if after:
        out += [AFTER_MARK] + fmt(after)
    return "\n".join(out)


# ── The old window cache ──────────────────────────────────────

# This lane's own cache table. The mechanism is analyzer.load_digests /
# save_digests; only the table name is ours.
DIGEST_TABLE = "task_digests"


def load_digests(con, day: str, model: str) -> dict:
    from ... import analyzer
    return analyzer.load_digests(con, DIGEST_TABLE, day, model)


# ── Judging ───────────────────────────────────────────────────

# Confidence order. **unknown is not in it** — see _above_bar.
CONF_ORDER = ("low", "medium", "high")


def _norm_conf(v) -> str:
    v = (v or "").strip().lower()
    return v if v in CONF_ORDER else "unknown"


def _above_bar(conf: str) -> bool:
    """Good enough to send you? The bar is [actions] min_confidence in config;
    none by default.

    **unknown always passes.** A model that gives no confidence is not
    unconfident — switch to one that won't output the field, and treating silence
    as a low score would make a whole day's proposals vanish without an error.
    It is the same pair to keep apart as "no data" and "not imported".
    """
    bar = ((_conf().get("actions") or {}).get("min_confidence") or "").strip().lower()
    if bar not in CONF_ORDER or conf == "unknown":
        return True
    return CONF_ORDER.index(conf) >= CONF_ORDER.index(bar)


def _owns_quote(window_text: str, quote: str) -> bool:
    """Is this proposal **this window's** output?

    Every window carries 30 minutes of context either side (to help the model
    follow the conversation). But what is said in the context belongs to **other**
    windows — without this, a sentence near a boundary is extracted by both
    neighbours and becomes two identical cards. Measured: one sentence at 16:45
    was extracted twice exactly this way.

    The (day, quote) key cannot stop it: the two quotes differ by a few
    characters (one picked up a trailing particle), so the keys differ. The right
    place to stop it is ownership, not deduplication.

    A quote found nowhere (the model smoothed out filler words) **counts**: a
    miss costs more than a duplicate, and the window that owns the sentence
    would extract it too — the worst case is two cards, not none.
    """
    q = (quote or "").strip()
    if not q:
        return False
    target = window_text.split(TARGET_MARK, 1)[-1].split(AFTER_MARK, 1)[0]
    if q in target:
        return True
    return q not in window_text


MARK_BLOCK_ZH = """
── 他在这段按了标记 ──────────────────────────────────────
录音中途他短按了设备上的键。这通常意味着**这里有该办的事** ——
请更仔细地看这一段，别放过一句带过的交代。

但没有就是没有：**不要**为了对得起这个标记编一条出来。宁可这一段
输出空数组。

设备自己在标记点做的摘要（可能有出入，以逐字稿为准）：
{summary}
"""

MARK_BLOCK_EN = """
── They pressed the marker here ──────────────────────────
They short-pressed the button on the device mid-recording. That usually
means **there is something to be done here** — read this stretch more
carefully and don't skip a commitment made in passing.

But absent is absent: do **not** invent one to justify the marker. An
empty array for this stretch is fine.

The device's own summary at the marker (may be off; the transcript wins):
{summary}
"""


def marks_in(events, a: int, b: int, lo_ms: int) -> list[dict]:
    """Hardware marks within the target stretch [a, b). Marks in the context do
    not count — the same rule as quote ownership: a mark belongs to its window."""
    out = []
    for e in events:
        if e.get("source") != "plaud" or e.get("kind") != "mark":
            continue
        m = (e["ts"] - lo_ms) // 60000
        if a <= m < b:
            out.append({**e, "_min": m})
    return sorted(out, key=lambda x: x["_min"])


def render_marks(marks: list[dict]) -> str:
    """Marks → their section of the prompt. No marks returns an empty string
    (the section does not appear)."""
    if not marks:
        return ""
    from ... import render as R, infer
    lines = []
    for m in marks:
        head = f"{R.hm(m['_min'])} {m.get('title') or ''}".rstrip()
        lines.append(head)
        for row in (m.get("content") or "").splitlines():
            if row.strip():
                lines.append(f"    {row.strip()}")
    tpl = MARK_BLOCK_ZH if infer.lang() == "zh" else MARK_BLOCK_EN
    return tpl.format(summary="\n".join(lines))


def _norm_domain(v) -> str:
    return v if (v := (v or "").strip().lower()) in DOMAINS else "unknown"


def _speakers_in(events, lo_ms: int, a: int, b: int) -> int:
    """How many distinct speakers in [a, b).

    **Counted locally, never asked of the model** — it is a deterministic fact,
    and handing it to a model costs money and gets it wrong.

    **The maximum across recordings**, not the union. Speaker labels are per
    recording and mean nothing across recordings, which rules out both obvious
    ways: counting labels makes two tracks' "Speaker 1" collide (four people
    counted as two); counting (file_id, label) counts one person once per track
    (measured: a nine-person meeting, nine labels on each of two tracks, union 18).

    Why the maximum: two tracks are independent diarizations of one scene, and
    neither recorded anyone extra — there were at least as many people as the
    track that heard most. In a meeting on headphones the wearable hears only you
    and the software hears four remote voices; the answer is 4.

    ⚠️ The claims lane's MIN_SPEAKERS gate counts the same thing; change both.
    """
    per: dict = {}
    for e in events:
        if not (a <= (e["ts"] - lo_ms) // 60000 < b):
            continue
        if not (e.get("text") or "").strip():
            continue
        per.setdefault(e.get("file_id"), set()).add(e.get("speaker") or "?")
    return max((len(v) for v in per.values()), default=0)


def _norm_type(v) -> str:
    """An unrecognised kind is not guessed at. A guess invents information, and a
    wrong one only pollutes looking back at what was sent."""
    v = (v or "").strip().lower()
    return v if v in TYPES else "unknown"


def _grams(s: str, n: int = ECHO_GRAM) -> set:
    from ... import analyzer
    return analyzer.grams(s, n)


def echo_score(quote: str, cands, window=None, at=None) -> float:
    """How much of this quote falls inside some nearby text. 0..1.

    **Coverage, not Jaccard.** The agent prompt is often much longer than the
    sentence (links added, context filled in, a template mixed in), and Jaccard
    is diluted by length so real echoes fail to match. The question is "how much
    of the quote does that text cover", one way.

    **Not literal containment.** Dictation garbles words — a product name heard
    as something else (say, "Kestrel" as "custard"). Measured,
    literal containment caught almost none; 3-grams caught them all.
    """
    return echo_match(quote, cands, window=window, at=at)[0]


def echo_match(quote: str, cands, window=None, at=None) -> tuple:
    """(score, the text it matched). The text is kept so the call can be
    checked afterwards."""
    q = (quote or "").strip()
    if len(q.replace(" ", "")) < ECHO_MIN_CHARS:
        return 0.0, None
    qg = _grams(q)
    if not qg:
        return 0.0, None
    win = ECHO_WINDOW_MIN if window is None else window
    best = (0.0, None)
    for m, text in cands:
        if at is not None and abs(m - at) > win:
            continue
        g = _grams(text or "")
        if not g:
            continue
        sc = len(qg & g) / len(qg)
        if sc > best[0]:
            best = (sc, text)
    return best


def echo_threshold() -> float:
    """0 or less turns the echo filter off."""
    v = (_conf().get("actions") or {}).get("echo_threshold")
    return float(v) if isinstance(v, (int, float)) else ECHO_THRESHOLD


def same_utterance(a: str, b: str) -> float:
    """How much two texts look like the same sentence. 0..1.

    **Containment (how much of the shorter lies in the longer), not Jaccard.**
    Two tracks split sentences differently as a rule; one cuts a sentence in two
    and the other does not, so the longer piece has extra, and Jaccard's union
    denominator punishes that — measured, it missed more than half the real
    duplicates. The question is "is the short one wholly covered by the other".

    Same direction and same 3-grams as echo_score; echo_score always takes the
    quote as numerator, this takes the shorter.

    ⚠️ The measure **is only trustworthy on long enough samples**. A short
    sentence has few grams, and they scatter into any long one for a high score
    (a real false merge: a five-character "uh, I'm on it" scored 0.75 against an
    unrelated turn). The length gate is in dedupe_recordings — the measure
    answers "how alike", and "is this sample worth trusting" is another question;
    mixing them makes both unclear.
    """
    ga, gb = _grams(a or ""), _grams(b or "")
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def dedupe_threshold() -> float:
    """0 or less turns cross-recording merging off."""
    v = (_conf().get("actions") or {}).get("dedupe_threshold")
    return float(v) if isinstance(v, (int, float)) else DEDUPE_THRESHOLD


def dedupe_recordings(rows: list[dict], quote: str = "") -> tuple:
    """(rows kept, how many merged). rows look like {"m", "text", "file_id", ...}.

    Only rows from **different file_ids**, close in time, and alike as text are
    merged. The same thing twice in one recording is something said twice;
    merging it would rewrite the facts.

    Which one stays: the one carrying the quote beats everything (it anchors the
    card; without it the excerpt has no centre and transcript_excerpt falls back
    to the middle); otherwise the longer, since the shorter was usually cut.
    """
    bar = dedupe_threshold()
    if bar <= 0:
        return list(rows), 0
    q = (quote or "").strip()
    kept: list[dict] = []
    merged = 0
    for r in rows:
        if len(r["text"].replace(" ", "")) < DEDUPE_MIN_CHARS:
            kept.append(r)                    # too short to trust a match
            continue
        for i, k in enumerate(kept):
            if k.get("file_id") == r.get("file_id"):
                continue                      # the same recording: said again, not a repeat
            if len(k["text"].replace(" ", "")) < DEDUPE_MIN_CHARS:
                continue
            if abs(k["ts"] - r["ts"]) > DEDUPE_NEAR_MS:
                continue
            if same_utterance(k["text"], r["text"]) < bar:
                continue
            merged += 1
            k_has = bool(q) and q in k["text"]
            r_has = bool(q) and q in r["text"]
            if r_has and not k_has:
                kept[i] = r
            elif not k_has and len(r["text"]) > len(k["text"]):
                kept[i] = r
            break
        else:
            kept.append(r)
    return kept, merged


def echo_pool(events, lo_ms: int) -> list[tuple]:
    """The candidates: agent prompts plus clipboard text.

    The clipboard is the stronger evidence, measured: on one day five quotes
    matched only 4% on the agent side and 61–90% on the clipboard. The clipboard
    holds the dictation tool's near-verbatim, unedited text; the copy in the
    agent's history has often been edited once already.
    """
    out = []
    for e in events:
        src, kind = e.get("source"), e.get("kind")
        if src in ("butler", "claude") or (src == "live" and kind == "clip"):
            text = (e.get("text") or "").strip()
            if text:
                out.append(((e["ts"] - lo_ms) // 60000, text))
    return out


def _minute_of(hhmm, fallback: int) -> int:
    """The model's HH:MM → minute of the day. If it cannot say, the window's
    start — half an hour off does not change a ±20-minute judgement."""
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError, TypeError):
        return fallback


PLUGIN = "actions"


def _self():
    import sys
    return sys.modules[__name__]


# ── One record shape over two stores ──────────────────────────
#
# Proposals found before this lane moved onto the core store are in the frozen
# `proposals` table; everything since is in analyzer_items. Both are read, and
# every function below works on records in the old table's vocabulary — one
# path through the logic, store-aware only where a record is written back.
#
# Frozen means no new findings go there. The lifecycle of findings already
# there continues in place: a proposal queued before the move is still sent,
# and your verdict on it still lands on it. Ids never collide — new finding ids
# start above every id the old table used (db.items_after_legacy) — so an id
# alone says which store a record lives in.

_LEGACY_COLS = ("id, day, t, task, quote, status, type, confidence, domain, "
                "marked, speakers, echo_score, echo_text, win_a, dispatch_id, "
                "verdict, verdict_note, result, model, created_ms")


def records(con, day: str | None = None, status=None) -> list[dict]:
    """Every proposal, old and new, as one list in the old table's shape."""
    q, args = f"SELECT {_LEGACY_COLS} FROM proposals", []
    if day is not None:
        q += " WHERE day=?"
        args.append(day)
    keys = [k.strip() for k in _LEGACY_COLS.split(",")]
    out = [dict(zip(keys, r), src="legacy") for r in con.execute(q, args)]
    for it in analyzer.load_items(con, PLUGIN, day=day):
        m = it["meta"]
        out.append({
            "id": it["id"], "day": it["day"], "t": it.get("t"),
            "task": it.get("task") or "", "quote": it["key"],
            "status": it["state"], "type": it.get("type"),
            "confidence": it.get("confidence"), "domain": it.get("domain"),
            "marked": 1 if it.get("marked") else 0,
            "speakers": it.get("speakers"),
            "echo_score": it.get("echo_score"), "echo_text": it.get("echo_text"),
            "win_a": it["win_a"], "dispatch_id": m.get("dispatch_id"),
            "verdict": m.get("verdict"), "verdict_note": m.get("verdict_note"),
            "result": m.get("result"), "model": it["model"],
            "dup_of": m.get("dup_of"),
            "created_ms": it["at_ms"], "src": "item"})
    if status is not None:
        want = {status} if isinstance(status, str) else set(status)
        out = [r for r in out if r["status"] in want]
    out.sort(key=lambda r: (r["day"], r["t"] or "", r["id"]))
    return out


def record(con, pid: int) -> dict | None:
    got = con.execute(f"SELECT {_LEGACY_COLS} FROM proposals WHERE id=?",
                      (pid,)).fetchone()
    if got:
        keys = [k.strip() for k in _LEGACY_COLS.split(",")]
        return dict(zip(keys, got), src="legacy")
    it = analyzer.get_item(con, PLUGIN, pid)
    if it is None:
        return None
    return next((r for r in records(con, day=it["day"]) if r["id"] == pid), None)


def _write(con, pid: int, status: str, expect=None, **fields) -> bool:
    """Move one proposal, in whichever store it lives. Returns whether it moved.

    expect is the status it must be in; a move from anywhere else is refused,
    so a verdict that landed first is not overwritten by a late send.
    """
    rec = record(con, pid)
    if rec is None:
        return False
    if expect is not None:
        allowed = {expect} if isinstance(expect, str) else set(expect)
        if rec["status"] not in allowed:
            return False
    if rec["src"] == "legacy":
        sets = ["status=?"] + [f"{k}=?" for k in fields]
        con.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id=?",
                    (status, *fields.values(), pid))
        con.commit()
        return True
    return analyzer.update_item(con, PLUGIN, pid, state=status,
                                meta={k: v for k, v in fields.items()})


# ── Scanning a day ────────────────────────────────────────────

def _bridge_cache(con, day: str, model: str) -> int:
    """Carry this lane's old cache over to the core's, window by window.

    The old cache (task_digests) recorded which exact window texts had been
    answered. Without carrying it over, the first round after the move would
    ask every window of the recent days again — paying to get the same findings,
    worded a little differently, which the old table's quotes would not catch.
    Where the old cache answered exactly the text the window renders to now, the
    window is recorded as answered. Anything that differs is asked, as it would
    have been.
    """
    old = load_digests(con, day, model)
    if not old:
        return 0
    n = 0
    for w in analyzer.windows(con, _self(), day, model):
        prev = old.get(w["a"])
        if not prev or prev[0] != w["text"] or not prev[1]:
            continue
        if analyzer.cached(con, PLUGIN, day, model, w["a"], w["digest"]):
            continue
        if con.execute("SELECT 1 FROM analyzer_runs WHERE plugin=? AND day=? "
                       "AND model=? AND win_a=?",
                       (PLUGIN, day, model, w["a"])).fetchone():
            continue          # the core has its own answer for this window
        analyzer.mark_ran(con, PLUGIN, day, model, w["a"], w["digest"])
        n += 1
    return n


DUP_PROMPT_EN = """These are to-dos picked out of one person's recorded day, each
with an id and the time it was said. Some may be **the same to-do said again**
in other words. Find those.

Same means one action on one thing: "book the car service" and "remind me to
get the car serviced" are the same. Different people, objects or dates make
different to-dos: "send Maya the numbers" and "send Sam the numbers" are two.
When unsure, they are different.

Only the ids marked NEW may be called repeats, and only of an id said earlier.

Output a JSON array of [repeat_id, earlier_id] pairs, or [] if there are none.
Output only JSON.

{items}"""

DUP_PROMPT_ZH = """下面是从一个人一天的录音里抽出来的待办，每条带 id 和说的时间。
其中可能有**同一件事换个说法又说了一遍**的。把这些找出来。

同一件事，指对同一个对象做同一个动作：「约车保养」和「提醒我去做汽车保养」
是同一件。对象、人或日期不同就是两件：「把数字发给小王」和「把数字发给小李」
是两件。拿不准就当成不同。

只有标了 NEW 的 id 可以被判成重复，而且只能是更早那条的重复。

输出 JSON 数组，每项是 [重复的 id, 更早的 id]；没有就输出 []。只输出 JSON。

{items}"""


def mark_duplicates(con, day: str, ids, call=None, model: str | None = None,
                    log=None) -> int:
    """Hold back this round's new proposals that restate one already found
    that day. Only new ones move — what was listed before stays where it is —
    and nothing moves when the model cannot be asked: a failed check costs a
    duplicate in the list, never a task. Returns how many were held."""
    from ... import analyzer as AN, infer, providers
    log = log or (lambda *_: None)
    new = set(ids or [])
    rows = [r for r in records(con, day=day)
            if r["status"] not in ("echo", "duplicate")]
    if not any(r["id"] in new and r["status"] == "proposed" for r in rows) \
            or len(rows) < 2:
        return 0
    rows.sort(key=lambda r: (_minute_of(r["t"], r["win_a"] or 0), r["id"]))
    items = "\n".join(f"{r['id']} {r['t'] or '--:--'}"
                      f"{' NEW' if r['id'] in new else ''}  {r['task']}"
                      for r in rows)
    tpl = DUP_PROMPT_ZH if infer.lang() == "zh" else DUP_PROMPT_EN
    try:
        call = call or providers.resolve(model or infer.MODEL)
        pairs = AN.parse_json(call(tpl.format(items=items)))
    except Exception as e:                              # noqa: BLE001
        log(f"  ⚠ actions {day}: could not check for repeats: {e}")
        return 0
    order = {r["id"]: i for i, r in enumerate(rows)}
    held = 0
    for p in pairs if isinstance(pairs, list) else []:
        try:
            rep, first = int(p[0]), int(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        # Trust the rule, not the reply: a repeat is new, the original is
        # listed, and earlier.
        if rep not in new or first not in order or rep not in order \
                or order[first] >= order[rep]:
            continue
        if _write(con, rep, "duplicate", expect="proposed", dup_of=first):
            held += 1
    return held


def scan(con, day: str, model: str | None = None, call=None, log=None,
         fresh: bool = False) -> dict:
    """Scan one day's recordings. Returns {"fresh", "windows", "fresh_calls",
    "reused", "failed", "inserted", "echo_filtered", "domain_filtered"} — the
    same shape this lane always returned, so the round's log line is unchanged.

    Idempotent: a proposal is identified by its quote within the day, and one
    you dismissed does not come back. Incremental: a window whose text is
    byte-identical to last round is not asked again. Both are the core's now
    (KEY, and the run cache); this is the lane's view of the result.
    """
    from ... import db, infer
    model = model or infer.MODEL
    db.items_after_legacy(con)
    if not fresh:
        _bridge_cache(con, day, model)
    r = analyzer.run(con, PLUGIN, _self(), day, model, call=call, log=log,
                     fresh=fresh)
    dupes = mark_duplicates(con, day, r.get("ids"), call=call, model=model,
                            log=log)
    new = [x for x in records(con, day=day) if x["src"] == "item"
           and x["id"] in set(r.get("ids") or [])]
    skip = {str(d).strip().lower() for d in
            ((_conf().get("actions") or {}).get("skip_domains") or [])}
    fresh_rows = [x for x in new if x["status"] not in ("echo", "duplicate")]
    return {"fresh": fresh_rows, "windows": r["called"], "duplicates": dupes,
            "fresh_calls": r["called"] - r["failed"], "reused": r["reused"],
            "failed": r["failed"], "inserted": len(new),
            "echo_filtered": len(new) - len(fresh_rows) - dupes,
            "domain_filtered": sum(1 for x in fresh_rows if not x["marked"]
                                   and (x["domain"] or "unknown") in skip)}


# ── Sending ───────────────────────────────────────────────────

def span_context(con, day: str, minute: int) -> dict | None:
    """"What was going on then" — **reusing the spans inference already computed,
    with no new model call**.

    The day's activity is already in the inferences table (activity, who with,
    category). Asking a model again would buy a second answer that may
    contradict the timeline — and two chains describing one stretch differently
    is exactly the "both look right, which do I believe" this project cannot
    accept.

    Nothing returns None, not an empty string: "inference has not run on that
    day" and "that half hour really was empty" are different, and the card must
    be able to say which.
    """
    from ... import infer, tz
    # Only the day's current cut: after a timezone change an old row at the
    # same minute of the day is a different moment.
    lo, hi = tz.bounds(con, day)
    rows = list(con.execute(
        "SELECT a_min, b_min, cat, payload, model FROM inferences "
        "WHERE day=? AND day_lo=? AND day_n=? AND a_min<=? AND b_min>? "
        "ORDER BY a_min", (day, lo, (hi - lo + 1) // 60_000, minute, minute)))
    if not rows:
        return None
    # A day may have results from several models. Prefer the configured one,
    # so the card and the timeline on the page are the same judgement.
    pick = next((r for r in rows if r[4] == infer.MODEL), rows[0])
    a, b, cat, payload, _ = pick
    try:
        d = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    return {"a": a, "b": b, "cat": cat,
            "activity": d.get("activity") or "",
            "with": d.get("with") or ""}


def transcript_excerpt(con, day: str, minute: int, quote: str,
                       win: int | None = None,
                       max_lines: int | None = None) -> str:
    """The transcript win minutes either side, with the sentence marked.

    **Computed at send time, never stored.** The events are already in the
    database; a second copy would be two truths for one piece of data, and
    changing the window would need a rescan.

    With speakers: whether something was said to a person or to oneself shows
    only in that column.
    """
    from ... import analyze, render as R
    spec = _conf().get("actions") or {}
    if win is None:
        v = spec.get("context_win_min")
        win = v if isinstance(v, int) and v > 0 else CONTEXT_WIN_MIN
    if max_lines is None:
        v = spec.get("context_max_lines")
        max_lines = v if isinstance(v, int) and v > 0 else CONTEXT_MAX_LINES
    cap = max_lines
    lo, _ = analyze.day_bounds(day, con)
    rows = []
    for ts, payload in con.execute(
            "SELECT ts, payload FROM events WHERE source='plaud' "
            "AND ts>=? AND ts<? ORDER BY ts",
            (lo + (minute - win) * 60000, lo + (minute + win) * 60000)):
        try:
            d = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        text = (d.get("text") or "").strip()
        if not text:
            continue
        rows.append({"ts": ts, "m": (ts - lo) // 60000, "text": text,
                     "speaker": d.get("speaker") or "?",
                     "file_id": d.get("file_id")})

    # Merge one sentence captured by two recordings first — otherwise half the
    # max_lines budget is spent on repeats. **Before** trimming: trimming first
    # would leave half the room empty.
    rows, merged = dedupe_recordings(rows, quote)

    # A stretch captured by N recordings needs N times the lines to show as
    # much distinct content. Deduplication cannot make that up (0.9 removes only
    # 7%), but this step is deterministic and compares no text. It keeps what
    # "lines are the unit a person reads" means: eight lines are eight different
    # things said, not eight rows.
    cap = cap * max(1, len({r["file_id"] for r in rows}))

    out, hit = [], None
    for r in rows:
        line = f"{R.hm(r['m'])} [{r['speaker']}] {r['text']}"
        # Find "this one" among dozens of lines, or the excerpt is just a wall
        if hit is None and quote and quote.strip() and quote.strip() in r["text"]:
            line += quote_mark()
            hit = len(out)
        out.append(line)

    if not out:
        return ""
    # Trim to the budget around that sentence. If it cannot be found (the
    # transcript and the quote disagree), take the middle — better than the top.
    center = hit if hit is not None else len(out) // 2
    a, b = max(0, center - cap), min(len(out), center + cap + 1)
    kept = out[a:b]
    # What was left out **is said**: a silent cut reads as "that was all that
    # was said".
    if a > 0:
        kept.insert(0, _card("before").format(a))
    if b < len(out):
        kept.append(_card("after").format(len(out) - b))
    # What was merged **is said** too — the same rule.
    if merged:
        kept.append(_card("merged").format(merged))
    return "\n".join(kept)


# The card's own words, per language. Send-time text, not a cache key: changing
# these re-runs nothing. The zh wording is what the lane always sent.
_CARD_TEXT = {
    "zh": {"origin": "── 出处 ", "doing": "当时在：{}{}", "with": "（和{}一起）",
           "no_infer": "当时在做什么：这一天还没推断过（umbra infer 没跑到这天）",
           "said": "他的原话：「{}」", "around": "── 前后的录音 ",
           "around_note": "（随身录音的自动转写，**可能有听错的字**；"
                          "上面那条任务如果读着别扭，先看这段原文再判断）",
           "ask": "如果这件事缺了上下文没法做，先说你缺什么，别猜。",
           "before": "…（前面略去 {} 句）", "after": "…（后面略去 {} 句）",
           "merged": "…（合并了 {} 句两条录音都录到的重复）"},
    "en": {"origin": "── Where this came from ", "doing": "Doing at the time: {}{}",
           "with": " (with {})",
           "no_infer": "Doing at the time: this day has not been inferred yet "
                       "(umbra infer has not reached it)",
           "said": "Their words: \"{}\"", "around": "── The recording around it ",
           "around_note": "(automatic transcription from a wearable recorder — "
                          "**some words may be misheard**; if the task above reads "
                          "oddly, read this before judging)",
           "ask": "If this cannot be done without more context, say what is "
                  "missing first; don't guess.",
           "before": "… ({} earlier lines omitted)", "after": "… ({} later lines omitted)",
           "merged": "… ({} lines captured by both recordings merged)"},
}


def _card(key: str) -> str:
    from ... import infer
    return _CARD_TEXT["zh" if infer.lang() == "zh" else "en"][key]


def quote_mark() -> str:
    """The marker on the quoted line of the excerpt, in the card's language."""
    from ... import infer
    return QUOTE_MARK_ZH if infer.lang() == "zh" else QUOTE_MARK_EN


def item_prompt(con, day: str, t: str | None, task: str, quote: str) -> str:
    """The text of the item sent to the destination.

    **The words go with it** — the agent must have what you said, not only the
    model's rewording; if the rewording is off, an agent that sees the words can
    correct it.

    Two blocks of context follow, because with one sentence you cannot recognise
    what you were talking about, let alone tell "I did say that" from "the
    transcriber misheard".

    Disambiguation ("that one", "him") is left to the destination: an agent
    there may have your email, calendar and chat; Umbra has only speech.
    Whoever has the information does the guessing.
    """
    when = f"{day} {t}" if t else day
    head = _card("origin")
    parts = [task, "", head + "─" * (50 - len(head)), when]

    minute = _minute_of(t, -1) if t else -1
    ctx = span_context(con, day, minute) if minute >= 0 else None
    if ctx:
        who = _card("with").format(ctx["with"]) if ctx["with"] else ""
        parts.append(_card("doing").format(ctx["activity"], who)
                     + f"   [{_hm_span(ctx['a'], ctx['b'])} · {ctx['cat']}]")
    else:
        # A missing line must not read as "nothing was happening then"
        parts.append(_card("no_infer"))
    parts += ["", _card("said").format(quote)]

    excerpt = transcript_excerpt(con, day, minute, quote) if minute >= 0 else ""
    if excerpt:
        head = _card("around")
        parts += ["", head + "─" * (50 - len(head)), _card("around_note"), "",
                  excerpt]

    parts += ["", "─" * 50, _card("ask")]
    return "\n".join(parts)


def _hm_span(a: int, b: int) -> str:
    return f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}"


# At most this many sent per round. **Capped by default**, because the backlog
# the moment sending is switched on is where this lane hurts most: measured, a
# real database held 32 old proposals over 44 days of speech. Dozens of cards
# landing at once means nobody reads that inbox again.
MAX_PER_RUN = 5


def dispatchable(con, today: str | None = None) -> list[tuple]:
    """The proposals that qualify to go out this round, oldest first.

    Three filters, each with one job:
      status='proposed'   not sent yet
      min_confidence      ones the model itself is unsure of (none by default)
      dispatch_days       too old to revive (none by default) — something said
                          three weeks ago and left alone is usually dead, and
                          turning it into a card to deal with is noise
    Then cut to max_per_run. **What is cut stays queued** for the next round:
    silently dropping something you said for good is far worse than sending it
    three days late.
    """
    import datetime as _dt
    spec = _conf().get("actions") or {}
    # Marked ones first: when the cap cuts, one a person pressed a button for
    # must not be pushed out by one the model guessed.
    rows = [(r["id"], r["day"], r["task"], r["quote"], r["confidence"], r["t"],
             r["domain"], r["speakers"], r["marked"])
            for r in sorted(records(con, status="proposed"),
                            key=lambda r: (-(r["marked"] or 0), r["day"],
                                           r["t"] or "", r["id"]))]
    # "No reminders for work." unknown always passes — the model saying
    # nothing is not the model saying work, and treating silence as a match
    # would swallow a whole day after a model switch.
    # Marked proposals skip all three gates below. Each of them guesses one thing
    # (sure enough? work? talking to oneself?), and a mark is the person's own
    # press — nobody presses a button for something they don't want.
    skip = {str(d).strip().lower() for d in (spec.get("skip_domains") or [])}
    if skip:
        rows = [r for r in rows if r[8] or (r[6] or "unknown") not in skip]

    # Proposals with only one person talking are probably said to an AI
    # (measured echo rate: 68% with one speaker, 34% with three). **Off by
    # default** — the echo filter already catches most, this gate catches the
    # rest, and what it wrongly kills is "remembering something real while
    # alone", which most deserves keeping. An unknown count (old proposals,
    # NULL) is not blocked: reading NULL as 1 would silently swallow everything
    # from before the field existed.
    ms = spec.get("min_speakers")
    if isinstance(ms, int) and ms > 0:
        rows = [r for r in rows if r[8] or r[7] is None or r[7] >= ms]

    days = spec.get("dispatch_days")
    if isinstance(days, int) and days > 0:
        today = today or str(_dt.date.today())
        floor = str(_dt.date.fromisoformat(today) - _dt.timedelta(days=days - 1))
        rows = [r for r in rows if r[1] >= floor]

    rows = [r[:6] for r in rows if r[8] or _above_bar(r[4] or "unknown")]
    cap = spec.get("max_per_run")
    cap = cap if isinstance(cap, int) and cap > 0 else MAX_PER_RUN
    return rows[:cap]


def _item(con, pid: int, day: str, t: str | None, task: str,
          quote: str) -> dict:
    """The item this lane sends. Shared with preview, so that what you are shown
    before sending is what is sent — a preview built separately drifts, and the
    drift is invisible until something goes out that nobody reviewed."""
    return {"title": task[:80],
            "body": item_prompt(con, day, t, task, quote),
            "key": f"umbra:{day}:{pid}",
            "visible": True,
            # Held for a person, never started on its own. A proposal is
            # something to decide about, not something to run.
            "interactive": True}


def preview(con, today: str | None = None) -> list[dict]:
    """**Exactly** what the next dispatch would send. Nothing is sent.

    Deliberately does not check for credentials — if "look before sending"
    required credentials first, it would come one step too late.
    """
    return [{"id": r[0], "day": r[1], "t": r[5], "task": r[2],
             "quote": r[3], "confidence": r[4],
             "body": _item(con, r[0], r[1], r[5], r[2], r[3])}
            for r in dispatchable(con, today=today)]


def _sink_name() -> str | None:
    """Which destination this lane sends to, or None when sending is off.

    Both halves of the gate live in sink.enabled_for. The old
    [actions] dispatch = "butler" key is still honoured so an upgrade does not
    silently stop sending.
    """
    from ... import sink as S
    name = S.enabled_for("actions", _conf())
    if name:
        return name
    legacy = ((_conf().get("actions") or {}).get("dispatch") or "").strip()
    return legacy or None


def dispatch_enabled() -> bool:
    """Is sending switched on?

    Asks the same question dispatch() acts on. It used to check only the old
    `[actions] dispatch = "butler"` key, so a sink configured the documented way
    — [plugins.actions] sink + send — was refused at the gate while dispatch()
    itself would have sent: the scheduled round never got as far as asking it.

    Extraction is local, free and has no side effects, so it runs whenever the
    lane is enabled. Sending is not: it creates around twenty cards somewhere
    else, and installing something that immediately starts writing into an
    inbox is a decision made before anyone has seen a single proposal.
    """
    return _sink_name() is not None


def dispatch(con, log=print, today: str | None = None) -> int:
    """Send the proposals that have not gone out yet. Returns how many went.

    No project or routing hint goes with them. The destination knows which
    projects exist and Umbra does not, so the decision is made on the side that
    has the information; anything it cannot place lands in its own catch-all.

    Held-back and truncated proposals **stay in the database** with their status
    untouched. Dropping them would make "it found this and chose not to send it"
    and "it found nothing" the same thing, and those two are fixed in completely
    different places. `umbra tasks list` still shows them.
    """
    from ... import sink as S
    name = _sink_name()
    if not name:
        return 0
    send = S.resolve(name)["send"]
    sent = 0
    for pid, day, task, quote, _conf_v, t in dispatchable(con, today=today):
        try:
            tid = send(_item(con, pid, day, t, task, quote))
        except S.Duplicate as d:
            # This lane keys on the proposal id, so a duplicate means the
            # destination already has it. Recording that is the whole point of
            # the key — otherwise every round resends everything.
            tid = d.receipt
        except S.Fatal:
            # The destination is misconfigured and every remaining item would
            # land the same way — each one running unattended. Stop rather than
            # lose a day's worth one at a time.
            con.commit()
            raise
        except Exception as e:                            # noqa: BLE001
            # **Status does not move on failure.** Marking it dispatched would
            # say it was sent when it was not.
            log(f"  ✗ [{pid}] dispatch failed; status stays proposed: {e}")
            continue
        _write(con, pid, "dispatched", dispatch_id=tid)
        S.record(con, PLUGIN, str(pid), name, S.SENT, external_id=tid)
        sent += 1
    con.commit()
    return sent


# ── What you tapped, coming back ──────────────────────────────

_KEY = re.compile(r"^umbra:(\d{4}-\d{2}-\d{2}):(\d+)$")

# The three buttons at the destination → the local state.
#
# "Already done" and "not a task" both close a proposal, but **only the second
# means extraction was wrong**. Already done means it was extracted perfectly and
# is merely stale — mixing it into the negatives would teach the model to miss
# that kind of real task. The two buttons sit close together in the UI; here
# they must stay far apart.
VERDICTS = {"ran": "approved",
            "not_a_task": "dismissed",
            "already_done": "dismissed"}


def record_verdicts(con, items) -> int:
    """Match what was tapped at the destination back to local proposals, by
    externalEventKey.

    Keys that are not recognised are skipped quietly — the destination also holds
    tasks from other sources, with keys in other shapes, and that is not an
    error.
    """
    n = 0
    for it in items or []:
        m = _KEY.match(str((it or {}).get("externalEventKey") or ""))
        if not m:
            continue
        verdict = (it.get("verdict") or "").strip()
        status = VERDICTS.get(verdict)
        if not status:
            continue
        pid = int(m.group(2))
        rec = record(con, pid)
        if rec is None or rec["day"] != m.group(1):
            continue
        if _write(con, pid, status, verdict=verdict,
                  verdict_note=it.get("verdictNote")):
            n += 1
    con.commit()
    return n


def pull_verdicts(con, log=print) -> int:
    """Collect what you tapped. Returns how many verdicts landed.

    In one request rather than one per proposal. The question is "what did
    somebody act on", not "what happened to task X" asked N times, and this lane
    is in no hurry. Receipts the destination names that we never issued are
    skipped, so a destination that reports everything it knows is still correct.
    """
    from ... import sink as S
    name = _sink_name()
    if not name:
        return 0
    fn = S.resolve(name).get("feedback")
    if not callable(fn):
        # A destination that reports nothing back is a legitimate one to send to;
        # it just means the taps happen somewhere Umbra cannot see.
        return 0
    try:
        items = [e.get("result") for e in fn() or []]
    except Exception as e:                                # noqa: BLE001
        log(f"  ✗ could not collect verdicts: {e}")
        return 0
    return record_verdicts(con, [i for i in items if i])


def close(con, pid: int, verdict: str) -> bool:
    """A verdict given here rather than at the destination — the page's ✓ and
    ✕, for anyone whose sink only receives. Same two words, same meaning:
    only not_a_task is an extraction error and lands in negatives().

    Only an open proposal closes; one already closed keeps the verdict it
    has. A proposal closed before it was sent is never sent: dispatch takes
    only `proposed`.
    """
    if verdict not in ("already_done", "not_a_task"):
        raise ValueError(f"unknown verdict {verdict!r}")
    return _write(con, pid, VERDICTS[verdict], expect=("proposed", "dispatched"),
                  verdict=verdict, verdict_note="marked on the page")


def reopen(con, pid: int) -> bool:
    """Undo close() or a duplicate hold: back to sent if it had gone out,
    else to proposed."""
    rec = record(con, pid)
    if rec is None or rec["status"] not in ("dismissed", "duplicate"):
        return False
    back = "dispatched" if rec.get("dispatch_id") else "proposed"
    return _write(con, pid, back, expect=("dismissed", "duplicate"),
                  verdict=None, verdict_note=None)


def negatives(con, limit: int = 50) -> list[dict]:
    """The negatives: the ones you explicitly marked "not a task".

    Only recorded for now — using them to change the prompt is another matter,
    which needs enough samples to see which phrasings the false extractions
    cluster in. Putting a handful into the prompt early would calibrate an
    unmeasured bias against noise.
    """
    rows = [r for r in records(con) if r["verdict"] == "not_a_task"]
    rows.sort(key=lambda r: (r["day"], r["t"] or ""), reverse=True)
    return [{"day": r["day"], "t": r["t"], "task": r["task"], "quote": r["quote"],
             "type": r["type"], "note": r["verdict_note"]} for r in rows[:limit]]


# ── Local state, and the old local outlet ─────────────────────

def queued(con) -> int:
    """How many proposals are waiting to go out, whatever max_per_run lets
    through in one round."""
    return len(records(con, status="proposed"))


def echoes(con) -> list[dict]:
    """Proposals held back as echoes: said aloud while dictating the same thing
    to an agent. Kept so "found and held back" stays distinguishable from
    "never found"."""
    rows = sorted(records(con, status="echo"),
                  key=lambda r: (r["day"], r["t"] or ""))
    rows.sort(key=lambda r: r["day"], reverse=True)
    return [{"day": r["day"], "t": r["t"], "task": r["task"], "quote": r["quote"],
             "score": r["echo_score"] or 0, "match": r["echo_text"] or ""}
            for r in rows]


def pending(con) -> list[tuple]:
    return [(r["id"], r["day"], r["t"], r["task"], r["quote"], r["type"],
             r["status"]) for r in records(con, status=("proposed", "dispatched"))]


def set_status(con, pid: int, status: str, result: str | None = None):
    _write(con, pid, status, result=result)


def make_sink():
    """The executor configured in [actions] — the old outlet for people without a
    destination service, used by `umbra tasks approve`.

    command: a shell command, with the task in UMBRA_TASK / UMBRA_QUOTE
    environment variables — **never interpolated into the string**, which would
    hand model output straight to a shell.
    webhook: POST JSON to a URL.
    """
    spec = _conf().get("actions") or {}
    kind = spec.get("sink")
    if kind == "command":
        cmd = spec["command"]

        def run(task: dict) -> str:
            env = {**os.environ, "UMBRA_TASK": task["task"],
                   "UMBRA_QUOTE": task.get("quote", ""),
                   "UMBRA_DAY": task.get("day", ""),
                   "UMBRA_TIME": task.get("t") or ""}
            r = subprocess.run(["/bin/sh", "-c", cmd], env=env,
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"sink command exited {r.returncode}: "
                                   f"{r.stderr.strip()[:200]}")
            return r.stdout.strip()[:400]
        return run
    if kind == "webhook":
        url = spec["url"]

        def post(task: dict) -> str:
            from ... import net
            net.post_json(url, task)
            return f"POST {url}"
        return post
    raise RuntimeError(
        "no sink configured. Add to ~/.config/umbra/config.toml:\n"
        "  [actions]\n"
        '  sink = "command"\n'
        '  command = "echo \\"$UMBRA_TASK\\" >> ~/todo.md"   # or sink = "webhook" + url')


def approve(con, pid: int, sink=None) -> bool:
    """Execute through the local outlet (make_sink).

    A failed execution **leaves the status alone** — marking it approved would
    say it went when it did not.
    """
    rec = record(con, pid)
    if not rec or rec["status"] != "proposed":
        return False
    task = {"day": rec["day"], "t": rec["t"], "task": rec["task"],
            "quote": rec["quote"]}
    sink = sink or make_sink()
    try:
        result = sink(task)
    except Exception as e:                              # noqa: BLE001
        print(f"  ✗ execution failed; status stays proposed: {e}")
        return False
    set_status(con, pid, "approved", str(result))
    return True


# ── The Analyzer declaration ──────────────────────────────────
#
# What this lane reads, how it slices a day, what it asks, and how to read the
# answer. The rest belongs to the core.

INPUT = "events:plaud"
WINDOW = 30
# A proposal is identified by the words themselves, within a day — the same key
# the old table deduplicated on. Rewordings of one task share a quote; two
# different tasks almost never do.
KEY = "quote"
# CONTEXT_MIN is 30, defined above. Thirty minutes either side, because a task
# revealed in passing usually needs the conversation around it to be a task at
# all — "we should do that" means nothing without what came before.


def PROMPT() -> str:
    """A callable: the wording is chosen from config at run time, and a
    module-level string would pin whichever language loaded first."""
    return _prompt()


def render_window(events, a, b, lo_ms, ctx=CONTEXT_MIN):
    """The source's own rendering, with this lane's marker wording.

    The markers are byte-identical to what this lane has always sent, because
    the rendered text is the inference cache key — rewording them would re-run
    every cached day at real cost, and this change is not worth that.
    """
    from ...importers import plaud
    return plaud.render_window(events, a, b, lo_ms, ctx=ctx,
                               marks=(TARGET_MARK, AFTER_MARK))


def params(con, day: str, window: dict) -> dict:
    """What no window contains: everything nearby that could make a proposal an
    echo.

    He dictates tasks to an agent. At the moment he says one out loud the work
    is already under way, so extracting it and pushing it back as a to-do turns
    the day's finished work into a queue of cards — measured on 8-17, 31 of 32
    proposals were that.

    It belongs in params rather than being loaded inside parse because it is an
    input to the judgement, and the cache key has to cover it: a prompt arriving
    later can turn something that was a real task into an echo, and a cache that
    did not notice would keep the stale answer.
    """
    from ... import analyze, db
    lo, hi = analyze.day_bounds(day, con)
    span = ECHO_WINDOW_MIN * 60000
    return {
        "echoes": echo_pool(db.load_range(con, lo - span, hi + span), lo),
        # The device buttons pressed during *this* half hour. The day's full set
        # would be wrong in the prompt: a mark means "look harder right here",
        # and pointing it at the wrong stretch is worse than not having it.
        "marks": render_marks(marks_in(window["events"], window["a"],
                                       window["b"], window["lo_ms"])),
        # Quotes the old table already holds for this day. The new store's key
        # cannot see them, so without this the first rounds after the move
        # would find each one again and send it a second time. The old table
        # never changes, so this never moves the cache.
        "_known": sorted({q for (q,) in con.execute(
            "SELECT quote FROM proposals WHERE day=?", (day,))}),
        # Read at judging time, as it always was: changing the threshold later
        # does not re-classify what was already found.
        "_echo_bar": echo_threshold(),
    }


def parse(items, window):
    """The model's array -> proposals, with this lane's own rejections.

    Kept plugin-local rather than pushed into the core because the rules differ
    per lane: quote ownership here runs at thirty minutes of context, where
    "not found in this window" does not mean what it means at zero.

    Each proposal is stored with how it was judged: whether a device button was
    pressed near it, how many people were talking around it, and whether it
    echoes something dictated to an agent. An echo arrives already held back —
    recorded, not dropped, because "it found this and judged it an echo" and "it
    found nothing" need different fixes, and only one of them is a bug.
    """
    p = window.get("params") or {}
    known = set(p.get("_known") or [])
    bar = p.get("_echo_bar", ECHO_THRESHOLD)
    events = window.get("events") or []
    lo = window.get("lo_ms") or 0
    speech = [e for e in events if e.get("kind") != "mark"]
    marks = marks_in(events, window["a"], window["b"], lo)
    out = []
    for x in items:
        if not isinstance(x, dict) or not x.get("task") or not x.get("quote"):
            continue                # nothing to check the proposal against
        if x["quote"] in known:
            continue                # already found, before the move
        if not _owns_quote(window["text"], x["quote"]):
            continue                # belongs to a neighbouring window
        at = _minute_of(x.get("time"), window["a"])
        # A mark is "this moment", not "this half hour".
        near = any(abs(m["_min"] - at) <= MARK_NEAR_MIN for m in marks)
        # Five minutes either side: who was talking around a sentence says
        # whether he was speaking to a person or to himself. The whole window
        # would let a conversation twenty minutes earlier decide it.
        nsp = _speakers_in(speech, lo, at - 5, at + 5)
        # Marked means not an echo: he pressed the button while speaking to
        # someone, which is emphasis. The echo filter exists for dictation to an
        # agent, and nobody presses a hardware button while dictating.
        score, hit = (echo_match(x["quote"], p.get("echoes") or [], at=at)
                      if bar > 0 and not near else (0.0, None))
        out.append({
            "a": window["a"], "b": window["b"],
            "t": x.get("time"), "task": x["task"], "quote": x["quote"],
            "type": _norm_type(x.get("type")),
            "confidence": _norm_conf(x.get("confidence")),
            "domain": _norm_domain(x.get("domain")),
            "marked": bool(near), "speakers": nsp,
            "echo_score": round(score, 3), "echo_text": (hit or "")[:500] or None,
            "state": "echo" if score >= bar > 0 else "proposed",
        })
    return out


def sync(con, days, log=print):
    """One scheduled round: extract the days handed over, then send and collect.

    Sending gets its own try. Extraction cost money and its results are already
    stored, and one 503 from the destination should not make the whole round
    count as never having run; the next round sends whatever has not gone out.
    """
    for day in days:
        r = scan(con, day)
        waiting = len(r["fresh"]) - r.get("domain_filtered", 0)
        log(f"  actions {day}: reused {r['reused']} / called "
            f"{r['fresh_calls']} windows -> extracted "
            f"{r.get('inserted', len(r['fresh']))}"
            f" / echoes {r.get('echo_filtered', 0)} / said again "
            f"{r.get('duplicates', 0)} / domain-filtered "
            f"{r.get('domain_filtered', 0)} / awaiting dispatch {waiting}")
    if not dispatch_enabled():
        return
    try:
        n = dispatch(con, log=log)
        if n:
            log(f"  actions: dispatched {n}")
        # Send before pulling: nothing sent this round has been acted on yet, so
        # what comes back belongs to earlier rounds. The other order is not
        # wrong, merely a wasted trip.
        v = pull_verdicts(con, log=log)
        if v:
            log(f"  actions: {v} verdicts pulled back")
    except Exception as e:                    # noqa: BLE001
        log(f"  ✗ actions dispatch/verdicts: {e}")


# ── The drawer (api.said / api.said_mark) ─────────────────────

def said(con, date: str, conf: dict) -> dict:
    """This lane's part of the day's drawer: the tasks heard that day, and how
    many were held back as echoes. Echoes — things said while dictating to an
    agent — are counted, not listed: saying them was already doing them. They
    are kept so "found but held back" stays distinguishable from "never found",
    which is why the count is sent at all. `sending` is whether anything here
    has left the machine."""
    from ... import sink
    tasks, held = [], 0
    for r in records(con, day=date):
        if r["status"] == "echo":
            held += 1
            continue
        t, w = r["t"], r["win_a"]
        w = w if w is not None else (analyzer.hm_min(t, 0) // 30) * 30
        tasks.append({"id": r["id"], "t": t or "", "at": analyzer.hm_min(t, w),
                      "win": [w, w + 30], "task": r["task"], "quote": r["quote"],
                      "type": r["type"] or "unknown",
                      "domain": r["domain"] or "unknown",
                      "confidence": r["confidence"] or "unknown",
                      "status": r["status"], "verdict": r["verdict"],
                      "marked": bool(r["marked"]),
                      "speakers": r["speakers"]})
    return {"tasks": tasks, "held": held,
            "sending": sink.enabled_for(PLUGIN, conf) is not None}


def said_mark(con, item_id: int, body: dict) -> dict:
    """The page's ✓ and ✕: {"verdict": "already_done" | "not_a_task"}, or null
    to reopen. Without them the drawer could only be read: a sink that only
    receives never sends a verdict back, so the only way to close a task was
    `umbra tasks dismiss` — which the drawer then did not show."""
    if record(con, item_id) is None:
        raise LookupError(f"no task {item_id}")
    verdict = body.get("verdict")
    ok = reopen(con, item_id) if verdict is None else close(con, item_id, verdict)
    return {"ok": ok}


# ── The command line (umbra tasks) ──────────────────────────

CLI_NAME = "tasks"
CLI_HELP = "tasks heard in what you said → a destination you choose → your verdicts"


def cli_args(p) -> None:
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "scan", "preview", "dispatch", "verdicts",
                            "approve", "dismiss", "echoes", "negatives"])
    p.add_argument("id", nargs="?", help="a date for scan; a proposal id for approve/dismiss")
    pv = p.add_mutually_exclusive_group()
    pv.add_argument("--done", dest="verdict", action="store_const", const="already_done",
                    help="dismiss: it was a real task, already dealt with")
    pv.add_argument("--not-a-task", dest="verdict", action="store_const", const="not_a_task",
                    help="dismiss: extraction was wrong (counts in negatives)")


def cli(args):
    """Tasks heard in what you said: scan → preview → dispatch → your verdicts.

    Nothing leaves this machine unless sending is switched on and you run
    dispatch (or a round does). The destination holds what arrives for review,
    so a proposal never starts work on its own.
    """
    from ... import db
    con = db.connect()
    if args.action == "scan":
        import datetime as _dt
        day = args.id or str(_dt.date.today())
        r = scan(con, day, log=print)
        print(f"  {day}: {len(r['fresh'])} new proposals "
              f"(called {r['fresh_calls']} / reused {r['reused']} windows"
              + (f", {r['failed']} failed" if r["failed"] else "") + ")")
    elif args.action == "preview":
        rows = preview(con)
        if not rows:
            print("  the next round would send nothing.")
        for r in rows:
            print(f"  [{r['id']}] {r['day']} {r['t'] or '--:--'} "
                  f"({r['confidence'] or '?'})  {r['task']}")
            print(f"        said: {r['quote']}")
        print(f"\n  {len(rows)} this round, {queued(con)} queued in all."
              f"\n  Nothing has been sent. To send: umbra tasks dispatch")
    elif args.action == "dispatch":
        print(f"  dispatched {dispatch(con)}")
    elif args.action == "verdicts":
        print(f"  pulled back {pull_verdicts(con)} verdicts")
    elif args.action == "approve":
        ok = approve(con, int(args.id))
        print("  ✓ executed" if ok else "  ✗ not executed (bad id / not pending / sink failed)")
    elif args.action == "dismiss":
        # The same two verdicts as the drawer, and one of them has to be
        # named: "dismissed" alone cannot say whether extraction was wrong,
        # and only "not a task" may count against it (negatives).
        verdict = args.verdict
        if verdict is None:
            raise SystemExit("  say which: umbra tasks dismiss <id> --done | --not-a-task")
        if close(con, int(args.id), verdict):
            print(f"  closed: {'not a task' if verdict == 'not_a_task' else 'done'}")
        else:
            print("  ✗ not closed (bad id, or already closed)")
    elif args.action == "echoes":
        rows = echoes(con)
        if not rows:
            print("  nothing was held back as an echo.")
        for r in rows:
            print(f"  {r['day']} {r['t'] or '--:--'} ({r['score']:.0%})  {r['task']}")
            print(f"        recorded: {r['quote'][:70]}")
            print(f"        matched:  {r['match'][:70]}")
    elif args.action == "negatives":
        neg = negatives(con)
        if not neg:
            print("  no negatives yet — nothing has been marked \"not a task\".")
        for n in neg:
            print(f"  {n['day']} {n['t'] or '--:--'} [{n['type'] or '?'}] {n['task']}")
            print(f"        quote: {n['quote']}")
            if n["note"]:
                print(f"        you said: {n['note']}")
    else:
        rows = pending(con)
        if not rows:
            print("  no pending proposals. Scan a day: umbra tasks scan 2026-08-19")
        for pid, day, t, task, quote, typ, status in rows:
            mark = "sent" if status == "dispatched" else "local"
            print(f"  [{pid}] {day} {t or '--:--'} [{typ or '?'}] {task}  ({mark})")
            print(f"        quote: {quote}")
