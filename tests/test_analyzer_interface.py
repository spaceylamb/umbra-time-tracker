"""The Analyzer interface: a prompt and a parser, and the core does the rest.

A plugin declares what it reads, how it slices a day, what it asks, and how to
read the answer. Windowing, rendering, the cache, concurrency, the model call,
storage and incrementality are the core's — and the incrementality is the reason
that split matters. Digest-as-cache-key is the most valuable and most
error-prone mechanism in this codebase, and its failure mode is silent: a plugin
that gets it wrong does not raise, it re-calls the model every round and bills
the user.

The end-to-end cases here write a real plugin file into a real directory and run
it. Nothing is monkeypatched into place, because the failure worth guarding
against is "the declaration looks right and nothing picks it up".
"""

import os
import sqlite3
import tempfile
import textwrap
import unittest
from unittest import mock

from umbra import analyzer, config, db, registry


class ResolveInputTest(unittest.TestCase):
    def test_the_three_layers(self):
        self.assertEqual(analyzer.resolve_input("spans"), ("spans", None))
        self.assertEqual(analyzer.resolve_input("day_events"), ("day_events", None))
        self.assertEqual(analyzer.resolve_input("events:plaud"), ("events", "plaud"))

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(analyzer.resolve_input("  spans "), ("spans", None))

    def test_an_unknown_layer_raises_rather_than_being_skipped(self):
        """A typo is a plugin trying to participate and failing, which is a
        different fact from a module that never declared itself an Analyzer —
        and only one of them wants telling."""
        with self.assertRaises(analyzer.BadDeclaration):
            analyzer.resolve_input("spanz")

    def test_events_without_a_source_raises(self):
        with self.assertRaises(analyzer.BadDeclaration):
            analyzer.resolve_input("events")
        with self.assertRaises(analyzer.BadDeclaration):
            analyzer.resolve_input("events:")

    def test_a_non_string_raises(self):
        for bad in (None, 30, ["spans"]):
            with self.subTest(bad):
                with self.assertRaises(analyzer.BadDeclaration):
                    analyzer.resolve_input(bad)


class ResolveWindowTest(unittest.TestCase):
    def test_thirty_minutes_covers_the_day_with_no_gaps_or_overlaps(self):
        w = analyzer.resolve_window(30)
        self.assertEqual(len(w), 48)
        self.assertEqual(w[0], (0, 30))
        self.assertEqual(w[-1], (1410, 1440))
        for (_, prev_end), (nxt_start, _) in zip(w, w[1:]):
            self.assertEqual(prev_end, nxt_start)

    def test_day_is_one_window_over_the_whole_day(self):
        self.assertEqual(analyzer.resolve_window("day"), [(0, 1440)])

    def test_a_day_is_the_largest_slice(self):
        """Anything wider makes one part of a period invalidate another's cache.

        A week-wide key means Friday's new data re-judges Monday, so a number
        read as 11.3 hours on Wednesday becomes 10.8 on Friday with nothing
        having changed.
        """
        with self.assertRaises(analyzer.BadDeclaration):
            analyzer.resolve_window("week")

    def test_a_window_that_does_not_divide_the_day_raises(self):
        """A partial trailing window would be judged against less input than
        every other window of the day, and nothing downstream would say so."""
        with self.assertRaises(analyzer.BadDeclaration):
            analyzer.resolve_window(50)

    def test_zero_and_negative_and_bool_raise(self):
        for bad in (0, -30, True):
            with self.subTest(bad):
                with self.assertRaises(analyzer.BadDeclaration):
                    analyzer.resolve_window(bad)


class RenderWindowTest(unittest.TestCase):
    """The default rendering: each event through its own source.

    The per-event line belongs to the source — that is already how the inference
    digest is built, and reusing it stops a second opinion growing about how a
    chrome visit reads.
    """

    def _ev(self, minute, source="zsh", **kw):
        return {"ts": minute * 60000, "dur_ms": 0, "source": source,
                "kind": kw.pop("kind", "command"), **kw}

    def test_events_render_through_their_source(self):
        got = analyzer.render_window([self._ev(545, cmd="pytest -q")],
                                     540, 570, 0)
        self.assertIn("$ pytest -q", got)

    def test_the_target_stretch_is_marked_off_from_its_context(self):
        """Without the separator, a model asked about one half hour answers
        about the whole window."""
        got = analyzer.render_window(
            [self._ev(535, cmd="before"), self._ev(545, cmd="inside"),
             self._ev(575, cmd="after")], 540, 570, 0, ctx=30)
        self.assertIn(analyzer.TARGET_MARK, got)
        self.assertIn(analyzer.AFTER_MARK, got)
        self.assertLess(got.index("before"), got.index(analyzer.TARGET_MARK))
        self.assertLess(got.index(analyzer.TARGET_MARK), got.index("inside"))
        self.assertLess(got.index(analyzer.AFTER_MARK), got.index("after"))

    def test_zero_context_shows_only_the_target(self):
        got = analyzer.render_window(
            [self._ev(535, cmd="before"), self._ev(545, cmd="inside")],
            540, 570, 0, ctx=0)
        self.assertNotIn("before", got)
        self.assertIn("inside", got)

    def test_a_window_with_nothing_in_the_target_renders_empty(self):
        """Asking a model to judge a stretch it has no records for is paying it
        to guess."""
        got = analyzer.render_window([self._ev(535, cmd="only context")],
                                     540, 570, 0, ctx=30)
        self.assertEqual(got, "")

    def test_a_source_that_renders_nothing_contributes_nothing(self):
        got = analyzer.render_window(
            [self._ev(545, source="whoop", kind="recovery", score=66),
             self._ev(546, cmd="real")], 540, 570, 0)
        self.assertIn("real", got)
        self.assertNotIn("66", got)

    def test_an_unknown_source_contributes_nothing_rather_than_raising(self):
        got = analyzer.render_window(
            [self._ev(545, source="strava", kind="activity"),
             self._ev(546, cmd="real")], 540, 570, 0)
        self.assertIn("real", got)

    def test_lines_come_out_in_time_order(self):
        got = analyzer.render_window(
            [self._ev(550, cmd="later"), self._ev(545, cmd="earlier")],
            540, 570, 0)
        self.assertLess(got.index("earlier"), got.index("later"))


PLUGIN = textwrap.dedent('''
    INPUT  = "events:zsh"
    WINDOW = 30
    PROMPT = "day={day} window={a}-{b}\\n{text}"

    def parse(items, window):
        return [{"a": i.get("a", 0), "b": i.get("b", 0), "note": i.get("note")}
                for i in items]
''')

PLUGIN_WITH_PARAMS = textwrap.dedent('''
    INPUT  = "events:zsh"
    WINDOW = "day"
    PROMPT = "goals={goals}\\n{text}"

    def params(con, day, window):
        return {"goals": ["ship it"]}

    def parse(items, window):
        return list(items)
''')


class _Base(unittest.TestCase):
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

    DAY = "2026-08-27"

    def events(self, *minutes):
        """Events at real minutes of DAY, not at epoch offsets.

        day_bounds() resolves the day in local time, so a ts of minute*60000
        lands in 1970 and load_range returns nothing — which shows up as every
        window rendering empty, not as an error.
        """
        from umbra import analyze
        lo, _hi = analyze.day_bounds(self.DAY)
        rows = [(f"zsh:{m}", lo + m * 60000, None, "zsh", "command",
                 {"cmd": f"cmd{m}"}) for m in minutes]
        db.insert(self.con, rows)


class DiscoveryTest(_Base):
    def test_a_declaring_plugin_is_found(self):
        self.write("demo", PLUGIN)
        self.assertIn("demo", registry.analyzers())

    def test_a_plugin_with_no_input_is_not_an_analyzer(self):
        """Not an error — a Sink-only plugin is a legitimate thing to write."""
        self.write("sinkonly", "def send(item):\n    return None\n")
        self.assertIn("sinkonly", registry.plugins()[0])
        self.assertNotIn("sinkonly", registry.analyzers())

    def test_an_unimportable_plugin_is_reported_not_silently_skipped(self):
        self.write("broken", "this is not valid python (\\n")
        _found, errors = registry.plugins()
        self.assertIn("broken", errors)

    def test_one_broken_plugin_does_not_hide_the_others(self):
        self.write("broken", "this is not valid python (\\n")
        self.write("demo", PLUGIN)
        self.assertIn("demo", registry.analyzers())

    def test_a_broken_plugin_does_not_break_source_rendering(self):
        """Rendering runs once per event, tens of thousands of times a day. An
        exception there would not break one plugin, it would empty the digest for
        every source on every day."""
        self.write("broken", "raise RuntimeError('boom')\\n")
        from umbra import digest
        self.assertEqual(
            digest.line({"ts": 0, "dur_ms": 0, "source": "zsh",
                         "kind": "command", "cmd": "ls", "_min": 540}),
            "$ ls")


class RunTest(_Base):
    def _run(self, name="demo", reply='[{"a": 545, "b": 546, "note": "x"}]',
             **kw):
        calls = []

        def call(prompt):
            calls.append(prompt)
            return reply

        mod = registry.analyzers()[name]
        r = analyzer.run(self.con, name, mod, self.DAY, "m", call=call, **kw)
        return r, calls

    def test_it_runs_end_to_end_and_stores_rows(self):
        self.write("demo", PLUGIN)
        self.events(545)
        r, calls = self._run()
        self.assertEqual(len(calls), 1)
        rows = analyzer.load_rows(self.con, "demo", self.DAY, "m")
        self.assertEqual([x["note"] for x in rows], ["x"])
        self.assertEqual(r["rows"], 1)

    def test_windows_with_no_input_are_never_sent(self):
        """47 empty windows of a day are 47 calls nobody should pay for."""
        self.write("demo", PLUGIN)
        self.events(545)
        r, calls = self._run()
        self.assertEqual(r["windows"], 48)
        self.assertEqual(r["empty"], 47)
        self.assertEqual(len(calls), 1)

    def test_an_unchanged_second_round_makes_no_calls(self):
        self.write("demo", PLUGIN)
        self.events(545)
        self._run()
        r, calls = self._run()
        self.assertEqual(calls, [])
        self.assertEqual(r["reused"], 1)

    def test_new_input_in_a_window_re_calls_only_that_window(self):
        self.write("demo", PLUGIN)
        self.events(545)
        self._run()
        self.events(575)                       # a second window gains input
        r, calls = self._run()
        self.assertEqual(len(calls), 1)
        self.assertEqual(r["reused"], 1)

    def test_fresh_bypasses_the_cache(self):
        self.write("demo", PLUGIN)
        self.events(545)
        self._run()
        _r, calls = self._run(fresh=True)
        self.assertEqual(len(calls), 1)

    def test_declared_params_reach_the_prompt_and_the_cache_key(self):
        self.write("goalish", PLUGIN_WITH_PARAMS)
        self.events(545)
        _r, calls = self._run("goalish", reply='[{"a":1,"b":2}]')
        self.assertIn("ship it", calls[0])

    def test_a_failing_window_is_counted_and_the_day_continues(self):
        self.write("demo", PLUGIN)
        self.events(545, 575)
        seen = []

        def call(prompt):
            seen.append(prompt)
            return "not json" if len(seen) == 1 else '[{"a":1,"b":2,"note":"ok"}]'

        r = analyzer.run(self.con, "demo", registry.analyzers()["demo"],
                         self.DAY, "m", call=call)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["rows"], 1)

    def test_a_failed_window_is_retried_next_round(self):
        """Caching a failure as a success means that stretch is never looked at
        again."""
        self.write("demo", PLUGIN)
        self.events(545)
        analyzer.run(self.con, "demo", registry.analyzers()["demo"],
                     self.DAY, "m", call=lambda p: "not json")
        _r, calls = self._run()
        self.assertEqual(len(calls), 1)

    def test_a_window_the_model_answered_nothing_for_is_not_re_asked(self):
        self.write("demo", PLUGIN)
        self.events(545)
        self._run(reply="[]")
        r, calls = self._run(reply="[]")
        self.assertEqual(calls, [])
        self.assertEqual(r["reused"], 1)

    def test_a_bad_declaration_raises_with_the_plugin_named(self):
        self.write("bad", 'INPUT = "spanz"\nWINDOW = 30\nPROMPT = "{text}"\n')
        with self.assertRaises(analyzer.BadDeclaration):
            analyzer.run(self.con, "bad", registry.analyzers()["bad"],
                         self.DAY, "m", call=lambda p: "[]")

    def test_input_filters_to_the_declared_source(self):
        self.write("demo", PLUGIN)
        self.events(545)
        from umbra import analyze
        lo, _hi = analyze.day_bounds(self.DAY)
        db.insert(self.con, [("chrome:1", lo + 546 * 60000, None, "chrome",
                              "visit", {"title": "a page"})])
        _r, calls = self._run()
        self.assertIn("cmd545", calls[0])
        self.assertNotIn("a page", calls[0])


if __name__ == "__main__":
    unittest.main()


class InferredLayersTest(_Base):
    """A plugin reading `spans` or `day_events` gets text, with no renderer of
    its own.

    Both layers are advertised in the interface and neither has a source behind
    it — they are Umbra's own output, so nothing in the source registry can
    render them. Before the core learned to, a plugin declaring INPUT = "spans"
    got an empty string for every window of every day: `empty 48` where
    `called 48` belonged, nothing written, nothing raised. No bundled plugin
    happened to read either layer through the default renderer, which is exactly
    why this is pinned here.
    """

    SPANS = textwrap.dedent('''
        INPUT  = "spans"
        WINDOW = "day"
        PROMPT = "{text}"

        def parse(items, window):
            return list(items)
    ''')

    EVENTS = SPANS.replace('INPUT  = "spans"', 'INPUT  = "day_events"')

    def spans(self):
        from umbra import inference as INF
        INF.save(self.con, self.DAY, "m", [
            {"start": "09:00", "end": "10:30", "cat": "code",
             "activity": "refactoring the analyzer",
             "also": ["reading the plan"]}])

    def day_events(self):
        from umbra import inference as INF
        INF.save_events(self.con, self.DAY, "m", [
            {"start": "09:00", "end": "10:30", "cat": "social",
             "title": "team offsite"}])

    def _text(self, name):
        mod = registry.analyzers()[name]
        wins = analyzer.windows(self.con, mod, self.DAY, "m")
        return wins[0]["text"] if wins else ""

    def test_a_span_renders(self):
        self.write("s", self.SPANS)
        self.spans()
        self.assertIn("refactoring the analyzer", self._text("s"))

    def test_a_span_carries_its_start_end_and_category(self):
        self.write("s", self.SPANS)
        self.spans()
        text = self._text("s")
        self.assertIn("09:00", text)
        self.assertIn("until 10:30", text)
        self.assertIn("code", text)

    def test_what_was_happening_in_parallel_is_not_dropped(self):
        """"also" is how a stretch counts for something that was not its
        headline. Dropping it silently undercounts, and reads as the model
        having decided."""
        self.write("s", self.SPANS)
        self.spans()
        self.assertIn("also: reading the plan", self._text("s"))

    def test_a_day_event_renders_by_its_title(self):
        self.write("e", self.EVENTS)
        self.day_events()
        self.assertIn("team offsite", self._text("e"))

    def test_a_day_with_no_inferences_is_empty_not_an_error(self):
        self.write("s", self.SPANS)
        self.assertEqual(self._text("s"), "")


class CurrentTest(_Base):
    """`current` is what a view asks before drawing a stored number.

    It has to cover both ways an answer goes stale — new data in the window, and
    a changed parameter the window does not contain — because a number that
    silently stops matching its input is worse than no number.
    """

    PLUGIN = textwrap.dedent('''
        import os

        INPUT  = "events:zsh"
        WINDOW = 720
        PROMPT = "{text}{extra}"

        def params(con, day, window):
            return {"extra": os.environ.get("EXTRA", "")}

        def parse(items, window):
            return list(items)
    ''')

    def _run(self):
        return analyzer.run(self.con, "p", registry.analyzers()["p"],
                            self.DAY, "m", call=lambda _p: "[]")

    def setUp(self):
        super().setUp()
        self.write("p", self.PLUGIN)
        self.events(545)

    def test_not_run_is_not_current(self):
        self.assertFalse(
            analyzer.current(self.con, "p", registry.analyzers()["p"],
                             self.DAY, "m"))

    def test_run_is_current(self):
        self._run()
        self.assertTrue(
            analyzer.current(self.con, "p", registry.analyzers()["p"],
                             self.DAY, "m"))

    def test_new_data_in_the_window_makes_it_stale(self):
        self._run()
        self.events(600)
        self.assertFalse(
            analyzer.current(self.con, "p", registry.analyzers()["p"],
                             self.DAY, "m"))

    def test_a_changed_parameter_makes_it_stale(self):
        """The half a window-only key cannot see. Goal attribution is the case
        that found it: edit a goal and the stored hours are about text you have
        already rewritten."""
        self._run()
        with mock.patch.dict(os.environ, {"EXTRA": "changed"}):
            self.assertFalse(
                analyzer.current(self.con, "p", registry.analyzers()["p"],
                                 self.DAY, "m"))

    def test_a_day_with_nothing_to_ask_is_current(self):
        """It has no outstanding question. Reporting it as pending leaves a
        warning on screen that no amount of running could ever clear."""
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        self.assertTrue(
            analyzer.current(con, "p", registry.analyzers()["p"],
                             self.DAY, "m"))


class NoQuestionTest(_Base):
    """A declared `params` returning None says there is no question here.

    Distinct from `{}`, which says "no parameters this window". Goal attribution
    found the difference: a day whose week has no goals has plenty of input and
    nothing to ask about it, and asking which of nothing a stretch advanced is
    paying for an empty answer.
    """

    PLUGIN = textwrap.dedent('''
        import os

        INPUT  = "events:zsh"
        WINDOW = 720
        PROMPT = "{text}"

        def params(con, day, window):
            return None if os.environ.get("MUTE") else {}

        def parse(items, window):
            return list(items)
    ''')

    def setUp(self):
        super().setUp()
        self.write("p", self.PLUGIN)
        self.events(545)

    def _run(self):
        calls = []
        r = analyzer.run(self.con, "p", registry.analyzers()["p"], self.DAY,
                         "m", call=lambda p: calls.append(p) or "[]")
        return r, calls

    def test_none_asks_nothing(self):
        with mock.patch.dict(os.environ, {"MUTE": "1"}):
            r, calls = self._run()
        self.assertEqual(calls, [])
        self.assertEqual(r["called"], 0)

    def test_none_is_counted_as_a_window_with_nothing_in_it(self):
        with mock.patch.dict(os.environ, {"MUTE": "1"}):
            r, _ = self._run()
        self.assertEqual((r["windows"], r["empty"]), (2, 2))

    def test_none_writes_nothing_so_the_answer_stays_open(self):
        """Not a recorded empty answer. Nothing was asked, and a day that was
        never judged must not read as a day that advanced nothing."""
        with mock.patch.dict(os.environ, {"MUTE": "1"}):
            self._run()
        self.assertEqual(analyzer.ran(self.con, "p", self.DAY, "m"), set())

    def test_an_empty_dict_still_asks(self):
        r, calls = self._run()
        self.assertEqual(len(calls), 1)
        self.assertEqual(r["called"], 1)
