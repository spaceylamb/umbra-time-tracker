"""Settings writes config.toml line by line, and never loses a comment.

The file is hand-annotated and often lives in a dotfiles repo; round-tripping
it through a TOML writer would drop every comment the first time someone
changed the language from the page.
"""

import os
import tempfile
import tomllib
import unittest
from unittest import mock

from umbra import config, configedit as E

ORIGINAL = """\
# my umbra config — keep this comment
[ui]
lang = "en"   # was zh until 2026-08

# how often
[schedule]
interval_min = 360

[groups.productivity]
en = "Productivity"
cats = ["code", "read"]
"""


class EditTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        p = mock.patch.object(config, "CONFIG_DIR", self.dir)
        p.start(); self.addCleanup(p.stop)
        self.write(ORIGINAL)

    def write(self, text):
        with open(E.path(), "w") as f:
            f.write(text)

    def read(self):
        with open(E.path()) as f:
            return f.read()

    def test_changes_only_the_value(self):
        E.update({"ui.lang": "zh"})
        out = self.read()
        self.assertIn("# my umbra config — keep this comment", out)
        self.assertIn("# how often", out)
        self.assertIn('lang = "zh"', out)
        self.assertEqual(out.count("\n"), ORIGINAL.count("\n"))
        # Everything else is byte-for-byte what it was.
        self.assertEqual(out.replace('lang = "zh"\n', ""),
                         ORIGINAL.replace('lang = "en"   # was zh until 2026-08\n', ""))

    def test_adds_a_key_to_an_existing_table(self):
        E.update({"ui.theme": "dark"})
        conf = tomllib.loads(self.read())
        self.assertEqual(conf["ui"], {"lang": "en", "theme": "dark"})

    def test_appends_a_missing_table(self):
        E.update({"sync.infer_days": 3})
        self.assertEqual(tomllib.loads(self.read())["sync"], {"infer_days": 3})
        self.assertTrue(self.read().startswith(ORIGINAL))

    def test_none_removes_the_key(self):
        E.update({"schedule.interval_min": None})
        self.assertEqual(tomllib.loads(self.read())["schedule"], {})

    def test_no_file_yet(self):
        os.remove(E.path())
        E.update({"ui.lang": "zh"})
        self.assertEqual(tomllib.loads(self.read()), {"ui": {"lang": "zh"}})

    def test_structures_are_not_editable(self):
        with self.assertRaises(E.Refused):
            E.update({"groups.productivity.cats": ["code"]})
        self.assertEqual(self.read(), ORIGINAL)

    def test_values_are_checked(self):
        for bad in ({"ui.lang": "fr"}, {"sync.infer_days": 99},
                    {"sync.infer_days": True}, {"schedule.interval_min": 5}):
            with self.assertRaises(E.Refused):
                E.update(bad)
        self.assertEqual(self.read(), ORIGINAL)

    def test_plugin_switches(self):
        E.update({"plugins.actions.send": True})
        self.assertEqual(tomllib.loads(self.read())["plugins"]["actions"], {"send": True})
        with self.assertRaises(E.Refused):
            E.update({"plugins.actions.send": "yes"})

    def test_a_shape_it_does_not_understand_is_left_alone(self):
        """ui written as an inline table: a line editor would append a second
        [ui] table and break the file. It must refuse instead."""
        odd = 'ui = { lang = "en" }\n'
        self.write(odd)
        with self.assertRaises(E.Refused):
            E.update({"ui.lang": "zh"})
        self.assertEqual(self.read(), odd)

    def test_all_or_nothing(self):
        with self.assertRaises(E.Refused):
            E.update({"ui.lang": "zh", "sync.infer_days": 0})
        self.assertEqual(self.read(), ORIGINAL)


if __name__ == "__main__":
    unittest.main()
