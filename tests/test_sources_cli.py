"""Which sources import runs is now discover()'s decision, and wrong-platform
sources stand aside on their own.

Before, cmd_import walked a hard-coded ALL: sources in your own directory never
ran, and on Linux the four Biome sources reported "no such file" every round —
the wrong environment shown as a fault.
"""

import sys
import unittest
from unittest import mock

from umbra import __main__ as cli, sources

# "Wrong platform" must be relative to the current platform — hard-coding
# "linux" made it the right platform on CI's Ubuntu runner, and three tests
# flipped (seen on the first run).
OTHER_PLATFORM = "linux" if sys.platform == "darwin" else "darwin"


class ImportRosterTest(unittest.TestCase):
    def test_the_list_comes_from_discover_not_a_hard_coded_one(self):
        mod = mock.Mock(spec=["collect"])
        mod.collect.return_value = []
        # roster() is the one place cmd_import gets its list
        with mock.patch.object(sources, "discover",
                               return_value=({"我的源": mod}, {})):
            names, _ = cli.roster(only=None)
        self.assertEqual(names, ["我的源"])

    def test_a_wrong_platform_source_is_not_in_the_list(self):
        mod = mock.Mock(spec=["collect", "PLATFORM"])
        mod.PLATFORM = OTHER_PLATFORM
        with mock.patch.object(sources, "discover",
                               return_value=({"外地源": mod}, {})):
            names, skipped = cli.roster(only=None)
        self.assertNotIn("外地源", names)
        self.assertIn("外地源", dict(skipped))

    def test_a_wrong_platform_source_named_with_only_is_reported_not_skipped(self):
        """--only is a person asking; skipping silently reads as "ran, but no
        data"."""
        mod = mock.Mock(spec=["collect", "PLATFORM"])
        mod.PLATFORM = OTHER_PLATFORM
        with mock.patch.object(sources, "discover",
                               return_value=({"外地源": mod}, {})):
            names, skipped = cli.roster(only=["外地源"])
        self.assertEqual(names, [])
        self.assertIn("外地源", dict(skipped))

    def test_a_broken_user_source_is_visible_in_the_result(self):
        with mock.patch.object(sources, "discover",
                               return_value=({}, {"坏源": "SyntaxError: x"})):
            _, skipped = cli.roster(only=None)
        self.assertIn("坏源", dict(skipped))


if __name__ == "__main__":
    unittest.main()
