"""A day cut into five-minute slots, each saying what was going on.

Asked for on 2026-08-16: to see where a day's time really went, so fixed slots
rather than segments by event density — which would shrink "two hours of
nothing" to one line.

The rules were set by the user too:
  · leave it empty when it cannot be judged
  · **when many sources still cannot decide, say so specifically**

The second is what matters most in this module. Measured over 2026-08-15's
288 slots, those "with events but no content-level evidence" were **0** —
idle / whoop / location cover continuously, so every slot has at least them.
The real ambiguity is not blank but **contradiction**:

    chrome + idle   11 slots   the browser has visits, but the Mac had no input
                               — you reading, or a background tab refreshing?

Blanks fill in as more sources join; a contradiction means one source present is
lying, and more sources will not make it go away.
"""

import unittest

from umbra import blocks

LO = 1786800000000          # some day at 00:00
MIN = 60_000


def ev(min_from_midnight, source, kind="x", dur_min=0, **payload):
    return {"ts": LO + min_from_midnight * MIN, "dur_ms": dur_min * MIN,
            "source": source, "kind": kind, **payload}


def at(bs, minute):
    return bs[minute // blocks.SLICE_MIN]


class ShapeTest(unittest.TestCase):
    def test_a_day_is_288_blocks(self):
        self.assertEqual(len(blocks.blocks([], LO)), 1440 // blocks.SLICE_MIN)

    def test_every_slot_carries_its_time(self):
        bs = blocks.blocks([], LO)
        self.assertEqual(bs[0]["t"], "00:00")
        self.assertEqual(bs[113]["t"], "09:25")
        self.assertEqual(bs[-1]["t"], "23:55")

    def test_a_slot_without_events_is_empty(self):
        """Empty is empty. Forcing a label is inventing one."""
        b = at(blocks.blocks([], LO), 500)
        self.assertIsNone(b["cat"])
        self.assertEqual(b["why"], [])


class LadderTest(unittest.TestCase):
    """The evidence ladder: says what you were doing > only where > unsure."""

    def test_sleep(self):
        bs = blocks.blocks([ev(0, "whoop", "sleep", dur_min=420)], LO)
        self.assertEqual(at(bs, 100)["cat"], "sleep")

    def test_workout(self):
        bs = blocks.blocks([ev(600, "whoop", "workout", dur_min=40,
                               sport="running")], LO)
        self.assertEqual(at(bs, 610)["cat"], "workout")

    def test_transit(self):
        bs = blocks.blocks([ev(400, "location", "transit", dur_min=60,
                               **{"from": "家", "to": "公司", "mode": "driving"})], LO)
        b = at(bs, 430)
        self.assertEqual(b["cat"], "transit")
        self.assertIn("家", b["label"])
        self.assertIn("公司", b["label"])

    def test_coding_beats_knowing_only_the_app(self):
        """A Claude conversation and a foreground app in one slot — the former
        says what was being done."""
        bs = blocks.blocks([
            ev(500, "app", "focus", dur_min=5, app="Terminal"),
            ev(501, "claude", "prompt", text="把这个分支合进 main", project="Umbra"),
        ], LO)
        b = at(bs, 502)
        self.assertEqual(b["cat"], "code")
        self.assertIn("Umbra", b["label"])

    def test_listening(self):
        bs = blocks.blocks([ev(300, "media", "playing", dur_min=20,
                               title="一本历史有声书", track="第I节",
                               app="weread")], LO)
        b = at(bs, 310)
        self.assertEqual(b["cat"], "listen")
        self.assertIn("历史有声书", b["label"])

    def test_browsing(self):
        bs = blocks.blocks([ev(500, "chrome", "visit",
                               title="GitHub - example/Umbra")], LO)
        self.assertEqual(at(bs, 500)["cat"], "browse")

    def test_only_the_app_is_known(self):
        bs = blocks.blocks([ev(500, "iphone", "focus", dur_min=5, app="微信")], LO)
        b = at(bs, 502)
        self.assertEqual(b["cat"], "app")
        self.assertIn("微信", b["label"])

    def test_only_the_place_is_known(self):
        """Location knows you were home but not what you did there — container
        level, not tier A."""
        bs = blocks.blocks([ev(500, "location", "stay", dur_min=60,
                               place="家", named=True)], LO)
        b = at(bs, 520)
        self.assertEqual(b["cat"], "place")
        self.assertIn("家", b["label"])


class LabelTest(unittest.TestCase):
    """Labels have to be readable. An unreadable label is no label — and no
    sanity check is possible."""

    def test_claude_uses_the_real_project_name(self):
        bs = blocks.blocks([ev(500, "claude", "prompt", text="x",
                               project="spaceyResearch")], LO)
        self.assertIn("spaceyResearch", at(bs, 500)["label"])

    def test_butler_shows_no_uuid(self):
        """butler's project holds a session id (46cd708f-054c-4dcb-…) and title
        is mostly empty. With no readable name, do not force one — a UUID is
        worse than nothing; it takes the space and says nothing."""
        b = at(blocks.blocks([ev(500, "butler", "task", provider="codex",
                                 project="46cd708f-054c-4dcb-9ce8-1889835d2ffd",
                                 title="")], LO), 500)
        self.assertNotIn("46cd708f", b["label"])
        self.assertIn("codex", b["label"])

    def test_butler_uses_the_title_when_there_is_one(self):
        b = at(blocks.blocks([ev(500, "butler", "task", provider="codex",
                                 project="uuid-xxx",
                                 title="整理导入流程")], LO), 500)
        self.assertIn("整理导入流程", b["label"])
        self.assertNotIn("uuid-xxx", b["label"])

    def test_no_label_ever_shows_a_uuid(self):
        """A backstop: an id field turning up in some future source must not leak
        into a label either."""
        import re
        UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
        for e in (ev(500, "butler", "task", provider="p", project="a1b2c3d4-e5f6-x"),
                  ev(500, "claude", "prompt", project="a1b2c3d4-e5f6-x", text="t")):
            lab = at(blocks.blocks([e], LO), 500)["label"]
            self.assertIsNone(UUID.search(lab), f"a UUID leaked into the label: {lab}")


class ParallelTest(unittest.TestCase):
    """Only one simultaneous thing can be the title, but the others must not be
    lost.

    Measured on the morning of 2026-08-16: driving to the beach while listening
    to an audiobook. Picking one rung, those 70 minutes were cut into "driving /
    reading / listening / driving" — reading as if they stopped to read on the
    way.

    So the main label is the **container** (transit, workout) and parallel
    activity goes in also. You cannot not drive; the audiobook sits on top.
    """

    def _both(self):
        return blocks.blocks([
            ev(400, "location", "transit", dur_min=70,
               **{"from": "家", "to": "海滩", "mode": "driving"}),
            ev(410, "media", "playing", dur_min=30, title="一本历史有声书",
               track="第I节"),
        ], LO)

    def test_the_main_label_is_transit(self):
        b = at(self._both(), 420)
        self.assertEqual(b["cat"], "transit")
        self.assertIn("海滩", b["label"])

    def test_the_audiobook_is_not_lost(self):
        b = at(self._both(), 420)
        self.assertTrue(b["also"], "the parallel activity was lost")
        self.assertIn("历史有声书", " ".join(b["also"]))

    def test_the_stretch_is_no_longer_chopped_up(self):
        cats = [b["cat"] for b in self._both()[80:94]]     # minutes 400–470
        self.assertEqual(set(cats), {"transit"}, f"chopped up: {cats}")

    def test_transit_beats_coding(self):
        """A real case (8-16 11:30–13:15): location says driving while Claude is
        running.

        The main label is "transit" — **location is a physical fact; the Claude
        events are the doubtful ones** (no typing while driving; most likely a
        background job on the Mac). The other way round, a commute would be
        labelled "coding" — exactly where checking is most needed.

        The previous test has transit without code, so swapping the two on the
        ladder would not turn it red — a mutation test caught that.
        """
        bs = blocks.blocks([
            ev(400, "location", "transit", dur_min=60,
               **{"from": "海滩", "to": "面馆", "mode": "driving"}),
            ev(420, "claude", "prompt", text="改一下", project="Umbra"),
        ], LO)
        b = at(bs, 420)
        self.assertEqual(b["cat"], "transit")
        self.assertIn("Claude", " ".join(b["also"]), "the coding was dropped entirely")
        self.assertTrue(b["conflict"], "typing code while driving should be a contradiction")

    def test_also_is_empty_without_parallel_activity(self):
        bs = blocks.blocks([ev(500, "zsh", "command", cmd="ls")], LO)
        self.assertEqual(at(bs, 500)["also"], [])

    def test_container_level_does_not_go_into_also(self):
        """"In some app" and "somewhere" are fallbacks, not parallel activities.
        Put into also, every slot would carry a string of filler."""
        bs = blocks.blocks([
            ev(500, "zsh", "command", cmd="ls"),
            ev(500, "iphone", "focus", dur_min=5, app="微信"),
            ev(500, "location", "stay", dur_min=60, place="家"),
        ], LO)
        self.assertEqual(at(bs, 500)["also"], [])


class SleepIsNeverHiddenTest(unittest.TestCase):
    """When a source said something plainly, the page must show what it said.

    A real case (2026-08-15 00:45): WHOOP's sleep ran 00:25→07:57, covering
    that slot; Git had a commit in it too. The ladder made "coding" the title,
    so the label showed only Git and **sleep vanished entirely** — and the user's
    first thought was "the WHOOP data didn't come in".

    The data was there all along (whoop in why, in conflict too); the display ate
    it. For this project it is the worst mistake to make: a whole day spent
    separating "no data" from "data not read", then mixing them up on screen.
    """

    def _both(self):
        return blocks.blocks([
            ev(25, "whoop", "sleep", dur_min=452),
            ev(45, "git", "reflog", action="commit", project="spaceyResearch"),
        ], LO)

    def test_sleep_shows_in_the_conflicts(self):
        cf = " ".join(at(self._both(), 45)["conflict"])
        self.assertIn("sleep", cf, "WHOOP says asleep, and neither label nor conflicts show it")

    def test_the_no_input_conflict_stays_too(self):
        """Both conflicts stay — keeping one is choosing the point for the user."""
        bs = blocks.blocks([
            ev(25, "whoop", "sleep", dur_min=452),
            ev(30, "idle", "away", dur_min=60),
            ev(45, "git", "reflog", action="commit", project="x"),
        ], LO)
        cf = at(bs, 45)["conflict"]
        self.assertGreaterEqual(len(cf), 2, f"conflicts were cut: {cf}")

    def test_listening_while_asleep_still_says_asleep(self):
        """The audiobook beats sleep for the title, but media is not "someone
        acting at the Mac", so the old logic raised no conflict — and sleep just
        silently vanished."""
        bs = blocks.blocks([
            ev(25, "whoop", "sleep", dur_min=452),
            ev(40, "media", "playing", dur_min=20, title="书", track="第一章"),
        ], LO)
        b = at(bs, 45)
        self.assertEqual(b["cat"], "listen")
        self.assertIn("sleep", " ".join(b["conflict"]), "sleep vanished entirely")

    def test_no_repeat_when_the_title_is_already_sleep(self):
        """With only WHOOP speaking the label is sleep itself; repeating it as a
        conflict is noise."""
        b = at(blocks.blocks([ev(25, "whoop", "sleep", dur_min=452)], LO), 100)
        self.assertEqual(b["cat"], "sleep")
        self.assertEqual(b["conflict"], [])


class DictationBlindSpotTest(unittest.TestCase):
    """idle measures HID events, and dictation produces none.

    A real case (2026-08-15 09:38:29): the user dictated a whole passage into the
    microphone and the clipboard caught the full text; 41 seconds later idle
    began, lasting until 09:44:54. That slot said "idle says no key or mouse
    input, but there was activity on the Mac", and the user asked: I was
    dictating — how is that no input?

    collector.py uses
        CGEventSourceSecondsSinceLastEventType(kCGAnyInputEventType)
    which counts only keyboard / mouse / trackpad / scroll wheel. macOS dictation
    goes through **the text input system** (insertText:) and synthesises no key
    events — so five minutes of talking to the microphone never resets that
    timer. As far as this signal can tell:

        five minutes talking to the microphone   ←→   five minutes away

    are identical. Tuning the threshold cannot help; it measures something else.
    And it errs in the worst direction — dictation is often the **most focused**
    time (composing long sentences), judged "not there".

    A changed clipboard is a ready counter-proof: **a background program does not
    write the sentence you dictated into the clipboard**. Window title changes
    will not do — background apps retitle themselves too often, and as "someone
    is here" that evidence would always be true.
    """

    DICTATED = "帮我看一下，好像那个库出了个新版本，改了默认配置"

    def _dictating(self):
        return blocks.blocks([
            ev(575, "chrome", "visit", dur_min=4, title="Butler"),
            ev(578, "live", "clip", text=self.DICTATED, app="com.google.Chrome"),
            ev(579, "idle", "away", dur_min=6),
        ], LO)

    def test_clipboard_evidence_means_no_conflict(self):
        cf = " ".join(at(self._dictating(), 578)["conflict"])
        self.assertNotIn("key/mouse", cf, f"the clipboard proves someone was there; not a conflict: {cf}")

    def test_but_what_idle_said_must_not_vanish(self):
        """The worst mistake this project can make is a source silently vanishing.
        Not a conflict ≠ not mentioned — it becomes an explanation, still visible."""
        b = at(self._dictating(), 578)
        self.assertTrue(b["note"], "what idle said was swallowed entirely")
        self.assertIn("dictation", " ".join(b["note"]))

    def test_without_clipboard_evidence_it_is_still_a_conflict(self):
        """The opposite anchor: without the clip the conflict must come back.
        Otherwise "never report" would turn the two above green too."""
        bs = blocks.blocks([
            ev(575, "chrome", "visit", dur_min=4, title="Butler"),
            ev(579, "idle", "away", dur_min=6),
        ], LO)
        b = at(bs, 578)
        self.assertIn("key/mouse", " ".join(b["conflict"]))
        self.assertEqual(b["note"], [])

    def test_window_title_changes_do_not_mean_someone_is_there(self):
        """live/window fires on every foreground switch, and when a background app
        retitles itself. As evidence, almost every slot would have "someone
        there" — no evidence at all."""
        bs = blocks.blocks([
            ev(575, "chrome", "visit", dur_min=4, title="Butler"),
            ev(578, "live", "window", title="Butler - Google Chrome"),
            ev(579, "idle", "away", dur_min=6),
        ], LO)
        self.assertIn("key/mouse", " ".join(at(bs, 578)["conflict"]))

    def test_another_source_s_clipboard_does_not_mean_someone_at_the_mac(self):
        """kind is a free field; sooner or later a second source will call

        something clip (a phone clipboard, most likely). Copying on the phone is
        exactly what does not prove someone at **the Mac** — they switched
        screens, the same as "Mac idle + phone active is no conflict". So both
        source and kind are checked. A mutation test found it: checking only kind
        was all green.
        """
        bs = blocks.blocks([
            ev(575, "chrome", "visit", dur_min=4, title="Butler"),
            ev(578, "iphone", "clip", text="copied on the phone"),
            ev(579, "idle", "away", dur_min=6),
        ], LO)
        b = at(bs, 578)
        self.assertIn("key/mouse", " ".join(b["conflict"]), "a phone clipboard was taken as someone at the Mac")
        self.assertEqual(b["note"], [])

    def test_no_filler_without_idle(self):
        """The clipboard changed but idle said nothing — nothing to explain."""
        bs = blocks.blocks([ev(578, "live", "clip", text="x", app="a")], LO)
        self.assertEqual(at(bs, 578)["note"], [])

    def test_blind_spots_and_conflicts_are_kept_apart(self):
        """⚠ means "you have to stop and decide". Putting what is already
        explained under ⚠ devalues the ones that really need you."""
        bs = blocks.blocks([
            ev(575, "location", "transit", dur_min=10, **{"from": "家", "to": "公司"}),
            ev(578, "live", "clip", text="x", app="a"),
            ev(578, "zsh", "command", cmd="ls"),
            ev(579, "idle", "away", dur_min=6),
        ], LO)
        b = at(bs, 578)
        self.assertIn("transit", " ".join(b["conflict"]), "a real conflict was wiped along the way")
        self.assertTrue(b["note"])

    def test_blind_spots_survive_merging_into_runs(self):
        """The same as conflicts — if any slot in a run has a blind spot, the run
        says so. Built so the blind spot is in a **later** slot, or "keep only
        the first" would pass too."""
        bs = blocks.blocks([
            ev(575, "chrome", "visit", dur_min=20, title="Butler"),
            ev(588, "live", "clip", text="x", app="a"),
            ev(589, "idle", "away", dur_min=3),
        ], LO)
        r = [x for x in blocks.runs(bs) if x["cat"] == "browse"][0]
        self.assertIn("dictation", " ".join(r["note"]), "a later slot's blind spot was lost")


class MacActiveTest(unittest.TestCase):
    """"Someone acting at the Mac" has to really prove someone at **this
    machine**.

    Measured on 2026-08-16: of 165 idle-conflict slots over three days, those
    really inside idle were

        chrome  135 slots (97 caused by it alone)
        butler   60 slots (9)
        claude   23 slots (1)
        git      16 slots (3)

    chrome is the bulk, and 65% of chrome records inside idle were synced from
    another device — proving "you browsed on the phone", not "someone is using
    this Mac".
    """

    def _idle_plus(self, *evs):
        return at(blocks.blocks([ev(500, "idle", "away", dur_min=30), *evs], LO), 510)

    def test_local_browsing_while_away_is_a_conflict(self):
        """Kept as it was: a background tab refreshing writes history too; this
        still needs your call."""
        b = self._idle_plus(ev(510, "chrome", "visit", title="some site"))
        self.assertTrue(b["conflict"])

    def test_synced_history_does_not_mean_this_mac_was_used(self):
        """Something searched on the phone syncs into History; it is not someone
        at the Mac."""
        b = self._idle_plus(ev(510, "chrome", "visit", title="searched on the phone", synced=True))
        self.assertEqual(b["conflict"], [], f"a synced record was taken as Mac activity: {b['conflict']}")

    def test_synced_history_still_counts_as_browsing(self):
        """You did browse, just on the phone. It is the only phone-browsing
        signal so far, too good to drop — not Mac activity ≠ not activity."""
        b = self._idle_plus(ev(510, "chrome", "visit", title="searched on the phone", synced=True))
        self.assertEqual(b["cat"], "browse")
        self.assertIn("searched on the phone", b["label"])

    def test_butler_does_not_mean_this_mac_was_used(self):
        """butler is both synchronous and asynchronous (the user, 2026-08-16).

        But "a butler prompt arrived during idle" has only three explanations:
        sent from the phone, an async job finishing, or dictated (handled
        separately). None is a contradiction on this Mac — someone really typing
        here would never trigger idle; typing is key input.

        The cost was measured: over three days butler alone caused 1/2/6 slots;
        in over 90% chrome/zsh/git were present too, and the conflict stands
        without it.
        """
        b = self._idle_plus(ev(510, "butler", "task", provider="codex", text="change it"))
        self.assertEqual(b["conflict"], [])

    def test_butler_can_still_be_the_label(self):
        """No longer evidence of "someone at the Mac"; saying what was going on
        is unchanged."""
        b = at(blocks.blocks([ev(500, "butler", "task", provider="codex",
                                 title="change it")], LO), 500)
        self.assertEqual(b["cat"], "code")

    def test_synced_is_a_cross_source_convention_not_chrome_only(self):
        """Any source marked synced stops counting as local activity.

        A chrome-only whitelist would also turn the above green (a mutation test
        caught it), but then the next person adding synced data would first have
        to discover a whitelist hidden here.
        """
        b = self._idle_plus(ev(510, "zsh", "command", cmd="ls", synced=True))
        self.assertEqual(b["conflict"], [])

    def test_the_terminal_and_git_still_count(self):
        """The opposite anchor: not every source was removed. zsh is interactive
        shell history and git commits happen here — against idle they still need
        your call."""
        for e in (ev(510, "zsh", "command", cmd="ls"),
                  ev(510, "git", "reflog", action="commit", project="P")):
            self.assertTrue(self._idle_plus(e)["conflict"], f"{e['source']} was wrongly excluded")


class CategoryNamesTest(unittest.TestCase):
    """Every rung of the ladder needs a readable name, held once in the whole
    project.

    Hit on 2026-08-17: adding PLAUD, blocks.py gained a talk rung and viz.py's
    BNAME followed, but __main__.py's CAT_NAME did not — speech showed as
    "(empty)" in the CLI, and the summary had two "(empty)" rows (one real
    blank, one speech).

    Three nearly identical dicts, three edits per rung, and missing one **raises
    nothing and only displays wrong**. Only "one copy of the data" cures it.
    """

    def test_every_rung_has_a_name(self):
        for cat in blocks.LADDER:
            self.assertIn(cat, blocks.CAT_NAME, f"rung {cat} has no display name")

    def test_no_extra_names(self):
        """The other way too: a deleted rung with its name left behind looks
        like it is still there."""
        self.assertEqual(set(blocks.CAT_NAME) - set(blocks.LADDER), set())

    def test_one_copy_in_the_whole_project(self):
        """assertIs, not assertEqual — the latter is green for "copied and
        currently the same", which is exactly this bug's shape."""
        from umbra import __main__ as cli, viz
        self.assertIs(cli.CAT_NAME, blocks.CAT_NAME)
        self.assertIs(viz.BNAME, blocks.CAT_NAME)

    def test_every_rung_has_a_band_code(self):
        """One character per slot in the band. A missing rung falls back to '.',
        indistinguishable from "nothing happened"."""
        from umbra import viz
        for cat in blocks.LADDER:
            self.assertIn(cat, viz.BCODE, f"rung {cat} is blank in the band")
        self.assertEqual(len(set(viz.BCODE.values())), len(viz.BCODE),
                         "two rungs share a code")


class RunsTest(unittest.TestCase):
    """Consecutive identical slots merge. 288 rows cannot be read or
    sanity-checked.

    Both the CLI and the page merge, so it is shared — copied, the two would
    drift apart sooner or later while you believed you were seeing one
    judgement.
    """

    def test_consecutive_identical_slots_merge(self):
        rs = blocks.runs(blocks.blocks([ev(0, "whoop", "sleep", dur_min=60)], LO))
        sleep = [r for r in rs if r["cat"] == "sleep"]
        self.assertEqual(len(sleep), 1)
        self.assertEqual(sleep[0]["n"], 12)          # 60 minutes = 12 slots
        self.assertEqual(sleep[0]["t"], "00:00")
        self.assertEqual(sleep[0]["end"], "01:00")

    def test_a_changed_label_breaks_the_run(self):
        rs = blocks.runs(blocks.blocks([
            ev(0, "whoop", "sleep", dur_min=30),
            ev(60, "whoop", "workout", dur_min=30),
        ], LO))
        cats = [r["cat"] for r in rs if r["cat"]]
        self.assertEqual(cats, ["sleep", "workout"])

    def test_one_conflicted_slot_marks_the_whole_run(self):
        """Merging must not wash conflicts away — they are what most needs
        seeing."""
        rs = blocks.runs(blocks.blocks([
            ev(0, "whoop", "sleep", dur_min=60),
            ev(30, "zsh", "command", cmd="git push"),
        ], LO))
        self.assertTrue(any(r["cf"] for r in rs), "the conflict vanished after merging")

    def test_empty_slots_merge_too(self):
        rs = blocks.runs(blocks.blocks([], LO))
        self.assertEqual(len(rs), 1)
        self.assertIsNone(rs[0]["cat"])
        self.assertEqual(rs[0]["n"], 288)

    def test_a_conflict_appearing_in_a_later_slot_is_kept(self):
        """Conflicts are unioned when merging.

        The construction matters: a **later** slot must bring a new conflict. If
        the first slot had the most, "keep the first" and "union" would agree
        and the test would prove nothing — a mutation test caught that once.
        """
        bs = blocks.blocks([
            ev(25, "whoop", "sleep", dur_min=60),
            ev(45, "git", "reflog", action="commit", project="P"),
            ev(50, "git", "reflog", action="commit", project="P"),
            ev(50, "idle", "away", dur_min=5),          # covers only the later slot
        ], LO)
        r = [x for x in blocks.runs(bs) if x["cat"] == "code"][0]
        self.assertGreaterEqual(len(r["conflict"]), 2,
                                f"a later slot's conflict was lost: {r['conflict']}")
        self.assertIn("sleep", " ".join(r["conflict"]))
        self.assertIn("key/mouse", " ".join(r["conflict"]))

    def test_conflicts_are_not_repeated(self):
        bs = blocks.blocks([
            ev(25, "whoop", "sleep", dur_min=60),
            ev(45, "git", "reflog", action="commit", project="P"),
            ev(50, "git", "reflog", action="commit", project="P"),
        ], LO)
        r = [x for x in blocks.runs(bs) if x["cat"] == "code"][0]
        self.assertEqual(len(r["conflict"]), len(set(r["conflict"])))

    def test_sources_used_are_combined(self):
        rs = blocks.runs(blocks.blocks([
            ev(0, "whoop", "sleep", dur_min=30),
            ev(10, "idle", "away", dur_min=5),
        ], LO))
        self.assertIn("whoop", rs[0]["why"])


class ConflictTest(unittest.TestCase):
    """"Many sources but no verdict" — the kind the user specifically asked to
    flag.

    It is not "no data" but **sources contradicting each other**. The two are
    kept apart: blanks fill as sources join; contradictions never vanish on
    their own however many are added.
    """

    def test_typing_code_while_asleep_is_a_conflict(self):
        """Either not really asleep or WHOOP's sleep boundary is off. Never pick
        one silently."""
        bs = blocks.blocks([
            ev(0, "whoop", "sleep", dur_min=420),
            ev(100, "zsh", "command", cmd="git push"),
        ], LO)
        b = at(bs, 100)
        self.assertTrue(b["conflict"], "asleep + typing was not flagged")
        self.assertIn("sleep", " ".join(b["conflict"]))

    def test_typing_code_while_driving_is_a_conflict(self):
        bs = blocks.blocks([
            ev(400, "location", "transit", dur_min=60,
               **{"from": "家", "to": "公司"}),
            ev(430, "zsh", "command", cmd="ls"),
        ], LO)
        self.assertTrue(at(bs, 430)["conflict"])

    def test_browsing_while_away_is_a_conflict(self):
        """Measured: 11 slots were this combination. A background tab refreshing
        writes history too."""
        bs = blocks.blocks([
            ev(500, "idle", "away", dur_min=30),
            ev(510, "chrome", "visit", title="some site"),
        ], LO)
        self.assertTrue(at(bs, 510)["conflict"])

    def test_listening_while_driving_is_no_conflict(self):
        """Parallel is not conflicting — these two are meant to happen together."""
        bs = blocks.blocks([
            ev(400, "location", "transit", dur_min=60,
               **{"from": "家", "to": "海滩"}),
            ev(410, "media", "playing", dur_min=20, title="书", track="第一章"),
        ], LO)
        self.assertEqual(at(bs, 415)["conflict"], [])

    def test_away_plus_phone_activity_is_no_conflict(self):
        """Mac idle, phone active — no contradiction at all; they switched screens."""
        bs = blocks.blocks([
            ev(500, "idle", "away", dur_min=30),
            ev(510, "iphone", "focus", dur_min=5, app="微信"),
        ], LO)
        self.assertEqual(at(bs, 510)["conflict"], [])


class EvidenceTest(unittest.TestCase):
    def test_every_slot_lists_its_evidence(self):
        """A sanity check needs the basis for the judgement visible."""
        bs = blocks.blocks([ev(500, "claude", "prompt", text="x", project="P")], LO)
        self.assertIn("claude", at(bs, 500)["why"])

    def test_an_event_spanning_slots_covers_each_one(self):
        bs = blocks.blocks([ev(0, "whoop", "sleep", dur_min=60)], LO)
        self.assertEqual([b["cat"] for b in bs[:12]], ["sleep"] * 12)
        self.assertIsNone(bs[12]["cat"])

    def test_it_does_not_spill_into_the_next_day(self):
        bs = blocks.blocks([ev(1400, "whoop", "sleep", dur_min=600)], LO)
        self.assertEqual(len(bs), 288)
        self.assertEqual(bs[-1]["cat"], "sleep")


if __name__ == "__main__":
    unittest.main()
