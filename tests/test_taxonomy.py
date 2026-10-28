"""The single source of AI categories: the key is the identity; Chinese and
English are only display.

From the 2026-08-19 open-source refactor. Before it, "sleep / coding / social"
was spread over three places (infer's prompt, inference's defaults, viz's two
colour tables); adding a category meant three files, and missing one showed as
"a grey stretch in the band that is never recognised" — talk really was grey
for a week before anyone noticed.

The rules:

  · the database (inferences.cat / day_events.cat) **stores keys**, not names
  · old data is in Chinese and is normalised on load — no migration, a smarter
    read
  · the prompt's category list is generated per language; the model answers
    with names, normalised to keys on save
  · an unrecognised name is **kept as it is**: a failed normalisation must not
    destroy what the model said, and viz already greys unknown cats
  · users can add their own categories in config.toml ("childcare", say)
"""

import tempfile
import unittest
from unittest import mock

from umbra import config, taxonomy


# taxonomy now reads config.toml itself (before, no caller passed conf, so
# [categories.*] was dead on the real path). The side effect: every assertion
# here describes **the built-in table** and must pin an empty configuration —
# otherwise a developer with categories of their own would have these tests
# following their config, while CI has none.
# The same kind of hole already blew up once, on prompt language, on 2026-08-20.
_EMPTY = None


def setUpModule():
    global _EMPTY
    _EMPTY = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
    _EMPTY.start()
    taxonomy.reload()


def tearDownModule():
    _EMPTY.stop()
    taxonomy.reload()

class TheTableTest(unittest.TestCase):
    def test_keys_are_unique(self):
        keys = [c["key"] for c in taxonomy.table()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_names_are_unique_within_each_language(self):
        """Two keys sharing a Chinese name would make normalisation a coin toss."""
        for lang in ("zh", "en"):
            names = [c[lang] for c in taxonomy.table()]
            self.assertEqual(len(names), len(set(names)), lang)

    def test_every_category_has_a_colour(self):
        for c in taxonomy.table():
            self.assertRegex(c["color"], r"^#[0-9a-f]{6}$", c["key"])

    def test_every_chinese_category_ever_stored_is_recognised(self):
        seen = ["不确定", "其他", "写代码", "吃饭", "在听", "在路上", "娱乐",
                "家务", "查资料", "浏览", "睡觉", "社交", "说话", "读书", "运动"]
        for name in seen:
            self.assertIsNotNone(taxonomy.key_of(name), name)


class NormalisationTest(unittest.TestCase):
    def test_chinese_name_to_key(self):
        self.assertEqual(taxonomy.key_of("睡觉"), "sleep")
        self.assertEqual(taxonomy.key_of("写代码"), "code")

    def test_the_key_itself_is_recognised(self):
        self.assertEqual(taxonomy.key_of("sleep"), "sleep")

    def test_english_names_ignore_case(self):
        self.assertEqual(taxonomy.key_of("Sleep"), "sleep")
        self.assertEqual(taxonomy.key_of("CODING"), "code")

    def test_unrecognised_returns_none(self):
        self.assertIsNone(taxonomy.key_of("发呆"))

    def test_norm_keeps_the_unrecognised_as_it_is(self):
        """A failed normalisation does not destroy what the model said — viz
        greys unknown cats, and turning it into unknown would disguise "the model
        used a new word" as "the model was unsure"."""
        self.assertEqual(taxonomy.norm("发呆"), "发呆")
        self.assertEqual(taxonomy.norm("睡觉"), "sleep")


class DisplayTest(unittest.TestCase):
    def test_names_by_language(self):
        self.assertEqual(taxonomy.label("sleep", "zh"), "睡觉")
        self.assertEqual(taxonomy.label("sleep", "en"), "Sleep")

    def test_an_unrecognised_key_is_returned_as_is(self):
        self.assertEqual(taxonomy.label("发呆", "zh"), "发呆")

    def test_every_category_has_a_distinct_colour(self):
        """Pin the property, not the values. The palette is **computed** (see
        the notes above _TABLE), and a recomputation changes every value;
        pinning a hex would only block improvements to the palette — and what
        it blocked would not be an error."""
        c = taxonomy.colors()
        for k in ("sleep", "code", "meal", "other"):
            self.assertRegex(c[k], r"^#[0-9a-f]{6}$", k)
        chromatic = [v for k, v in c.items() if k not in ("other", "unknown")]
        self.assertEqual(len(chromatic), len(set(chromatic)),
                         "two categories share a colour")

    def test_talking_finally_has_a_colour(self):
        """AICOL once missed "talk" and it was grey on the page for a week. A
        generated table cannot miss one."""
        self.assertIn("talk", taxonomy.colors())
        self.assertNotEqual(taxonomy.colors()["talk"], "#4b5563")


class PromptCategoryListTest(unittest.TestCase):
    def test_chinese_list(self):
        cats = taxonomy.prompt_cats("zh")
        self.assertIn("睡觉", cats)
        self.assertIn("不确定", cats)

    def test_english_list(self):
        cats = taxonomy.prompt_cats("en")
        self.assertIn("Sleep", cats)

    def test_legacy_categories_are_not_in_the_prompt(self):
        """"At a place / using apps" were early categories, later withdrawn — old
        data must be recognised,
        but a new prompt must not teach the model to use them."""
        self.assertNotIn("在某地", taxonomy.prompt_cats("zh"))
        self.assertIsNotNone(taxonomy.key_of("在某地"))


class UserExtensionTest(unittest.TestCase):
    CONF = {"categories": {"childcare": {
        "zh": "带娃", "en": "Childcare", "color": "#f0abfc"}}}

    def test_categories_added_in_config_join_the_table(self):
        t = taxonomy.table(self.CONF)
        self.assertIn("childcare", [c["key"] for c in t])

    def test_added_categories_reach_the_prompt_and_normalisation(self):
        self.assertIn("带娃", taxonomy.prompt_cats("zh", self.CONF))
        self.assertEqual(taxonomy.key_of("带娃", self.CONF), "childcare")


if __name__ == "__main__":
    unittest.main()
