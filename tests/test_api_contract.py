"""The API contract: what umbra serve returns matches api.schema.json.

ADR 0001 makes the schema the load-bearing part of the Python/TypeScript
boundary. These tests are the Python half of that promise — the TypeScript half
is that its types are generated from the same file. If a payload grows a field,
loses one, or changes a type, this goes red before the page finds out.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from umbra import api, config, db, devseed
from umbra.schema import validate as V

DAY = devseed.DAY
ON = {"plugins": {"actions": {"enabled": True}, "claims": {"enabled": True}}}


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.path = devseed.seed(cls.dir)
        # The demo's own config: both drawer lanes on, extracting only.
        cls.patch = mock.patch.object(config, "load", return_value=ON)
        cls.patch.start()
        # Saving new wording starts a judging subprocess. A subprocess does not
        # inherit this test's patches: it would open the real database and call
        # a real model. Stand in for it, always.
        cls.started = []
        cls.judge = mock.patch.object(
            api, "start_judging",
            side_effect=lambda wk: cls.started.append(wk) or True)
        cls.judge.start()

    @classmethod
    def tearDownClass(cls):
        cls.judge.stop()
        cls.patch.stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def test_meta(self):
        m = api.meta(self.con)
        V.validate(m, "Meta")
        self.assertEqual(m["order"][-1], DAY)
        self.assertEqual(len(m["order"]), devseed.BACK + 1)

    def test_meta_carries_both_palettes(self):
        """The page needs both themes' category colours, and they are the same
        categories — a key present in one and not the other would draw a
        category in only one theme."""
        p = api.meta(self.con)["palette"]
        self.assertEqual(set(p["light"]), set(p["dark"]))
        self.assertIn("code", p["light"])

    def test_day(self):
        d = api.day(DAY, self.con)
        V.validate(d, "Day")
        self.assertEqual(len(d["ev"][d["evuse"]]), len(devseed.EVENTS))

    def test_detail(self):
        V.validate(api.detail(DAY, self.con), "DayDetail")

    def test_detail_digests_are_decoded(self):
        """Over HTTP the server sends JSON and lets Content-Encoding compress
        it; the gzip+base64 packing was for a file on disk."""
        dg = api.detail(DAY, self.con)["dg"]
        self.assertIsInstance(dg, list)
        self.assertIn("text", dg[0])

    def test_raw(self):
        r = api.raw(DAY, self.con)
        V.validate(r, "Raw")
        self.assertGreater(len(r["records"]), 0)

    def test_every_seeded_day_validates(self):
        for d in api.meta(self.con)["order"]:
            day = api.day(d, self.con)
            V.validate(day, "Day")
            self.assertEqual(V.bcat_errors(day), [], d)

    def test_failed_windows_reach_the_day(self):
        import datetime as dt
        d = (dt.date.fromisoformat(DAY) - dt.timedelta(days=devseed.FAILED)).isoformat()
        day = api.day(d, self.con)
        V.validate(day, "Day")
        self.assertEqual([(w["a"], w["b"]) for w in day["failed"]["windows"]],
                         devseed.FAILED_WINDOWS)

    def test_said(self):
        d = api.said(DAY, self.con)
        V.validate(d, "Said")
        self.assertEqual(len(d["tasks"]), len(devseed.TASKS))
        self.assertEqual(d["held"], devseed.ECHOES)
        self.assertEqual(len(d["claims"]), len(devseed.CLAIMS))
        # Sending is off by default, and the drawer says so.
        self.assertFalse(d["sending"])
        self.assertEqual(d["lookup_sink"], "")

    def test_said_without_a_key_says_nothing_looks_claims_up(self):
        keys = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
        env = {k: v for k, v in os.environ.items() if k not in keys}
        with mock.patch.dict(os.environ, env, clear=True):
            d = api.said(DAY, self.con)
        V.validate(d, "Said")
        self.assertEqual((d["lookup"], d["lookup_by"]), (False, ""))
        self.assertFalse(any(c["waiting"] for c in d["claims"]))

    def test_said_with_a_one_way_sink_the_model_looks_up_and_the_sink_gets_the_card(self):
        """A command or a webhook cannot look anything up: a model does, its
        findings come back here, and the sink is where the day's card goes."""
        conf = {"plugins": {"claims": {"enabled": True, "sink": "notes", "send": True}},
                "sinks": {"notes": {"kind": "command", "command": "true"}}}
        with mock.patch("umbra.config.load", return_value=conf), \
                mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            d = api.said(DAY, self.con)
        V.validate(d, "Said")
        self.assertTrue(d["lookup"])
        self.assertEqual(d["lookup_by"], "gemini-3.8-flash")
        self.assertEqual(d["lookup_sink"], "notes")
        self.assertTrue(d["lookup_answers"])

    def test_a_task_closed_on_the_page_shows_and_not_a_task_is_a_negative(self):
        """The drawer's ✓ / ✕ — for anyone whose sink never sends a verdict
        back. Only "not a task" says extraction was wrong."""
        from umbra.plugins import actions as A
        tasks = lambda: {t["id"]: t for t in api.said(DAY, self.con)["tasks"]}
        open_ids = [i for i, t in tasks().items() if t["status"] == "proposed"]
        a, b = open_ids[:2]
        self.assertEqual(api.said_mark("task", a, {"verdict": "already_done"}, self.con), {"ok": True})
        self.assertEqual(api.said_mark("task", b, {"verdict": "not_a_task"}, self.con), {"ok": True})
        self.addCleanup(api.said_mark, "task", a, {"verdict": None}, self.con)
        self.addCleanup(api.said_mark, "task", b, {"verdict": None}, self.con)
        got = tasks()
        V.validate(api.said(DAY, self.con), "Said")
        self.assertEqual((got[a]["status"], got[a]["verdict"]), ("dismissed", "already_done"))
        self.assertEqual([n["task"] for n in A.negatives(self.con)], [got[b]["task"]])
        # Closed once is closed: a second verdict does not overwrite the first.
        self.assertEqual(api.said_mark("task", a, {"verdict": "not_a_task"}, self.con), {"ok": False})

    def test_undo_puts_a_task_back_where_it_was(self):
        sent = next(t for t in api.said(DAY, self.con)["tasks"] if t["status"] == "dispatched")
        api.said_mark("task", sent["id"], {"verdict": "already_done"}, self.con)
        api.said_mark("task", sent["id"], {"verdict": None}, self.con)
        back = next(t for t in api.said(DAY, self.con)["tasks"] if t["id"] == sent["id"])
        self.assertIsNone(back["verdict"])
        self.assertIn(back["status"], ("proposed", "dispatched"))

    def test_a_claim_held_as_a_repeat_reads_as_closed(self):
        """A repeat of an earlier round's claim (claims._mark_repeats) leaves the
        count and sinks to the end, the way a task said twice does — showing it
        open would put the same claim in front of the reader twice."""
        from umbra.plugins import claims as C
        rec = next(r for r in C.records(self.con, day=DAY) if r["src"] == "item")
        before = rec["status"]
        C._write(self.con, rec, "duplicate")
        self.con.commit()
        self.addCleanup(lambda: (C._write(self.con, rec, before), self.con.commit()))
        got = next(c for c in api.said(DAY, self.con)["claims"] if c["id"] == rec["id"])
        V.validate(api.said(DAY, self.con), "Said")
        self.assertEqual(got["status"], "duplicate")
        self.assertTrue(got["seen"])

    def test_a_claim_marked_seen(self):
        c = api.said(DAY, self.con)["claims"][0]
        self.assertFalse(c["seen"])
        api.said_mark("claim", c["id"], {"seen": True}, self.con)
        self.addCleanup(api.said_mark, "claim", c["id"], {"seen": False}, self.con)
        self.assertTrue(next(x for x in api.said(DAY, self.con)["claims"]
                             if x["id"] == c["id"])["seen"])

    def test_verdicts_on_what_does_not_exist_are_404(self):
        with self.assertRaises(api.NotFound):
            api.said_mark("task", 999999, {"verdict": "already_done"}, self.con)
        with self.assertRaises(api.NotFound):
            api.said_mark("claim", 999999, {"seen": True}, self.con)

    def test_echoes_are_counted_not_listed(self):
        quotes = {t["quote"] for t in api.said(DAY, self.con)["tasks"]}
        self.assertFalse(any(q.startswith("echo ") for q in quotes))

    def test_week(self):
        from umbra import goals as G
        w = api.week(G.week_of(DAY), self.con)
        V.validate(w, "Week")
        self.assertEqual(w["goals"], devseed.GOALS)
        self.assertIn(DAY, w["done"])
        self.assertGreater(w["totals"]["1"], 0)

    def test_setting_goals(self):
        from umbra import goals as G
        wk = G.week_of("2026-08-20")
        w = api.set_goals(wk, ["one", " ", "two"], self.con)
        V.validate(w, "Week")
        self.assertEqual(w["goals"], ["one", "two"])
        with self.assertRaises(ValueError):
            api.set_goals(wk, ["a", "b", "c", "d"], self.con)
        self.assertEqual(api.week(wk, self.con)["goals"], ["one", "two"])
        with self.assertRaises(api.NotFound):
            api.week("2026-37", self.con)

    def test_targets(self):
        from umbra import goals as G
        wk = G.week_of("2026-08-20")
        w = api.set_goals(wk, [{"text": "a", "target": 10, "unit": "hours"}, "b"], self.con)
        V.validate(w, "Week")
        self.assertEqual([(p["target"], p["unit"]) for p in w["priorities"]],
                         [(10.0, "hours"), (None, None)])
        for bad in ({"text": "a", "target": 10, "unit": "days"},
                    {"text": "a", "target": -1, "unit": "hours"}):
            with self.assertRaises(ValueError):
                api.set_goals(wk, [bad], self.con)

    def test_a_target_does_not_change_the_attribution_key(self):
        """Changing 10h to 12h must not throw away a week of judgement."""
        from umbra import goals as G
        wk = G.week_of(DAY)
        before = api.week(wk, self.con)["gdig"]
        api.set_goals(wk, [{"text": g, "target": 12, "unit": "hours"} for g in devseed.GOALS], self.con)
        self.assertEqual(api.week(wk, self.con)["gdig"], before)
        api.set_goals(wk, devseed.GOALS, self.con)

    def test_new_wording_starts_judging_at_once(self):
        """Why attribution once ran on the edge: Save had to answer in seconds,
        not at the next scheduled round hours away."""
        from umbra import goals as G
        wk = G.week_of("2026-08-20")
        self.started.clear()
        api.set_goals(wk, ["something new"], self.con)
        self.assertEqual(self.started, [wk])

    def test_a_changed_target_starts_nothing(self):
        """A target sits outside the goal digest; there is no new question."""
        from umbra import goals as G
        wk = G.week_of("2026-08-20")
        api.set_goals(wk, ["same words"], self.con)
        self.started.clear()
        api.set_goals(wk, [{"text": "same words", "target": 5, "unit": "hours"}],
                      self.con)
        self.assertEqual(self.started, [])

    def test_clearing_a_week_starts_nothing(self):
        from umbra import goals as G
        wk = G.week_of("2026-08-20")
        self.started.clear()
        api.set_goals(wk, [], self.con)
        self.assertEqual(self.started, [])

    def test_the_week_says_whether_it_is_being_judged(self):
        from umbra import goals as G
        wk = G.week_of(DAY)

        class Running:
            def poll(self):
                return None

        class Finished:
            def poll(self):
                return 0

        with mock.patch.dict(api._JUDGING, {wk: Running()}):
            self.assertTrue(api.week(wk, self.con)["judging"])
        api._JUDGING[wk] = Finished()
        self.assertFalse(api.week(wk, self.con)["judging"])
        self.assertNotIn(wk, api._JUDGING, "a finished job is reaped")

    def test_a_failed_judging_run_is_reported_not_swallowed(self):
        """Without it the page goes from "judging" to "not yet judged" and
        nothing says an attempt was made and failed."""
        from umbra import goals as G
        wk = G.week_of(DAY)

        class Failed:
            def poll(self):
                return 1

        from umbra import ops
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(ops, "LOG_DIR", d):
            with open(os.path.join(d, f"judge-{wk}.log"), "w") as f:
                f.write("  goals: 7 days · asked 3\n"
                        "  ⚠ 2026-08-17: GEMINI_API_KEY is not set\n"
                        "Traceback (most recent call last):\nSystemExit: 1\n")
            api._JUDGING[wk] = Failed()
            w = api.week(wk, self.con)
        V.validate(w, "Week")
        self.assertTrue(w["judge_failed"])
        # The run's own reason, not "the model could not be reached".
        self.assertEqual(w["judge_error"], "2026-08-17: GEMINI_API_KEY is not set")
        api._JUDGE_FAILED.discard(wk)

    def test_a_spawned_run_loads_this_code_not_the_current_directorys(self):
        """`python -m umbra` puts the working directory first on the import
        path; a server started inside another checkout judged with its code."""
        from umbra import ops
        with mock.patch("subprocess.Popen") as popen:
            ops.spawn(["plugins", "run", "goals"])
        self.assertEqual(popen.call_args.kwargs["cwd"], ops.repo_dir())

    def test_settings(self):
        from umbra import settings
        V.validate(settings.get(), "Settings")

    def test_actions_are_an_allowlist(self):
        from umbra import settings
        with self.assertRaises(KeyError):
            settings.act("rm -rf /")

    def test_config(self):
        V.validate(api.config_get(), "Config")

    def test_retrying_one_source_only_takes_a_real_one(self):
        """only= goes onto a command line; it must name a source that exists."""
        with self.assertRaises(api.NotFound):
            api.start_import(self.con, only="chrome; rm -rf ~")

    def test_setup(self):
        V.validate(api.setup(), "Setup")

    def test_import_progress(self):
        V.validate(api.import_progress(self.con), "ImportProgress")
        run = db.progress_start(self.con, ["chrome"])
        db.progress_set(self.con, run, "chrome", "reading")
        p = api.import_progress(self.con)
        V.validate(p, "ImportProgress")
        self.assertTrue(p["run"]["running"])

    def test_unknown_day_is_not_found(self):
        with self.assertRaises(api.NotFound):
            api.day("2020-01-01", self.con)
        with self.assertRaises(api.NotFound):
            api.day("yesterday", self.con)


class ValidatorTest(unittest.TestCase):
    """The validator has to fail, or the contract tests above prove nothing."""

    def setUp(self):
        self.good = {"date": DAY, "bcat": "." * 288,
                     "cov": {"A": 1, "B": 0, "off": 0, "C": 0, "away": 0},
                     "pct": 0.1, "counts": {}, "n_min": 1440, "end": 1440,
                     "travel": False,
                     "tz": [{"at": 0, "wall": 0, "zone": "UTC", "abbr": "UTC", "off_s": 0}]}

    def test_accepts_a_good_day(self):
        V.validate(self.good, "Day")

    def test_missing_field(self):
        del self.good["pct"]
        self.assertTrue(V.errors(self.good, "Day"))

    def test_wrong_type(self):
        self.good["cov"]["A"] = "1"
        self.assertTrue(V.errors(self.good, "Day"))

    def test_bool_is_not_an_integer(self):
        self.good["cov"]["A"] = True
        self.assertTrue(V.errors(self.good, "Day"))

    def test_unexpected_field(self):
        self.good["surprise"] = 1
        self.assertTrue(V.errors(self.good, "Day"))

    def test_bcat_length(self):
        """bcat has one slot per five minutes of *this* day, which a schema
        cannot say (288 is only the ordinary day) — so it is checked here."""
        self.assertEqual(V.bcat_errors(self.good), [])
        self.good["bcat"] = "." * 287
        self.assertTrue(V.bcat_errors(self.good))
        self.good.update(bcat="." * 300, n_min=1500)
        self.assertEqual(V.bcat_errors(self.good), [])

    def test_unknown_keyword_is_refused(self):
        """A keyword the validator skips would be a constraint TypeScript
        enforces and Python never checks."""
        root = {"$defs": {"X": {"type": "string", "format": "email"}}}
        with self.assertRaises(V.Invalid):
            V.validate("a", "X", root)


if __name__ == "__main__":
    unittest.main()
