"""Three priorities a week, and whether a stretch of time advanced one.

Three layers; do not conflate them:

    goals        the three sentences you wrote      once a week    goals table
    attribution  which goals each stretch advanced  model-judged   analyzer_rows
    hours        the attribution, summed            arithmetic     not stored

This module owns the first and reads the second. The judging itself is a plugin
(umbra/plugins/goals/), because it is a prompt and a parser and nothing else —
the core runs it, stores it, and decides when it need not run again.

## Why attribution is stored rather than cached

Attribution looks like derived data (goals + spans -> attribution), as though it
could be recomputed at any time. But it has an input **you do not control: the
model itself**. Today that is gemini-3.7-flash; in a year that version may not
exist. Treated as a cache, opening this week again next year recomputes it, and
"11.2 hours on Umbra that week" quietly becomes a different number, with no
warning. History would have been rewritten.

So it follows the same rule as inferences: **store it with the model name**.
Along with a digest of the goal text (gdig), so that a judgement made against
goals you have since rewritten is never read as a judgement about the ones you
have now. Goal 3 is a position, and rewriting goal 3 does not move the hours
that were credited to what used to be there.

Size is not a concern: about 90 rows a week, so 46,000 rows over ten years,
roughly 5 MB — while the events table is already 158,000 rows and 101 MB.
**Everything is kept; nothing is pruned.**

## Attribution is cached per day, not per week

If the cache key were "the whole week's set of spans", new data arriving on
Friday would change the key and Monday would be re-judged — you saw 11.3 hours
on Wednesday, and 10.8 on Friday, having changed nothing. Sharding by day
confines the effect to the one day. Same reasoning as infer.py's per-window
incrementality.
"""

import datetime as dt
import hashlib

SCHEMA = """
-- The weekly goals you wrote yourself. Three a week (not enforced; however
-- many you write is how many there are). Live: this is where they are written
-- and read.
CREATE TABLE IF NOT EXISTS goals (
    week    TEXT NOT NULL,          -- ISO week, '2026-W34'; sorts as a string
    idx     INTEGER NOT NULL,       -- which one, starting at 1
    text    TEXT NOT NULL,
    set_at  INTEGER NOT NULL,       -- when it was set (milliseconds)
    PRIMARY KEY (week, idx)
);

-- FROZEN HISTORY. Attribution judged before the plugin move lives here and is
-- read as it stands; nothing writes these two tables any more. New judgements
-- go to the core's analyzer_rows under the plugin name "goals", carrying the
-- same goal digest in the row. Rewriting these rows into the new shape would be
-- a refactor editing history, and the one thing attribution must never do is
-- come back with a different number than it gave at the time.
--
-- Which goal the model assigned a stretch of time to. The granularity is a
-- **span** (an activity interval inside a 30-minute window), not a day_event —
-- events are merged, and a two-hour "working on a personal project" may contain
-- only 40 minutes actually advancing the goal. Counted per event, the whole
-- block would be credited.
CREATE TABLE IF NOT EXISTS goal_attrib (
    day      TEXT NOT NULL,
    a_min    INTEGER NOT NULL,      -- aligned with inferences.a_min
    b_min    INTEGER NOT NULL,
    goal_idx INTEGER NOT NULL,
    model    TEXT NOT NULL,         -- which model judged it; see the docstring
    gdig     TEXT NOT NULL,         -- digest of the goal text at judgement time
    why      TEXT NOT NULL DEFAULT '',   -- one line of reasoning; this is what
                                         -- the tooltip shows
    PRIMARY KEY (day, a_min, goal_idx, model, gdig)
);
CREATE INDEX IF NOT EXISTS goal_attrib_day ON goal_attrib(day);

-- "Attribution has run for this day." **Must be separate from goal_attrib** —
-- a day may have nothing attributed at all, in which case goal_attrib holds zero
-- rows, which looks exactly like "this day has not been computed yet". Without
-- this table, every day that genuinely contained no goal time is recomputed
-- every round, and the difference is never visible.
CREATE TABLE IF NOT EXISTS goal_runs (
    day    TEXT NOT NULL,
    model  TEXT NOT NULL,
    gdig   TEXT NOT NULL,
    at_ms  INTEGER NOT NULL,
    PRIMARY KEY (day, model, gdig)
);
"""


def week_of(day: str) -> str:
    """'2026-08-17' -> '2026-W34'.

    ISO weeks: starting Monday, aligned with the page's week view (monday()).
    Around a year boundary the ISO year can differ from the calendar year
    (2027-01-01 belongs to 2026-W53), and that is correct — following ISO is what
    keeps string ordering from going backwards at the end of a year.
    """
    y, w, _ = dt.date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def gdigest(texts) -> str:
    """A set of goal texts -> a digest. One component of the attribution cache
    key.

    Order participates: goal 1 and goal 3 are not the same thing, and attribution
    is stored by index.
    """
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def save_goals(con, week: str, texts, now_ms: int | None = None) -> int:
    """Write one week's goals. **Delete then write** — going from three down to
    one must make the old third disappear, or the page shows a goal you already
    deleted."""
    now_ms = now_ms if now_ms is not None else 0
    rows = [(week, i + 1, t, now_ms)
            for i, t in enumerate(t.strip() for t in texts) if t]
    with con:
        con.execute("DELETE FROM goals WHERE week=?", (week,))
        con.executemany("INSERT INTO goals (week, idx, text, set_at) "
                        "VALUES (?, ?, ?, ?)", rows)
    return len(rows)


UNITS = ("hours", "sessions")


def set_targets(con, week: str, targets) -> None:
    """targets: one (target, unit) or None per goal, in goal order.

    Kept apart from save_goals so the goal texts — and so gdigest, the
    attribution cache key — never depend on a target: changing "10 hours" to
    "12 hours" must not throw away a week of judgement and pay to redo it.
    """
    with con:
        for i, t in enumerate(targets, 1):
            target, unit = t if t else (None, None)
            con.execute("UPDATE goals SET target=?, unit=? WHERE week=? AND idx=?",
                        (target, unit, week, i))


def load_targets(con, week: str) -> list:
    """[(target, unit) or None, ...] in goal order."""
    return [(t, u) if t is not None else None for t, u in con.execute(
        "SELECT target, unit FROM goals WHERE week=? ORDER BY idx", (week,))]


def sessions(rows, gap_min: int = 30) -> int:
    """How many separate stretches a set of attributed rows makes: rows closer
    than gap_min run together as one session. Counting rows would make a
    two-hour workout judged in four half hours into four workouts."""
    n, end = 0, None
    for a, b in sorted((r["a"], r["b"]) for r in rows):
        if end is None or a - end >= gap_min:
            n += 1
        end = b if end is None else max(end, b)
    return n


def load_goals(con, week: str) -> list:
    """One week's goals, by index. Never set means empty — and empty is not an
    error."""
    return [r[0] for r in con.execute(
        "SELECT text FROM goals WHERE week=? ORDER BY idx", (week,))]


def weeks(con) -> list:
    """Every week that has goals, newest first."""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT week FROM goals ORDER BY week DESC")]


#: How many weeks back to judge automatically. 0 would be this week only.
#: One week back is for late-arriving recordings: Plaud uploads in a batch once a
#: day, and a seven-hour delay across midnight was measured on 2026-08-24.
#: Further back is not touched on a schedule — a stored judgement is history, and
#: two weeks is the real window in which the underlying data still moves. Past
#: that, a change should be something a person asked for.
BACK = 1


def attrib_days(con, week: str | None = None, back: int = BACK) -> list:
    """The days worth judging: these two weeks, in the weeks that have goals.

    A week with no goals is skipped here rather than in the plugin, so that no
    connection, no rendering and no key is built for it. Days with no stretches
    are **not** skipped: the core drops a window with nothing in it at no cost,
    and a second opinion here about what counts as empty is a second place to be
    wrong.
    """
    import datetime as dt
    week = week or week_of(dt.date.today().isoformat())
    cur = dt.date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    out = []
    for i in range(max(back, 0) + 1):
        mon = cur - dt.timedelta(weeks=i)
        if not load_goals(con, week_of(mon.isoformat())):
            continue
        out += [(mon + dt.timedelta(j)).isoformat() for j in range(7)]
    return sorted(out)


# ── Stretches ─────────────────────────────────────────────────

def spans(events, a: int, b: int, lo_ms: int) -> list[dict]:
    """The stretches of one window, as attribution sees them.

    Four fields, in this order: a, b, act, also. The page sent exactly these for
    months and the prompt's wording is built around them. A fifth field would be
    a change to the model's input, which is a change to the key of every answer
    already stored.

    Shared by the plugin's rendering and its parsing on purpose. The model refers
    to a stretch by its position in this list; computed twice, the two copies
    would eventually disagree by one and every attribution would shift by a
    stretch, each row still perfectly well formed.
    """
    from . import inference as INF
    out = []
    for e in events:
        m = (e["ts"] - lo_ms) // 60000
        if not a <= m < b:
            continue
        out.append({"a": INF.minute(e["start"]), "b": INF.minute(e["end"]),
                    "act": e.get("activity") or "",
                    "also": list(e.get("also") or [])})
    return out


# ── Attribution: reading ──────────────────────────────────────
#
# Two stores, one answer. Attribution judged before the move is in goal_attrib,
# keyed by (day, a_min, goal_idx, model, gdig); everything since is in the core's
# analyzer_rows under the plugin name below, carrying the same goal digest in the
# row. Both are read here. **Neither is rewritten** — the old rows are history,
# and history is not something a refactor gets to edit.
#
# Precedence is per day and not per row: once the new store has judged a day, it
# owns that day. Merging the two would double every stretch judged under both.

PLUGIN = "goals"


def _judged(con, day: str, model: str, gdig: str):
    """The new store's rows for one day, or None if it has never judged it."""
    from . import analyzer
    if not analyzer.ran(con, PLUGIN, day, model):
        return None
    return [{"a": r["a"], "b": r["b"], "g": r["goal"], "why": r.get("why") or ""}
            for r in analyzer.load_rows(con, PLUGIN, day, model)
            if r.get("gdig") == gdig and r.get("goal")]


def _frozen(con, day: str, model: str, gdig: str) -> list:
    return [{"a": a, "b": b, "g": g, "why": w} for a, b, g, w in con.execute(
        "SELECT a_min, b_min, goal_idx, why FROM goal_attrib "
        "WHERE day=? AND model=? AND gdig=? ORDER BY a_min, goal_idx",
        (day, model, gdig))]


def load_attrib(con, day: str, model: str, gdig: str) -> list:
    """One day's attribution under one set of goals: [{a, b, g, why}, ...]

    Empty means the model was asked and credited nothing, or that nobody has
    asked yet. Those are different facts and done_days() is what separates them —
    this reads rows, and a row is the only thing it can honestly report.
    """
    rows = _judged(con, day, model, gdig)
    return _frozen(con, day, model, gdig) if rows is None else rows


def totals(con, days, model: str, gdig: str) -> dict:
    """{goal_idx: minutes}.

    When one stretch is attributed to two goals, **both get the full duration**
    (decided by the user on 2026-08-23): 10:00-10:30 advancing two things at once
    credits 30 minutes to each. Splitting it into 15+15 would be invented
    precision — nothing establishes that the half hour divided evenly.
    So the goals sum to more than the elapsed time. That is intended, not a
    double-counting bug.
    """
    out: dict = {}
    for day in days:
        for r in load_attrib(con, day, model, gdig):
            out[r["g"]] = out.get(r["g"], 0) + (r["b"] - r["a"])
    return out


def done_days(con, days, model: str, gdig: str) -> set:
    """Of these days, the ones whose attribution is current.

    Current means judged against exactly these goals **and** exactly the
    stretches that exist now. That is stricter than the old table could answer:
    its key held the goal digest but nothing about the stretches, so a recording
    arriving late changed a day's stretches and the stale number kept its tick.
    Both moves shift the core's digest, so both are covered.

    A day the new store has never judged falls back to the old table's marker,
    which is the best that can be said of it — the rows are there, and there is
    no way left to ask what input produced them.

    This is what stops "0.0 h" from reading as an answer when it is really a
    question nobody has asked yet.
    """
    from . import analyzer, registry
    mod = registry.analyzers().get(PLUGIN)
    out = set()
    for day in days:
        if analyzer.ran(con, PLUGIN, day, model):
            if mod is not None and analyzer.current(con, PLUGIN, mod, day,
                                                    model):
                out.add(day)
        elif con.execute("SELECT 1 FROM goal_runs WHERE day=? AND model=? "
                         "AND gdig=?", (day, model, gdig)).fetchone():
            out.add(day)
    return out
