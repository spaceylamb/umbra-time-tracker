"""A synthetic database for developing the frontend against.

The design centres on one day — Tuesday 8 September 2026 — and this writes
that day into a database, plus the twenty days before it so the three-week
strip has something to draw. Everything is deterministic: the same call writes
the same rows, so screenshots of the served page are comparable across changes.

It writes through the same functions the importers and inference use
(`db.insert`, `inference.save*`, `db.record_import`), never straight into the
tables, so the page is built from rows shaped exactly like real ones.

Nothing here is real data, and nothing here is a test double for the model:
the "model" rows are written by hand to look like what one would produce.

    python3 -m umbra.devseed /tmp/umbra-dev      # then:
    UMBRA_DATA_DIR=/tmp/umbra-dev UMBRA_CONFIG_DIR=/tmp/umbra-dev umbra serve

The config it writes defines the three headline groups the page shows, chosen
so the numbers come out round: Productivity 9.7h, Sleep 7.2h, Exercise 0.8h.
"""

import datetime as dt
import os
import random
import sys

from . import analyze, db
from . import inference as INF

DAY = "2026-09-08"
MODEL = "gemini-3.7-flash"
BACK = 20                       # days before DAY, for the strip
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

PROMPTS = ["move claims.py under plugins/ and keep the import path working",
           "why does test_claims fail after the move",
           "split the viz payload into resident and on-demand",
           "fingerprint the day cache on the code that builds it",
           "the week grid needs a hairline between blocks"]
COMMANDS = ["git status", "python3 -m unittest", "rg claims umbra/", "git diff",
            "python3 -m umbra viz --fresh", "ls umbra/plugins"]
PAGES = [("https://news.ycombinator.com/", "Hacker News"),
         ("https://mail.google.com/", "Inbox"),
         ("https://github.com/sherwinx/umbra-time-tracker/pulls", "Pull requests"),
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
            add("whoop", "workout", a, {"sport": "Weightlifting"}, b - a)
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
    """Priorities for DAY's week, and attribution for its judged
    days — written to goal_attrib/goal_runs, the table older judgements live
    in, which goals.load_attrib reads as it stands."""
    from . import goals as G
    wk = G.week_of(DAY)
    G.save_goals(con, wk, GOALS, now_ms=1)
    G.set_targets(con, wk, [(10, "hours"), (10, "hours"), (5, "sessions")])
    gdig = G.gdigest(GOALS)
    mon = dt.date.fromisocalendar(int(wk[:4]), int(wk[6:]), 1)
    for i in range(7):
        day = (mon + dt.timedelta(i)).isoformat()
        evs = INF.load_events(con, day).get(MODEL) or []
        if not evs:
            continue
        for e in evs:
            g = (1 if "claims" in e["title"] or "plugins" in e["title"] or "PR" in e["title"]
                 else 2 if "UI" in e["title"] or "viz" in e["title"]
                 else 3 if e["cat"] == "sleep" and e["a"] == 0 else None)
            if g:
                con.execute("INSERT OR IGNORE INTO goal_attrib (day, a_min, b_min, goal_idx, "
                            "model, gdig, why) VALUES (?,?,?,?,?,?,?)",
                            (day, e["a"], e["b"], g, MODEL, gdig, e["title"]))
        con.execute("INSERT OR IGNORE INTO goal_runs (day, model, gdig, at_ms) "
                    "VALUES (?,?,?,?)", (day, MODEL, gdig, 1))
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


def _variant(rnd: random.Random):
    """A plausible other day: same shape, shifted and thinned — and never
    overlapping, as composed events never do: each starts no earlier than
    the one before it ended."""
    out = []
    last = 0
    for a, b, cat, title in EVENTS:
        if cat != "sleep" and rnd.random() < 0.15:
            continue
        s = rnd.choice((-10, -5, 0, 5, 10))
        a2 = max(last, _min(a) + (0 if cat == "sleep" else s))
        b2 = min(1440, _min(b) + s)
        if b2 - a2 >= 10:
            last = b2
            out.append((_hm(a2), _hm(b2) if b2 < 1440 else "23:59", cat, title))
    return out


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


def seed(data_dir: str, config_dir: str | None = None) -> str:
    """Write the synthetic database under data_dir and return its path.
    With config_dir, also write the config the page expects."""
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
    for i in range(BACK, -1, -1):
        day = (last - dt.timedelta(days=i)).isoformat()
        evs = EVENTS if i == 0 else _variant(rnd)
        db.insert(con, _raw(day, evs, rnd))
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
    for src in ("chrome", "claude", "zsh", "git", "whoop"):
        db.record_import(con, src, ok=True)
    # The Sources page's "Needs you": one source whose login expired — it worked, then
    # three runs in a row were refused.
    db.record_import(con, "plaud", ok=True)
    for _ in range(3):
        db.record_import(con, "plaud", ok=False,
                         error="401 — the stored token expired")
    con.commit()
    con.close()
    return path


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/umbra-dev"
    print(seed(d, config_dir=d))
    print(f"UMBRA_DATA_DIR={d} UMBRA_CONFIG_DIR={d} python3 -m umbra serve")
