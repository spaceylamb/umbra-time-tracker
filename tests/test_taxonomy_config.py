"""The category table and personal rules: replaceable whole, and really on the
running path.

Done together because they exposed the same hole:

  1. `[categories.*]` used to be **dead on the real path** — viz passed conf (so
     page colours matched), but infer and inference never did, so the model
     was never told about custom categories and its answers never normalised
     to them.
     Tests that passed conf by hand were green; in use it did nothing.
  2. The 17 built-ins could be added to but not replaced. "Separate work from
     creative" needs a different set, not one more category.

Replacing affects only **the table given to the model** and **display**, not
**what is recognised**: keys stored for months (code / browse / research) must
still read and display after a replacement — otherwise one config change greys
out history.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import config, db, infer, taxonomy

MINE = """
[taxonomy]
replace = true

[categories.work]
zh = "工作"
en = "Work"

[categories.create]
zh = "创造"
en = "Creating"

[categories.sleep]
zh = "睡觉"
en = "Sleep"

[categories.chore]
zh = "个人事务"
en = "Personal admin"

[[rules]]
signal = "飞书 / Lark / Shopify"
means  = "cat = work"

[[rules]]
signal = "在邮件里读科技资讯"
means  = "cat = news"
"""


def with_conf(text):
    """Put a config.toml under UMBRA_CONFIG_DIR and clear caches.

    ⚠️ A test pinning CONFIG_DIR **must pin DB_DIR too**. On the real path (no
    explicit conf) the palette writes new categories' assignments into the data
    directory — unpinned, this file's work / create would land in the
    developer's real palette.json.
    """
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    return _Both(mock.patch.object(config, "CONFIG_DIR", d),
                 mock.patch.object(db, "DB_DIR", tempfile.mkdtemp()))


class _Both:
    """Two patches used as one, so call sites keep the start()/stop() shape."""

    def __init__(self, *patches):
        self._patches = patches

    def start(self):
        for p in self._patches:
            p.start()

    def stop(self):
        for p in self._patches:
            p.stop()


class ConfigReachesTheRunningPathTest(unittest.TestCase):
    def setUp(self):
        self.p = with_conf(MINE); self.p.start()
        self.addCleanup(self.p.stop)
        taxonomy.reload()
        self.addCleanup(taxonomy.reload)

    def test_my_categories_are_in_the_prompt_without_passing_conf(self):
        """This used to be False — configured, and the model never knew."""
        self.assertIn("创造", taxonomy.prompt_cats("zh"))
        self.assertIn("创造", infer.cats("zh"))

    def test_after_replace_the_built_ins_are_not_offered_to_the_model(self):
        cats = taxonomy.prompt_cats("zh")
        self.assertNotIn("浏览", cats)
        self.assertNotIn("查资料", cats)

    def test_old_data_is_still_recognised(self):
        """Replacing must not grey out history: months of stored code / browse."""
        self.assertEqual(taxonomy.norm("code"), "code")
        self.assertEqual(taxonomy.norm("写代码"), "code")
        self.assertEqual(taxonomy.norm("browse"), "browse")

    def test_old_categories_keep_their_names_and_colours(self):
        self.assertEqual(taxonomy.label("browse", "zh"), "浏览")
        self.assertTrue(taxonomy.colors().get("browse"))

    def test_my_categories_normalise(self):
        self.assertEqual(taxonomy.norm("创造"), "create")
        self.assertEqual(taxonomy.norm("Creating"), "create")


class NothingChangesWithoutConfigTest(unittest.TestCase):
    def setUp(self):
        self.p = with_conf(""); self.p.start()
        self.addCleanup(self.p.stop)
        taxonomy.reload()
        self.addCleanup(taxonomy.reload)

    def test_the_built_in_table_is_unchanged(self):
        self.assertIn("写代码", taxonomy.prompt_cats("zh"))
        self.assertIn("浏览", taxonomy.prompt_cats("zh"))

    def test_without_rules_the_prompt_is_unchanged_word_for_word(self):
        """By default there are no personal rules — the section must not appear
        at all."""
        self.assertEqual(taxonomy.prompt_rules("zh"), "")
        out = infer.prompt_tpl().format(
            a="09:00", b="09:30", wa="08:00", wb="10:30",
            cats="x", rules=taxonomy.prompt_rules("zh"), text="t")
        self.assertNotIn("个人规则", out)
        self.assertNotIn("Personal rules", out)


class PersonalRulesInThePromptTest(unittest.TestCase):
    def setUp(self):
        self.p = with_conf(MINE); self.p.start()
        self.addCleanup(self.p.stop)
        taxonomy.reload()
        self.addCleanup(taxonomy.reload)

    def test_rules_appear_in_the_rendered_prompt(self):
        block = taxonomy.prompt_rules("zh")
        self.assertIn("飞书 / Lark / Shopify", block)
        self.assertIn("cat = work", block)

    def test_both_languages_have_the_section(self):
        self.assertTrue(taxonomy.prompt_rules("zh"))
        self.assertTrue(taxonomy.prompt_rules("en"))

    def test_the_template_leaves_room_for_it(self):
        for tpl in (infer.PROMPT_ZH, infer.PROMPT_EN):
            self.assertIn("{rules}", tpl)


if __name__ == "__main__":
    unittest.main()
