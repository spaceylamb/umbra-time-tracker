"""Category colours, derived rather than written down.

Until now taxonomy._TABLE held seventeen hand-picked hex values. A table is not
a system: it cannot answer what the eighteenth category should look like, and
config.toml has always let anyone declare one. The fallback for a category
without a colour was a single grey, so **every user-defined category was grey** —
the same failure the taxonomy module was created to end, arriving by a different
door.

Worse, hexes chosen independently collide. Every pair flagged in the palette
audit — browsing against research, coding against at-a-place, sleep against
transit against social — came from picking values one at a time and only
noticing the clash afterwards. Fixing one by hand moved the collision somewhere
else, which is the signature of a set with more members than the space holds,
not of a badly chosen value.

So a category no longer stores a colour. It stores a **family**, and the family
stores **one number — its hue**:

    colour(category, theme) = oklch( L[theme], C[theme], hue[family] ± step )

L is fixed per theme and C is capped, so no mark can be muddier, darker or
louder than another; the two themes are the same hue at two lightnesses and
cannot drift apart; and "do these two look too similar" becomes "how many
degrees apart are they", which a function can enforce.

## Why families, and why not [groups.*]

Hue reliably separates **8 to 12 things** at the size the timeline draws them —
an 8px dot in the event list, a 6px sliver in the day bar. That is a property of
eyes, not of palettes; thirty distinct hues is not a hard problem but an
impossible one, and evenly spacing thirty around the wheel produces a gradient,
which reads as *no* categories at all.

The way past the ceiling is to give the two dimensions different jobs. Hue
answers *which family* — the question you ask of the whole bar at a glance,
without reading. A step inside the hue answers *which member* — the question you
only ask of one block, at which point its label is already next to you. Eight
families holding two steps each is sixteen categories where nobody ever has to
tell one apart from the other fifteen.

This is deliberately **not** taxonomy.groups(). Those are metric groups —
"productivity = code + read + research" — an arithmetic concern that exists to
sum time, may overlap, and is often absent entirely. Borrowing them for colour
would mean an unconfigured install has no palette, and that adding sleep to a
productivity total would recolour it. Families are a separate, visual thing.

## Where the uniformity has to stop

The sentence above — "L is fixed per theme" — was too strong, and it cost four
rounds of the palette looking wrong in ways the distance metric could not see.
A hue's available chroma at a fixed lightness varies **threefold** around the
wheel, so one pair of stops does not buy one appearance. It buys a clean violet,
an olive, and a murky teal, all nominally "equal weight".

Three arcs therefore carry their own stops — see BANDS. 50–140° browns below
its gamut peak (the old Meal #975800 and Coding #d37600 were exactly this);
185–262° is the gamut's deepest valley, where the shared stops can only produce
something desaturated and dim; and 263–282° is the reverse, blue being richest
when dark, so it drops where the others lift. Those are sRGB facts, not
preferences.

The cost is paid in family tightness: two members of a family now read as
relatives less clearly than before. That is the right trade, because the two
steps were never really earning their keep as *kinship* — measurement showed
only one family of eight reading as kin — they earn it through the **area
rule**, giving the higher-volume member the quieter stop.
"""

import math

# ── The constants that carry the taste ─────────────────────────
#
# These four numbers decide more about how the product feels than any individual
# hue in it. L 0.62 at C 0.145 is mineral and quiet; L 0.72 at C 0.24 would be
# candy. Tuning the palette means tuning these, not editing a colour.
#
# The lightnesses are not symmetric between themes on purpose. A dark mark on a
# light ground advances while a light mark on a dark ground recedes, so light
# mode has to sit higher than a naive mirror would put it — at the old L*47 the
# data was as dark as the body text and the two fought for the top of the
# hierarchy.
#
# The pairs read (step 0, step 1), *not* (bright, dim) — light mode steps up
# from its first stop and dark mode steps down from its first. Anything that
# needs "the brighter of the two" has to ask, not index.
LIGHTNESS = {"light": (0.58, 0.66), "dark": (0.78, 0.72)}

# **Chroma is a fraction of what the hue can hold, not a number.**
#
# Every flat cap tried here was wrong in both directions at once, because the
# sRGB gamut is not a cylinder. At L 0.72 magenta can carry C 0.265 and teal
# can carry 0.123 — more than twice as much. A flat cap set loud enough for
# teal makes magenta and violet shout; set quiet enough for them, it washes
# teal out. 0.145 did the first, 0.088 did the second, and both readings were
# correct about the colour in front of them.
#
# The fraction is measured, not chosen. Three values picked by eye across
# months of this palette — a violet at C 0.169, a teal at 0.108, an earlier
# dark teal at 0.120 — all sit at 0.86 to 0.87 of their own gamut ceiling.
# Three independent guesses landing on one ratio is the rule showing itself.
#
# This also does, for free, the job the per-hue cap was hand-written to do:
# yellow and orange have *low* ceilings at these lightnesses, so the same
# fraction keeps them modest without anyone deciding that it should.
#
# 0.86 was the fit, and it did not transfer. The three reference swatches sat
# at L 0.65–0.69; the stops that actually ship are 0.58 and 0.66, and a hue's
# gamut ceiling *falls* as lightness rises away from the middle. Holding the
# fraction while moving the lightness therefore quietly spent chroma nobody
# agreed to spend. Re-fitted at the stops we ship, it is 0.95 — which lands
# within sight of Tailwind's own saturation while keeping equal weight, the
# family structure and the generated overflow that adopting Tailwind costs.
GAMUT_FRACTION = 0.95

# The fraction alone over-corrects at the magenta end, where sRGB's gamut
# bulges in a way perception does not follow: 0.86 of magenta's ceiling is
# C 0.25, louder than anything this palette has ever shipped. So there is still
# a ceiling — not as *the* rule, but as the point past which any hue shouts
# whatever its gamut allows. 0.17 is the most saturated value anyone here has
# picked and kept.
CHROMA_CEILING = 0.19

# So the weight doctrine is: equal L and equal **gamut fraction** means equal
# weight — not equal absolute chroma. Equal C is the thing that made yellow
# shout while violet whispered, which is the opposite of what it promised.

# ── Arcs that cannot use the shared stops ──────────────────────
#
# The gamut ceiling at a fixed lightness varies **threefold** around the wheel:
# at L 0.66 magenta holds C 0.274 and cyan holds 0.101. A single pair of stops
# therefore cannot mean the same thing everywhere. Three arcs have to be told
# where to sit, and the reasons pull in opposite directions:
#
#   50–140°  goes *brown*. Yellow and yellow-green below their own gamut peak
#            are not darker yellows, they are olive and mud — a different
#            colour, not a quieter one. (This was the original WARM_BAND. Its
#            stop was "the brighter of the two", which at 0.66 was still not
#            far enough: Meal stayed mustard and At-a-place stayed olive.)
#            Lifted.
#
#   185–262° goes *murky*. Cyan and azure are the gamut's deepest valley — 213°
#            cannot reach C 0.15 at any lightness whatsoever, and peaks at
#            L 0.84 — so at the shared stops they arrive desaturated, and a
#            desaturated dark cyan reads as dirty rather than calm. Lifted.
#
#   263–282° is the opposite case, and it is why Coding moved here. Blue is
#            *richest when dark*: 263° peaks at L 0.52 with C 0.267, where cyan
#            has 0.09. Lifting it would throw that away; the shared stops would
#            waste it. So this arc drops, and gets the deep register nothing
#            else on the wheel can reach at that chroma. Coding had been in the
#            cool arc, lifted, alongside Chores, Research and Browsing — four
#            bright cool blocks at nearly one lightness, which no angle search
#            could separate (the best on offer was 1.14× strangers-to-kin).
#            The way out was not another angle but another register.
#
# Each arc names its own (step 0, step 1) pair, and **every arc keeps a step**,
# pointing the same way as the shared stops: step 1 is the quieter one — lighter
# on a light ground, darker on a dark one.
#
# The warm arc used to flatten — both members at one lightness, separating by
# hue alone. That was inherited from the original WARM_BAND and it was wrong:
# Transit and Meal are 16° apart, and with the lightness cue removed they came
# out 0.046 apart, the closest pair in the whole set by a wide margin. Sixteen
# degrees of yellow is not a difference anyone can see. The floor of this arc
# is set by 60°, which holds up at 0.70; 76° and 130° want the top.
#
# The deep arc's step 1 sits well away from step 0 (0.70 against 0.57 in light)
# because the slot it would fill is 5° from Fun. Nothing ships there; it is
# the free slot a `family = "build"` line in config.toml would land in, and at
# a matching lightness it would be Fun's twin.
#
# A family must not straddle an edge, or its two members would sit at different
# lightnesses for a reason that has nothing to do with them. The cool arc ends
# at 262° rather than 250° for exactly that: `screen` sits at 252°, so its free
# slot is 260°, and it has to stay in the same register as Browsing.
BANDS = (((50, 140), {"light": (0.70, 0.80), "dark": (0.78, 0.72)}),
         ((185, 262), {"light": (0.70, 0.76), "dark": (0.78, 0.74)}),
         ((263, 282), {"light": (0.57, 0.70), "dark": (0.68, 0.58)}))

# The quiet stop may be light, or it may be maximally saturated. Not both.
#
# The area rule reads "step 1 is the quieter stop", and in light mode that is
# the *lighter* one — nearer the canvas, so nearer to disappearing. At the red
# and magenta end of the wheel that stops being true: those hues have gamut to
# spare (0.23–0.31 where cyan has 0.10), so the lighter stop arrives lighter
# *and* fully saturated, which is the recipe for neon. Social at L 0.66 C 0.190
# was #f0555b — the loudest thing on the page while nominally the quiet member
# of its pair, and close enough to --color-alert (#a93226, hue 28°) to read as
# a warning. Above RIDGE_CHROMA the quiet stop gives chroma back.
#
# Only bites in light mode, and that is the point rather than an exception:
# in dark mode the quiet stop is the *darker* one, and a dark saturated red
# recedes exactly as the area rule promises.
RIDGE_CHROMA = 0.22
GLARE_CEILING = 0.165

# How far the two members of a family sit either side of its hue. Wide enough to
# separate when they abut, narrow enough that they still read as relatives.
STEP_DEGREES = 8

# The closest two members of *different* families may sit. Same order as the
# within-family step on purpose: cross that line and a stranger starts reading
# as a relative, which is worse than two colours merely being similar — it
# asserts a kinship that is not there.
MIN_SEPARATION = 8

# So a family needs this much room between its neighbours' centres: it sits at
# the midpoint and reaches STEP_DEGREES toward each side, and both neighbours
# reach STEP_DEGREES back. 2 * (MIN_SEPARATION + 2 * STEP_DEGREES).
MIN_GAP = 2 * (MIN_SEPARATION + 2 * STEP_DEGREES)


def _srgb(l_, m_, s_):
    """OKLab's cone responses -> sRGB channels, before clipping."""
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def _linear(lightness, chroma, hue):
    h = math.radians(hue)
    a, b = chroma * math.cos(h), chroma * math.sin(h)
    return _srgb(lightness + 0.3963377774 * a + 0.2158037573 * b,
                 lightness - 0.1055613458 * a - 0.0638541728 * b,
                 lightness - 0.0894841775 * a - 1.2914855480 * b)


def _in_gamut(lightness, chroma, hue):
    return all(-0.001 <= c <= 1.001 for c in _linear(lightness, chroma, hue))


def _gamma(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def oklch(lightness: float, chroma: float, hue: float) -> str:
    """OKLCH -> '#rrggbb', lowercase.

    Lowercase because tests/test_taxonomy.py matches ^#[0-9a-f]{6}$, and because
    the whole point of this module is that nobody ever types one of these.
    """
    return "#" + "".join("%02x" % round(_gamma(c) * 255)
                         for c in _linear(lightness, chroma, hue))


def _oklab(hex_color: str):
    s = str(hex_color).strip().lstrip("#")
    rgb = [int(s[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
               for c in rgb]
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s_,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s_,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s_)


def distance(a: str, b: str) -> float:
    """How far apart two colours look, in OKLab.

    Degrees of hue are the wrong unit for that question and always were: one
    degree buys far less separation through green and teal than through magenta,
    which is why the family angles in taxonomy are uneven. This is the unit to
    check a change against — the last time two categories were "fixed" by eye,
    each fix moved the collision somewhere else for four rounds.
    """
    return math.dist(_oklab(a), _oklab(b))


def hue_of(hex_color: str) -> int | None:
    """'#rrggbb' -> the hue it is asking for, or None if it is a grey.

    config.toml has always accepted `color = "#..."` on a category, and dropping
    that would break people's files. So it is still honoured — as a **request
    for a hue rather than a pixel value**. Keep the angle they picked, replace
    the lightness and chroma with the system's, and the category joins the
    palette instead of sitting outside it looking like a bug.
    """
    s = str(hex_color).strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        rgb = [int(s[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return None
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
               for c in rgb]
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s_
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s_
    # 0.035 sits in measured empty space: the greys that turn up in practice
    # reach 0.027 (#7a8494, this module's own former fallback — nobody chose it,
    # they copied it, so reading it as "I would like blue" would be inventing an
    # intention), while the least saturated colour anyone has actually picked is
    # a taupe at 0.040.
    if math.hypot(a, bb) < 0.035:
        return None          # a near-grey is not asking for a hue
    return int(math.degrees(math.atan2(bb, a))) % 360


def _gamut_ceiling(lightness: float, hue: float) -> float:
    """Everything this hue can physically hold at this lightness.

    Binary search rather than a table, because a table is the thing this module
    exists to delete. Worth knowing on its own: the spread of this number around
    the wheel — 0.101 at cyan against 0.274 at magenta, at the same lightness —
    is why one pair of stops cannot serve every hue, and so why BANDS exists.
    """
    lo, hi = 0.0, 0.45
    for _ in range(28):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _in_gamut(lightness, mid, hue) else (lo, mid)
    return lo


def _usable_chroma(lightness: float, hue: float,
                   ceiling: float = CHROMA_CEILING) -> float:
    """GAMUT_FRACTION of what the hue holds, never above the ceiling.

    The fraction doubles as the safety margin: nothing lands on the gamut wall,
    where rounding to eight bits starts shifting the hue it was asked for.
    """
    return min(_gamut_ceiling(lightness, hue) * GAMUT_FRACTION, ceiling)


def color(hue: int, step: int = 0, theme: str = "dark") -> str:
    """The colour of a family's member. step is 0 or 1.

    Three rules, in order: the shared stops, then whichever BAND this hue falls
    in, then the glare ceiling at the quiet stop. See the constants above —
    each exists because a specific block on the page looked wrong.
    """
    stops = LIGHTNESS[theme]
    shifted = (hue + (STEP_DEGREES if step else -STEP_DEGREES)) % 360
    lightness = stops[1] if step else stops[0]

    ceiling = CHROMA_CEILING
    # The quiet stop may be light, or maximally saturated, not both — and it is
    # the lighter one only in light mode, so this is where the neon came from.
    if step and stops[1] > stops[0] and _gamut_ceiling(lightness, shifted) > RIDGE_CHROMA:
        ceiling = GLARE_CEILING

    for (low, high), band in BANDS:
        if low <= shifted <= high:
            lightness = band[theme][1 if step else 0]
            break

    return oklch(lightness, _usable_chroma(lightness, shifted, ceiling), shifted)


# ── Off the wheel ──────────────────────────────────────────────
#
# Some categories are circumstance rather than activity. "Other" is a bucket,
# "Unknown" is the model declining to answer, and "Using apps" says which window
# was focused rather than what was being done. Giving them a hue spends scarce
# separation on non-answers and, worse, makes "we do not know" look like a
# finding. They take the neutral ramp, and so does anything that overflows the
# families — the long tail is still drawn, still countable and still labelled;
# it has simply stopped claiming to be memorable.
# Two ramps, not one. Circumstance has to *recede* — pale in light mode, dim in
# dark — because "we do not know" must never look like an answer. The tail is
# hueless but fully present: those are real activities that simply arrived after
# the wheel was full, and rendering them at the same weight as Unknown would say
# the opposite of what is true.
# The three of them used to sit inside 0.08 of lightness of each other, which
# made "recedes" true of the group and false of its members: Other, Unknown and
# Using-apps were one grey smear, and the bar could not be read as three things.
# Receding is a floor, not a target — spread them across the ramp and keep the
# ordering that says what they are. Unknown recedes furthest (it is the model
# declining to answer); Using-apps is the most present of the three, because
# naming the focused window is at least a fact.
CIRCUMSTANCE = {"light": ("#a8abb3", "#cbcdd3", "#8b8f98"),
                "dark": ("#6e7077", "#44454a", "#8a8d95")}
TAIL = {"light": ("#8b8d93", "#7c7e84", "#9a9ca2", "#6d6f75"),
        "dark": ("#9a9ca2", "#8b8d93", "#a9abb0", "#7c7e84")}


def neutral(index: int, theme: str = "dark", circumstance: bool = False) -> str:
    """A hueless slot. Wraps, and past one lap two categories do share a value.

    That is not a bug to fix with a longer ramp. By the time the wheel and the
    ramp are both spent, colour has genuinely run out of things to say, and the
    label beside every mark has been carrying the identity all along.
    """
    ramp = (CIRCUMSTANCE if circumstance else TAIL)[theme]
    return ramp[index % len(ramp)]


def is_clear(hue: int, taken) -> bool:
    """Would a family at this hue keep MIN_SEPARATION from every existing one?"""
    return all(min((hue - h) % 360, (h - hue) % 360) >= MIN_GAP / 2
               for h in taken)


def next_hue(taken) -> int | None:
    """The midpoint of the largest remaining gap, or None when the wheel is full.

    Deterministic and optimal for one addition, and existing families never
    move — nobody's Coding colour shifts because a stranger added Gardening.

    None is a real answer, not a failure. The wheel decides when it is full, and
    it decides by geometry rather than by a count somebody guessed: a gap
    narrower than MIN_GAP cannot hold a family without its members reading as
    relatives of the families either side. Everything after that goes to the
    neutral ramp, which is the honest outcome — eight to twelve hues is what a
    reader can separate at 8px, and squeezing in a thirteenth does not add a
    category, it subtracts one from the twelve that were working.
    """
    taken = sorted(int(h) % 360 for h in taken)
    if not taken:
        return 0
    if len(taken) == 1:
        return (taken[0] + 180) % 360
    gaps = [(taken[(i + 1) % len(taken)] - h) % 360 for i, h in enumerate(taken)]
    widest = max(range(len(gaps)), key=lambda i: gaps[i])
    if gaps[widest] < MIN_GAP:
        return None
    return int(taken[widest] + gaps[widest] / 2) % 360
