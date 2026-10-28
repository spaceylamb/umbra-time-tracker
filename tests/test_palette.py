"""The palette is **computed**, not written down.

taxonomy._TABLE used to hold seventeen hand-picked hexes, and categories users
added in config.toml all fell to the same grey fallback — that is, "a
user-defined category is not a category on the page", the very bug taxonomy
was created to end, back through another door.

This file pins **properties** only, never values:

  · every mark weighs the same within a theme — none darker, duller or louder
  · the two themes are two lightnesses of one hue, never drifting apart
  · members of different families differ by at least MIN_SEPARATION, or
    strangers read as relatives
  · when the wheel is full is decided by geometry, not by a number someone
    picked
  · "unknown" must recede further than real categories — an answer must not
    look like no answer

Pinning a hex would only block improvements to the palette — and what it
blocked would not be an error.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from umbra import config, palette, taxonomy

_PATCHES: list = []


def setUpModule():
    """The same trap as test_taxonomy: taxonomy reads config.toml itself, and a
    developer with categories of their own would have these assertions follow
    their config.

    ⚠️ DB_DIR must be pinned too, and for **the whole module**, not one class.
    Palette assignments are written to disk, so the unpinned classes wrote
    fixtures like c0…c7 / childcare / g into the developer's real palette.json —
    read back on the next run, so assertions followed the previous run's
    leftovers. A test polluting the real data directory is worse than the
    failures it causes.
    """
    from umbra import db
    for target, attr in ((config, "CONFIG_DIR"), (db, "DB_DIR")):
        p = mock.patch.object(target, attr, tempfile.mkdtemp())
        p.start()
        _PATCHES.append(p)
    taxonomy.reload()


def tearDownModule():
    for p in _PATCHES:
        p.stop()
    _PATCHES.clear()
    taxonomy.reload()


def _luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
               for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _chromatic(theme):
    return {k: v for k, v in taxonomy.colors(theme=theme).items()
            if k not in taxonomy._OFF_WHEEL}


class ConversionTest(unittest.TestCase):
    def test_output_is_six_lowercase_hex_digits(self):
        for hue in range(0, 360, 17):
            self.assertRegex(palette.color(hue, 0), r"^#[0-9a-f]{6}$")

    def test_nothing_escapes_the_gamut(self):
        """Out of range, _gamma clamps to 00 or ff, and the hue is no longer the
        one asked for."""
        for theme in ("light", "dark"):
            for hue in range(0, 360, 5):
                for step in (0, 1):
                    c = palette.color(hue, step, theme)
                    self.assertNotIn(c, ("#000000", "#ffffff"))

    def test_a_hex_reads_back_as_a_hue(self):
        for hue in (25, 118, 236, 325):
            got = palette.hue_of(palette.color(hue, 1))
            self.assertLess(min((got - hue) % 360, (hue - got) % 360), 12, hue)

    def test_grey_carries_no_hue_request(self):
        """The fallback grey must not be read as "I'd like blue"."""
        for grey in ("#7a8494", "#ffffff", "#808080", "not a colour"):
            self.assertIsNone(palette.hue_of(grey), grey)


class EqualWeightWithinAThemeTest(unittest.TestCase):
    """C is capped and L held to a window, so no category can be darker,
    duller or louder than another. The area rule is then not something to
    re-decide every time a category is added."""

    def test_the_lightness_window_is_bounded(self):
        """What is pinned is **OKLab L**, not relative luminance.

        This used to pin a relative-luminance ratio < 2.0, the wrong ruler: Y
        weighs yellow-greens hugely (at one L, #b1bc21 has 2.7× the Y of
        #cf3869), so "equal weight" was judged in a space this system never
        designs in. palette.py's creed is "equal L + equal gamut fraction",
        meaning OKLab L —
        so that is what is pinned.

        The window is not zero because BANDS deliberately move two hue ranges
        off the shared stop. Its ends come from LIGHTNESS and BANDS; this only
        makes sure nobody quietly widens it."""
        for theme in ("light", "dark"):
            ls = [palette._oklab(v)[0] for v in _chromatic(theme).values()]
            self.assertLess(max(ls) - min(ls), 0.24, theme)

    def test_nothing_is_darker_than_body_text(self):
        """The floor is body text: data darker than the words would compete for
        the same level."""
        for theme in ("light", "dark"):
            for key, value in _chromatic(theme).items():
                self.assertGreater(palette._oklab(value)[0], 0.531,
                                   f"{theme} {key}")

    def test_nothing_is_mud(self):
        """55–130° turns brown at the lower stop — Coding's #d37600 and Meal's
        #975800 came from exactly that. The exception has to really apply."""
        for theme in ("light", "dark"):
            for key, value in _chromatic(theme).items():
                self.assertGreater(_luminance(value), 0.15, f"{theme} {key}")


class ThemesDoNotDriftTest(unittest.TestCase):
    def test_a_category_has_the_same_hue_in_both_themes(self):
        light, dark = _chromatic("light"), _chromatic("dark")
        for key in light:
            a, b = palette.hue_of(light[key]), palette.hue_of(dark[key])
            self.assertIsNotNone(a, key)
            self.assertLess(min((a - b) % 360, (b - a) % 360), 12, key)

    def test_both_themes_have_the_same_categories(self):
        self.assertEqual(set(taxonomy.colors(theme="light")),
                         set(taxonomy.colors(theme="dark")))


class SeparationTest(unittest.TestCase):
    def test_built_in_categories_are_all_distinct(self):
        for theme in ("light", "dark"):
            values = list(taxonomy.colors(theme=theme).values())
            self.assertEqual(len(values), len(set(values)), theme)

    def test_different_families_have_an_absolute_floor(self):
        """**The one that really matters**: can two unrelated categories be told
        apart.

        This used to pin only a ratio (strangers > 2× relatives), not an absolute
        value — and a ratio can be pumped up by making relatives more alike,
        which makes no pair easier to read. The absolute floor follows
        MIN_SEPARATION: 8° of hue at this system's L/C is about 0.027, and the
        floor is a little over twice that.

        **Across families** only: two members of one family being close is the
        design, not a defect — by the time you need to tell them apart the label
        is right there (transit/meal are 0.046 apart)."""
        import itertools
        family = {c["key"]: c["family"] for c in taxonomy.known()}
        for theme in ("light", "dark"):
            cols = _chromatic(theme)
            worst = min((palette.distance(cols[a], cols[b]), a, b)
                        for a, b in itertools.combinations(cols, 2)
                        if family[a] != family[b])
            self.assertGreater(worst[0], 0.06, f"{theme}: {worst[1]}/{worst[2]}")

    # A test_strangers_twice_as_far_as_relatives (strangers > 2× relatives)
    # used to live here.
    #
    # **Removed, not loosened.** It went down to 1.5×, then 1.14, because it
    # dropped each time a colour was fixed — and every drop was for a good
    # reason: BANDS gave two hue ranges usable lightness, and once the warm band
    # got its stop back Transit/Meal went from 0.046 to 0.109. A threshold that
    # has to come down each time the palette improves is not a constraint but a
    # ratchet the wrong way: kept, it would be lowered again at the next
    # improvement while pretending to guard something.
    #
    # What it meant to guard did not hold when measured either: four of nine
    # families have one member, and of the rest upkeep's two members are 0.092
    # apart, further than the strangers chore and research (0.078). Families do
    # no work now — what the two stops really earn is **the area rule** (the
    # larger one takes the quieter stop), which does not depend on kinship.
    #
    # What really needs guarding is the test below: any two categories from
    # different families are absolutely distinguishable.

    def test_every_family_s_free_slot_keeps_the_floor_too(self):
        """A free slot is where `family = "..."` in config.toml lands.

        Built-ins being distinct is not enough: a family's free slot sitting
        against another family's category means a user following the docs gets
        that category's twin. Measured before moving Coding to 263°: at 257°
        screen's slot (260°) was 0.061 from it and build's slot 0.062 from Fun,
        both grazing the line; at 263°, with the dark band's second stop pulled
        apart, the worst slot is 0.067."""
        builtin = [c for c in taxonomy.known() if c["hue"] is not None]
        taken = {(c["family"], c["step"]) for c in builtin}
        for theme in ("light", "dark"):
            cols = {c["key"]: c["color"] for c in builtin}
            cols = {k: taxonomy.colors(theme=theme)[k] for k in cols}
            family = {c["key"]: c["family"] for c in builtin}
            for fam, hue in taxonomy._FAMILIES.items():
                for step in (0, 1):
                    if (fam, step) in taken:
                        continue
                    slot = palette.color(hue, step, theme)
                    worst = min((palette.distance(slot, cols[k]), k)
                                for k in cols if family[k] != fam)
                    self.assertGreater(worst[0], 0.06,
                                       f"{theme} {fam}/{step} vs {worst[1]}")

    def test_members_of_different_families_never_get_as_close_as_relatives(self):
        """Two stops within a family differing by STEP_DEGREES is **on purpose** —
        workout and chores should look related. Across families the same
        distance is a bug: it claims a kinship that does not exist."""
        placed = []
        for c in taxonomy.known():
            if c["hue"] is None:
                continue
            shift = palette.STEP_DEGREES * (1 if c["step"] else -1)
            placed.append(((c["hue"] + shift) % 360, c["family"], c["key"]))
        for hue, family, key in placed:
            for other, other_family, other_key in placed:
                if family == other_family:
                    continue
                gap = min((hue - other) % 360, (other - hue) % 360)
                self.assertGreaterEqual(
                    gap, palette.MIN_SEPARATION, f"{key} vs {other_key}")


class TheWheelSaysWhenItIsFullTest(unittest.TestCase):
    def test_an_empty_wheel_starts_at_zero(self):
        self.assertEqual(palette.next_hue([]), 0)

    def test_it_lands_mid_largest_gap(self):
        self.assertEqual(palette.next_hue([0, 90, 180]), 270)
        self.assertEqual(palette.next_hue([0, 10, 350]), 180)

    def test_existing_families_never_move(self):
        """Someone adding "gardening" must not move anyone's Coding colour."""
        taken = [25, 118, 236]
        before = [palette.color(h, 0) for h in taken]
        taken.append(palette.next_hue(taken))
        self.assertEqual([palette.color(h, 0) for h in taken[:3]], before)

    def test_too_full_says_none(self):
        """Full or not is geometry, not a cap someone picked."""
        crowded = list(range(0, 360, palette.MIN_GAP // 2))
        self.assertIsNone(palette.next_hue(crowded))

    def test_room_remains_after_the_built_in_families(self):
        """With no room at all, "define your own categories" would not exist."""
        hues = list(taxonomy._FAMILIES.values())
        added = 0
        while (h := palette.next_hue(hues)) is not None:
            hues.append(h)
            added += 1
        self.assertGreaterEqual(added, 2)


class UserCategoriesTest(unittest.TestCase):
    """These pin the real bug: a category added in config is grey on the page."""

    PLAIN = {"categories": {"childcare": {"zh": "带娃", "en": "Childcare"}}}

    def setUp(self):
        """A clean data directory per test. Palette assignments are written to
        disk (that is their point), and a module-wide directory would be filled
        by earlier tests until childcare found no room and fell back to grey —
        assertions following test order."""
        from umbra import db
        self.patches = [mock.patch.object(db, "DB_DIR", tempfile.mkdtemp()),
                        mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())]
        for p in self.patches:
            p.start()
        taxonomy.reload()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        taxonomy.reload()

    def test_writing_nothing_still_gets_a_colour(self):
        color = taxonomy.colors(self.PLAIN)["childcare"]
        self.assertNotIn(color, palette.CIRCUMSTANCE["dark"])
        self.assertIsNotNone(palette.hue_of(color))

    def test_it_does_not_collide_with_a_built_in(self):
        values = list(taxonomy.colors(self.PLAIN).values())
        self.assertEqual(len(values), len(set(values)))

    def test_it_can_join_a_named_family(self):
        conf = {"categories": {"errand2": {"zh": "跑腿", "family": "ambient"}}}
        cat = [c for c in taxonomy.known(conf) if c["key"] == "errand2"][0]
        self.assertEqual(cat["hue"], taxonomy._FAMILIES["ambient"])

    def test_color_in_config_is_a_hue_request_not_a_pixel(self):
        conf = {"categories": {"g": {"zh": "园艺", "color": "#f0abfc"}}}
        got = taxonomy.colors(conf)["g"]
        self.assertNotEqual(got, "#f0abfc")

    def test_a_hue_request_too_close_is_declined(self):
        """#f0abfc lies within three degrees of the input family. Granting it
        would make two categories nearly identical, and the user only wanted
        "pink", not that exact pixel."""
        conf = {"categories": {"g": {"zh": "园艺", "color": "#f0abfc"}}}
        cats = {c["key"]: c for c in taxonomy.known(conf)}
        self.assertNotEqual(cats["g"]["hue"], taxonomy._FAMILIES["input"])


class OverflowTest(unittest.TestCase):
    MANY = {"categories": {f"c{i}": {"zh": f"类{i}"} for i in range(8)}}

    def test_a_full_wheel_goes_neutral_but_does_not_vanish(self):
        cats = {c["key"]: c for c in taxonomy.known(self.MANY)}
        tail = [k for k, c in cats.items() if c["hue"] is None
                and k not in taxonomy._OFF_WHEEL]
        self.assertTrue(tail, "some of eight new categories should overflow")
        for key in tail:
            self.assertRegex(cats[key]["color"], r"^#[0-9a-f]{6}$")

    def test_overflow_must_not_look_like_unknown(self):
        """An answer must not look like no answer — principle zero, not a colour
        preference."""
        for theme in ("light", "dark"):
            cols = taxonomy.colors(self.MANY, theme)
            reserved = set(palette.CIRCUMSTANCE[theme])
            for key, value in cols.items():
                if key in taxonomy._OFF_WHEEL:
                    self.assertIn(value, reserved, f"{theme} {key}")
                else:
                    self.assertNotIn(value, reserved, f"{theme} {key}")

    def test_unknown_recedes_more_than_real_categories(self):
        for theme, further in (("light", False), ("dark", True)):
            cols = taxonomy.colors(self.MANY, theme)
            unknown = _luminance(cols["unknown"])
            real = [_luminance(v) for k, v in cols.items()
                    if k not in taxonomy._OFF_WHEEL]
            if further:
                self.assertLess(unknown, min(real), theme)
            else:
                self.assertGreater(unknown, max(real), theme)


class OnceWrittenNeverChangesTest(unittest.TestCase):
    """"The colour learned on Tuesday is still it on Friday" — or colour is
    worth nothing here.

    "Mid largest gap" is stable only while the gaps are. Recomputed at every
    start, removing a category would quietly recolour everything declared
    after it, wiping months of a person's memory at once.
    """

    def setUp(self):
        from umbra import db
        self.dir = tempfile.mkdtemp()          # a fresh one per test, over the module's
        self.cfg = tempfile.mkdtemp()
        self.patches = [mock.patch.object(db, "DB_DIR", self.dir),
                        mock.patch.object(config, "CONFIG_DIR", self.cfg)]
        for p in self.patches:
            p.start()
        taxonomy.reload()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        taxonomy.reload()

    def _conf(self, *keys):
        """Write a real config.toml and read it back through the real path.

        Passing a dict in would not do: a hypothetical config does not persist,
        by design, and persistence is the whole subject of this class.
        """
        body = "".join(f'[categories.{k}]\nzh = "{k}"\n' for k in keys)
        with open(os.path.join(self.cfg, "config.toml"), "w", encoding="utf-8") as f:
            f.write(body)
        taxonomy.reload()
        return None

    def test_written_the_first_time_it_is_seen(self):
        taxonomy.colors(self._conf("a", "b"))
        with open(os.path.join(self.dir, "palette.json"), encoding="utf-8") as f:
            rows = json.load(f)
        self.assertEqual(set(rows), {"a", "b"})
        self.assertIsNotNone(rows["a"]["hue"])

    def test_read_back_the_same_after_a_restart(self):
        first = taxonomy.colors(self._conf("a", "b"))
        taxonomy.reload()                       # a simulated restart
        self.assertEqual(taxonomy.colors(self._conf("a", "b")), first)

    def test_removing_an_earlier_one_does_not_recolour_later_ones(self):
        """The whole reason to persist."""
        before = taxonomy.colors(self._conf("a", "b", "c"))["c"]
        taxonomy.reload()
        self.assertEqual(taxonomy.colors(self._conf("b", "c"))["c"], before)

    def test_a_removed_category_keeps_its_place(self):
        """Old data is still in the database and keeps showing in its colour —
        the reason known() exists. So entries are never cleaned up, and a
        newcomer never takes its hue."""
        gone = taxonomy.colors(self._conf("a"))["a"]
        taxonomy.reload()
        taxonomy.colors(self._conf("b"))         # a removed from config
        taxonomy.reload()
        self.assertEqual(taxonomy.colors(self._conf("a"))["a"], gone)
        self.assertNotEqual(taxonomy.colors(self._conf("b"))["b"], gone)

    def test_a_corrupt_file_still_opens(self):
        """What is lost is this round's stability, not "Umbra won't open"."""
        with open(os.path.join(self.dir, "palette.json"), "w") as f:
            f.write("{not json")
        taxonomy.reload()
        self.assertRegex(taxonomy.colors(self._conf("a"))["a"], r"^#[0-9a-f]{6}$")

    def test_an_unwritable_directory_does_not_blow_up(self):
        import os as _os
        _os.chmod(self.dir, 0o500)
        try:
            self.assertRegex(taxonomy.colors(self._conf("a"))["a"],
                             r"^#[0-9a-f]{6}$")
        finally:
            _os.chmod(self.dir, 0o700)


if __name__ == "__main__":
    unittest.main()
