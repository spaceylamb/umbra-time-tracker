"""What speech counts as in the five-minute block view.

This layer arrived only after the speech source did, so at first plaud was in
neither CONTENT nor any LADDER rule — and slots with someone talking showed as
blank.

Two judgements are the whole point of this file:

  1. **talk must not go into CONTENT.** That tuple does two jobs: classifying
     LADDER's "code", and making up MAC_ACTIVE ("someone is working the Mac").
     Speech is neither — labelling it code is wrong, and counting it as Mac
     activity is worse: talking happens precisely **away from the computer**,
     which is the reason to read this source.
  2. **talk ranks above code and below transit/workout.** The old rule, the
     container first: talking while driving, driving is the container; but
     typing a few lines during a meeting at the computer, "talking" explains
     those five minutes better than "coding".
"""

import unittest

from umbra import blocks

LO = 1786800000000
MIN = 60_000


def ev(min_from_midnight, source, kind="x", dur_min=0, **payload):
    return {"ts": LO + min_from_midnight * MIN, "dur_ms": dur_min * MIN,
            "source": source, "kind": kind, **payload}


def speech(minute, text="今天先对一下排期", speaker="Speaker 1", dur_min=1):
    return ev(minute, "plaud", "speech", dur_min, text=text, speaker=speaker)


def at(bs, minute):
    return bs[minute // blocks.SLICE_MIN]


class TalkCategoryTest(unittest.TestCase):
    def test_speech_gets_its_own_category(self):
        b = at(blocks.blocks([speech(600)], LO), 600)
        self.assertEqual(b["cat"], "talk")

    def test_label_says_what_was_said(self):
        """Like browse using the page title — the label carries the part with
        information."""
        b = at(blocks.blocks([speech(600, text="把 PLAUD 接进时间线")], LO), 600)
        self.assertIn("PLAUD", b["label"])

    def test_why_records_the_source(self):
        b = at(blocks.blocks([speech(600)], LO), 600)
        self.assertIn("plaud", b["why"])


class LadderPositionTest(unittest.TestCase):
    def test_talk_beats_code(self):
        """A meeting at the computer: "talking" explains those five minutes
        better than "coding"."""
        bs = blocks.blocks([ev(600, "claude", "prompt", project="shadow"),
                            speech(600)], LO)
        self.assertEqual(at(bs, 600)["cat"], "talk")

    def test_transit_still_wins(self):
        """Talking while driving: driving is the container — the same reason as
        "listening to a book while driving"."""
        bs = blocks.blocks([ev(600, "location", "transit", dur_min=30,
                               **{"from": "家", "to": "公司", "mode": "driving"}),
                            speech(600)], LO)
        self.assertEqual(at(bs, 600)["cat"], "transit")

    def test_workout_still_wins(self):
        bs = blocks.blocks([ev(600, "whoop", "workout", dur_min=30, sport="Running"),
                            speech(600)], LO)
        self.assertEqual(at(bs, 600)["cat"], "workout")

    def test_talk_shows_up_as_a_parallel_activity(self):
        """There can be one main label, but "also talking" must not be lost."""
        bs = blocks.blocks([ev(600, "location", "transit", dur_min=30,
                               **{"from": "家", "to": "公司", "mode": "driving"}),
                            speech(600, text="路上先聊两句")], LO)
        self.assertTrue(any("聊两句" in a for a in at(bs, 600)["also"]))


class NotMacActivityTest(unittest.TestCase):
    """This group matters most in the file."""

    def test_plaud_is_not_a_content_source(self):
        """In CONTENT it would be labelled "code"."""
        self.assertNotIn("plaud", blocks.CONTENT)

    def test_plaud_is_not_mac_activity(self):
        self.assertNotIn("plaud", blocks.MAC_ACTIVE)

    def test_talking_while_the_mac_is_idle_is_not_a_conflict(self):
        """Talking to someone away from the computer — an entirely normal day,
        not sources disagreeing.

        In MAC_ACTIVE, every "in a meeting while nobody touched the Mac" slot
        would be flagged as a conflict. That would drown the conflict signal —
        which exists to catch "one of the sources is lying".
        """
        bs = blocks.blocks([ev(600, "idle", "idle", dur_min=30), speech(600)], LO)
        self.assertEqual(at(bs, 600)["conflict"], [])

    def test_talking_while_asleep_surfaces_the_sleep_claim(self):
        """When talking outranks sleep, the sleep claim must still be visible.

        This follows main's newer rule ("whenever WHOOP says asleep and the main
        label is not sleep, say so") — and speech is exactly the case it guards:
        talking at 7am while WHOOP says asleep until 7:57 means either you woke
        early or the sleep boundary is wrong, and both are worth seeing.
        Swallowing the sleep quietly is the mistake.

        Note this is **not** "speech treated as Mac activity" — the two tests
        below guard that, by an entirely different test (MAC_ACTIVE).
        """
        bs = blocks.blocks([ev(400, "whoop", "sleep", dur_min=120), speech(420)], LO)
        self.assertEqual(at(bs, 420)["cat"], "talk")
        self.assertIn("WHOOP says this stretch was sleep", at(bs, 420)["conflict"])

    def test_sleep_alone_is_not_a_conflict(self):
        """With nothing outranking it, sleep is sleep; it must not conflict with
        itself."""
        bs = blocks.blocks([ev(400, "whoop", "sleep", dur_min=120)], LO)
        self.assertEqual(at(bs, 420)["conflict"], [])

    def test_talking_on_the_road_is_not_a_conflict(self):
        bs = blocks.blocks([ev(600, "location", "transit", dur_min=30,
                               **{"from": "家", "to": "公司"}),
                            speech(600)], LO)
        self.assertEqual(at(bs, 600)["conflict"], [])

    def test_a_real_conflict_still_fires(self):
        """Don't switch conflict detection off altogether — idle plus real Mac
        activity is still reported."""
        bs = blocks.blocks([ev(600, "idle", "idle", dur_min=30),
                            ev(600, "zsh", "command", cmd="ls")], LO)
        self.assertTrue(at(bs, 600)["conflict"])


if __name__ == "__main__":
    unittest.main()
