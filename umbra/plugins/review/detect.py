"""What you repeat on a rhythm, counted: the candidates the model then judges.

## Why across weeks

Four analyses in a row looked for repetition inside one week, found none, and
concluded there was none. Wrong: something done once a week is ×1 in one week,
and any "repeated three times" bar filters out every periodic task there is.
Across weeks the same data showed a grocery order every 7–9 days, a report every
14±0 days, and `commit → merge → deploy` typed twenty times.

The window is 16 weeks (a fortnightly thing needs six to show, a monthly one
twelve — see recur.py) and the report goes out monthly: with a window that long,
two weekly lists are nearly identical, and a list that does not change stops
being read by the third week.

## What is counted

    page         a page you open on a rhythm (browser)
    auth         a sign-in you keep redoing — a real cost: make it last longer
    mail         one kind of mail arriving on a rhythm
    instruction  the same instruction in different words, to an agent
    burst        a few dense days on one site, then gone
    project      a batch of new sites that came up together
    offline      places, workouts, calls, listening, reading

Every candidate carries evidence lines, and one without is dropped as made up.
What is filtered out is counted by reason.

## Your own lists

The built-in filters are generic: search pages, redirects, inboxes, the big
reading and video sites. What one person reads, or which sites are their
company's intranet, is config:

    [plugins.review]
    reading          = ["myblog.example.com"]    # sites you read, not work in
    ignore           = ["^intranet\\.example\\.com/"]   # patterns on the page key
    agent_prompts    = ["^Daily briefing for"]   # programs writing to your agents
    machine_providers = ["cron-bot"]             # Butler providers that are programs
"""

import collections
import datetime as dt
import json
import re

from . import behavior, burst, fold, offline, project, recur

WINDOW_WEEKS = 16
#: Below this much history the plugin waits: a fortnightly rhythm needs six
#: weeks to show at all.
MIN_WEEKS = 6
#: A flow of more steps than this is everything done on a site, not one flow.
MAX_FLOW_STEPS = 8
#: Path segments kept in a page key. The whole path makes every order its own
#: page; the host alone merges a shop's front page with its checkout.
PATH_SEGMENTS = 2
WEEK_MS = 7 * 86_400_000
#: The sources a review reads. History shorter than MIN_WEEKS in all of them
#: means there is nothing to say yet.
SOURCES = ("chrome", "claude", "butler", "location", "whoop", "intent", "media", "weread")

# ── Noise ─────────────────────────────────────────────────────
# The first run on a real history found 918 items; reading them showed these
# three kinds repeat relentlessly and are not "something you do".

#: Entry pages and redirects: a way in, not an action. Maps and travel are
#: deliberately absent — route planning and fare searches are work, and the
#: first cut lost 66 flight searches by sweeping them in. A shop's search
#: results and ad slots are entries; its product, order and return pages are not.
_NOISE = re.compile(
    r"^(?:"
    r"www\.google\.com/(?:search|url|aclk)"
    r"|(?:www\.|maps\.)?google\.com/?$"
    r"|(?:www\.)?gmail\.com/"
    r"|mail\.google\.com/mail"
    r"|www\.youtube\.com/(?:watch|results)"
    r"|news\.ycombinator\.com/item"
    r"|substack\.com/redirect"
    r"|www\.amazon\.com/(?:s(?:/|$)|ref=|sspa)"
    r"|[^/]*/(?:.*/)?click"
    r")")

#: Reading: periodic (the same newsletter, the same channel) but nothing to
#: automate — a subscription already arrives on its own. A blocklist of reading
#: sites rather than an allowlist of tools, because an allowlist would also
#: drop a bank whose paths the privacy layer has redacted, which is real signal.
READING = ("substack.com", "youtube.com", "youtu.be", "medium.com", "reddit.com",
           "x.com", "twitter.com", "wikipedia.org", "zhihu.com", "bilibili.com",
           "douban.com", "nature.com", "pubmed.ncbi.nlm.nih.gov")

#: Sign-in pages look like redirects but are signal: signing in again every few
#: days is a real cost, and "make the session longer" can be done. Recognised
#: before any filter — accounts.youtube.com is a sign-in, not a video.
_AUTH = re.compile(r"(^accounts\.|oauth|/authorize|/authen|servicelogin|/signin"
                   r"|/login|finish_google_sso|/sso)", re.I)

#: Gmail's own views, not subjects.
_MAIL_UI = re.compile(
    r"^(?:Inbox|Spam|Scheduled|Drafts|Sent|Starred|Snoozed|Trash|Chats"
    r"|All Mail|Important|Search results|Gmail|\(no subject\)|\w+ Mail)"
    r"(?:\s*\(#\))?$", re.I)

_ID = re.compile(r"^\d{4,}$|^[0-9a-f-]{16,}$|^[A-Z0-9]{10,}$")
_HOST = re.compile(r"https?://([^/]+)(/[^?#]*)?")

#: A program speaking to an agent in a role ("You are the … agent"). Written by
#: a schedule, not by you, whoever's name is on the task.
AGENT_PROMPTS = (r"^You are (?:the|a|an) [\w \-]{0,40}agent\b",
                 r"^你是[一-鿿\w \-]{0,20}(的)?[\w \-]*agent")

# ── Clustering instructions ──────────────────────────────────
# Overlap (shared / the shorter), not Jaccard: "ok, you go ahead and commit,
# merge and then deploy" and "commit, merge, deploy" share three words, but the
# longer one dilutes Jaccard to 0.25. Spoken instructions differ in length;
# what matters is how much of the shorter one is covered. Overlap is generous on
# small sets, hence also at least three shared tokens.
CLUSTER_THRESHOLD = 0.30
MIN_SHARED_TOKENS = 3
MIN_INSTRUCTION_CHARS = 12
#: How long a program's task can queue before it runs here (measured: 8 days).
QUEUE_MS = 14 * 86_400_000
_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[a-z][a-z0-9_\-]{2,}")
_STOP = set("the and for you can this that with from have not are was its it's "
            "一个 我们 这个 那个 可以 一下 现在 需要 问题 就是 什么 帮我 然后 已经 但是 "
            "如果 应该 因为 所以 好的 你的 我的".split())

# ── Levers ───────────────────────────────────────────────────
#: What each kind naturally allows — not the model's judgement; whether a given
#: one is worth doing is. **The one table**: the model's choices (LEVERS) and
#: the card's words (LEVER_WORDS) both come from here. Kept in three places
#: once, three new kinds went into one and the model had to pick a wrong lever
#: from the old list, with each place looking consistent on its own.
_LEVERS = {"burst": "recall", "project": "recall", "offline": "delegate",
           "auth": "session", "mail": "unsubscribe", "instruction": "schedule",
           "page": "push"}
#: route and drop only the model can tell ("an agent should connect both
#: ends", "just stop").
LEVERS = tuple(dict.fromkeys([*_LEVERS.values(), "route", "drop"]))
LEVER_WORDS = {
    "unsubscribe": "Unsubscribe or mute",
    "session": "Make the sign-in last longer",
    "schedule": "Put it on a schedule",
    "push": "Make it come to you",
    "route": "Let an agent connect both ends",
    "drop": "Stop doing it",
    "recall": "Next time, lay out last time's path",
    "delegate": "Hand it off, or fold it into one trip",
}


def lever(c: dict) -> str:
    return _LEVERS.get(c.get("kind"), "")


# ── Config ───────────────────────────────────────────────────

def _spec(conf: dict | None) -> dict:
    if conf is None:
        from ... import config
        conf = config.load()
    return ((conf.get("plugins") or {}).get("review")) or {}


def _compile(patterns, what: str):
    try:
        return [re.compile(p, re.I) for p in patterns]
    except re.error as e:
        raise ValueError(f"[plugins.review] {what}: bad pattern ({e}): {patterns}") from e


class Filters:
    """The built-in lists plus yours."""

    def __init__(self, conf: dict | None):
        spec = _spec(conf)
        doms = [*READING, *(spec.get("reading") or [])]
        self.reading = re.compile(r"^(?:[^/]*\.)?(?:%s)/"
                                  % "|".join(re.escape(d.strip("/")) for d in doms), re.I)
        self.ignore = _compile(spec.get("ignore") or [], "ignore")
        self.prompts = _compile([*AGENT_PROMPTS, *(spec.get("agent_prompts") or [])],
                                "agent_prompts")
        self.providers = set(spec.get("machine_providers") or [])

    def drop(self, key: str) -> str | None:
        """Why this page key is not a candidate, or None."""
        if _NOISE.match(key):
            return "entry or redirect page"
        if self.reading.match(key):
            return "reading"
        if any(p.search(key) for p in self.ignore):
            return "ignored in config"
        return None

    def is_machine(self, payload: dict) -> bool:
        """A Butler task a program sent: through the dispatch API (source_app),
        or by a provider you named as a program."""
        return bool(payload.get("source_app")) or payload.get("provider") in self.providers

    def is_agent_prompt(self, text: str) -> bool:
        t = (text or "").strip()
        return any(p.search(t) for p in self.prompts)


# ── Keys ─────────────────────────────────────────────────────

def window_start(until_ms: int, weeks: int) -> int:
    return until_ms - weeks * WEEK_MS


def page_key(url: str) -> str | None:
    """URL -> action key. Numeric ids become <id>, or one page is a hundred."""
    m = _HOST.match(url or "")
    if not m:
        return None
    seg = [s for s in (m.group(2) or "/").split("/") if s][:PATH_SEGMENTS]
    return m.group(1) + "/" + "/".join("<id>" if _ID.match(s) else s for s in seg)


def mail_key(title: str) -> str | None:
    """A Gmail title -> a kind of mail. Numbers normalised: different order
    numbers, the same kind of mail."""
    t = re.sub(r"\s*-\s*[\w.+-]+@[\w.-]+.*$", "", title or "")
    t = re.sub(r"\s*-\s*Gmail\s*$", "", t)
    t = re.sub(r"^\(\d+\)\s*", "", t)
    t = re.sub(r"\d+", "#", t).strip()
    if not t or _MAIL_UI.match(t):
        return None
    return t[:64]


def merge_flows(cands: list) -> list:
    """One checkout split into six candidates -> one.

    Same host, and the same days (≥60% overlap): steps of one flow happen on the
    same day, while opening a shop's front page and ordering there overlap only
    partly. Whether to merge is decided per host up front — deciding while
    scanning merged a 20-path site once the window had slid to leave exactly 8.
    """
    def host(c):
        return c["key"].split("/")[0]

    per_host = collections.Counter(host(c) for c in cands if c["kind"] == "page")
    no_merge = {h for h, n in per_host.items() if n > MAX_FLOW_STEPS}
    used, out = set(), []
    for i, c in enumerate(cands):
        if i in used:
            continue
        if c["kind"] != "page" or host(c) in no_merge:
            out.append(c)
            continue
        mine = c.get("days") or set()
        group = [i]
        for j in range(i + 1, len(cands)):
            o = cands[j]
            if j in used or o["kind"] != "page" or host(o) != host(c):
                continue
            theirs = o.get("days") or set()
            if mine and theirs and len(mine & theirs) / min(len(mine), len(theirs)) >= 0.6:
                group.append(j)
        if len(group) == 1:
            out.append(c)
            continue
        used.update(group)
        members = [cands[k] for k in group]
        best = max(members, key=lambda g: g["n"])
        out.append(dict(best, key=host(c), steps=sorted({g["key"] for g in members}),
                        n=len(set().union(*[g.get("days") or set() for g in members]))))
    return out


def _tokens(text: str) -> set:
    """A sentence -> tokens. Chinese as three-character windows: two collide
    by accident too easily."""
    t = re.sub(r"https?://\S+", " ", (text or "").lower())
    out = {w for w in _WORD.findall(t) if w not in _STOP}
    cj = _CJK.findall(t)
    out |= {"".join(cj[i:i + 3]) for i in range(len(cj) - 2)} - _STOP
    return out


def cluster(items, threshold: float = CLUSTER_THRESHOLD):
    """[(ts, text)] -> [[(ts, text)...]], greedy, one pass.

    No embeddings: "why are these two one thing" must be answerable at a glance.
    **The seed does not grow.** It used to absorb each member's tokens, and the
    first cluster became a magnet — A like B, B like C, so unrelated A and C
    together, and one ordinary instruction showed as ×17. Wordings far apart now
    split into two clusters; two honest ×3s beat one false ×17.
    """
    clusters = []
    for ts, text in sorted(items, key=lambda r: r[0]):
        g = _tokens(text)
        if len(g) < 4:
            continue
        for c in clusters:
            shared = len(g & c["g"])
            if (shared >= MIN_SHARED_TOKENS
                    and shared / max(1, min(len(g), len(c["g"]))) >= threshold):
                c["m"].append((ts, text))
                break
        else:
            clusters.append({"g": set(g), "m": [(ts, text)]})
    return [c["m"] for c in clusters]


def _rows(con, source: str, lo: int, hi: int):
    for ts, payload in con.execute(
            "SELECT ts, payload FROM events WHERE source=? AND ts>=? AND ts<? ORDER BY ts",
            (source, lo, hi)):
        try:
            yield ts, json.loads(payload)
        except (ValueError, TypeError):
            continue


def instructions(con, lo: int, hi: int, conf: dict | None = None,
                 filters: Filters | None = None):
    """Every instruction **you typed** in the window, Butler's copy and the
    local one counted once. Returns (rows, raw / kept).

    81% of local prompts were a second record of one sentence (a task sent
    through Butler is logged on both sides); undeduplicated, every family's
    count doubles.
    """
    flt = filters or Filters(conf)
    typed, machine = [], []
    # One queue's length further back: a copy run at the window's start may
    # have been created before it.
    for ts, p in _rows(con, "butler", lo - QUEUE_MS, hi):
        (machine if flt.is_machine(p) else typed).append((ts, p.get("text") or ""))
    typed = [(ts, t) for ts, t in typed if ts >= lo]
    gone = fold.agent_dupe_index(machine)
    mine = fold.agent_dupe_index(typed)

    def machine_copy(ts, text, cwd):
        """A program's task run here by the Butler daemon leaves its own local
        copy, and it can queue for days (median 130 minutes, longest 8 days).
        The long pairing only applies to copies run in a Butler worktree, and a
        twin you sent yourself at the same moment makes it yours — otherwise the
        same words typed two days later would be swallowed."""
        if fold.is_agent_dupe(gone, ts, text):
            return True
        if ".butler-worktrees/" not in (cwd or "") or fold.is_agent_dupe(mine, ts, text):
            return False
        return any(0 <= ts - t <= QUEUE_MS
                   for t in gone.get((text or "")[:fold.DUPE_PREFIX], ()))

    local = [(ts, p.get("text") or "") for ts, p in _rows(con, "claude", lo, hi)
             if not machine_copy(ts, p.get("text") or "", p.get("cwd"))]
    kept = [(ts, text) for ts, text, _ in fold.dedupe_agent(typed, local)
            if len(re.sub(r"\s", "", text)) >= MIN_INSTRUCTION_CHARS
            and not flt.is_agent_prompt(text)]
    raw = len(typed) + len(local)
    return kept, round(raw / len(kept), 2) if kept else 1.0


def _evidence(samples, limit: int = 4):
    """Evidence lines: the day, and the title or words seen then."""
    seen, out = set(), []
    for ts, text in samples:
        t = re.sub(r"\s+", " ", (text or "")).strip()[:90]
        if t and t not in seen:
            seen.add(t)
            out.append({"day": str(fold.day_of(ts)), "text": t})
            if len(out) >= limit:
                break
    return out


def already_automated(con, lo: int, hi: int, conf: dict | None = None) -> list[dict]:
    """The tasks a program already sends on a rhythm. Listed apart, never mixed
    into suggestions: they are the answer, not the question — and seeing one
    that stopped is useful."""
    flt = Filters(conf)
    rows = [(ts, p.get("text") or "") for ts, p in _rows(con, "butler", lo, hi)
            if flt.is_machine(p)]
    out = []
    for group in cluster(rows):
        c = recur.cadence([fold.day_of(ts) for ts, _ in group])
        ev = _evidence(group)
        if c and ev:
            out.append(dict(c, key=ev[0]["text"][:60], evidence=ev))
    out.sort(key=lambda r: (r["last"], r["n"]), reverse=True)
    return out


def worth_surfacing(c: dict) -> bool:
    """Is this candidate worth a line?

    A 16-week window finds hundreds; most are noise. Tightening by count drowns
    the fortnightly-but-important under daily trivia; by regularity puts a
    search page first. The default is span: at least three weeks. Bursts and
    projects passed their own tests (their definition is "short"), and offline
    actions have no span to judge. Short but frequent and regular passes too:
    that is usually something that only started two weeks ago (a bank every
    2–3 days for 18 days was cut by the span rule alone).
    """
    if c.get("kind") in ("burst", "project"):
        return True
    if c.get("span_days", 999) >= 21:
        return True
    return c.get("n", 0) >= 6 and c.get("regular", False)


def candidates(con, weeks: int = WINDOW_WEEKS, until_ms: int | None = None,
               with_dropped: bool = False, conf: dict | None = None):
    """Everything with a rhythm in the window, most recent first.
    with_dropped=True returns (candidates, {reason: count})."""
    until_ms = until_ms or int(dt.datetime.now().timestamp() * 1000)
    lo = window_start(until_ms, weeks)
    flt = Filters(conf)

    pages, samples = [], collections.defaultdict(list)
    mails, msamp = [], collections.defaultdict(list)
    for ts, p in _rows(con, "chrome", lo, until_ms):
        k = page_key(p.get("url"))
        if k:
            pages.append((ts, k))
            samples[k].append((ts, p.get("title") or p.get("url")))
        if "mail.google.com" in (p.get("url") or ""):
            mk = mail_key(p.get("title"))
            if mk:
                mails.append((ts, mk))
                msamp[mk].append((ts, p.get("title")))
    f = fold.fold(pages, fold.GAP_MIN["chrome"])

    out, dropped = [], collections.Counter()
    for c in recur.detect(f.actions):
        ev = _evidence(samples[c["key"]])
        if not ev:
            continue
        kind = "auth" if _AUTH.search(c["key"]) else "page"
        why = flt.drop(c["key"]) if kind == "page" else None
        if why:
            dropped[why] += 1
            continue
        out.append(dict(c, kind=kind, fold_ratio=f.ratio, evidence=ev,
                        days={fold.day_of(t) for t in f.actions[c["key"]]}))

    # Bursts first, and the rhythm gives way: eight straight days on a visa
    # site reads as "daily" to recur and is then dropped for its short span —
    # both sides reject it and it vanishes. One thing appears once.
    for b in burst.detect(f.actions, today=str(fold.day_of(until_ms))):
        ev = _evidence(samples[b["key"]])
        if not ev or flt.drop(b["key"]):
            continue
        out.append(dict(b, kind="burst", fold_ratio=f.ratio, evidence=ev,
                        # No rhythm and no next date; None rather than invented.
                        name="project", n=b["days"], median_gap=0, jitter=0,
                        regular=False, next_expected=None))
    bursts = {c["key"] for c in out if c["kind"] == "burst"}
    out = [c for c in out if c["kind"] == "burst" or c["key"] not in bursts]

    # Projects by host: a project spans sites. Its sites are then not
    # candidates of their own, or one project shows up five times.
    byhost = collections.defaultdict(list)
    for k, v in f.actions.items():
        if not flt.drop(k):
            byhost[k.split("/")[0]] += v
    taken = set()
    for pr in project.detect(byhost, today=str(fold.day_of(until_ms)),
                             window_start=str(fold.day_of(lo))):
        more = f" and {len(pr['keys']) - 3} more" if len(pr["keys"]) > 3 else ""
        out.append(dict(pr, kind="project", key=" + ".join(pr["keys"][:3]) + more,
                        fold_ratio=f.ratio,
                        evidence=[{"day": str(pr["first"]), "text": k} for k in pr["keys"][:6]],
                        name="project", n=pr["days"], median_gap=0, jitter=0,
                        regular=False, next_expected=None))
        taken |= set(pr["keys"])
    out = [c for c in out if c["kind"] == "project" or c["key"].split("/")[0] not in taken]

    # Offline: the web's noise rules do not apply — "at:Gym" is not a domain.
    off = offline.actions(con, lo, until_ms)
    for c in recur.detect(off):
        out.append(dict(c, kind="offline", fold_ratio=1.0,
                        evidence=[{"day": str(fold.day_of(t)), "text": c["key"]}
                                  for t in off[c["key"]][-3:]]))

    mf = fold.fold(mails, fold.GAP_MIN["chrome"])
    for c in recur.detect(mf.actions):
        ev = _evidence(msamp[c["key"]])
        if ev:
            out.append(dict(c, kind="mail", fold_ratio=mf.ratio, evidence=ev))

    ins, iratio = instructions(con, lo, until_ms, filters=flt)
    for group in cluster(ins):
        c = recur.cadence([fold.day_of(ts) for ts, _ in group])
        ev = _evidence(group)
        if c and ev:
            out.append(dict(c, kind="instruction", key=ev[0]["text"][:60],
                            hits=len(group), fold_ratio=iratio, evidence=ev))

    out.sort(key=lambda r: (r["last"], r["n"]), reverse=True)
    out = merge_flows(out)
    # akey is the key across months, so it must be deterministic: a title the
    # model writes changes wording every round.
    kept = [dict(c, lever=lever(c), akey=f"{c['kind']}:{c['key']}"[:160])
            for c in out if worth_surfacing(c)]
    return (kept, dict(dropped)) if with_dropped else kept


def coverage(con, weeks: int = WINDOW_WEEKS, until_ms: int | None = None) -> dict:
    """How many weeks of the window each source actually covers. "Four weeks of
    history" and "nothing periodic" must not look alike; an absent source is
    absent, not zero."""
    until_ms = until_ms or int(dt.datetime.now().timestamp() * 1000)
    lo = window_start(until_ms, weeks)
    out = {}
    for source, first, last, n in con.execute(
            "SELECT source, MIN(ts), MAX(ts), COUNT(*) FROM events WHERE ts < ? "
            "GROUP BY source", (until_ms,)):
        got = max(0, min(last, until_ms) - max(first, lo)) / WEEK_MS
        out[source] = {"weeks": round(got, 1), "n": n, "short": got < weeks * 0.75,
                       "first": fold.day_of(first)}
    return out


def history_weeks(con, until_ms: int | None = None) -> float:
    """How far back the sources a review reads go, in weeks."""
    until_ms = until_ms or int(dt.datetime.now().timestamp() * 1000)
    first = con.execute("SELECT MIN(ts) FROM events WHERE source IN (%s) AND ts < ?"
                        % ",".join("?" * len(SOURCES)), (*SOURCES, until_ms)).fetchone()[0]
    return 0.0 if first is None else (until_ms - first) / WEEK_MS


def by_lever(cands: list) -> dict:
    """Grouped by what can be done, most frequent first. Grouping by rhythm
    scattered one kind of fix over three tiers; what a person acts on is "these
    to unsubscribe", "these sign-ins to lengthen"."""
    g = collections.defaultdict(list)
    for c in cands:
        g[c.get("lever") or "other"].append(c)
    for v in g.values():
        v.sort(key=lambda c: -c.get("n", 0))
    return dict(g)


def render(con, weeks: int = WINDOW_WEEKS, until_ms: int | None = None,
           conf: dict | None = None) -> str:
    """The whole count as text: `umbra review`. Nothing here calls a model."""
    until_ms = until_ms or int(dt.datetime.now().timestamp() * 1000)
    lo = window_start(until_ms, weeks)
    cands, dropped = candidates(con, weeks, until_ms, with_dropped=True, conf=conf)
    L = [f"  {weeks} weeks  ({fold.day_of(lo)} – {fold.day_of(until_ms)})",
         f"  {len(cands)} things on a rhythm"
         + (" · filtered out: " + ", ".join(f"{k} {v}" for k, v in dropped.items())
            if dropped else ""), ""]
    groups = by_lever(cands)
    for lev in LEVERS:
        rows = groups.get(lev, [])
        if not rows:
            continue
        L.append(f"  ══ {LEVER_WORDS[lev]} ({len(rows)})")
        for c in rows[:15]:
            reg = " " if c.get("regular") else "~"
            steps = f"  ⟨{len(c['steps'])} steps⟩" if c.get("steps") else ""
            nxt = f" · next ~{c['next_expected']}" if c.get("next_expected") else ""
            L.append(f"   {reg}×{c['n']:<3} {c['name']:<16} last {c['last']}{nxt}")
            L.append(f"        {c['key'][:68]}{steps}")
        if len(rows) > 15:
            L.append(f"   … and {len(rows) - 15} more")
        L.append("")

    ins, _ = instructions(con, lo, until_ms, conf=conf)
    b = behavior.summarize(ins)
    if b["total"]:
        L.append(f"  ══ The role you play in {b['total']} instructions")
        for t in behavior.TYPES:
            n = b["counts"].get(t, 0)
            if n:
                trend = " ".join(f"{k[-3:]}:{v}" for k, v in sorted(b["weekly"][t].items())[-8:])
                L.append(f"   {t:<11} {n:>4} ({n / b['total'] * 100:4.1f}%)  → {behavior.LEVERS[t]}")
                L.append(f"        weekly {trend}")
        L.append(f"   unclassified {b['counts']['unclassified']:>4} "
                 f"({b['unclassified_share'] * 100:.0f}%)  ← not in the dictionary, "
                 "not necessarily absent")
        L.append("")

    auto = already_automated(con, lo, until_ms, conf=conf)
    if auto:
        L.append(f"  ══ Already running on its own ({len(auto)}, sent by a program)")
        for a in auto[:8]:
            L.append(f"   ×{a['n']:<3} {a['name']:<16} last {a['last']}")
            L.append(f"        {a['key'][:66]}")
        L.append("")

    L.append("  ── Coverage " + "─" * 50)
    for src, v in sorted(coverage(con, weeks, until_ms).items(), key=lambda kv: -kv[1]["weeks"]):
        flag = "  ⚠ shorter than the window: long rhythms cannot show" if v["short"] else ""
        L.append(f"   {src:<10} {v['weeks']:>5.1f} wk · {v['n']:>6} rows · since {v['first']}{flag}")
    L.append("")
    L.append("  ── Fold ratio (rows ÷ actions; unfolded, every number above would be this much bigger)")
    for kind in ("page", "mail", "instruction"):
        r = next((c["fold_ratio"] for c in cands if c["kind"] == kind), None)
        if r is not None:
            L.append(f"   {kind:<12} {r}:1")
    return "\n".join(L)
