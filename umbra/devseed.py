"""A synthetic database for developing the frontend against.

The design centres on one day — Tuesday 8 September 2026 — and this writes
that day into a database, plus the three whole weeks before its own, so the
strip has something to draw and Week has weeks to page back through. The weeks
get better as they go — late nights and hours lost to feeds early on, more of
every priority by the end — so a comparison with last week shows a trend, not
noise. Everything is deterministic: the same call writes the same rows, so
screenshots of the served page are comparable across changes.

It writes through the same functions the importers and inference use
(`db.insert`, `inference.save*`, `db.record_import`), never straight into the
tables, so the page is built from rows shaped exactly like real ones.

Nothing here is real data, and nothing here is a test double for the model:
the "model" rows are written by hand to look like what one would produce.

    python3 -m umbra.devseed /tmp/umbra-dev      # then:
    UMBRA_DATA_DIR=/tmp/umbra-dev UMBRA_CONFIG_DIR=/tmp/umbra-dev umbra serve

    python3 -m umbra.devseed --site OUT          # the public demo, as a static site

The config it writes defines the three headline groups the page shows, chosen
so the numbers come out round: Productivity 9.7h, Sleep 7.2h, Exercise 0.8h.
"""

import datetime as dt
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

from . import analyze, db
from . import inference as INF

DAY = "2026-09-08"
MODEL = "gemini-3.7-flash"
BACK = 22                       # days before DAY: back to the Monday three weeks before its own
# The public demo's one moment: its imports ran then, and its page's clock
# stands there — the morning after DAY, once the night's records are in.
DEMO_NOW = int(dt.datetime(2026, 9, 9, 8, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
UNJUDGED = 2                    # this many days before DAY: no model has looked
FAILED = 1                      # this many days before DAY: two calls failed (17c)
FAILED_WINDOWS = [(840, 870), (870, 900)]   # 14:00-15:00

# The reference day, event for event. The last stretch has no event on
# purpose: 23:10-24:00 is the one the model said nothing about.
EVENTS = [
    ("00:00", "07:10", "sleep",   "Asleep (WHOOP)"),
    ("07:10", "07:40", "chore",   "Morning routine"),
    ("07:40", "08:10", "meal",    "Breakfast at home"),
    ("08:10", "08:40", "browse",  "Morning news and feeds"),
    ("08:40", "09:05", "transit", "Biking to the office"),
    ("09:05", "09:30", "browse",  "Email and Slack catch-up"),
    ("09:30", "09:45", "talk",    "Team standup"),
    ("09:45", "12:30", "code",    "Umbra: moving claims into the plugins package"),
    ("12:30", "13:10", "meal",    "Lunch with coworkers"),
    ("13:10", "13:40", "social",  "Coffee walk with a colleague"),
    ("13:40", "15:30", "code",    "PR review and fixing the claims tests"),
    ("15:30", "16:00", "read",    "Reading about local-first sync engines"),
    ("16:00", "18:00", "code",    "Umbra: viz payload split, cache fingerprint"),
    ("18:05", "18:25", "transit", "Walking to the gym"),
    ("18:25", "19:15", "workout", "Weightlifting at the gym"),
    ("19:15", "20:20", "meal",    "Dinner at home"),
    ("20:30", "22:30", "code",    "Evening: UI redesign sketches"),
    ("22:35", "23:10", "read",    "Reading a book on economic history in WeRead"),
]

# A Saturday or Sunday: nobody bikes to the office. Same rules as EVENTS.
WEEKEND = [
    ("00:00", "08:20", "sleep",   "Asleep (WHOOP)"),
    ("08:20", "09:10", "meal",    "Slow breakfast"),
    ("09:10", "09:50", "browse",  "News and feeds"),
    ("10:00", "11:20", "workout", "Long run by the river"),
    ("11:30", "12:30", "chore",   "Groceries and laundry"),
    ("12:40", "13:40", "meal",    "Lunch with friends"),
    ("13:40", "16:00", "social",  "Afternoon out with friends"),
    ("16:10", "17:30", "read",    "Reading on the balcony"),
    ("17:40", "18:40", "chore",   "Cooking"),
    ("18:40", "19:30", "meal",    "Dinner at home"),
    ("19:40", "21:30", "browse",  "A film and some scrolling"),
    ("21:40", "23:00", "read",    "Reading a novel in WeRead"),
]

# How each week before DAY's goes, by how many weeks back it is: nights spent
# up past midnight, and blocks of the first priority lost to feeds instead.
# Counts, not chances, so the trend is the same on every run.
ARC = {3: (5, 6), 2: (3, 3), 1: (1, 0), 0: (0, 0)}

PROMPTS = ["move claims.py under plugins/ and keep the import path working",
           "why does test_claims fail after the move",
           "split the viz payload into resident and on-demand",
           "fingerprint the day cache on the code that builds it",
           "the week grid needs a hairline between blocks"]
COMMANDS = ["git status", "python3 -m unittest", "rg claims umbra/", "git diff",
            "python3 -m umbra viz --fresh", "ls umbra/plugins"]
PAGES = [("https://news.ycombinator.com/", "Hacker News"),
         ("https://mail.google.com/", "Inbox"),
         ("https://github.com/spaceylamb/umbra-time-tracker/pulls", "Pull requests"),
         ("https://www.inkandswitch.com/local-first/", "Local-first software"),
         ("https://excalidraw.com/", "umbra week view — Excalidraw")]


def _min(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _raw(day: str, events, rnd: random.Random) -> list:
    """Raw rows that would plausibly have produced these events."""
    lo, _ = analyze.day_bounds(day)
    at = lambda m: lo + int(m * 60_000)       # noqa: E731
    rows = []

    def add(src, kind, m, payload, dur_min=None):
        rows.append((f"{src}:{day}:{len(rows)}", at(m),
                     int(dur_min * 60_000) if dur_min else None,
                     src, kind, payload))

    for a, b, cat, title in events:
        a, b = _min(a), _min(b)
        if cat == "sleep":
            add("whoop", "sleep", a, {}, b - a)
        elif cat == "workout":
            add("whoop", "workout", a,
                {"sport": "Running" if "run" in title.lower() else "Weightlifting"}, b - a)
        elif cat == "code":
            for m in range(a, b, 7):
                add("claude", "prompt", m + rnd.random() * 5,
                    {"text": rnd.choice(PROMPTS), "project": "umbra"})
                if rnd.random() < 0.6:
                    add("zsh", "command", m + 3, {"cmd": rnd.choice(COMMANDS)})
            for m in range(a + 40, b, 55):
                add("git", "commit", m, {"repo": "umbra",
                                         "msg": title.split(": ")[-1][:60]})
        elif cat in ("browse", "read"):
            for m in range(a, b, 4):
                url, t = rnd.choice(PAGES)
                add("chrome", "visit", m + rnd.random() * 3,
                    {"url": url, "title": t})
    return rows


def _spans(events) -> list:
    """First-pass guesses: each event cut at the 30-minute windows."""
    out = []
    for a, b, cat, title in events:
        a, b = _min(a), _min(b)
        m = a
        while m < b:
            e = min(b, (m // 30 + 1) * 30)
            out.append({"start": _hm(m), "end": _hm(e), "cat": cat,
                        "activity": title, "confidence": "high",
                        "evidence": [f"{_hm(m)} {title}"]})
            m = e
    return out


def _digests(events) -> list:
    wins = sorted({(_min(a) // 30) * 30 for a, _, _, _ in events}
                  | {m for a, b, _, _ in events
                     for m in range((_min(a) // 30) * 30, _min(b), 30)})
    return [{"start": _hm(w), "end": _hm(w + 30),
             "win": (_hm(max(0, w - 60)), _hm(min(1439, w + 90))),
             "n": 12, "text": f"[{_hm(w)}] summarised records for this window"}
            for w in wins]


# What was said aloud on DAY. Tasks, then claims; the claims come
# from windows with two or more people talking, which is the lane's own gate.
TASKS = [
    ("09:38", "followup", "work", "Send Maya the benchmark numbers from the parser rewrite",
     "I’ll send you the parser numbers this afternoon"),
    ("12:52", "question", "personal", "Find out whether the lease renewal needs 60 or 90 days’ notice",
     "was the lease renewal sixty or ninety days’ notice again"),
    ("13:24", "errand", "personal", "Book the car in for its service before the 20th",
     "the car needs its service before the twentieth"),
    ("20:41", "idea", "work", "A week view that sorts days by how unlike the rest they were",
     "could the week view sort days by how different they were from the rest"),
]
ECHOES = 9                      # said while dictating to an agent: held back
CLAIMS = [
    ("12:41", "speaker 2", "The new capital-gains inclusion rate took effect in June",
     "the new capital-gains rate already kicked in in June", "number", "unsure", 1, "it has a date in it"),
    ("12:47", "speaker 1", "SQLite can’t take concurrent writes at all",
     "SQLite just can’t do concurrent writes", "mechanism", "likely_wrong", 0, "the model doubts it"),
    ("13:02", "speaker 1", "Postgres added MERGE in version 15",
     "Postgres only got MERGE in 15", "entity", "confident_right", 1,
     "a version number is exactly what goes stale"),
    ("13:06", "speaker 2", "Light takes about eight minutes to reach Earth",
     "light takes about eight minutes to get here", "number", "confident_right", 0,
     "stable fact, no number that moves"),
]


GOALS = ["Ship the plugin interfaces", "Redesign the timeline page", "Sleep before midnight"]


def _goals(con) -> None:
    """The same priorities every week the seed covers, and attribution for
    their judged days — written to goal_attrib/goal_runs, the table older
    judgements live in, which goals.load_attrib reads as it stands. Keeping
    them the same is what makes week against week a trend."""
    from . import goals as G
    gdig = G.gdigest(GOALS)
    first = dt.date.fromisoformat(DAY) - dt.timedelta(days=BACK)
    mon = first - dt.timedelta(days=first.weekday())
    while mon.isoformat() <= DAY:
        wk = G.week_of(mon.isoformat())
        G.save_goals(con, wk, GOALS, now_ms=1)
        G.set_targets(con, wk, [(10, "hours"), (10, "hours"), (5, "sessions")])
        for i in range(7):
            day = (mon + dt.timedelta(i)).isoformat()
            evs = INF.load_events(con, day).get(MODEL) or []
            if not evs:
                continue
            for e in evs:
                g = (1 if _ships(e["title"])
                     else 2 if "UI" in e["title"] or "viz" in e["title"]
                     else 3 if e["cat"] == "sleep" and e["a"] == 0 else None)
                if g:
                    con.execute("INSERT OR IGNORE INTO goal_attrib (day, a_min, b_min, goal_idx, "
                                "model, gdig, why) VALUES (?,?,?,?,?,?,?)",
                                (day, e["a"], e["b"], g, MODEL, gdig, e["title"]))
            con.execute("INSERT OR IGNORE INTO goal_runs (day, model, gdig, at_ms) "
                        "VALUES (?,?,?,?)", (day, MODEL, gdig, 1))
        mon += dt.timedelta(days=7)
    con.commit()


def _said(con, day: str) -> None:
    """Tasks and claims, written where the two lanes write them now: the core's
    findings store. Seeding the frozen proposals/claims tables instead would
    keep the page's drawer green after the live path had broken."""
    from . import analyzer

    def win(t):
        return (_min(t) // 30) * 30

    # One sent, one dealt with: the drawer shows each state, and a closed
    # task stays listed, faded, rather than vanishing.
    states = {1: "dispatched", len(TASKS) - 1: "dismissed"}
    for i, (t, typ, dom, task, quote) in enumerate(TASKS):
        analyzer.save_items(con, "actions", day, MODEL, win(t), "seeded", [{
            "a": _min(t), "b": _min(t) + 1, "t": t, "task": task, "quote": quote,
            "type": typ, "domain": dom, "confidence": "high", "marked": False,
            "speakers": 1, "echo_score": 0.0, "echo_text": None,
            "state": states.get(i, "proposed")}], "quote", now_ms=1)
    for i in range(ECHOES):
        t = _hm(600 + i * 7)
        analyzer.save_items(con, "actions", day, MODEL, win(t), "seeded", [{
            "a": _min(t), "b": _min(t) + 1, "t": t, "task": f"dictated step {i}",
            "quote": f"echo {i}", "echo_score": 0.9, "state": "echo"}],
            "quote", now_ms=1)
    for t, spk, claim, quote, kind, verdict, absolute, why in CLAIMS:
        analyzer.save_items(con, "claims", day, MODEL, win(t), "seeded", [{
            "a": _min(t), "b": _min(t) + 1, "t": t, "speaker": spk,
            "claim": claim, "quote": quote, "kind": kind, "verdict": verdict,
            "absolute": absolute, "why": why}], "quote", now_ms=1)
    con.commit()


def _ships(title: str) -> bool:
    """Does this block count toward the first priority (see _goals)?"""
    return "claims" in title or "plugins" in title or "PR" in title


def _variant(rnd: random.Random, weekend: bool = False, late: bool = False,
             lose: int = 0, sketch: bool = False, up_tonight: bool = False):
    """A plausible other day: same shape, shifted and thinned — and never
    overlapping, as composed events never do: each starts no earlier than
    the one before it ended. late: up past midnight, so the night's sleep
    starts after it. lose: this many first-priority blocks go to feeds.
    sketch: a weekend evening with an hour on the redesign. up_tonight: the
    next day starts late, so this one ends scrolling rather than asleep.

    Unlike DAY, whose last stretch is left blank on purpose, these days run to
    midnight, lose at most one short event (never the night's first or last), and
    close the half-hour seams that shifting leaves — a made-up day full of "the model said nothing" reads as broken."""
    src = list(WEEKEND if weekend else EVENTS)
    src.append(("23:15", "24:00", "browse", "Up late, scrolling") if up_tonight
               else ("23:15", "24:00", "sleep", "Asleep (WHOOP)"))
    if sketch:
        src = [e for e in src if e[0] < "19:40"] + [("19:40", "21:30", "code", "Evening: UI redesign sketches")]
    if late:
        up = rnd.choice((40, 55, 70))
        src = [("00:00", _hm(up), "browse", "Up late, scrolling")] + [
            (_hm(up), b, c, t) if c == "sleep" and a == "00:00" else (a, b, c, t)
            for a, b, c, t in src]
    out = []
    last = 0
    dropped = False
    for a, b, cat, title in src:
        if cat == "code" and _ships(title) and lose > 0:
            lose -= 1
            cat, title = "browse", "Lost in feeds and tabs"
        elif (cat not in ("sleep", "code") and not dropped and _min(b) - _min(a) <= 50
              and a != "00:00" and b != "24:00" and rnd.random() < 0.12):
            dropped = True
            continue
        s = rnd.choice((-10, -5, 0, 5, 10))
        a2 = max(last, _min(a) + (0 if cat == "sleep" else s))
        if a2 - last <= 30:
            a2 = last
        b2 = 1440 if b == "24:00" else min(1440, _min(b) + s)
        if b2 - a2 >= 10:
            last = b2
            out.append((_hm(a2), _hm(b2) if b2 < 1440 else "23:59", cat, title))
    return out


def _reference_rng() -> random.Random:
    """The random stream DAY's raw records were first cut from: Random(8) after
    twenty days of the original, simpler variants. Replaying it keeps DAY
    byte-for-byte what the README's screenshots show, whatever the other days
    do now — they draw from a stream of their own."""
    rnd = random.Random(8)
    for _ in range(20):
        out, last = [], 0
        for a, b, cat, title in EVENTS:
            if cat != "sleep" and rnd.random() < 0.15:
                continue
            s = rnd.choice((-10, -5, 0, 5, 10))
            a2 = max(last, _min(a) + (0 if cat == "sleep" else s))
            b2 = min(1440, _min(b) + s)
            if b2 - a2 >= 10:
                last = b2
                out.append((_hm(a2), _hm(b2) if b2 < 1440 else "23:59", cat, title))
        _raw(DAY, out, rnd)
    return rnd


def _arc(rnd: random.Random, last: dt.date) -> dict:
    """ARC laid out on days: which nights run late, which weekdays lose how
    many first-priority blocks. day -> keyword arguments for _variant."""
    week0 = last - dt.timedelta(days=last.weekday())
    plan = {}
    for k, (late, lose) in ARC.items():
        if not k:
            continue
        mon = week0 - dt.timedelta(days=7 * k)
        days = [mon + dt.timedelta(days=i) for i in range(7)]
        for d in rnd.sample(days, late):
            plan.setdefault(d.isoformat(), {})["late"] = True
        weekdays = days[:5]
        for j in range(lose):
            d = weekdays[j % 5].isoformat()
            plan.setdefault(d, {})["lose"] = plan.get(d, {}).get("lose", 0) + 1
    return plan


CONFIG = """\
[ui]
lang = "en"

# The drawer's two lanes, extracting only: nothing is sent anywhere.
[plugins.actions]
enabled = true

[plugins.claims]
enabled = true

[groups.productivity]
en = "Productivity"
zh = "Productivity"
cats = ["code", "read"]

[groups.sleep]
en = "Sleep"
zh = "睡眠"
cats = ["sleep"]

[groups.exercise]
en = "Exercise"
zh = "运动"
cats = ["workout"]
"""


def seed(data_dir: str, config_dir: str | None = None, demo: bool = False) -> str:
    """Write the synthetic database under data_dir and return its path.
    With config_dir, also write the config the page expects. demo: the public
    demo's variant — every source in order, nothing that needs you."""
    os.makedirs(data_dir, exist_ok=True)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.toml"), "w", encoding="utf-8") as f:
            f.write(CONFIG)
    path = os.path.join(data_dir, "events.db")
    if os.path.exists(path):
        os.remove(path)
    con = db.connect(path)
    rnd = random.Random(8)
    last = dt.date.fromisoformat(DAY)
    week0 = last - dt.timedelta(days=last.weekday())
    plan = _arc(rnd, last)
    ref = _reference_rng()
    for i in range(BACK, -1, -1):
        d = last - dt.timedelta(days=i)
        day = d.isoformat()
        k = (week0 - (d - dt.timedelta(days=d.weekday()))).days // 7
        tomorrow = (d + dt.timedelta(days=1)).isoformat()
        evs = EVENTS if i == 0 else _variant(
            rnd, weekend=d.weekday() >= 5, sketch=d.weekday() == 6 and k <= 1,
            up_tonight=plan.get(tomorrow, {}).get("late", False), **plan.get(day, {}))
        db.insert(con, _raw(day, evs, ref if i == 0 else rnd))
        if i == UNJUDGED:
            continue            # recorded, never read: the hatch
        INF.save(con, day, MODEL, _spans(evs))
        INF.save_events(con, day, MODEL,
                        [{"start": a, "end": b, "cat": c, "title": t}
                         for a, b, c, t in evs])
        INF.save_digests(con, day, MODEL, _digests(evs))
        if i == FAILED:
            # The events around the failed hour still exist — the second pass
            # composes from whatever spans there are — but nothing covers it.
            lo = min(a for a, _ in FAILED_WINDOWS)
            hi = max(b for _, b in FAILED_WINDOWS)
            kept = [e for e in evs if _min(e[1]) <= lo or _min(e[0]) >= hi]
            INF.save(con, day, MODEL, _spans(kept))
            INF.save_events(con, day, MODEL,
                            [{"start": a, "end": b, "cat": c, "title": t}
                             for a, b, c, t in kept])
            INF.save_failures(con, day, MODEL, [
                {"a": a, "b": b, "at": 1788880000000 + a, "reason": "HTTP 503: model overloaded"}
                for a, b in FAILED_WINDOWS])
    _said(con, DAY)
    _goals(con)
    at = DEMO_NOW if demo else None
    for src in ("chrome", "claude", "zsh", "git", "whoop"):
        db.record_import(con, src, ok=True, at=at)
    # The Sources page's "Needs you": one source whose login expired — it worked, then
    # three runs in a row were refused. Not in the demo: made-up days have
    # nothing for a visitor to fix.
    db.record_import(con, "plaud", ok=True, at=at)
    for _ in range(0 if demo else 3):
        db.record_import(con, "plaud", ok=False,
                         error="401 — the stored token expired")
    con.commit()
    con.close()
    return path


# Where the demo's "Get Umbra" goes.
LINK = "https://github.com/spaceylamb/umbra-time-tracker#quick-start"


def site(out: str, home: str | None = None) -> dict:
    """The public demo: these three weeks as a static site, flagged so the page
    says it is a demo and its clock stands still.

    Built in a child process whose home is a throwaway directory: where the
    database and the config live is fixed when umbra is first imported, so
    only a fresh interpreter can be pointed somewhere else — and the export
    then writes every path as ~/…, naming nothing of the machine that built
    it. home: that directory (made and removed here when not given)."""
    made = home is None
    home = home or tempfile.mkdtemp(prefix="umbra-demo-")
    os.makedirs(home, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".demo-", dir=home)
    try:
        data, conf = os.path.join(work, "Umbra"), os.path.join(work, ".config", "umbra")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("UMBRA_", "XDG_"))}
        env.update(HOME=work, UMBRA_DATA_DIR=data, UMBRA_CONFIG_DIR=conf,
                   PYTHONPATH=os.pathsep.join(
                       [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
                       + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])))
        code = ("import json, sys; from umbra import config, db, devseed; "
                "print(json.dumps(devseed._write_site(sys.argv[1], sys.argv[2], db.DB_PATH, config.CONFIG_DIR)))")
        argv = [sys.executable, *(["-S"] if sys.flags.no_site else []), "-c", code,
                os.path.abspath(out), work]
        r = subprocess.run(argv, env=env, check=True, capture_output=True, text=True)
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if made:
            shutil.rmtree(home, ignore_errors=True)


def _write_site(out: str, home: str, db_path: str, config_dir: str) -> dict:
    """The half of site() that runs in the child. seed() deletes the database
    it is pointed at, so this refuses anything but a fresh one inside home."""
    from . import export
    inside = os.path.realpath(home) + os.sep
    if not os.path.realpath(db_path).startswith(inside):
        raise RuntimeError(f"the demo writes only inside {home}, not {db_path}")
    if os.path.exists(db_path):
        raise RuntimeError(f"refusing to write the demo over an existing database: {db_path}")
    seed(os.path.dirname(db_path), config_dir=config_dir, demo=True)
    return export.write(out, demo={"link": LINK, "now": DEMO_NOW})


if __name__ == "__main__":
    if sys.argv[1:2] == ["--site"] and len(sys.argv) == 3:
        r = site(sys.argv[2])
        print(f"{sys.argv[2]}  ({r['files']} files, {r['bytes'] / 1e6:.1f} MB) — the public demo")
        sys.exit(0)
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/umbra-dev"
    print(seed(d, config_dir=d))
    print(f"UMBRA_DATA_DIR={d} UMBRA_CONFIG_DIR={d} python3 -m umbra serve")
