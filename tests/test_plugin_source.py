"""A source dropped into the config directory, working end to end.

This is the capability the registry exists to provide, so it is tested the way a
person actually gets it: by writing a real .py file into a real directory and
asking whether the rest of the system can see it. Nothing is monkeypatched into
place — the failure being guarded against is precisely "the wiring looks right
in isolation and nothing picks it up".

Before this, a community source could implement collect() perfectly and still be
invisible: no line in the digest, no category from the rule layer, no lane on
the page, and no way to say its durations were real. Four separate modules each
kept their own list of source names, and being absent from any of them failed
silently.
"""

import os
import tempfile
import textwrap
import unittest
from unittest import mock

from umbra import config, digest, registry, segment

PLUGIN = textwrap.dedent('''
    """A third-party source, written the way the docs describe."""

    SOURCE = "strava"

    LANES = [{"key": "strava", "kind": None,
              "origin": ("Strava API", "activities, start time and duration")}]

    DURATION = "trusted"


    def collect(since_ms=None):
        return []


    def render(e):
        if e.get("kind") != "activity":
            return None
        return f"\\U0001F3C3 {e.get('sport') or '?'} {e.get('km') or 0}km"


    def classify(e):
        return "workout" if e.get("kind") == "activity" else None
''')


class PluginSourceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "sources"), exist_ok=True)
        with open(os.path.join(self.dir, "sources", "strava.py"), "w") as f:
            f.write(PLUGIN)
        self.patch = mock.patch.object(config, "SOURCES_DIR",
                                       os.path.join(self.dir, "sources"))
        self.patch.start()
        registry.reload()

    def tearDown(self):
        self.patch.stop()
        registry.reload()

    def _event(self, **kw):
        return {"ts": 0, "dur_ms": kw.pop("dur", 0), "source": "strava",
                "kind": kw.pop("kind", "activity"), "_min": 540, **kw}

    def test_the_model_sees_it(self):
        """The one that matters most. Everything else is recoverable by
        looking; this one changes what the model is judging from."""
        self.assertEqual(digest.line(self._event(sport="run", km=8)),
                         "🏃 run 8km")

    def test_it_can_say_a_record_has_no_position_in_time(self):
        """The same escape hatch the built-ins use for a daily total or a score.
        Without it, a plugin's summary rows would be rendered as moments."""
        self.assertIsNone(digest.line(self._event(kind="weekly_total", km=42)))

    def test_the_rule_layer_can_classify_it(self):
        from umbra import blocks
        cat, _ = blocks._pick([self._event(sport="run", km=8)])
        self.assertEqual(cat, "workout")

    def test_it_gets_a_lane_on_the_page(self):
        self.assertIn(("strava", "strava", None), registry.lanes())

    def test_its_lane_declares_where_the_data_came_from(self):
        self.assertEqual(registry.origins()["strava"],
                         ("Strava API", "activities, start time and duration"))

    def test_it_can_declare_that_its_durations_are_real(self):
        self.assertIn("strava", registry.trusted_duration())
        e = self._event(sport="run", km=8, dur=1_800_000)
        self.assertEqual(segment.end_of(e), e["ts"] + 1_800_000)

    def test_it_does_not_disturb_the_built_ins(self):
        """A plugin is added, not swapped in. The regression this catches is a
        registry that rebuilds from user sources alone."""
        self.assertEqual(digest.line({"ts": 0, "dur_ms": 0, "source": "zsh",
                                      "kind": "command", "cmd": "ls", "_min": 540}),
                         "$ ls")


class PartialDeclarationTest(unittest.TestCase):
    """Every declaration is optional, and omitting one is not an error.

    A source that only wants to collect data should not be forced to write four
    functions it has no opinion about. What it must not do is get a *guessed*
    answer for the ones it skipped — absent is honest, invented is not.
    """

    MINIMAL = textwrap.dedent('''
        SOURCE = "minimal"

        def collect(since_ms=None):
            return []
    ''')

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "sources"), exist_ok=True)
        with open(os.path.join(self.dir, "sources", "minimal.py"), "w") as f:
            f.write(self.MINIMAL)
        self.patch = mock.patch.object(config, "SOURCES_DIR",
                                       os.path.join(self.dir, "sources"))
        self.patch.start()
        registry.reload()

    def tearDown(self):
        self.patch.stop()
        registry.reload()

    def test_it_is_registered(self):
        self.assertIsNotNone(registry.spec("minimal"))

    def test_it_renders_nothing_rather_than_raising(self):
        e = {"ts": 0, "dur_ms": 0, "source": "minimal", "kind": "x", "_min": 540}
        self.assertIsNone(digest.line(e))

    def test_it_has_no_lane_and_no_invented_origin(self):
        self.assertNotIn("minimal", {k for k, _, _ in registry.lanes()})
        self.assertNotIn("minimal", registry.origins())

    def test_its_durations_are_not_trusted_by_default(self):
        """The safe default. chrome's visit_duration is the cautionary tale:
        trusted by mistake, it stretches a background tab across hours."""
        self.assertNotIn("minimal", registry.trusted_duration())


class BrokenPluginTest(unittest.TestCase):
    """One unimportable source must not take the others down with it.

    The registry is consulted once per event, so an exception here would not
    break one source — it would empty the digest entirely, for every source, on
    every day.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "sources"), exist_ok=True)
        with open(os.path.join(self.dir, "sources", "broken.py"), "w") as f:
            f.write("this is not valid python (\n")
        self.patch = mock.patch.object(config, "SOURCES_DIR",
                                       os.path.join(self.dir, "sources"))
        self.patch.start()
        registry.reload()

    def tearDown(self):
        self.patch.stop()
        registry.reload()

    def test_the_built_ins_still_render(self):
        self.assertEqual(digest.line({"ts": 0, "dur_ms": 0, "source": "zsh",
                                      "kind": "command", "cmd": "ls", "_min": 540}),
                         "$ ls")

    def test_the_error_is_still_reportable(self):
        """Silently swallowed is not the same as handled. `umbra sources` has to
        be able to say why the source did not run."""
        from umbra import sources
        _found, errors = sources.discover()
        self.assertIn("broken", errors)


if __name__ == "__main__":
    unittest.main()
