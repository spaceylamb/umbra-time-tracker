"""The plugin documentation describes the plugin interface that exists.

A doc that has drifted is worse than no doc: someone follows it, their plugin
does nothing, and there is no error anywhere to explain why. These tests read
docs/writing-a-plugin.md and check it against the code — the declaration names it
promises are the ones the runner reads, and the worked example produces the
counts it says it does.

The counts matter more than they look. `called 0` on an unchanged second run is
the documented signal that incrementality works, and it is the number a plugin
author checks to know their cache key is right. If that example ever stops
producing it, the doc is teaching people to accept a broken cache.
"""

import os
import re
import sqlite3
import tempfile
import textwrap
import unittest
from unittest import mock

from umbra import analyze, analyzer, config, db, registry, sink

DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "writing-a-plugin.md")


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _doc() -> str:
    return _read("docs", "writing-a-plugin.md")


class DeclarationsExistTest(unittest.TestCase):
    """Everything the doc tells an author to declare is read by the runner."""

    ANALYZER = ("INPUT", "WINDOW", "PROMPT", "parse", "params",
                "CONTEXT_MIN", "render_window", "TABLES", "sync", "KEY")
    SINK = ("send", "poll", "render")

    def test_the_doc_documents_every_analyzer_declaration(self):
        doc = _doc()
        for name in self.ANALYZER:
            with self.subTest(name):
                self.assertIn(name, doc)

    def test_the_runner_reads_every_documented_analyzer_declaration(self):
        """A promised name the runner never looks at is a name that does
        nothing, silently."""
        src = _read("umbra", "analyzer.py")
        for name in self.ANALYZER:
            if name == "TABLES":
                continue                # ensure_tables takes it from the loader
            with self.subTest(name):
                self.assertIn(f'"{name}"', src, f"{name} is documented but unread")

    def test_the_sink_resolver_exposes_every_documented_function(self):
        src = _read("umbra", "sink.py")
        for name in self.SINK:
            with self.subTest(name):
                self.assertIn(f'"{name}"', src)

    def test_the_documented_prompt_placeholders_are_the_ones_supplied(self):
        """A placeholder the runner does not supply raises KeyError at format
        time — on the first real run, after the plugin looked fine."""
        src = _read("umbra", "analyzer.py")
        line = next(l for l in src.splitlines() if "tpl.format(" in l)
        for key in ("day", "text", "a", "b"):
            with self.subTest(key):
                self.assertIn(f"{key}=", line)

    def test_the_documented_reader_helpers_exist(self):
        """A doc promising a function a view calls on every draw. If it goes,
        the page either stops asking or crashes — and the first one is silent."""
        for name in ("current", "load_rows", "drop", "load_items", "update_item"):
            with self.subTest(name):
                self.assertTrue(callable(getattr(analyzer, name, None)))
                self.assertIn(f"analyzer.{name}(", _doc())

    def test_the_documented_item_fields_are_the_ones_a_sink_receives(self):
        butler = _read("umbra", "plugins", "butler.py")
        for field in ("title", "body", "key", "visible"):
            with self.subTest(field):
                self.assertIn(f'"{field}"', butler)


# The Analyzer example, copied out of the doc rather than rewritten.
MOOD = textwrap.dedent('''
    """How the day felt, from what was typed."""

    INPUT  = "events:zsh"       # what to read
    WINDOW = 30                 # minutes per question
    PROMPT = "Rate the mood of this stretch.\\n{text}"


    def parse(items, window):
        """The model's JSON array -> the rows to store."""
        return [{"a": i["a"], "b": i["b"], "mood": i["mood"]} for i in items]
''')


class WorkedExampleTest(unittest.TestCase):
    """The doc's example, run as written."""

    DAY = "2026-09-09"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "plugins"))
        with open(os.path.join(self.dir, "plugins", "mood.py"), "w") as f:
            f.write(MOOD)
        self.patch = mock.patch.object(config, "PLUGINS_DIR",
                                       os.path.join(self.dir, "plugins"))
        self.patch.start()
        registry.reload()
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        lo, _hi = analyze.day_bounds(self.DAY)
        db.insert(self.con, [(f"zsh:{m}", lo + m * 60000, None, "zsh",
                              "command", {"cmd": f"git commit -m x{m}"})
                             for m in (545, 610)])

    def tearDown(self):
        self.patch.stop()
        registry.reload()

    def _run(self):
        return analyzer.run(
            self.con, "mood", registry.analyzers()["mood"], self.DAY, "m",
            call=lambda p: '[{"a":545,"b":546,"mood":"focused"}]')

    def test_the_example_is_discovered_as_written(self):
        self.assertIn("mood", registry.analyzers())

    def test_the_first_run_reports_the_documented_counts(self):
        r = self._run()
        self.assertEqual(
            (r["windows"], r["called"], r["reused"], r["empty"], r["rows"]),
            (48, 2, 0, 46, 2))

    def test_an_unchanged_second_run_calls_nothing(self):
        """The documented signal that incrementality is working. An author who
        sees anything but zero here has a cache key that moves between rounds."""
        self._run()
        r = self._run()
        self.assertEqual(
            (r["windows"], r["called"], r["reused"], r["empty"], r["rows"]),
            (48, 0, 2, 46, 0))

    def test_load_rows_returns_what_the_doc_says(self):
        self._run()
        rows = analyzer.load_rows(self.con, "mood", self.DAY)
        self.assertEqual(rows[0]["mood"], "focused")
        self.assertEqual((rows[0]["a"], rows[0]["b"]), (545, 546))

    def test_drop_removes_the_plugins_rows(self):
        self._run()
        analyzer.drop(self.con, "mood")
        self.assertEqual(analyzer.load_rows(self.con, "mood", self.DAY), [])


class SinkExampleTest(unittest.TestCase):
    """The doc's Sink example, and the environment variable it advertises."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def test_the_documented_command_env_var_exists(self):
        """The config example writes $UMBRA_BODY. If the sink never sets it, the
        example appends empty lines and looks like it worked."""
        out = os.path.join(self.dir, "todo.md")
        impl = sink.resolve("todo", {"sinks": {"todo": {
            "kind": "command",
            "command": f'printf "%s\\n" "$UMBRA_BODY" >> {out}'}}})
        impl["send"]({"title": "x", "body": "buy a wetsuit"})
        with open(out) as f:
            self.assertEqual(f.read().strip(), "buy a wetsuit")

    def test_a_send_only_sink_is_complete(self):
        """The doc says one function is a whole Sink. It has to be true."""
        sent = []
        impl = {"send": lambda item: sent.append(item) or None}
        sink.send(self.con, "demo", "i1", {"title": "t", "body": "b"}, "s", impl)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sink.state(self.con, "demo", "i1", "s")["status"],
                         sink.ANSWERED)


class ConfigExampleTest(unittest.TestCase):
    """The three states the doc describes are the three the code enforces."""

    def test_installed_is_not_enabled(self):
        self.assertFalse(registry.enabled("mood", {}))

    def test_enabled_is_not_allowed_to_send(self):
        conf = {"plugins": {"actions": {"enabled": True, "sink": "todo"}}}
        self.assertTrue(registry.enabled("actions", conf))
        self.assertIsNone(sink.enabled_for("actions", conf))

    def test_the_documented_toml_shape_reads_correctly(self):
        """Exactly the keys the doc's config block uses."""
        conf = {"plugins": {"actions": {
            "enabled": True, "sink": "todo", "send": True}}}
        self.assertTrue(registry.enabled("actions", conf))
        self.assertEqual(sink.enabled_for("actions", conf), "todo")

    def test_goals_is_the_documented_exception(self):
        self.assertTrue(registry.enabled("goals", {}))
        for other in ("claims", "actions"):
            with self.subTest(other):
                self.assertFalse(registry.enabled(other, {}))


class DocShapeTest(unittest.TestCase):
    def test_no_absolute_paths_leak_into_the_doc(self):
        doc = _doc()
        self.assertNotIn("/Users/", doc)
        self.assertNotIn(os.path.expanduser("~") + "/ai", doc)

    def test_it_points_at_the_bundled_plugins_by_their_real_path(self):
        """The doc claims the shipped three load through this same path. If they
        move, the claim becomes false."""
        self.assertIn("umbra/plugins/", _doc())
        for name in ("goals", "claims", "actions"):
            with self.subTest(name):
                self.assertIn(name, _doc())

    def test_every_fenced_python_block_parses(self):
        """A doc whose examples do not compile teaches syntax errors."""
        blocks = re.findall(r"```python\n(.*?)```", _doc(), re.S)
        self.assertTrue(blocks)
        for i, b in enumerate(blocks):
            with self.subTest(block=i):
                compile(b, f"<doc block {i}>", "exec")

    def test_every_fenced_toml_block_parses(self):
        import tomllib
        blocks = re.findall(r"```toml\n(.*?)```", _doc(), re.S)
        self.assertTrue(blocks)
        for i, b in enumerate(blocks):
            with self.subTest(block=i):
                tomllib.loads(b)


if __name__ == "__main__":
    unittest.main()
