"""Which stretches of a day advanced this week's priorities.

You write up to three sentences for the week. This asks, for each stretch the
inference pass produced, which of them it advanced. Summing that is arithmetic
and is not stored.

## Why this is a plugin, and what it demonstrates

It is the only bundled lane that reads Umbra's own output rather than a source's
records, the only one whose prompt depends on something no window contains, and
the only one with a page behind it. Between them the three bundled lanes cover
every shape the interface has to support, which is why they ship inside the
package: a contract nothing exercises is a contract nobody has checked.

## Why the attribution is stored rather than recomputed

It looks derived -- goals plus spans in, attribution out -- so it looks safe to
throw away and rebuild. But one of its inputs is the model itself, and that is
not something anybody controls. Today's is one version; in a year that version
may not exist. Treated as a cache, reopening a week next year silently
recomputes it, and "11.2 hours on Umbra that week" quietly becomes a different
number with nothing to say it changed. History would have been rewritten.

So it follows the rule inferences already follow: keep the model name, and keep
the answer. That is also why the core stores analyzer output and never expires
it, and why the rows below carry the digest of the goal text they were judged
against.

## Why the window is a day

`goal_attrib` has always been keyed per day, and the old candidate list always
yielded per-day jobs, for a reason worth keeping: with a week-wide cache key,
new data arriving on Friday changes the key and Monday is judged again. A number
read as 11.3 hours on Wednesday becomes 10.8 on Friday with nothing having
changed. The week supplies the goal text and decides which days are candidates;
it is never the shard.

## Where this used to run

On Cloudflare, in JavaScript, because pressing Submit on a phone had to answer
in seconds while the Mac might be asleep. `umbra/attrib.py` then ran that same
file in a `node` subprocess rather than write a second implementation -- one
prompt, two callers, which was the right call under that constraint and cost a
Node install and a Cloudflare account to attribute anything.

A local server removes the constraint that put it there. The prompt, the parse
and the row building are ported here; the edge path is gone. Until `umbra serve`
lands the cost is that goals are read-only on a deployed page and are edited
locally.
"""

from ... import goals as G
from ... import render

# What the core reads off this plugin (see docs/writing-a-plugin.md).
TITLE = "Weekly goals"
WHAT = "Set up to three goals a week and see how much time went to each."
ORDER = 1
ENABLED_BY_DEFAULT = True     # the one that only reads and draws

INPUT = "spans"
WINDOW = "day"

#: Ported from the page's own attribution endpoint, which ran this wording for
#: months. The reading notes are not decoration: the "recognise my own
#: vocabulary" line is what stops a project's codename reading as an unrelated
#: activity, and the "when unsure, leave it out" line is the difference between
#: an hours figure you trust and one you argue with.
PROMPT = """Below is what I was doing in each stretch of {day}. Decide which of
this week's goals each stretch advanced.

My goals this week:
{goal_list}

Reading them
- One stretch may serve several goals, or none.
- What follows "also:" was happening in parallel and counts too.
- Recognise my own vocabulary: a project's old name, a codename, a submodule,
  the tools and sources it uses. If the stretch genuinely moved the goal along,
  it counts.
- A name merely mentioned, with no work done on it, does not count. Using a tool
  for something else and building the tool itself are different things.
- When unsure, leave it out. Undercounting beats overcounting.

One line per stretch: index <TAB> time <TAB> what I was doing
{text}

Output only JSON, nothing else:
[{{"i": index, "g": [goal numbers...], "why": "under ten words, why"}}]
List only stretches that served at least one goal. None means [].
"""


def render_window(events, a: int, b: int, lo_ms: int, ctx: int = 0) -> str:
    """The day's stretches, numbered, one per line.

    The default rendering would do for reading; it will not do for answering.
    The model has to name the stretches it picked, and an index is the only
    reference short enough that it names them rather than paraphrasing them --
    a model asked to quote a time range back gets one digit wrong eventually,
    and a wrong range is credited to the wrong hour with nothing to catch it.

    The index is a position in `spans(events, a, b, lo_ms)` and `parse` reads it
    back through that same function. If the two ever disagree about which
    stretches are in the window, every attribution shifts by one and each row
    still looks perfectly well formed.
    """
    return "\n".join(
        f"{i}\t{render.hm(s['a'])} {s['b'] - s['a']}min\t{s['act']}"
        + ("; also: " + ", ".join(s["also"]) if s["also"] else "")
        for i, s in enumerate(G.spans(events, a, b, lo_ms)))


def params(con, day: str, window: dict) -> dict | None:
    """The week's goals, and the stretches the indices refer to.

    The goals appear in no window, which is the whole reason this hook exists: a
    key covering only the window would keep serving numbers computed against
    text that has since been rewritten, and nothing would say so.

    Returning None when the week has no goals says there is no question here.
    Asking which of nothing a stretch advanced is paying for an empty answer.

    `_goals` and `_spans` are carried for `parse` rather than for the prompt --
    the leading underscore marks them as such. They are in the key as well, which
    is redundant with the rendered text and harmless: the same content hashed
    twice is still the same key.
    """
    texts = G.load_goals(con, G.week_of(day))
    if not texts:
        return None
    return {
        "goal_list": "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1)),
        "_goals": texts,
        "_spans": G.spans(window["events"], window["a"], window["b"],
                          window["lo_ms"]),
    }


def parse(items, window):
    """The model's array -> one row per (stretch, goal) it advanced.

    An index or goal number outside range is dropped rather than clamped. The
    model invented it, and crediting invented time to a real goal is the one
    failure here that would make the hours untrustworthy -- quietly, and in the
    direction that flatters.

    A stretch serving two goals credits its **full duration to each**. Splitting
    it evenly would be invented precision: nothing establishes that a half hour
    advancing two things divided in half. So the goals sum to more than the
    elapsed time, which is intended rather than double counting.

    Every row carries `gdig`, the digest of the goal text it was judged against.
    The old table kept that in its primary key; the core's row store has one
    shape for every plugin, so it lives in the row -- and it has to live
    somewhere, because a row judged against goals you have since rewritten is
    about a goal 3 that no longer exists.
    """
    p = window.get("params") or {}
    spans, goals = p.get("_spans") or [], p.get("_goals") or []
    gdig = G.gdigest(goals)
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        try:
            s = spans[int(x["i"])]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        for g in x.get("g") or []:
            if isinstance(g, bool) or not isinstance(g, int):
                continue
            if not 1 <= g <= len(goals):
                continue
            out.append({"a": s["a"], "b": s["b"], "goal": g, "gdig": gdig,
                        "why": str(x.get("why") or "")[:60]})
    return out


def sync(con, days, log=print):
    """One scheduled round: judge this week and last, whatever days were handed.

    The scheduler hands over the days that just received data. Attribution
    wants a range of its own: a late recording can land in last week, and a
    goal set this morning applies to Monday too. Days whose week has no goals
    are not even listed (goals.attrib_days); days with no stretches cost
    nothing, because the core skips a window with no question in it.
    """
    from ... import analyzer
    wanted = G.attrib_days(con)
    if not wanted:
        log("  goals: no priorities set in the last two weeks")
        return None
    import sys
    r = analyzer.run_days(con, G.PLUGIN, sys.modules[__name__], wanted, log=log)
    log(f"  goals: {len(wanted)} days in range, {r['called'] - r['failed']} newly judged"
        + (f", {r['failed']} calls failed" if r["failed"] else "")
        + (f", {len(r['errors'])} days failed" if r["errors"] else ""))
    for day, err in r["errors"].items():
        # A day that was never judged reads as a day that advanced nothing.
        log(f"  ⚠ goals {day}: {err}")
    return r
