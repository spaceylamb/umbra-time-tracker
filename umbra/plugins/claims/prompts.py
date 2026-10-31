"""The text the fact-check lane sends to a model: extraction, the hand-off to a
sink, the web lookup, and the phone card.

Changing a word here changes the digest every window is cached under, so
every user's next round re-reads every day: say so in the PR."""


PROMPT_ZH = """下面是一段多人对话的逐字稿（随身录音自动转写，可能有听错的字）。
这段话发生在 {day}。

**今天是 {today}。你的训练知识有截止日期，很可能落后于此。** 所以遇到你
没听说过的公司收购、产品发布、政策变更、新闻事件，**判 unsure 而不是
likely_wrong** —— 「我不知道」和「这是假的」是两回事。实测这条不写清楚
时，录音前几周真实发生的事会被一律判成编造。

找出其中**可以被外部事实核查的断言** —— 说话人当成事实讲出来、而对错
不取决于他是谁的话。

值得挑出来的：
  number      数字、日期、量级
  entity      具体的人 / 机构 / 产品
  rule        规则、政策、要求
  mechanism   因果、机制
  comparison  比较级、最高级

**不要**挑出来的：口味偏好审美、他自己的计划和状态、明确说了是猜的、
对在场的人的评价、指着眼前东西说的话（外部世界没有可核查的对象）。

**也不要**挑不是在场的人说的话：电视、广播、视频、播客、广告、站台
广播在旁边放着，录音一样会录进去。这类话通常是写好的稿子 —— 句子完整、
像播音或旁白、提到频道或节目、和前后的对话接不上。拿不准一句话是不是
在场的人说的，就别挑。

对每一条判两件事：

verdict —— 用你自己的知识先判一次，这决定要不要花钱联网：
  confident_right  你相当确定这话是对的
  unsure           你拿不准 / 记不清 / 有争议 / **你没听说过**
  likely_wrong     你相当确定这话有问题

absolute —— 这句话有没有一个「会随时间失效或存在例外」的锚点：
  true    把话说死了（必须 / 只要 / 仅凭 / 不需要 / 禁止 / 上限是 X /
          满 N 年 / 一律 / 所有 —— 换了说法但意思一样的也算）；带门槛
          数字、生效日期、截止日期；会变的价格、费率、版本号
  false   物理常量、天文事实、数学定义、几十年不变的法规与地理事实、
          一次性的历史事件

为什么要 absolute：例外和门槛是最容易过期、也最容易被压扁的东西 ——
实测「乐高必须有儿童陪同」漏了成人夜场、「禁止货币交易」漏了咖啡摊、
「满 8 年」把纳税年度压成了自然年。这类哪怕你很确定，也值得再查一遍。

quote 必须逐字照抄，一个字都不能改 —— 转写可能把 billion 听成 trillion，
改过的引文就分不清是谁的错了。

输出 JSON 数组，没有就 []：
{{"time":"HH:MM","speaker":"逐字稿里的说话人标签",
  "claim":"把断言写成一句独立成立的话（别人不看上下文也能懂）",
  "quote":"原话逐字","kind":"number|entity|rule|mechanism|comparison|other",
  "verdict":"confident_right|unsure|likely_wrong","absolute":true,
  "why":"一句话：你为什么这么判"}}

只输出 JSON。

{text}"""


PROMPT_EN = """Below is a transcript of a conversation between several people
(transcribed automatically from a wearable recorder; some words may be misheard).
It took place on {day}.

**Today is {today}. Your training knowledge has a cutoff and is probably older
than this.** So for an acquisition, a product launch, a policy change or a news
event you have not heard of, **answer unsure, not likely_wrong** — "I don't
know" and "this is false" are different things. Measured: without this said
plainly, real events from the weeks before a recording are judged invented.

Find the **claims that could be checked against outside facts** — things a
speaker states as fact, whose truth does not depend on who says them.

Worth picking:
  number      numbers, dates, magnitudes
  entity      specific people / organisations / products
  rule        rules, policies, requirements
  mechanism   causes, mechanisms
  comparison  comparatives, superlatives

**Do not** pick: taste and preference, the speaker's own plans and state,
anything offered explicitly as a guess, remarks about people present, or things
said about what is in front of them (the outside world has nothing to check).

**Nor** anything not said by the people in the room. A TV, radio, video,
podcast, advert or station announcement playing nearby is recorded just the
same. It usually reads as a script — complete, polished sentences, a
newsreader's or narrator's voice, a channel or programme named, nothing to do
with the conversation around it. If you cannot tell whether a line came from
someone present, leave it out.

Judge two things for each:

verdict — your own first judgement, from your knowledge; it decides whether a
paid lookup is worth it:
  confident_right  you are fairly sure it is right
  unsure           you are not sure / don't remember / it is disputed / **you
                   haven't heard of it**
  likely_wrong     you are fairly sure it is wrong

absolute — does the claim hang on an anchor that can expire or has exceptions:
  true    stated categorically (must / only / just by / no need / forbidden /
          at most X / after N years / always / all — or the same idea in other
          words); a threshold number, an effective date, a deadline; a price,
          rate or version number that changes
  false   physical constants, astronomy, mathematical definitions, laws and
          geography unchanged for decades, one-off historical events

Why absolute: exceptions and thresholds are what go stale first and what gets
flattened in speech — measured, "children must be accompanied" missed the
adults-only evenings, "no cash payments" missed the coffee stand, and "after 8
years" turned tax years into calendar years. Even when you are sure, these are
worth checking.

quote must be copied word for word, unchanged — the transcriber may have heard
billion as trillion, and an edited quote hides whose mistake it was.

Output a JSON array, or [] if there are none:
{{"time":"HH:MM","speaker":"the speaker label from the transcript",
  "claim":"the claim as one sentence that stands on its own",
  "quote":"the exact words","kind":"number|entity|rule|mechanism|comparison|other",
  "verdict":"confident_right|unsure|likely_wrong","absolute":true,
  "why":"one sentence: why you judged it so"}}

Output only JSON.

{text}"""


def _prompt() -> str:
    from ... import infer
    return PROMPT_ZH if infer.lang() == "zh" else PROMPT_EN


# ── Sending for verification ──────────────────────────────────

CHECK_TAIL_ZH = """
## 要求
每条给出：**判定**（正确 / 不准确 / 错误 / 查不到定论）、**实际情况**
（一两句：错在哪、正确的是什么）、**出处**（至少一个可点链接）。

原话读着像转写错误（billion/trillion 这类）就明说 —— 那是自动转写的锅，
不是说话人错了。

**离线初判只是线索，不是结论。** 实测有若干条它判「错」判对了、但给出的
「正确答案」本身也是错的。请独立核实，不要沿用它的说法。

按实际影响排序：签证 / 税 / 钱 / 旅行日期在前，闲聊常识在后或直接标注
「无实际影响」。中文回答。
"""


CHECK_TAIL_EN = """
## What to give
For each: a **verdict** (right / inaccurate / wrong / no firm answer found),
**what is actually so** (a sentence or two: what is wrong, and what is right),
and a **source** (at least one link that opens).

If a quote reads like a transcription error (billion / trillion and the like),
say so — that is the transcriber's fault, not the speaker's.

**The offline first pass is a lead, not a finding.** Measured: several times it
was right to call something wrong and then gave a "correct answer" that was
itself wrong. Verify independently; do not adopt its wording.

Order by real-world consequence: visas, tax, money and travel dates first; small
talk last, or marked "no practical impact". Answer in English.
"""


VERDICT_ZH = {"confident_right": "应该对", "unsure": "不确定", "likely_wrong": "可能错"}


# ── Looking claims up ─────────────────────────────────────────

LOOKUP_PROMPT_EN = """Search the web before you answer: run at least one search for this claim and base the verdict on what it returns. An answer from memory alone is not accepted.

Today is {today}. Your training knowledge has a cutoff and is probably older
than that, so judge by what the search finds, not by memory.

On {day}, someone said this out loud. A wearable recorded it and it was
transcribed automatically, so words can be misheard.

Claim: {claim}
Their words: "{quote}"
An offline first pass, from a model's memory: {first}{why}
That first pass is a lead, not a finding: it has been wrong both ways. Check
independently and do not repeat its reasoning.

Find out whether the claim is true, against current and reliable sources. If the
words read like a transcription error (billion for trillion, a misheard name),
say so: that is the transcriber's fault, not the speaker's.

Look it up first, and say in a few sentences what the sources show. Then end
your reply with a JSON object:
{{"verdict": "right" | "inaccurate" | "wrong" | "unsettled",
 "actual": "one or two sentences in English: what is actually so, and what the claim got wrong",
 "transcription_error": true | false}}

"inaccurate" is mostly right with a detail wrong; "unsettled" is when the
sources disagree or you found no firm answer."""


LOOKUP_PROMPT_ZH = """回答前先联网搜索：至少针对这句话搜一次，判定以搜到的内容为准。只凭记忆的回答不算数。

今天是 {today}。你的训练知识有截止日期，很可能早于今天，所以以搜到的内容为准，
不要凭记忆判断。

{day} 有人当面说了下面这句话。随身录音笔录下后自动转写，可能有听错的字。

断言：{claim}
原话：「{quote}」
离线初判（模型凭记忆）：{first}{why}
初判只是线索，不是结论：它两个方向都错过。请独立核实，不要沿用它的理由。

对照当下可靠的来源，查明这句话对不对。原话读着像转写错误（billion 和 trillion、
听错的人名这类）就明说：那是转写的错，不是说话人的错。

先查，用几句话说出来源怎么说，然后在回复最后附上一个 JSON 对象：
{{"verdict": "right" | "inaccurate" | "wrong" | "unsettled",
 "actual": "用中文写一两句：实际情况是什么，这句话错在哪里",
 "transcription_error": true | false}}

"inaccurate" 是大体对、某个细节错；"unsettled" 是来源说法不一，或者查不到定论。"""


def lookup_prompt(r: dict, today: str) -> str:
    """One claim's lookup, in the configured language."""
    from ... import infer
    zh = infer.lang() == "zh"
    why = r.get("why") or ""
    return (LOOKUP_PROMPT_ZH if zh else LOOKUP_PROMPT_EN).format(
        today=today, day=r["day"], claim=r["claim"], quote=r["quote"],
        first=(r.get("verdict") or "unsure").replace("_", " "),
        why=(f"（{why}）" if zh else f" ({why})") if why else "")


CARD_PROMPT_ZH = """把下面这份事实核查报告压成一张**手机上能一眼扫完**的卡片。

读它的人当时就在场，他要的是「我哪句说错了、正确的是什么」，不是一份
研究材料。

## 必须是纯文本

卡片显示的地方未必渲染 markdown（参考实现的界面把它当纯文本插进去，
只保留换行）。所以：

  · 不要 **粗体**、不要 *斜体*、不要 `反引号`
  · 不要 [文字](链接) —— 链接直接裸写 URL
  · 不要 --- 分隔线、不要 # 标题
  · 用空行和缩进分层，那是唯一有效的排版手段

写成 markdown 的话，星号和括号会原样糊在他脸上 —— 实测就是这么翻的车。

## 格式

  第一行：一句话总结（几条里几条说错了，最要紧的是哪条）

  然后每条一段，只留真正说错或不准确的：

  ✕ 一句话说清错在哪
     你说的：「原话截断到 30 字以内」
     实际：一两句，别展开
     https://出处链接              ← 每条最多一个，裸 URL 顶格写

  正确的那些合并成一行收尾：另外 N 条没问题（xxx、yyy）

## 硬要求

  · 不要参考资料附录、不要罗列一堆链接 —— 实测原报告 37 行是链接，
    手机上根本读不动
  · 转写错误单独标出来（「这句可能是转写听错了，不是你说错」）
  · 全文 600 字以内
  · 直接输出卡片正文，不要任何前言后语

报告可能不止一份（同一天分几批查的，用「=== Report N ===」隔开）。合成
**一张**卡：只有一个总结行，说错和没问题各出现一次，条数按合并后的算。
几份报告里说的是同一件事的（措辞不同、原话截的位置不同），只写一段。

报告：

{report}"""


CARD_PROMPT_EN = """Compress the fact-check report below into a card that can be
**read at a glance on a phone**.

The reader was there. They want "which of the things I said were wrong, and
what is right" — not research material.

## Plain text only

Wherever this card is displayed may not render markdown (the reference sink's
UI inserts it as bare text, keeping only line breaks). So:

  · no **bold**, no *italics*, no `backticks`
  · no [text](link) — write links as bare URLs
  · no --- rules, no # headings
  · blank lines and indentation are the only layout that works

Written as markdown, the asterisks and brackets land in the reader's face
exactly as typed — measured, that is how it went wrong.

## Shape

  First line: one-sentence summary (how many of how many were wrong, and which
  matters most)

  Then one paragraph per claim, only the ones actually wrong or inaccurate:

  ✕ one sentence on what is wrong
     you said: "the quote, cut to under 30 words"
     actually: a sentence or two, no more
     https://source-link             <- at most one per claim, bare, unindented

  Close with the right ones on one line: N others were fine (xxx, yyy)

## Hard rules

  · no references appendix, no list of links — measured, 37 lines of the
    original report were links, unreadable on a phone
  · mark transcription errors separately ("this was probably misheard by the
    transcriber, not misspoken")
  · under 120 words in all
  · output the card itself, with no preamble or sign-off

There may be more than one report (one day checked in batches, separated by
"=== Report N ==="). Make **one** card: a single summary line, the wrong ones
and the fine ones once each, counted after merging. Where reports cover the
same thing (worded differently, quoted from a different point), write it once.

Report:

{report}"""


def card_prompt() -> str:
    """The card prompt in the configured language."""
    from ... import infer
    return CARD_PROMPT_ZH if infer.lang() == "zh" else CARD_PROMPT_EN


# This lane's separator wording, its own rather than the tasks lane's: this text
# is part of the inference cache key, so if the lanes shared it, one rewording its
# separator would invalidate the other's entire history and re-run it at real cost.
TARGET_MARK = "───── the stretch you are judging starts here ─────"


AFTER_MARK = "───── context after that stretch ─────"
