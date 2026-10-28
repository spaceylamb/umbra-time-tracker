"""Every source, against the four places that know source names by heart.

A source that lands rows in the database is not thereby part of the system. Four
separate modules decide, by matching on the source name, whether it is ever seen
again:

    digest.line()                       whether the model sees it at all
    blocks.LADDER                       whether the rule layer can classify it
    viz.LANES / viz.ORIGIN              whether it gets a row on the page
    segment.trusted_duration_sources()  whether its durations mean anything

Miss one and the failure is silent in the specific way this project exists to
prevent: the source imports fine, `umbra stats` counts its rows, the freshness
report is green — and the model never reads a word of it.

Those four now read a single registry, and each source declares what it is.
This file pins the built-ins' wiring across that change — every assertion below
passed before the refactor and passes after it, which is the whole point of
having written them first.
"""

import unittest

from umbra import blocks, digest, segment, viz
from umbra.importers import SOURCE_OF

# Names as they appear in events.source. live and idle are written by the
# resident collector rather than an importer, so they are not in SOURCE_OF.
STORED_SOURCES = sorted(set(SOURCE_OF.values()) | {"live", "idle"})


def _event(source, **payload):
    return {"ts": 0, "dur_ms": payload.pop("dur", 0), "source": source,
            "kind": payload.pop("kind", ""), **payload}


# One representative event per source, shaped the way its importer writes it.
SAMPLES = {
    "chrome":   {"kind": "visit", "title": "Umbra - GitHub"},
    "claude":   {"kind": "prompt", "text": "merge this branch"},
    "butler":   {"kind": "task", "text": "triage the alert"},
    "zsh":      {"kind": "command", "cmd": "pytest -q"},
    "git":      {"kind": "commit", "project": "Umbra", "action": "fix"},
    "app":      {"kind": "focus", "bundle": "us.zoom.xos"},
    "iphone":   {"kind": "focus", "bundle": "us.zoom.xos"},
    "media":    {"kind": "play", "title": "Dune", "track": "Ch 12"},
    "intent":   {"kind": "send", "bundle": "com.apple.MobileSMS", "verb": "send"},
    "location": {"kind": "stay", "place": "home"},
    "whoop":    {"kind": "sleep"},
    "weread":   {"kind": "reading", "book": "Sapiens"},
    "plaud":    {"kind": "speech", "text": "hello", "speaker": "Speaker 1"},
    "live":     {"kind": "window", "app": "zoom.us", "title": "Zoom Meeting"},
    "idle":     {"kind": "away"},
}


class SampleCoverageTest(unittest.TestCase):
    def test_every_stored_source_has_a_sample(self):
        """Adding a source without adding a sample would make every test below
        pass by simply not testing it."""
        self.assertEqual(sorted(SAMPLES), STORED_SOURCES)


class ModelVisibilityTest(unittest.TestCase):
    def test_every_source_renders_a_line_for_the_model(self):
        """The one that matters most, and the one with no other alarm.

        A source missing here contributes nothing to the digest, so the model
        judges without it. Nothing raises, no count drops, and the page still
        shows the source's rows in the drill-down — so it looks present while
        being invisible to the judgement.
        """
        missing = [s for s in STORED_SOURCES
                   if not digest.line({**_event(s, **SAMPLES[s]), "_min": 540})]
        self.assertEqual(missing, [], f"invisible to the model: {missing}")


class RuleLayerTest(unittest.TestCase):
    # Sources that deliberately have no category on the ladder. Each is here for
    # a reason, not by omission, so the list is asserted rather than skipped.
    NO_CATEGORY = {
        # Actions only, never content — "sent a message" is not an activity you
        # spent a slot doing.
        "intent",
        # These two answer "was anybody at the machine", which feeds conflicts
        # and blind spots rather than a category. blocks.py's docstring argues
        # this at length: a contradiction is not a classification.
        "live", "idle",
    }

    def test_sources_with_a_category_get_one(self):
        for s in STORED_SOURCES:
            if s in self.NO_CATEGORY:
                continue
            with self.subTest(s):
                cat, _ = blocks._pick([_event(s, **SAMPLES[s])])
                self.assertIsNotNone(cat, f"{s} produced no category")

    def test_the_sources_without_a_category_still_have_none(self):
        """The reverse anchor. Giving live or idle a category would make every
        slot where somebody stepped away classify as an activity."""
        for s in sorted(self.NO_CATEGORY):
            with self.subTest(s):
                cat, _ = blocks._pick([_event(s, **SAMPLES[s])])
                self.assertIsNone(cat, f"{s} unexpectedly classified as {cat}")

    def test_the_categories_themselves(self):
        """Pinned individually, because the ladder's *order* is the design.

        Talking outranks coding, and containers (workout, transit) outrank both.
        A reordering that changed these would still leave every assertion above
        green.
        """
        expected = {"chrome": "browse", "claude": "code", "butler": "code",
                    "zsh": "code", "git": "code", "app": "app", "iphone": "app",
                    "media": "listen", "location": "place", "whoop": "sleep",
                    "weread": "read", "plaud": "talk"}
        got = {s: blocks._pick([_event(s, **SAMPLES[s])])[0] for s in expected}
        self.assertEqual(got, expected)


class PageVisibilityTest(unittest.TestCase):
    def test_every_source_has_at_least_one_lane(self):
        lanes = {src for _, src, _ in viz.LANES}
        missing = [s for s in STORED_SOURCES if s not in lanes]
        self.assertEqual(missing, [], f"no lane on the page: {missing}")

    def test_every_lane_declares_where_its_data_came_from(self):
        """The origin line under a lane name has to say who recorded it.

        Not a restatement of the lane name — "Claude Code <- Claude Code" says
        nothing. A lane with no origin renders a blank line where the provenance
        should be, which reads as "we do not know" rather than "nobody filled
        this in".
        """
        missing = [k for k, _, _ in viz.LANES if k not in viz.ORIGIN]
        self.assertEqual(missing, [], f"lanes with no origin: {missing}")

    def test_origin_short_line_is_not_a_restatement_of_the_lane(self):
        for key, (short, _long) in viz.ORIGIN.items():
            with self.subTest(key):
                self.assertNotEqual(short.strip().lower(), key.lower())


class DurationSemanticsTest(unittest.TestCase):
    def test_trusted_duration_sources_are_exactly_these(self):
        """Which durations mean attention, and which are an artefact.

        chrome's visit_duration is tab lifetime and reaches hours for a
        background tab (one real record: 17,942s); treating it as attention
        blows the timeline apart. This set is small on purpose and every
        addition needs an argument.
        """
        self.assertEqual(segment.trusted_duration_sources(),
                         {"app", "idle", "plaud"})

    def test_an_untrusted_duration_collapses_to_a_point(self):
        e = _event("chrome", kind="visit", title="x", dur=17_942_000)
        self.assertEqual(segment.end_of(e), e["ts"])

    def test_a_trusted_duration_is_kept(self):
        e = _event("app", kind="focus", bundle="us.zoom.xos", dur=1_800_000)
        self.assertEqual(segment.end_of(e), e["ts"] + 1_800_000)


class UndeclaredSourceTest(unittest.TestCase):
    """A source that declares nothing gets nothing — and that is the design.

    This class used to document the plugin blocker: four modules each kept their
    own list of source names, so a third-party source was silently half-present
    no matter what it did. That is fixed — the positive case now lives in
    test_plugin_source.py, where a real file dropped into the config directory
    renders, classifies, gets a lane and declares its durations.

    What is pinned here is the other half: the default for a source that has not
    said anything. It must be absence, never a guess. A rendered line invented
    for an unknown record shape would be fed to the model as fact, and a
    category assigned by default would put the source on the ladder above ones
    that earned their place.
    """

    UNDECLARED = _event("strava", kind="activity", sport="run", distance_km=8)

    def test_it_renders_nothing(self):
        self.assertIsNone(digest.line({**self.UNDECLARED, "_min": 540}))

    def test_it_gets_no_category(self):
        cat, _ = blocks._pick([self.UNDECLARED])
        self.assertIsNone(cat)

    def test_it_gets_no_lane(self):
        self.assertNotIn("strava", {src for _, src, _ in viz.LANES})

    def test_its_durations_are_not_trusted(self):
        """The safe direction: an understated duration loses resolution, an
        overstated one invents time that was never spent."""
        e = dict(self.UNDECLARED, dur_ms=1_800_000)
        self.assertEqual(segment.end_of(e), e["ts"])


if __name__ == "__main__":
    unittest.main()
