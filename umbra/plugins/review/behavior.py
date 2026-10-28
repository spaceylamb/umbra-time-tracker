"""The role you play in an instruction, not what it says.

On one real history only one cluster of instructions asked the same thing
across days — yet "what's the status" came up 186 times in nine weeks, never
about the same task. What repeats is the role. Clustering by content can never
find it, so there are two layers: content (detect.cluster) and role (here).

The dictionary may miss things but may not pretend: anything unrecognised is
`unclassified`, and its share is reported. "Not in the dictionary" and "not
something you do" must not look alike. No model: five roles with explicit
words, and how much is missed is reported anyway.
"""

import collections
import datetime as dt
import re

#: Order is priority: "keep going — is it done yet?" is asking for status,
#: the stronger signal (you do not know the state over there).
_RULES = [
    ("ask_status", re.compile(
        r"(进度|进展|怎么样|咋样|什么情况|啥情况|跑完|做完了|好了吗|完成了吗|还在跑|搞定了|到哪了"
        r"|有没有(新的)?(消息|进展|结论)|\bstatus\b|\bprogress\b|done yet|finished yet"
        r"|is it (done|finished|ready)|how'?s it going|any (news|update))", re.I)),
    ("fix_retry", re.compile(
        r"(重新(跑|试|做|来)|重跑|再试|再跑|修一下|修复一下|改一下再|回滚"
        r"|try again|retry|re-?run|roll ?back|fix (it|that|this))", re.I)),
    ("approve", re.compile(
        r"(按(照)?你(说|的|推荐|建议)|同意|可以[，,。.]?\s*(就这么|你去|开始)"
        r"|没问题[，,]?\s*(那就|你)|就这么(做|来|办)|批准|\bapprove|go ahead"
        r"|sounds good|ship it|lgtm)", re.I)),
    ("nudge", re.compile(
        r"^(好的?[，,、]?\s*|ok(ay)?[,.]?\s*)?(继续|接着|你继续|一直做"
        r"|go on|continue|keep going|carry on)", re.I)),
    ("summarize", re.compile(
        r"(总结一下|给我总结|归纳一下|整理成(一份|一个)?(文档|摘要|报告)|summar|recap)", re.I)),
]

TYPES = tuple(name for name, _ in _RULES)

#: What each role would take off your hands. Not the model's judgement —
#: whether a given one is worth doing is still yours.
LEVERS = {
    "ask_status": "tell you when it finishes, so you do not have to ask",
    "fix_retry": "retry on failure, and report the cause",
    "approve": "make approve-then-run one step",
    "nudge": "let the agent run to the end instead of stopping to wait",
    "summarize": "produce the summary on a schedule",
    "unclassified": "",
}


def classify(text: str) -> str:
    t = (text or "").strip()
    return next((name for name, pat in _RULES if pat.search(t)), "unclassified")


def _week(ms: int) -> str:
    return "%d-W%02d" % dt.datetime.fromtimestamp(ms / 1000).isocalendar()[:2]


def summarize(rows) -> dict:
    """[(ts_ms, text)] -> counts per role, per week, and the unclassified share.
    Weekly, because "status?" going from 18 a week to 50 is the news a total
    hides."""
    counts = collections.Counter()
    weekly = collections.defaultdict(collections.Counter)
    for ts, text in rows:
        k = classify(text)
        counts[k] += 1
        weekly[k][_week(ts)] += 1
    total = sum(counts.values())
    return {"counts": counts, "weekly": {k: dict(v) for k, v in weekly.items()},
            "total": total,
            "unclassified_share": round(counts["unclassified"] / total, 3) if total else 0.0}
