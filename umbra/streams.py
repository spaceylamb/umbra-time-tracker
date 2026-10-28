"""The two sources the resident collector writes: live and idle.

They have no importer. collector.py produces them continuously rather than being
pulled, and it cannot even be imported off macOS because it pulls in PyObjC — so
the declarations describing those rows live here, where every platform can read
them without being able to produce them.

The split is not cosmetic. Everything downstream needs to know how a live/window
row renders and how an idle stretch classifies, on a Linux machine reading a
database copied from a Mac just as much as on the Mac itself.
"""

SOURCE = "live"          # this module describes two: live and idle

LANES = [
    {"key": "live", "kind": None,
     "origin": ("Umbra daemon", "window title + clipboard collector")},
    {"key": "idle", "kind": None,
     "origin": ("Umbra daemon", "no-input detection")},
]

# A dict because this module answers for two source names and they differ.
# idle's dur_ms is how long nobody touched anything, measured directly. live's
# rows are point events — a window title is true at an instant, and claiming a
# duration for it would stretch one title across everything that followed.
DURATION = {"idle": "trusted", "live": "point"}


def render(e) -> str | None:
    """One line for the model.

    lock / unlock / activate produce nothing: they are too granular to be worth
    a line, and the state change they represent is already visible in whether
    window rows keep arriving.
    """
    s, k = e.get("source"), e.get("kind")

    if s == "idle":
        return "💤 no key or mouse input in this stretch"

    if k == "clip":
        t = (e.get("text") or "").strip()
        return f"📋 clipboard: {t}" if t else None

    if k == "window":
        t = e.get("title")
        if not t:
            return None
        # A Chrome window title is the same fact as chrome/visit, with a
        # " - Google Chrome - ..." suffix added. 1,743 records a day, almost all
        # duplicates.
        # But **non-Chrome ones must not be dropped** — the hard evidence behind
        # judging 09:03 as "in a Zoom work meeting" (the window title "Zoom
        # Meeting") exists nowhere else.
        if "chrome" in (e.get("app") or "").lower():
            return None
        return f"▢ window: {e.get('app') or ''} — {t}"

    return None


def classify(e) -> str | None:
    """No category, deliberately, for either of them.

    These two answer "was anybody at the machine", which feeds contradictions
    and blind spots rather than an activity. Giving idle a category would make
    every slot where somebody stepped away classify as something they were
    doing; giving live one would double-count whatever the screen sources
    already said.
    """
    return None
