"""The single source of truth for activity categories.

The key is the identity; the Chinese and English strings are only display.

Before this module, "sleep / coding / social" lived in three places — the infer
prompt, the default in inference, and two colour tables in viz. Adding a
category meant editing three files, and missing one showed up as "a stretch of
the colour bar that is permanently unidentifiable grey". "talk" really was grey
for a week before anyone noticed, because the hand-copied AICOL table had left
it out. The same table written twice will drift; blocks.py's BCODE already paid
that tuition once.

The rules (the module docstring of tests/test_taxonomy.py is the same list):

  · the store holds keys; old rows hold Chinese names, normalised on read
    rather than migrated
  · the prompt's category list is generated per language; the model replies
    with display names, normalised back to keys on save
  · an unrecognised name is **kept verbatim** — a failed normalisation must
    not destroy what the model said
  · config.toml's [categories.*] adds your own; [taxonomy] replace = true
    swaps the built-in table out entirely ("separate work from creative work"
    is not a request to append one more category)
  · a replacement affects **the table given to the model** and **display**, not
    **what can be recognised** — months of old keys are in the store, and they
    must still read and display after a swap, or one config change turns all
    history grey
  · when conf is not passed, **read it** via config.load(). Every call site
    used to omit it, which made [categories.*] dead on the real code path: the
    config was written, and the model was never told

legacy=True marks categories an early prompt taught the model and later
withdrew ("at a place" / "using apps"). Old data must still be recognised; new
prompts should not teach them again.
"""

import json
import os
import tempfile

from . import palette

# Colours are **derived, not written down** — see palette.py for why, and for
# the ceiling that forces the shape below. A category names a family; the family
# owns one number, its hue; the two members of a family differ by a step.
#
# The families are paired by what the reader is actually asking when they glance
# at the bar, which is never "which of fourteen" but "what kind of hour was
# that". Workout and Chores looking related is correct — both are upkeep — and
# so is Reading sitting beside Listening.
#
# An earlier version of this table held seventeen hand-picked hex values and a
# comment explaining that thirteen colours cannot all clear a colour-blindness
# threshold. That was true and it was the wrong conclusion: the fix is not a
# better search over seventeen independent values, it is to stop having
# seventeen independent values.
#
# Four families carry one member instead of two — ambient, focus, screen and
# build. Two of those were deliberate from the start: packed full, the
# `family = "..."` key in config.toml would be a setting that can never be
# honoured, and it is the one control this design gives a user. The other two
# fell out of moving Coding (see _TABLE). Every free slot is tested to keep the
# same distance from every other family that the built-ins keep.
# Placed, not divided. Evenly spaced hues are the tell that nobody looked: a
# degree buys far less separation through green and teal than it does through
# magenta, so 120→158 is deliberately the tightest gap and 66→120 the widest.
# These angles were searched against OKLab distance across both themes. The
# objective used to be that the closest cross-family pair stays twice as far
# apart as the within-family step; that ratio was retired once the lightness
# bands made it fall every time a colour was fixed (see test_palette.py). What
# is enforced now is an absolute floor between any two different families, and
# the same floor for each family's free slot — where a `family = "..."` line in
# config.toml would land. Re-run that check before hand-editing one of these.
# The angles landed within 0–13° of Tailwind's palette without being aimed at
# it, which is worth knowing twice over: it says the search was not arbitrary,
# and it gives everyone a word to use instead of a number. Snapping the rest of
# the way does not work — Tailwind packs cyan, sky and blue inside 215–260°,
# which is fine when you pick one accent and fatal when fourteen marks sit edge
# to edge. Measured: fully snapped, the stranger-to-kin ratio falls 2.14 → 1.04
# and Research and Browsing become the same colour.
_FAMILIES = {
    "social":  14,    # talking to people             ~rose      (warm band: no)
    "errands": 68,    # the logistics of a day        ~amber     (warm band)
    "ambient": 122,   # somewhere, doing something    ~lime      (warm band)
    "upkeep":  158,   # maintaining body and home     ~emerald
    "focus":   205,   # finding things out            ~cyan      (cool band)
    "screen":  252,   # in front of something, idly   ~sky       (cool band)
    "build":   271,   # making things                 ~blue      (deep band)
    "rest":    292,   # off duty                      ~violet
    "input":   334,   # taking something in           ~fuchsia
}

# Circumstance rather than activity: a bucket, a declined answer, and "which
# window had focus". They take the neutral ramp — spending hue on a non-answer
# is how "we do not know" ends up looking like a finding.
_OFF_WHEEL = ("other", "unknown", "app")

# Which member takes step 1 is the area rule, and it is not arbitrary. Step 1
# sits nearer the ground in *both* themes — 0.72 against a near-white page, 0.68
# against a near-black one — so it is the quieter of the pair either way. The
# member that usually occupies more of the day therefore takes it: Sleep is
# about a third of every day and Coding most of a working one, and a mark that
# large has to be the calm one or it shouts the whole page down. The previous
# palette had Sleep as the most saturated violet it owned, which was this rule
# inverted, and it is why the bar read as loud before anyone could say why.
#
# **Coding is the one deliberate exception.** It is the largest block on most
# working days, so by this rule it should be the calmest — and for five rounds it
# was, in the cool arc, where it read as murky or as a twin of Browsing. It now
# takes step 0 of its own family in the deep band: the most present colour on
# the page, on purpose, because in a tool that exists to account for working
# time it is the thing the page is about. It used to share `focus` with
# Research; the two cannot sit in one arc any more, since the arc that suits
# Research (lifted cyan) is exactly the one that made Coding look wrong.
_TABLE = [
    # key        zh        en            family     step
    ("fun",      "娱乐",   "Fun",        "rest",    0, {}),
    ("sleep",    "睡觉",   "Sleep",      "rest",    1, {}),
    ("workout",  "运动",   "Workout",    "upkeep",  0, {}),
    ("chore",    "家务",   "Chores",     "upkeep",  1, {}),
    ("research", "查资料", "Research",   "focus",   0, {}),
    ("code",     "写代码", "Coding",     "build",   0, {}),
    ("read",     "读书",   "Reading",    "input",   0, {}),
    ("listen",   "在听",   "Listening",  "input",   1, {}),
    ("talk",     "说话",   "Talking",    "social",  0, {}),
    ("social",   "社交",   "Social",     "social",  1, {}),
    ("transit",  "在路上", "Transit",    "errands", 0, {}),
    ("meal",     "吃饭",   "Meal",       "errands", 1, {}),
    ("browse",   "浏览",   "Browsing",   "screen",  0, {}),
    # Step 1, not 0, and it is the only member `ambient` has. The step is there
    # to separate two members; with one member there is nothing to separate
    # from, so it takes whichever stop looks better. In the warm arc that is the
    # top one — at step 0 this lands on 114° at L 0.70, which is an olive.
    ("place",    "在某地", "At a place", "ambient", 1, {"legacy": True}),
    ("other",    "其他",   "Other",      None,      0, {}),
    ("unknown",  "不确定", "Unknown",    None,      1, {}),
    ("app",      "用 app", "Using apps", None,      2, {"legacy": True}),
]

# There is no budget constant here on purpose. How many families fit is not a
# number anyone should get to pick — palette.next_hue() answers it from the
# geometry of what is already placed, and says None when the wheel is full.


_CONF: dict | None = None


def _conf(conf: dict | None = None) -> dict:
    """An explicit argument wins; otherwise read the config file (cached once).

    The cache exists so norm() does not touch the disk for every event — that
    is tens of thousands of calls a day. Tests that change the config directory
    must call reload().
    """
    if conf is not None:
        return conf
    global _CONF
    if _CONF is None:
        from . import config
        _CONF = config.load()
    return _CONF


def reload() -> None:
    """Drop the caches. Call after editing config.toml, or after a test swaps
    CONFIG_DIR or the data directory."""
    global _CONF, _ASSIGNED
    _CONF = _ASSIGNED = None


# ── Where an assignment is written down ────────────────────────
#
# A colour is only worth anything if Tuesday's colour is still Friday's, and
# "the midpoint of the largest remaining gap" is only stable while the gaps are.
# Computed fresh every load, deleting one category would silently recolour every
# category declared after it — months of memory, spent for nothing.
#
# So the first time a category is seen it is assigned and **written down**, and
# after that the file is the answer. Entries are never pruned, including for
# categories removed from config.toml: the whole reason known() exists is that
# months of old rows still have to display, and they have to display in the
# colour they have always had.
#
# Built-ins are not in here. They are in _TABLE, which is a better place for
# something that ships.

_ASSIGNED: dict | None = None


def _assign_path() -> str:
    from . import db
    return os.path.join(db.DB_DIR, "palette.json")


def _assignments() -> dict:
    """key -> {"family", "hue", "step"}. Missing or corrupt reads as empty.

    Tolerant on purpose: a palette file that cannot be parsed should cost this
    run its stable colours, not stop Umbra from opening.
    """
    global _ASSIGNED
    if _ASSIGNED is None:
        try:
            with open(_assign_path(), encoding="utf-8") as f:
                loaded = json.load(f)
            _ASSIGNED = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            _ASSIGNED = {}
    return _ASSIGNED


def _remember(rows: dict) -> None:
    """Atomic write, mode 600 — these are the user's own category names.

    A failure here is not fatal and is not raised: the assignment already in
    memory is correct for this run, and the next run recomputes it. Losing
    stability is bad; refusing to draw the page because the disk is full is
    worse.
    """
    path = _assign_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def _own(conf: dict, persist: bool = False) -> list[dict]:
    """Categories defined in the config file, each given a place in the palette.

    `persist` is false unless the caller is asking about the **real** config.
    Passing a config dict in is a hypothetical — "what would these categories
    look like" — and a hypothetical must not write to the user's data
    directory. Built without that distinction, four test files quietly wrote
    their fixtures into a live palette.json, and read them back on the next
    run. A write on a read path is the smell; this is the fence around it.

    This used to hand every one of them the same grey, which meant **a
    user-defined category was invisible as a category** — the exact failure this
    module was written to end, arriving through the config file instead of
    through a hand-copied table.

    Three ways in, in order: name an existing `family` and take its free step;
    otherwise open a family of your own, at the hue your `color` asks for or at
    the midpoint of the largest remaining gap; and once the wheel is full, the
    neutral ramp. The last one is not a failure mode, it is the honest answer —
    past a dozen hues nobody can tell them apart anyway.
    """
    hues = dict(_FAMILIES)
    free = {f: [0, 1] for f in _FAMILIES}
    for _, _, _, fam, step, _ in _TABLE:
        if fam and step in free.get(fam, ()):
            free[fam].remove(step)

    # Everything already written down is placed first, whether or not it is
    # still in config.toml. Doing this before the loop is what makes the
    # guarantee true: a newcomer sees a wheel that includes the departed, so it
    # cannot be handed a hue that used to mean something else.
    stored = _assignments()
    for key, row in stored.items():
        fam, hue, step = row.get("family"), row.get("hue"), row.get("step", 0)
        if fam and hue is not None:
            hues.setdefault(fam, hue)
            if step in free.get(fam, [0, 1]):
                free.setdefault(fam, [0, 1]).remove(step)
            else:
                free.setdefault(fam, [0, 1])

    # Overflow slots are written down too, so a tail category keeps the same
    # grey rather than shuffling along the ramp when a neighbour is removed.
    spare = 1 + max((row.get("step", 0) for row in stored.values()
                     if row.get("hue") is None), default=-1)
    # A config entry whose key is already a built-in is a *rename*, not a new
    # category — `[categories.sleep] zh = "睡眠"` must not cost Sleep its family
    # and drop it into the neutral ramp. It keeps its place unless the config
    # explicitly names a different family.
    builtin = {k: (fam, step) for k, _, _, fam, step, _ in _TABLE if fam}

    out, fresh = [], {}
    for key, spec in ((conf or {}).get("categories") or {}).items():
        if key in builtin and not spec.get("family"):
            fam, step = builtin[key]
            out.append({"key": key, "zh": spec.get("zh", key),
                        "en": spec.get("en", key), "family": fam,
                        "hue": _FAMILIES[fam], "step": step})
            continue
        if key in stored:
            row = stored[key]
            fam, step = row.get("family"), row.get("step", 0)
            hue = row.get("hue")
        elif (fam := spec.get("family")) in free and free[fam]:
            step, hue = free[fam].pop(0), hues[fam]
            fresh[key] = {"family": fam, "hue": hue, "step": step}
        else:
            asked = palette.hue_of(spec.get("color") or "")
            if asked is not None and not palette.is_clear(asked, hues.values()):
                asked = None      # too close to an existing family to honour
            hue = palette.next_hue(hues.values()) if asked is None else asked
            if hue is None:
                fam, step, spare = None, spare, spare + 1
            else:
                fam, step = key, 0
                hues[fam], free[fam] = hue, [1]
            fresh[key] = {"family": fam, "hue": hue, "step": step}
        out.append({"key": key, "zh": spec.get("zh", key),
                    "en": spec.get("en", key),
                    "family": fam, "hue": hue, "step": step})

    if fresh:
        stored.update(fresh)
        if persist:
            _remember(stored)
    return out


def _builtin() -> list[dict]:
    return [{"key": k, "zh": zh, "en": en, "family": fam, "step": step,
             "hue": _FAMILIES.get(fam) if fam else None,
             "circumstance": k in _OFF_WHEEL, **extra}
            for k, zh, en, fam, step, extra in _TABLE]


def _paint(cats: list[dict], theme: str = "dark") -> list[dict]:
    """Attach the derived colour. Nothing upstream of here stores one."""
    return [{**c, "color": (
        palette.color(c["hue"], c["step"], theme) if c.get("hue") is not None
        else palette.neutral(c["step"], theme, c.get("circumstance", False)))}
        for c in cats]


def table(conf: dict | None = None) -> list[dict]:
    """The table **offered to the model and used for display**.

    With replace=true, only your own set."""
    own = _own(_conf(conf), persist=conf is None)
    conf = _conf(conf)
    if own and (conf.get("taxonomy") or {}).get("replace"):
        return _paint(own)
    return _paint(_builtin() + own)


def known(conf: dict | None = None) -> list[dict]:
    """The table of what can be **recognised** — always includes the built-ins.

    Keeping this separate from table() is the whole point: after a swap, months
    of code/browse rows in the store still need to normalise, and still need a
    name and a colour. Merged into one table, a single config change drops all
    history into the unrecognised grey fallback.
    """
    own = _own(_conf(conf), persist=conf is None)
    seen = {c["key"] for c in own}
    return _paint(own + [c for c in _builtin() if c["key"] not in seen])


def key_of(name, conf: dict | None = None) -> str | None:
    """Any spelling (key / Chinese / English, case-insensitive) -> key.
    None when unrecognised."""
    s = str(name).strip()
    for c in known(conf):
        if s == c["key"] or s == c["zh"] or s.lower() == c["en"].lower():
            return c["key"]
    return None


def norm(name, conf: dict | None = None) -> str:
    """Normalise: convert when recognised, otherwise **keep verbatim**.

    viz has a grey fallback for categories it does not know; rewriting to
    "unknown" instead would disguise "the model said something new" as "the
    model was unsure", which are different facts.
    """
    return key_of(name, conf) or str(name)


def label(key: str, lang: str = "zh", conf: dict | None = None) -> str:
    for c in known(conf):
        if c["key"] == key:
            return c.get(lang) or c["key"]
    return key


def labels(lang: str = "zh", conf: dict | None = None) -> dict:
    """key -> display name, injected into the front end."""
    return {c["key"]: c.get(lang) or c["key"] for c in known(conf)}


def colors(conf: dict | None = None, theme: str = "dark") -> dict:
    """key -> '#rrggbb' for one theme.

    Both themes are the same hue at two lightnesses, so they cannot drift apart
    the way two hand-maintained tables did. Dark is the default because that is
    what the page has always rendered as; passing "light" is not a second
    palette, it is the same one read at a different height.
    """
    # Pass `conf` through untouched rather than resolving it here: known() has
    # to be able to tell "no config given" from "this config given", because
    # that is what decides whether the assignment is written down.
    return {c["key"]: c["color"] for c in _paint(known(conf), theme)}


def prompt_cats(lang: str = "zh", conf: dict | None = None) -> str:
    """The category list for the prompt (space-separated, same shape as the
    original CATS constant)."""
    return " ".join(c.get(lang) or c["key"] for c in table(conf)
                    if not c.get("legacy"))


# ── Personal rules ─────────────────────────────────────────────
#
# "Lark usually means company work" is a judgement that holds for exactly one
# person, and it often contains a company name. Same class of thing as
# places.json, which holds a home address: it lives in the config directory and
# never enters the repo. With no config — the open-source default — this
# section does not appear at all and the prompt is byte-for-byte what it was.
#
# A rule is **a hint to the model, not a hard override**. Writing
# "Lark → work" does not make the program stamp a label whenever it sees Lark;
# it tells the model what that signal means in this person's life. A hard
# override would manufacture a batch of unexplainable judgements, which
# conflicts with "no un-auditable conclusions".

_RULE_HEAD = {"zh": "个人规则（这个人的生活里，这些信号意味着）",
              "en": "Personal rules (what these signals mean in this person's life)"}


def prompt_rules(lang: str = "zh", conf: dict | None = None) -> str:
    """Render as a prompt section; empty string when there are no rules."""
    rules = (_conf(conf).get("rules") or [])
    if not rules:
        return ""
    bullet = "·" if lang == "zh" else "-"
    lines = []
    for r in rules:
        sig, means = r.get("signal"), r.get("means")
        if not sig or not means:
            continue
        note = f"（{r['note']}）" if lang == "zh" and r.get("note") else (
            f" ({r['note']})" if r.get("note") else "")
        lines.append(f"{bullet} {sig} → {means}{note}")
    if not lines:
        return ""
    return "\n" + _RULE_HEAD[lang if lang in _RULE_HEAD else "en"] + "\n" + "\n".join(lines) + "\n"


# ── Metric groups ──────────────────────────────────────────────
#
# "Productivity = work + create + read + news" is a **derived** number. It is
# not a 14th category and it is never asked of the model — the model judges
# categories, and the sum is plain arithmetic. Which buys three things:
#   · changing a group definition needs no re-inference; historical numbers
#     follow immediately
#   · no drift — a sum is not right some days and wrong on others
#   · unconfigured, the layer does not exist and the page omits the whole row
#
# Sleep goes through the same mechanism (a group containing only sleep). No
# special case for it: two numbers that look alike and are computed alike, so
# the reader never has to remember which one is a category and which a group.


def families() -> list[str]:
    """The family names, in wheel order — for showing categories grouped."""
    return list(_FAMILIES)


def assignment_path() -> str:
    """Where the remembered colour assignment lives (palette.json)."""
    return _assign_path()


def groups(conf: dict | None = None) -> dict:
    """{key: {"zh": ..., "en": ..., "cats": [...]}}, in config file order.

    cats may name a key that never appeared in [categories.*] — it references
    the key itself, so built-in categories can be grouped too.
    """
    out = {}
    for key, spec in ((_conf(conf).get("groups") or {})).items():
        cats = list(spec.get("cats") or [])
        if not cats:
            continue
        out[key] = {"key": key, "cats": cats,
                    "zh": spec.get("zh", key), "en": spec.get("en", key)}
    return out
