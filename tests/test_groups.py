"""Headline groups: sums over categories, not a fourteenth category.

Productivity = work + create + reading + news. What matters is that it is
**derived**: the model judges categories only, and groups are arithmetic. So

  · changing a group needs no re-inference, and history follows at once
  · no drift — a sum is never right one day and wrong the next
  · without configuration this layer does not exist and the page shows nothing

Sleep uses the same mechanism (a group containing only sleep), with no special
case: two numbers that look alike are computed alike, and nobody has to
remember which is a category and which a group.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import config, taxonomy

CONF = """
[categories.work]
zh = "工作"

[categories.create]
zh = "创造"

[categories.sleep]
zh = "睡觉"

[groups.productivity]
zh = "Productivity"
cats = ["work", "create", "read", "news"]

[groups.sleep]
zh = "睡眠"
cats = ["sleep"]
"""


def with_conf(text):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    return mock.patch.object(config, "CONFIG_DIR", d)


class ReadConfigTest(unittest.TestCase):
    def setUp(self):
        self.p = with_conf(CONF); self.p.start()
        self.addCleanup(self.p.stop)
        taxonomy.reload(); self.addCleanup(taxonomy.reload)

    def test_groups_and_their_members_are_listed(self):
        g = taxonomy.groups()
        self.assertEqual(g["productivity"]["cats"],
                         ["work", "create", "read", "news"])
        self.assertEqual(g["sleep"]["cats"], ["sleep"])

    def test_groups_have_display_names(self):
        self.assertEqual(taxonomy.groups()["productivity"]["zh"], "Productivity")

    def test_config_order_is_kept(self):
        """Productivity before sleep on purpose — it is the one you want first."""
        self.assertEqual(list(taxonomy.groups()), ["productivity", "sleep"])

    def test_members_may_be_categories_not_yet_defined(self):
        """read / news have no [categories.*] here and still count — a group
        refers to keys and does not need the category defined in the same file."""
        self.assertIn("news", taxonomy.groups()["productivity"]["cats"])


class NoConfigNoGroupsTest(unittest.TestCase):
    def setUp(self):
        self.p = with_conf(""); self.p.start()
        self.addCleanup(self.p.stop)
        taxonomy.reload(); self.addCleanup(taxonomy.reload)

    def test_no_groups_by_default(self):
        """Without configuration the row does not appear at all."""
        self.assertEqual(taxonomy.groups(), {})




if __name__ == "__main__":
    unittest.main()
