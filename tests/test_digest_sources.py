"""What each source looks like by the time the model sees it.

These are **characterization tests**, written deliberately before the refactor
that moves the per-source rendering out of digest.line()'s if/elif chain and into
declarations owned by each source module. That refactor must not change one byte
of what the model receives, and the only way to know is to pin the current output
first. Written afterwards, these tests would pin whatever the refactor produced,
regressions included, which is worth nothing as a safety net.

The failure this guards against is the worst kind for this project. If a source
stops rendering, nothing raises: the digest is simply a little shorter, the model
judges a little worse, and no counter anywhere goes down. digest.py's docstring
calls this silent fabrication and lists the three times it has already happened
(WHOOP recovery read as a workout, weread daily read as an instant, location gap
read as a place). The negative cases below are exactly that list.

Six sources — claude, butler, git, app, media, intent — had no coverage here at
all before this file, which is to say half of what the model reads was
unverified.
"""

import unittest

from umbra import digest


def line(source, kind="", minute=540, **payload):
    """Render one event the way digest.render would.

    _min is the minute-of-day the renderer uses for the timestamps it prints;
    540 is 09:00, which keeps the expected strings readable.
    """
    e = {"ts": 0, "dur_ms": payload.pop("dur", 0), "source": source,
         "kind": kind, "_min": minute, **payload}
    return digest.line(e)


class RenderedShapeTest(unittest.TestCase):
    """The exact string each source contributes.

    Pinned in full rather than by substring: the prefix glyph, the label and the
    separator are all part of what the model reads. It uses them to tell a
    dictated instruction from a page title, and changing one silently changes how
    a stretch of time is judged.
    """

    def test_claude_and_butler_both_read_as_dictated_instructions(self):
        """Both are the user talking to an AI, so both render identically.

        The prompt tells the model that "to AI" lines are dictated instructions
        and therefore work rather than chat. Two different prefixes here would
        make one of the two sources fall outside that instruction.
        """
        self.assertEqual(line("claude", "prompt", text="merge this branch into main"),
                         "💬 to AI: merge this branch into main")
        self.assertEqual(line("butler", "task", text="triage the alert email"),
                         "💬 to AI: triage the alert email")

    def test_git(self):
        self.assertEqual(
            line("git", "commit", project="Umbra", action="fix the cache key"),
            "⎇ git Umbra: fix the cache key")

    def test_foreground_app_is_named_and_says_which_device(self):
        """"computer" versus "phone" is load-bearing.

        The same bundle id in the foreground means different things on the two
        devices, and the model is asked to reconstruct one timeline across both.
        """
        self.assertEqual(line("app", "focus", bundle="us.zoom.xos"),
                         "📱 computer foreground: Zoom")
        self.assertEqual(line("iphone", "focus", bundle="us.zoom.xos"),
                         "📱 phone foreground: Zoom")

    def test_media(self):
        self.assertEqual(line("media", "play", title="Dune", track="Chapter 12"),
                         "🎧 listening: Dune — Chapter 12")

    def test_intent_carries_the_action_and_never_the_content(self):
        """intent is collected as actions only; there is no message body to leak.

        Pinned here as well as at the importer because this is the last point
        before the text reaches a model — and, if the page is deployed, the
        internet.
        """
        got = line("intent", "send", bundle="com.apple.MobileSMS",
                   verb="sendMessage")
        self.assertEqual(got, "✉️ Messages · sendMessage")

    def test_speech_carries_the_speaker(self):
        """Who was talking separates a lecture from a discussion."""
        self.assertEqual(line("plaud", "speech", speaker="Speaker 1", text="hello"),
                         "🗣 Speaker 1: hello")

    def test_terminal_and_idle(self):
        self.assertEqual(line("zsh", "command", cmd="pytest -q"), "$ pytest -q")
        self.assertEqual(line("idle", "away"),
                         "💤 no key or mouse input in this stretch")


class NoPositionInTimeTest(unittest.TestCase):
    """Records with no position in time must not appear at all.

    Every one of these has already been rendered wrongly once, and the shape was
    identical each time: the source distinguishes "something that happened at a
    moment" from "a summary of a day" through its kind, and the code handled four
    kinds with a binary else. The model believed the result in every case.
    """

    def test_whoop_recovery_is_not_a_workout(self):
        """A recovery score is a number for the day, not a stretch of activity.
        Rendered, it produced a "workout" span out of nowhere."""
        self.assertIsNone(line("whoop", "recovery", score=66))

    def test_whoop_cycle_is_not_a_moment(self):
        self.assertIsNone(line("whoop", "cycle", strain=12.5))

    def test_weread_daily_total_is_not_a_moment(self):
        """"73 minutes of reading that day" has no clock time attached.
        Rendered, it claimed reading at midnight Beijing time — every day."""
        self.assertIsNone(line("weread", "daily", minutes=73))

    def test_location_gap_is_not_a_place(self):
        """A gap means the data stopped, so the person could be anywhere.
        Rendered, it produced "at ?" — which reads as a place called ?."""
        self.assertIsNone(line("location", "gap", dur=3600_000))

    def test_empty_text_produces_no_line(self):
        """An empty transcript segment is not a silent utterance."""
        for src, kind in (("plaud", "speech"), ("claude", "prompt"),
                          ("chrome", "visit")):
            with self.subTest(src):
                self.assertIsNone(line(src, kind, text="", title=""))


class OngoingSpanTest(unittest.TestCase):
    """Records with a real duration say so, and say when they started."""

    def test_sleep_states_its_span(self):
        got = line("whoop", "sleep", dur=7 * 3600_000)
        self.assertEqual(got, "⌚ WHOOP: asleep (09:00 continuing until 16:00)")

    def test_stay_states_its_span(self):
        got = line("location", "stay", place="home", dur=3600_000)
        self.assertEqual(got, "📍 at: home (09:00 continuing until 10:00)")

    def test_a_span_shorter_than_a_minute_is_not_annotated(self):
        """Below a minute the start/end annotation is noise, not information."""
        self.assertEqual(line("location", "stay", place="home", dur=30_000),
                         "📍 at: home")


class BrowserWindowTitleTest(unittest.TestCase):
    """live/window duplicates chrome/visit for browsers, and only for browsers."""

    def test_chrome_window_titles_are_dropped(self):
        """A day carries ~1,700 of these and they say nothing chrome/visit does
        not already say, with a " - Google Chrome" suffix added."""
        self.assertIsNone(
            line("live", "window", app="Google Chrome", title="Umbra - GitHub"))

    def test_non_browser_window_titles_are_kept(self):
        """This is the only place some evidence exists at all.

        The hard evidence behind judging a stretch as "in a Zoom work meeting"
        was the window title "Zoom Meeting", which appears in no other source.
        """
        self.assertEqual(line("live", "window", app="zoom.us", title="Zoom Meeting"),
                         "▢ window: zoom.us — Zoom Meeting")


class UnknownSourceTest(unittest.TestCase):
    """A source digest.line has never heard of renders as nothing.

    This is the current behaviour and the reason the refactor matters: a
    community source can land rows in the database and still be **completely
    invisible to the model**, with no error and nothing in any log. The
    assertion is here so that when per-source rendering moves into the source
    modules, this test is the one that has to change — deliberately.
    """

    def test_a_third_party_source_contributes_no_line_today(self):
        self.assertIsNone(line("strava", "activity", sport="run", distance_km=8))


if __name__ == "__main__":
    unittest.main()
