"""The Sink interface: what leaves Umbra, and what may come back.

This is the only interface with an effect outside the machine, which shapes what
the tests are for. Most of them are about state after a failure, because the
failure that matters here is not a crash — it is an item marked sent that never
went, which means it never goes out and nothing says so.

The interface is one required function and two optional ones. A destination that
cannot answer simply does not implement `poll`; that is the whole difference
between a webhook and a backend with an agent behind it. An earlier cut split the
interface by whether the destination had agent capability, which put the
implementer's business into the contract.
"""

import json
import os
import sqlite3
import tempfile
import textwrap
import unittest
from unittest import mock

from umbra import config, db, registry, sink

PLUGIN = "actions"


class ResolveTest(unittest.TestCase):
    def test_a_configured_command_sink_resolves(self):
        impl = sink.resolve("todo", {"sinks": {"todo": {
            "kind": "command", "command": "true"}}})
        self.assertTrue(callable(impl["send"]))
        self.assertNotIn("poll", impl)

    def test_a_configured_webhook_sink_resolves(self):
        impl = sink.resolve("hook", {"sinks": {"hook": {
            "kind": "webhook", "url": "https://example.com/h"}}})
        self.assertTrue(callable(impl["send"]))

    def test_an_unknown_sink_names_the_config_keys_to_set(self):
        """An error a person can act on beats a stack trace."""
        with self.assertRaises(RuntimeError) as e:
            sink.resolve("nope", {})
        msg = str(e.exception)
        self.assertIn("[sinks.nope]", msg)
        self.assertIn("kind", msg)

    def test_an_unknown_kind_lists_the_kinds_that_exist(self):
        with self.assertRaises(RuntimeError) as e:
            sink.resolve("x", {"sinks": {"x": {"kind": "carrier-pigeon"}}})
        self.assertIn("command", str(e.exception))

    def test_a_command_sink_without_a_command_says_so(self):
        with self.assertRaises(RuntimeError):
            sink.resolve("x", {"sinks": {"x": {"kind": "command"}}})

    def test_a_webhook_sink_without_a_url_says_so(self):
        with self.assertRaises(RuntimeError):
            sink.resolve("x", {"sinks": {"x": {"kind": "webhook"}}})


class TwoGatesTest(unittest.TestCase):
    """Naming a destination and authorising delivery to it are different
    decisions. Configuring a sink to see what would go out must not start it
    going out."""

    def test_off_when_nothing_is_configured(self):
        self.assertIsNone(sink.enabled_for(PLUGIN, {}))

    def test_off_when_a_sink_is_named_but_sending_is_not_switched_on(self):
        self.assertIsNone(sink.enabled_for(
            PLUGIN, {"plugins": {PLUGIN: {"sink": "todo"}}}))

    def test_off_when_sending_is_on_but_no_sink_is_named(self):
        self.assertIsNone(sink.enabled_for(
            PLUGIN, {"plugins": {PLUGIN: {"send": True}}}))

    def test_on_only_when_both_are_set(self):
        self.assertEqual(sink.enabled_for(
            PLUGIN, {"plugins": {PLUGIN: {"sink": "todo", "send": True}}}),
            "todo")

    def test_one_plugin_being_on_does_not_switch_another_on(self):
        conf = {"plugins": {"actions": {"sink": "todo", "send": True}}}
        self.assertIsNone(sink.enabled_for("claims", conf))


class CommandSinkTest(unittest.TestCase):
    """The item is model output and a transcript quote. Neither belongs on a
    command line."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "out.txt")

    def read(self):
        with open(self.out) as f:
            return f.read()

    def _send(self, item, cmd):
        impl = sink.resolve("t", {"sinks": {"t": {"kind": "command",
                                                  "command": cmd}}})
        return impl["send"](item)

    def test_fields_arrive_as_environment_variables(self):
        self._send({"task": "call Sam", "day": "2026-09-08"},
                   f'printf "%s|%s" "$UMBRA_TASK" "$UMBRA_DAY" > {self.out}')
        self.assertEqual(self.read(), "call Sam|2026-09-08")

    def test_the_whole_item_is_available_as_json(self):
        self._send({"task": "x", "n": 2}, f'printf "%s" "$UMBRA_ITEM" > {self.out}')
        self.assertEqual(json.loads(self.read()), {"task": "x", "n": 2})

    def test_the_item_is_never_interpolated_into_the_command(self):
        """Shell metacharacters in model output must stay inert."""
        marker = os.path.join(self.dir, "pwned")
        self._send({"task": f'"; touch {marker}; echo "'},
                   f'printf "%s" "$UMBRA_TASK" > {self.out}')
        self.assertFalse(os.path.exists(marker))
        self.assertIn("touch", self.read())

    def test_a_none_field_becomes_an_empty_string_not_the_word_none(self):
        self._send({"t": None}, f'printf "[%s]" "$UMBRA_T" > {self.out}')
        self.assertEqual(self.read(), "[]")

    def test_a_nonzero_exit_raises(self):
        with self.assertRaises(RuntimeError):
            self._send({"task": "x"}, "exit 3")


class DispatchStateTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.sent = []

    def _impl(self, receipt=None, poll=None, fail=False):
        def send(item):
            if fail:
                raise RuntimeError("destination refused")
            self.sent.append(item)
            return receipt
        impl = {"send": send}
        if poll is not None:
            impl["poll"] = poll
        return impl

    def test_a_send_only_sink_lands_answered_because_nothing_is_coming(self):
        """Leaving it at `sent` would grow a queue of things waiting forever on
        a destination that was never going to reply."""
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s", self._impl())
        self.assertEqual(sink.state(self.con, PLUGIN, "i1", "s")["status"],
                         sink.ANSWERED)

    def test_a_sink_with_poll_waits_at_sent(self):
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s",
                  self._impl(receipt="ext-1", poll=lambda e: None))
        st = sink.state(self.con, PLUGIN, "i1", "s")
        self.assertEqual(st["status"], sink.SENT)
        self.assertEqual(st["external_id"], "ext-1")

    def test_a_failed_send_is_not_marked_sent(self):
        """The failure this interface exists to prevent: an item marked sent
        that never went means it never goes out and nothing says so."""
        with self.assertRaises(RuntimeError):
            sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s",
                      self._impl(fail=True))
        self.assertEqual(sink.state(self.con, PLUGIN, "i1", "s")["status"],
                         sink.FAILED)

    def test_a_failed_item_can_be_sent_again(self):
        with self.assertRaises(RuntimeError):
            sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s",
                      self._impl(fail=True))
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s", self._impl())
        self.assertEqual(sink.state(self.con, PLUGIN, "i1", "s")["status"],
                         sink.ANSWERED)

    def test_attempts_accumulate_so_a_stuck_item_is_visible(self):
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s",
                          self._impl(fail=True))
        self.assertEqual(sink.state(self.con, PLUGIN, "i1", "s")["attempt"], 3)

    def test_polling_records_the_answer(self):
        impl = self._impl(receipt="ext-1", poll=lambda e: {"verdict": "ok"})
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s", impl)
        got = sink.poll(self.con, PLUGIN, "s", impl)
        st = sink.state(self.con, PLUGIN, "i1", "s")
        self.assertEqual(got, 1)
        self.assertEqual(st["status"], sink.ANSWERED)
        self.assertEqual(st["result"], {"verdict": "ok"})

    def test_polling_leaves_an_unfinished_item_waiting(self):
        impl = self._impl(receipt="ext-1", poll=lambda e: None)
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s", impl)
        self.assertEqual(sink.poll(self.con, PLUGIN, "s", impl), 0)
        self.assertEqual(sink.state(self.con, PLUGIN, "i1", "s")["status"],
                         sink.SENT)

    def test_polling_a_send_only_sink_returns_zero_rather_than_raising(self):
        """Asking a webhook for its answer is a reasonable thing to do blindly;
        the honest reply is "none"."""
        impl = self._impl()
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "s", impl)
        self.assertEqual(sink.poll(self.con, PLUGIN, "s", impl), 0)

    def test_two_plugins_do_not_see_each_others_dispatches(self):
        sink.send(self.con, "actions", "i1", {"t": "a"}, "s", self._impl())
        sink.send(self.con, "claims", "i1", {"t": "c"}, "s", self._impl())
        self.assertIsNotNone(sink.state(self.con, "actions", "i1", "s"))
        self.assertEqual(
            [r["item_id"] for r in sink.pending(self.con, "actions",
                                                sink.ANSWERED)], ["i1"])

    def test_the_same_item_to_two_sinks_is_two_dispatches(self):
        sink.send(self.con, PLUGIN, "i1", {"t": "x"}, "a", self._impl())
        sink.send(self.con, PLUGIN, "i1", {"t": "x"}, "b", self._impl())
        self.assertIsNotNone(sink.state(self.con, PLUGIN, "i1", "a"))
        self.assertIsNotNone(sink.state(self.con, PLUGIN, "i1", "b"))

    def test_an_unsent_item_has_no_state(self):
        self.assertIsNone(sink.state(self.con, PLUGIN, "nope", "s"))


PLUGIN_SINK = textwrap.dedent('''
    """A community sink: send only, no answer coming."""
    SENT = []

    def send(item):
        SENT.append(item)
        return "receipt-1"
''')

PLUGIN_SINK_WITH_POLL = textwrap.dedent('''
    def send(item):
        return "ext-9"

    def poll(receipt):
        return {"from": receipt}

    def render(result):
        return f"answered by {result['from']}"
''')


class PluginSinkTest(unittest.TestCase):
    """A sink written as a file in the plugins directory, resolved by name.

    This is the path a community Slack or email sink takes, so it is tested the
    way one would actually arrive: a real file in a real directory.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "plugins"), exist_ok=True)
        self.patch = mock.patch.object(config, "PLUGINS_DIR",
                                       os.path.join(self.dir, "plugins"))
        self.patch.start()
        registry.reload()
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def tearDown(self):
        self.patch.stop()
        registry.reload()

    def write(self, name, body):
        with open(os.path.join(self.dir, "plugins", f"{name}.py"), "w") as f:
            f.write(body)
        registry.reload()

    def test_a_send_only_plugin_sink_resolves_and_sends(self):
        self.write("mysink", PLUGIN_SINK)
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "mysink")
        self.assertEqual(sink.state(self.con, PLUGIN, "i1", "mysink")["status"],
                         sink.ANSWERED)

    def test_a_plugin_declaring_poll_and_render_exposes_both(self):
        self.write("full", PLUGIN_SINK_WITH_POLL)
        impl = sink.resolve("full")
        self.assertTrue(callable(impl["poll"]))
        self.assertEqual(impl["render"]({"from": "ext-9"}),
                         "answered by ext-9")

    def test_answers_tells_a_sink_that_reports_back_from_one_that_only_receives(self):
        """Fact-checking needs the first kind; Settings says which one is named."""
        self.write("full", PLUGIN_SINK_WITH_POLL)
        self.write("mysink", PLUGIN_SINK)
        self.assertTrue(sink.answers("full", {}))
        self.assertFalse(sink.answers("mysink", {}))
        self.assertFalse(sink.answers("todo", {"sinks": {"todo": {
            "kind": "command", "command": "true"}}}))
        self.assertIsNone(sink.answers("nope", {}))

    def test_a_plugin_sink_round_trips_through_poll(self):
        self.write("full", PLUGIN_SINK_WITH_POLL)
        sink.send(self.con, PLUGIN, "i1", {"task": "x"}, "full")
        self.assertEqual(sink.poll(self.con, PLUGIN, "full"), 1)
        self.assertEqual(sink.state(self.con, PLUGIN, "i1", "full")["result"],
                         {"from": "ext-9"})

    def test_a_plugin_sink_wins_over_a_configured_one_of_the_same_name(self):
        """Same rule as a user source overriding a built-in: whoever wrote the
        file in your own directory meant it."""
        self.write("todo", PLUGIN_SINK)
        impl = sink.resolve("todo", {"sinks": {"todo": {
            "kind": "command", "command": "false"}}})
        self.assertEqual(impl["send"]({"t": "x"}), "receipt-1")

    def test_a_plugin_with_no_send_is_not_a_sink(self):
        self.write("analyzeronly", 'INPUT = "spans"\nWINDOW = 30\n')
        with self.assertRaises(RuntimeError):
            sink.resolve("analyzeronly", {})


class SchemaTest(unittest.TestCase):
    def test_the_table_comes_from_the_one_assembled_constant(self):
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        self.assertTrue(con.execute(
            "SELECT name FROM sqlite_master WHERE name='sink_dispatch'").fetchone())


if __name__ == "__main__":
    unittest.main()
