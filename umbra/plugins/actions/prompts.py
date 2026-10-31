"""The text the to-do lane sends to a model, and the words a card is made of.

Changing a word here changes the digest every window is cached under, so
every user's next round re-reads every day: say so in the PR."""


# The separators are this lane's own, not digest's constants: each lane must be
# able to reword its own without touching the other — the reason they are
# separate — and one shared string would be a hidden wire between them. They are
# also part of the cache key: rewording one re-runs every cached day of this lane.
TARGET_MARK = "───── the stretch you are judging starts here ─────"


AFTER_MARK = "───── context after that stretch ─────"


QUOTE_MARK_ZH = "  ⟵ 就是这句"


QUOTE_MARK_EN = "  ⟵ this one"


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
