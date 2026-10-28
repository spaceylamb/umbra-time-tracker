"""A plugin whose only output is what it sends has one switch, and the page
says where things go.

Missing data alerts and Things to automate make nothing you can see on this
Mac: the alert and the monthly list *are* the plugin. A second "send" switch
under them meant "on" could be on and do nothing visible — the half-state the
plugins are not supposed to have. So such a plugin declares SEND_ONLY: on
means sending to its sink, and with no sink named it is blocked, saying so.

The two that are useful locally (to-dos, fact-check — both show in the day's
drawer) keep their second switch, worded as what it is: also send a copy.

And a sink's name is whatever someone typed in config.toml; "to notes" says
nothing about where a thing goes. The page says what the sink actually is.
"""

import types
import unittest
from unittest import mock

from umbra import configedit, registry, settings, sink
from umbra.plugins import butler, staleness
from umbra.plugins import review


def _mods(**mods):
    return mock.patch.object(registry, "plugins", return_value=(mods, {}))


class EnabledForTest(unittest.TestCase):
    def test_a_send_only_plugin_sends_once_a_sink_is_named(self):
        mod = types.SimpleNamespace(SEND_ONLY=True)
        with _mods(alerts=mod):
            self.assertEqual(sink.enabled_for("alerts", {"plugins": {"alerts": {"sink": "butler"}}}),
                             "butler")
            self.assertIsNone(sink.enabled_for("alerts", {"plugins": {"alerts": {}}}))

    def test_any_other_still_needs_send(self):
        mod = types.SimpleNamespace(SENDS=True)
        with _mods(todo=mod):
            self.assertIsNone(sink.enabled_for("todo", {"plugins": {"todo": {"sink": "butler"}}}))
            self.assertEqual(sink.enabled_for(
                "todo", {"plugins": {"todo": {"sink": "butler", "send": True}}}), "butler")

    def test_the_two_bundled_ones_are_send_only(self):
        for mod in (staleness, review):
            with self.subTest(mod.__name__):
                self.assertTrue(mod.SEND_ONLY)
                self.assertFalse(getattr(mod, "SENDS", False))


class DescribeTest(unittest.TestCase):
    def test_butler(self):
        self.assertEqual(sink.describe("butler", {}), butler.DESTINATION)

    def test_a_command(self):
        conf = {"sinks": {"notes": {"kind": "command",
                                    "command": 'printf "%s" "$UMBRA_BODY" >> ~/alerts.md'}}}
        self.assertEqual(sink.describe("notes", conf),
                         'the command printf "%s" "$UMBRA_BODY" >> ~/alerts.md')

    def test_a_long_command_is_shortened(self):
        conf = {"sinks": {"n": {"kind": "command", "command": "x" * 200}}}
        self.assertLessEqual(len(sink.describe("n", conf)), 80)

    def test_a_webhook_names_its_host_only(self):
        """The path of a webhook URL is often its secret."""
        conf = {"sinks": {"hook": {"kind": "webhook",
                                   "url": "https://hooks.example.com/T000/B000/secret"}}}
        self.assertEqual(sink.describe("hook", conf), "the webhook at hooks.example.com")

    def test_one_that_is_not_set_up(self):
        self.assertIsNone(sink.describe("nowhere", {}))


class SettingsRowTest(unittest.TestCase):
    def row(self, mod, conf):
        with _mods(butler=butler,
                   p=types.SimpleNamespace(__file__="/elsewhere/p.py", sync=lambda *a: 0, **mod)):
            return next(r for r in settings._plugins(conf) if r["name"] == "p")

    def test_send_only_says_where_and_has_no_second_switch(self):
        conf = {"plugins": {"p": {"sink": "butler", "enabled": True}}}
        r = self.row({"SEND_ONLY": True}, conf)
        self.assertEqual((r["send_only"], r["can_send"], r["sending"]), (True, False, True))
        self.assertEqual(r["destination"], butler.DESTINATION)

    def test_a_second_switch_says_what_it_sends(self):
        r = self.row({"SENDS": True, "SEND_WHAT": "each to-do"},
                     {"plugins": {"p": {"sink": "butler"}}})
        self.assertEqual((r["can_send"], r["send_what"], r["sending"]), (True, "each to-do", False))

    def test_no_sink_no_destination(self):
        r = self.row({"SEND_ONLY": True}, {})
        self.assertIsNone(r["destination"])


class EditableTest(unittest.TestCase):
    def test_a_send_only_plugin_has_no_send_key(self):
        with _mods(alerts=types.SimpleNamespace(SEND_ONLY=True),
                   todo=types.SimpleNamespace(SENDS=True, SEND_WHAT="each to-do")):
            keys = configedit.editable()
        self.assertNotIn("plugins.alerts.send", keys)
        self.assertIn("each to-do", keys["plugins.todo.send"][3])


class NamesTest(unittest.TestCase):
    """What the plugins are called on the page."""

    def test_names(self):
        from umbra.plugins import actions, claims
        self.assertEqual(staleness.TITLE, "Missing data alerts")
        self.assertEqual(review.TITLE, "Things to automate")
        self.assertEqual(actions.TITLE, "To-dos from recordings")
        self.assertEqual(claims.TITLE, "Fact-check")


if __name__ == "__main__":
    unittest.main()
