"""A plugin says what it is; the core reads what it said.

Before, the core knew the three bundled plugins by name: their titles and
descriptions (settings._PLUGIN_WHAT), which are on by default
(registry.DEFAULT_ENABLED), which had a config section before plugins existed
(registry.LEGACY_SECTIONS), which can send (settings, a tuple), and which of
their keys the page may change (configedit.EDITABLE). A plugin of anyone else's
could declare none of that, and a bundled one could not be taken out without
editing the core.

Now each is a module attribute, and the core reads it off whatever is installed:

    TITLE, WHAT            how Settings names it
    ENABLED_BY_DEFAULT     on before anyone says otherwise (False if absent)
    LEGACY_SECTION         an old [<name>] section means it was in use
    SENDS                  it can send to a sink: Settings shows the second switch
    SETTINGS               {key: (kind, default, constraint, what)} the page may
                           change, as plugins.<name>.<key>

Every installed plugin gets plugins.<name>.enabled; one that SENDS also gets
plugins.<name>.send.
"""

import types
import unittest
from unittest import mock

from umbra import configedit, registry, settings


def _mods(**mods):
    return mock.patch.object(registry, "plugins", return_value=(mods, {}))


class EnablementTest(unittest.TestCase):
    def test_a_plugin_says_whether_it_starts_on(self):
        with _mods(on=types.SimpleNamespace(ENABLED_BY_DEFAULT=True),
                   off=types.SimpleNamespace()):
            self.assertTrue(registry.enabled("on", {}))
            self.assertFalse(registry.enabled("off", {}))

    def test_an_old_section_turns_on_only_a_plugin_that_had_one(self):
        conf = {"lane": {"x": 1}, "other": {"x": 1}}
        with _mods(lane=types.SimpleNamespace(LEGACY_SECTION=True),
                   other=types.SimpleNamespace()):
            self.assertTrue(registry.enabled("lane", conf))
            self.assertFalse(registry.enabled("other", conf))

    def test_the_bundled_ones_declare_what_the_core_used_to_know(self):
        found, _ = registry.plugins()
        self.assertTrue(found["goals"].ENABLED_BY_DEFAULT)
        for name in ("claims", "actions"):
            with self.subTest(name):
                m = found[name]
                self.assertFalse(getattr(m, "ENABLED_BY_DEFAULT", False))
                self.assertTrue(m.LEGACY_SECTION)
                self.assertTrue(m.SENDS)
        self.assertEqual(found["claims"].TITLE, "Fact-check")


class SettingsRowTest(unittest.TestCase):
    def test_the_row_is_what_the_plugin_says(self):
        mod = types.SimpleNamespace(TITLE="Mood", WHAT="Guesses the day's mood.\nMore.",
                                    SENDS=True, __file__="/elsewhere/mood.py")
        with _mods(mood=mod):
            row = next(p for p in settings._plugins({}) if p["name"] == "mood")
        self.assertEqual((row["title"], row["what"], row["can_send"]),
                         ("Mood", "Guesses the day's mood.", True))

    def test_without_a_title_it_is_named_and_described_by_its_docstring(self):
        mod = types.SimpleNamespace(__doc__="Counts steps.", __file__="/elsewhere/steps.py")
        with _mods(steps=mod):
            row = next(p for p in settings._plugins({}) if p["name"] == "steps")
        self.assertEqual((row["title"], row["what"], row["can_send"]),
                         ("steps", "Counts steps.", False))


class EditableKeysTest(unittest.TestCase):
    def _keys(self, **mods):
        with _mods(**mods):
            return configedit.editable()

    def test_every_installed_plugin_can_be_switched(self):
        keys = self._keys(a=types.SimpleNamespace(ENABLED_BY_DEFAULT=True),
                          b=types.SimpleNamespace())
        self.assertEqual(keys["plugins.a.enabled"][:2], ("bool", True))
        self.assertEqual(keys["plugins.b.enabled"][:2], ("bool", False))
        self.assertNotIn("plugins.a.send", keys)

    def test_one_that_sends_gets_the_send_switch_off(self):
        keys = self._keys(s=types.SimpleNamespace(SENDS=True))
        self.assertEqual(keys["plugins.s.send"][:2], ("bool", False))

    def test_a_plugins_own_keys_are_editable_under_its_name(self):
        keys = self._keys(s=types.SimpleNamespace(
            SETTINGS={"depth": ("int", 2, (1, 5), "how deep")}))
        self.assertEqual(keys["plugins.s.depth"], ("int", 2, (1, 5), "how deep"))

    def test_the_core_keys_are_still_there(self):
        keys = self._keys()
        for k in ("ui.lang", "ui.theme", "schedule.interval_min"):
            self.assertIn(k, keys)

    def test_an_undeclared_key_is_still_refused(self):
        with _mods(s=types.SimpleNamespace()):
            with self.assertRaises(configedit.Refused):
                configedit._check("plugins.s.depth", 3)


def _bundled() -> set[str]:
    import os
    from umbra import plugins
    return {os.path.splitext(n)[0] for n in os.listdir(plugins.DIR)
            if not n.startswith("_") and (n.endswith(".py")
                                          or os.path.isdir(os.path.join(plugins.DIR, n)))}


def plugin_reaches(path: str) -> list[str]:
    """Where a module outside umbra/plugins reaches into one bundled plugin by
    name: `from .plugins import claims`, `from .plugins.claims import x`,
    `plugins.claims`. Importing the package itself (`from . import plugins`,
    for its DIR) is not a reach, nor is `.items()` on a dict that happens to be
    called plugins."""
    import ast
    bundled = _bundled()
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    bad = []
    for node in ast.walk(tree):
        # `from . import plugins` has no module and is the package; anything
        # importing *from* a path through plugins names one of them.
        if isinstance(node, ast.ImportFrom) and node.module \
                and "plugins" in node.module.split("."):
            bad.append(f"{node.lineno} from {'.' * node.level}{node.module} import "
                       + ", ".join(a.name for a in node.names))
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and node.value.id == "plugins" and node.attr in bundled):
            bad.append(f"{node.lineno} plugins.{node.attr}")
    return bad


class CoreNamesNoPluginTest(unittest.TestCase):
    """The core reads plugins through the registry and never by name.

    Before, the drawer imported the two bundled lanes and the CLI looked them up
    by name — so the core could not ship without them, and a plugin of the same
    name in your own directory was not always the one that ran. With
    declarations and hooks there is no reason left for the core to name one.
    (db.py keeps the frozen tables of the lanes that predate plugins; that is
    history, not a reach.)
    """

    def test_no_core_module_reaches_into_a_bundled_plugin(self):
        import glob
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for f in glob.glob(os.path.join(here, "umbra", "**", "*.py"), recursive=True):
            rel = os.path.relpath(f, here)
            if rel.startswith(os.path.join("umbra", "plugins")):
                continue
            bad += [f"{rel}:{b}" for b in plugin_reaches(f)]
        self.assertEqual(bad, [])

    def test_the_check_would_catch_one(self):
        import os
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "api.py")
        with open(path, "w") as f:
            f.write("from . import plugins\nx = plugins.DIR\n"
                    "def f():\n    from .plugins import claims as C\n"
                    "    from umbra.plugins.actions import close\n    return plugins.goals\n"
                    "def g(plugins):\n    return plugins.items()\n")
        self.assertEqual(plugin_reaches(path),
                         ["4 from .plugins import claims",
                          "5 from umbra.plugins.actions import close", "6 plugins.goals"])


if __name__ == "__main__":
    unittest.main()
