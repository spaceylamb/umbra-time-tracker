"""Bundled plugins load through the path a community plugin uses.

That sameness is the whole reason they ship inside the package rather than in an
examples/ directory at the repo root. If the loader cannot load its own three, it
cannot load anyone's — and nothing else in the system would reveal that, because
a bundled plugin wired up by hand would work perfectly while the community path
stayed broken.

The second thing pinned here is that being installed is not being enabled, and
being enabled is not being allowed to send. Three states, because installing
Umbra must not start writing into somebody's inbox before they have seen a
single result.
"""

import os
import tempfile
import textwrap
import unittest
from unittest import mock

from umbra import config, plugins, registry, sink

ANALYZER = textwrap.dedent('''
    INPUT  = "events:zsh"
    WINDOW = 30
    PROMPT = "{text}"

    def parse(items, window):
        return list(items)
''')


class _Base(unittest.TestCase):
    def setUp(self):
        self.user = tempfile.mkdtemp()
        self.bundled = tempfile.mkdtemp()
        self.patches = [
            mock.patch.object(config, "PLUGINS_DIR", self.user),
            mock.patch.object(plugins, "DIR", self.bundled),
        ]
        for p in self.patches:
            p.start()
        registry.reload()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        registry.reload()

    def write(self, where, name, body):
        with open(os.path.join(where, f"{name}.py"), "w") as f:
            f.write(body)
        registry.reload()


class SearchPathTest(unittest.TestCase):
    def test_bundled_comes_before_the_user_directory(self):
        """Order is precedence: later wins, so the user's own file is last."""
        dirs = [d for d, _bundled in registry._plugin_dirs()]
        self.assertEqual(dirs[0], plugins.DIR)
        self.assertEqual(dirs[-1], config.PLUGINS_DIR)

    def test_the_bundled_directory_is_inside_the_package(self):
        """Inside the package so it ships with a wheel. umbra.plugins is a
        package rather than a data directory, which is what makes
        `packages.find` include it without a package-data entry."""
        import umbra
        pkg = os.path.dirname(os.path.abspath(umbra.__file__))
        self.assertEqual(os.path.commonpath([pkg, plugins.DIR]), pkg)
        self.assertTrue(os.path.exists(os.path.join(plugins.DIR, "__init__.py")))


class DiscoveryTest(_Base):
    def test_a_bundled_plugin_is_found_with_no_user_configuration(self):
        self.write(self.bundled, "demo", ANALYZER)
        self.assertIn("demo", registry.analyzers())

    def test_a_user_plugin_is_found(self):
        self.write(self.user, "mine", ANALYZER)
        self.assertIn("mine", registry.analyzers())

    def test_a_user_plugin_overrides_a_bundled_one_of_the_same_name(self):
        """Whoever wrote the file in their own directory meant it — the same
        rule a user source follows over a built-in."""
        self.write(self.bundled, "overridden", ANALYZER + "\nORIGIN = 'bundled'\n")
        self.write(self.user, "overridden", ANALYZER + "\nORIGIN = 'user'\n")
        self.assertEqual(registry.analyzers()["overridden"].ORIGIN, "user")

    def test_both_directories_contribute(self):
        self.write(self.bundled, "one", ANALYZER)
        self.write(self.user, "two", ANALYZER)
        self.assertEqual(set(registry.analyzers()), {"one", "two"})

    def test_a_missing_user_directory_is_not_an_error(self):
        """Most people never create one."""
        self.write(self.bundled, "demo", ANALYZER)
        with mock.patch.object(config, "PLUGINS_DIR", "/nonexistent/path"):
            registry.reload()
            self.assertIn("demo", registry.analyzers())

    def test_a_broken_bundled_plugin_does_not_hide_the_others(self):
        self.write(self.bundled, "broken", "this is not valid python (\n")
        self.write(self.bundled, "demo", ANALYZER)
        self.assertIn("demo", registry.analyzers())
        self.assertIn("broken", registry.plugins()[1])


class EnablementTest(_Base):
    """Installed, enabled, and allowed to send are three different states."""

    def test_a_plugin_that_says_so_is_on_by_default(self):
        """The default is the plugin's own (ENABLED_BY_DEFAULT); bundled goals
        says True because it only reads and draws (test_plugin_declarations)."""
        self.write(self.bundled, "goals", "ENABLED_BY_DEFAULT = True\n")
        self.assertTrue(registry.enabled("goals", {}))

    def test_the_two_that_can_send_are_off_by_default(self):
        for name in ("claims", "actions"):
            with self.subTest(name):
                self.assertFalse(registry.enabled(name, {}))

    def test_an_unknown_plugin_is_off_by_default(self):
        """A plugin nobody named running by default is a decision made on the
        user's behalf."""
        self.assertFalse(registry.enabled("something-new", {}))

    def test_config_turns_one_on(self):
        self.assertTrue(registry.enabled(
            "claims", {"plugins": {"claims": {"enabled": True}}}))

    def test_config_turns_one_off(self):
        self.assertFalse(registry.enabled(
            "goals", {"plugins": {"goals": {"enabled": False}}}))

    def test_a_disabled_plugin_is_discovered_but_not_returned_as_active(self):
        """Discovered, so `umbra plugins` can list it and say it is off.
        Not active, so nothing runs it.

        The fixture is deliberately not named after a real bundled plugin: a
        bundled name resolves to the genuine package member, not to whatever a
        test wrote into a temp directory, and a fixture that collides would be
        testing the real plugin while appearing to test the fake.
        """
        self.write(self.bundled, "offbydefault", ANALYZER)
        self.assertIn("offbydefault", registry.analyzers())
        self.assertNotIn("offbydefault",
                         registry.analyzers(only_enabled=True, conf={}))

    def test_an_enabled_plugin_is_returned_as_active(self):
        self.write(self.bundled, "offbydefault", ANALYZER)
        conf = {"plugins": {"offbydefault": {"enabled": True}}}
        self.assertIn("offbydefault",
                      registry.analyzers(only_enabled=True, conf=conf))


class ThreeStatesTest(_Base):
    """Enabling analysis never enables sending.

    Analysis is local and costs cents. Sending creates records in someone else's
    system — verification runs to a few dollars a day and dispatch creates about
    twenty cards. Installing something and having it immediately write into your
    inbox is a decision made before you have seen one result.
    """

    def test_enabled_does_not_imply_allowed_to_send(self):
        conf = {"plugins": {"actions": {"enabled": True}}}
        self.assertTrue(registry.enabled("actions", conf))
        self.assertIsNone(sink.enabled_for("actions", conf))

    def test_naming_a_sink_still_does_not_allow_sending(self):
        conf = {"plugins": {"actions": {"enabled": True, "sink": "todo"}}}
        self.assertIsNone(sink.enabled_for("actions", conf))

    def test_all_three_together_allow_it(self):
        conf = {"plugins": {"actions": {
            "enabled": True, "sink": "todo", "send": True}}}
        self.assertTrue(registry.enabled("actions", conf))
        self.assertEqual(sink.enabled_for("actions", conf), "todo")


if __name__ == "__main__":
    unittest.main()
