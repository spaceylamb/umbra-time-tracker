"""Fact-checking inside the lane: each claim looked up on the web, one by one.

    [plugins.claims]
    lookup       = "model"              # or "sink" (an agent does it), or "off"
    lookup_model = "gemini-3.8-flash"   # any model that can search

Before, the lane only extracted claims and had the model guess from memory; the
lookup itself was handed to a sink, and the only sink that answered was one
person's agent service. Without it, nobody else ever got past the guess.

Now the lane asks a searching model about each claim worth checking, and keeps
what it found on the claim: a verdict, what is actually so, and the pages the
search returned. The report and card steps that follow are unchanged; a sink, if
one is named, only delivers.

Costs money, so it is off until switched on, reaches back only a week (turning it
on must not check months of backlog), and stops at a daily cap.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import analyzer, db
from umbra.plugins import claims

TODAY = "2026-10-24"
DAY = "2026-10-23"
ON = {"plugins": {"claims": {"lookup": "model", "lookup_model": "gemini-3.8-flash"}}}


def _con():
    con = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    con.executescript(db.SCHEMA)
    return con


def _add(con, day=DAY, t="10:00", claim="Postgres added MERGE in 15",
         quote="Postgres only got MERGE in 15", verdict="unsure", absolute=1, why="a version"):
    h, m = map(int, t.split(":"))
    a = h * 60 + m
    analyzer.save_items(con, "claims", day, "seed", a - a % 30, "seed", [{
        "a": a, "b": a + 1, "t": t, "speaker": "Speaker 2", "claim": claim,
        "quote": quote, "kind": "entity", "verdict": verdict, "absolute": absolute,
        "why": why}], "quote", now_ms=1)
    con.commit()


ANSWER = {"text": json.dumps({"verdict": "right",
                              "actual": "MERGE arrived in PostgreSQL 15 (2022).",
                              "transcription_error": False}),
          "sources": [{"url": "https://www.postgresql.org/docs/15/sql-merge.html",
                       "title": "PostgreSQL: MERGE"}],
          "searches": 1}


class Asker:
    """A fake searching model: records prompts, answers from a list."""

    def __init__(self, *answers):
        self.answers = list(answers) or [ANSWER]
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        a = self.answers[min(len(self.prompts), len(self.answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a


def _run(con, ask=None, conf=ON, today=TODAY):
    ask = ask or Asker()
    msgs = []
    with mock.patch.object(claims, "_conf", return_value=conf), \
            mock.patch("umbra.config.load", return_value=conf):
        n = claims.lookup(con, ask=ask, today=today, log=msgs.append)
    return n, ask, msgs


def _one(con):
    return claims.records(con)[0]


KEYS = {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "ANTHROPIC_API_KEY": "a"}


def _env(*have):
    """An environment holding only these provider keys."""
    env = {k: v for k, v in os.environ.items() if k not in KEYS}
    env.update({k: KEYS[k] for k in have})
    return mock.patch.dict(os.environ, env, clear=True)


class ModeTest(unittest.TestCase):
    """The plugin is the feature: on means claims are pulled out and looked up.
    There is no "extract but do not check" — a list of the model's guesses from
    memory is not something anyone switches a fact-checker on for."""

    def _mode(self, conf):
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf):
            return claims.lookup_mode()

    def test_a_model_looks_them_up_unless_said_otherwise(self):
        self.assertEqual(self._mode({}), "model")

    def test_off_is_not_a_way_of_looking_up(self):
        self.assertEqual(self._mode({"plugins": {"claims": {"lookup": "off"}}}), "model")

    def test_a_sink_that_reports_back_when_asked_for(self):
        conf = {"plugins": {"claims": {"lookup": "sink", "sink": "butler", "send": True}}}
        self.assertEqual(self._mode(conf), "sink")

    def test_an_existing_butler_setup_keeps_working_as_it_did(self):
        """Someone with sink + send naming a sink that reports back, and no
        `lookup` key, was verifying through it; upgrading must not change that."""
        conf = {"plugins": {"claims": {"sink": "butler", "send": True}}}
        self.assertEqual(self._mode(conf), "sink")

    def test_a_one_way_sink_only_delivers(self):
        """A command or a webhook cannot look anything up; with one named, the
        model looks claims up and the sink gets the day's card."""
        conf = {"plugins": {"claims": {"sink": "notes", "send": True}},
                "sinks": {"notes": {"kind": "command", "command": "true"}}}
        self.assertEqual(self._mode(conf), "model")


class LookupModelTest(unittest.TestCase):
    def _model(self, conf=None):
        conf = conf or {}
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf):
            return claims.lookup_model()

    def test_the_one_named(self):
        with _env():
            self.assertEqual(self._model({"plugins": {"claims": {"lookup_model": "gpt-6-luna"}}}),
                             "gpt-6-luna")

    def test_otherwise_the_first_provider_with_a_key(self):
        with _env("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            self.assertEqual(self._model(), claims.LOOKUP_MODELS["gpt"])
        with _env("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
            self.assertEqual(self._model(), claims.LOOKUP_MODELS["gemini"])

    def test_none_when_nothing_can_search(self):
        with _env():
            self.assertIsNone(self._model())


class BlockedTest(unittest.TestCase):
    """Either the plugin works or it says what it is missing — never half."""

    def _blocked(self, conf=None):
        conf = conf or {}
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf):
            return claims.blocked(conf)

    def test_no_key_for_any_searching_model_blocks_it_and_says_which_keys(self):
        with _env():
            why = self._blocked()
        for k in KEYS:
            self.assertIn(k, why)

    def test_one_key_is_enough(self):
        with _env("ANTHROPIC_API_KEY"):
            self.assertIsNone(self._blocked())

    def test_a_sink_that_looks_things_up_needs_no_key(self):
        with _env():
            self.assertIsNone(self._blocked({"plugins": {"claims": {
                "lookup": "sink", "sink": "butler", "send": True}}}))

    def test_a_blocked_round_does_nothing_at_all(self):
        """Not even extraction: a fact-checker that cannot check does not run."""
        con = _con()
        with _env(), mock.patch.object(claims, "_conf", return_value={}), \
                mock.patch("umbra.config.load", return_value={}), \
                mock.patch.object(claims, "scan") as scan:
            msgs = []
            claims.sync(con, [DAY], log=msgs.append)
        scan.assert_not_called()
        self.assertTrue(any("API_KEY" in m for m in msgs), msgs)


class SettingsTest(unittest.TestCase):
    """What Settings › Plugins shows for the plugin: why it cannot run, if it
    cannot; which model looks claims up, with each provider's key status; the
    daily cap. Picking a provider writes lookup_model; picking a sink that
    reports back writes lookup = "sink"."""

    def _row(self, conf=None):
        from umbra import settings
        conf = conf or {}
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf):
            return next(p for p in settings._plugins(conf) if p["name"] == "claims")

    def test_ready_with_one_key_and_says_which_it_uses(self):
        with _env("GEMINI_API_KEY"):
            row = self._row()
        self.assertIsNone(row["blocked"])
        lk = row["lookup"]
        self.assertEqual((lk["using"], lk["model"], lk["cap"]),
                         ("gemini", claims.LOOKUP_MODELS["gemini"], claims.MAX_LOOKUPS))
        self.assertEqual([(c["value"], c["key_set"]) for c in lk["choices"]],
                         [("gemini", True), ("gpt", False), ("claude", False)])
        self.assertEqual(lk["choices"][2]["key_env"], "ANTHROPIC_API_KEY")

    def test_no_key_at_all_and_the_row_says_why(self):
        with _env():
            row = self._row()
        self.assertIn("GEMINI_API_KEY", row["blocked"])

    def test_the_chosen_provider_and_the_cap_are_read_back(self):
        conf = {"plugins": {"claims": {"lookup_model": claims.LOOKUP_MODELS["claude"],
                                       "max_lookups": 5}}}
        with _env("GEMINI_API_KEY"):
            lk = self._row(conf)["lookup"]
        self.assertEqual((lk["using"], lk["cap"]), ("claude", 5))

    def test_a_sink_that_reports_back_joins_the_choices(self):
        conf = {"plugins": {"claims": {"sink": "butler", "send": True}}}
        with _env():
            row = self._row(conf)
        self.assertIsNone(row["blocked"])
        self.assertEqual(row["lookup"]["using"], "sink")
        self.assertEqual(row["lookup"]["choices"][-1]["value"], "sink")
        self.assertEqual(row["lookup"]["choices"][-1]["label"], "butler")

    def test_the_page_may_write_the_three_keys(self):
        from umbra import configedit
        keys = configedit.editable()
        self.assertEqual(keys["plugins.claims.lookup_model"][2], list(claims.LOOKUP_MODELS.values()))
        self.assertEqual(keys["plugins.claims.lookup"][2], ["model", "sink"])
        self.assertEqual(keys["plugins.claims.max_lookups"][:2], ("int", claims.MAX_LOOKUPS))

    def test_the_payload_validates(self):
        from umbra.schema import validate as V
        with _env("GEMINI_API_KEY"):
            row = self._row()
        self.assertEqual(V.errors(row, "SettingsPlugin"), [])


class DrawerTest(unittest.TestCase):
    """What the drawer gets for each claim: what the lookup found (verdict,
    what is actually so, misheard or not, sources), or that one is still to
    come; and for the tab, who looks claims up and how many today."""

    def setUp(self):
        self.con = _con()
        _add(self.con, t="10:00", quote="looked up")
        _add(self.con, t="11:00", quote="still to come")
        _add(self.con, t="12:00", quote="not worth it", verdict="confident_right", absolute=0)
        def ask(prompt):
            if "still to come" in prompt:
                raise RuntimeError("503")
            return ANSWER
        _run(self.con, ask)

    def _said(self):
        with _env("GEMINI_API_KEY"), mock.patch.object(claims, "_conf", return_value=ON), \
                mock.patch("umbra.config.load", return_value=ON), \
                mock.patch.object(claims.time, "strftime", return_value=TODAY):
            return claims.said(self.con, DAY, ON)

    def test_each_claim_says_what_was_found_or_that_it_is_coming(self):
        by = {c["quote"]: c for c in self._said()["claims"]}
        found = by["looked up"]["found"]
        self.assertEqual((found["verdict"], found["actual"], found["misheard"], found["model"]),
                         ("right", "MERGE arrived in PostgreSQL 15 (2022).", False, "gemini-3.8-flash"))
        self.assertEqual(found["sources"], ANSWER["sources"])
        self.assertFalse(by["looked up"]["waiting"])
        self.assertIsNone(by["still to come"]["found"])
        self.assertTrue(by["still to come"]["waiting"])
        self.assertFalse(by["not worth it"]["waiting"])

    def test_the_tab_says_who_looks_them_up_and_how_many_today(self):
        d = self._said()
        self.assertEqual((d["lookup"], d["lookup_by"], d["looked_today"]),
                         (True, "gemini-3.8-flash", 1))

    def test_it_validates(self):
        from umbra.schema import validate as V
        self.assertEqual(V.errors({"date": DAY, **self._said()}, "Said"), [])


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        _add(self.con)

    def test_a_claim_is_looked_up_and_keeps_what_was_found(self):
        n, ask, _ = _run(self.con)
        self.assertEqual(n, 1)
        r = _one(self.con)
        self.assertEqual(r["status"], "checked")
        found = r["lookup"]
        self.assertEqual(found["verdict"], "right")
        self.assertEqual(found["actual"], "MERGE arrived in PostgreSQL 15 (2022).")
        self.assertEqual(found["sources"], ANSWER["sources"])
        self.assertEqual((found["model"], found["searches"]), ("gemini-3.8-flash", 1))

    def test_the_report_carries_the_verdict_and_its_sources(self):
        _run(self.con)
        report = _one(self.con)["report"]
        for part in ("Postgres added MERGE in 15", "right",
                     "MERGE arrived in PostgreSQL 15", "https://www.postgresql.org/docs/15/sql-merge.html"):
            self.assertIn(part, report)

    def test_the_prompt_gives_the_claim_its_words_and_the_date(self):
        _, ask, _ = _run(self.con)
        p = ask.prompts[0]
        for part in ("Postgres added MERGE in 15", "Postgres only got MERGE in 15",
                     TODAY, DAY, "a lead, not a finding", "transcription"):
            self.assertIn(part, p)

    def test_the_prompt_follows_the_configured_language(self):
        with mock.patch("umbra.infer.lang", return_value="zh"):
            _, ask, _ = _run(self.con)
        self.assertIn("今天是", ask.prompts[0])

    def test_the_first_thing_the_prompt_says_is_search(self):
        """Measured 2026-10-24: told to search further down, gemini-3.8-flash
        answered 6 of 10 claims from memory. Said first, it searched every one."""
        for lang in ("en", "zh"):
            with mock.patch("umbra.infer.lang", return_value=lang):
                first = claims.lookup_prompt(_one(self.con), TODAY).split("\n")[0]
            self.assertTrue("search" in first.lower() or "搜索" in first, first)

    def test_the_prompt_does_not_ask_for_json_alone(self):
        """Measured 2026-10-24: told "reply with only a JSON object",
        gemini-3.8-flash searched for 0 of 4 claims; told to say what the
        sources show and end with the JSON, it searched for all 4."""
        for lang in ("en", "zh"):
            with mock.patch("umbra.infer.lang", return_value=lang):
                p = claims.lookup_prompt(_one(self.con), TODAY)
            self.assertNotIn("only a JSON", p)
            self.assertNotIn("只回一个 JSON", p)

    def test_the_json_at_the_end_is_the_one_read(self):
        text = ('The sources say {roughly} that it is true.\n'
                + ANSWER["text"])
        _run(self.con, Asker({**ANSWER, "text": text}))
        self.assertEqual(_one(self.con)["lookup"]["verdict"], "right")

    def test_an_answer_without_a_search_is_not_a_check(self):
        """A verdict from memory is what the offline first pass already gave.
        It is retried, and in the end set aside, like any failed lookup."""
        _, _, msgs = _run(self.con, Asker({**ANSWER, "searches": 0, "sources": []}))
        r = _one(self.con)
        self.assertEqual((r["status"], r["attempt"]), ("new", 1))
        self.assertTrue(any("without searching" in m for m in msgs), msgs)

    def test_links_written_into_the_answer_are_taken_out_of_it(self):
        """OpenAI writes its citations into the text as ([site](url))."""
        text = json.dumps({"verdict": "wrong", "actual":
                           "It drives on the left. ([nzta.govt.nz](https://nzta.govt.nz/x?utm_source=openai))"})
        _run(self.con, Asker({**ANSWER, "text": text}))
        self.assertEqual(_one(self.con)["lookup"]["actual"], "It drives on the left.")

    def test_once_checked_never_asked_again(self):
        _run(self.con)
        n, ask, _ = _run(self.con)
        self.assertEqual((n, ask.prompts), (0, []))

    def test_a_link_the_model_wrote_is_not_a_source(self):
        """Only pages the search returned count. A URL written into the answer
        could be anything."""
        text = json.dumps({"verdict": "wrong", "actual": "x",
                           "sources": ["https://made-up.example/proof"]})
        _run(self.con, Asker({**ANSWER, "text": text}))
        urls = [s["url"] for s in _one(self.con)["lookup"]["sources"]]
        self.assertNotIn("https://made-up.example/proof", urls)

    def test_an_answer_wrapped_in_prose_or_a_fence_still_reads(self):
        text = "Here is what I found:\n```json\n" + ANSWER["text"] + "\n```"
        _run(self.con, Asker({**ANSWER, "text": text}))
        self.assertEqual(_one(self.con)["lookup"]["verdict"], "right")

    def test_a_verdict_outside_the_four_is_not_written(self):
        bad = {**ANSWER, "text": json.dumps({"verdict": "probably", "actual": "x"})}
        _run(self.con, Asker(bad))
        self.assertEqual(_one(self.con)["status"], "new")

    def test_a_failed_lookup_stays_new_and_is_tried_again(self):
        _, _, msgs = _run(self.con, Asker(RuntimeError("503")))
        r = _one(self.con)
        self.assertEqual((r["status"], r["attempt"]), ("new", 1))
        self.assertTrue(any("✗" in m for m in msgs), msgs)
        _run(self.con)
        self.assertEqual(_one(self.con)["status"], "checked")

    def test_three_failures_and_it_is_set_aside(self):
        """Set aside rather than left new, or a day with one claim nobody can
        check would never be finished and its card would never go out."""
        for _ in range(3):
            _run(self.con, Asker(RuntimeError("503")))
        self.assertEqual(_one(self.con)["status"], "skipped")
        n, ask, _ = _run(self.con)
        self.assertEqual((n, ask.prompts), (0, []))


class WhatGetsLookedUpTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()

    def test_only_claims_worth_a_lookup(self):
        """The lane's filter (checkable): what the model doubts, or what has a
        number or a date that goes stale. A constant it is sure of is not."""
        _add(self.con, claim="Light takes 8 minutes", quote="light 8 min",
             verdict="confident_right", absolute=0)
        n, ask, _ = _run(self.con)
        self.assertEqual((n, ask.prompts), (0, []))

    def test_a_week_back_and_no_further(self):
        _add(self.con, day="2026-10-18", quote="seven days")   # today and six back: in
        _add(self.con, day="2026-10-17", quote="eight days")   # out
        _run(self.con)
        got = {r["quote"]: r["status"] for r in claims.records(self.con)}
        self.assertEqual(got, {"seven days": "checked", "eight days": "new"})

    def test_a_daily_cap(self):
        for i in range(3):
            _add(self.con, t=f"1{i}:00", quote=f"q{i}")
        conf = {"plugins": {"claims": {**ON["plugins"]["claims"], "max_lookups": 2}}}
        n, _, msgs = _run(self.con, conf=conf)
        self.assertEqual(n, 2)
        self.assertTrue(any("cap" in m for m in msgs), msgs)
        n, ask, _ = _run(self.con, conf=conf)          # same day: the cap holds
        self.assertEqual((n, ask.prompts), (0, []))
        n, _, _ = _run(self.con, conf=conf, today="2026-10-25")
        self.assertEqual(n, 1)


class ModelChoiceTest(unittest.TestCase):
    def test_without_a_lookup_model_the_first_provider_with_a_key(self):
        con = _con()
        _add(con)
        conf = {"plugins": {"claims": {"lookup": "model"}}}
        seen = []
        with _env("OPENAI_API_KEY"), mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf), \
                mock.patch("umbra.providers.lookup",
                           side_effect=lambda m, conf=None: seen.append(m) or Asker()):
            claims.lookup(con, today=TODAY, log=lambda *_: None)
        self.assertEqual(seen, [claims.LOOKUP_MODELS["gpt"]])

    def test_a_model_that_cannot_search_is_said_once_and_nothing_moves(self):
        con = _con()
        _add(con)
        conf = {"plugins": {"claims": {"lookup": "model", "lookup_model": "llama3"}},
                "providers": {"o": {"kind": "openai", "models": ["llama"],
                                    "base_url": "http://localhost:11434/v1"}}}
        msgs = []
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf):
            self.assertEqual(claims.lookup(con, today=TODAY, log=msgs.append), 0)
        self.assertEqual(_one(con)["status"], "new")
        self.assertEqual(len([m for m in msgs if "cannot search" in m]), 1)


class CardTest(unittest.TestCase):
    def test_one_days_findings_make_one_card(self):
        """Each claim carries its own report now; the card is made from all of
        the day's, read as one list rather than as separate reports."""
        con = _con()
        _add(con, t="10:00", quote="q1")
        _add(con, t="11:00", quote="q2", claim="Rust 2024 shipped in 1.85")
        _run(con)
        seen = []
        n = claims.render_cards(con, call=lambda p: seen.append(p) or "CARD")
        self.assertEqual(n, 1)
        report = seen[0].split("Report:\n", 1)[1]
        self.assertNotIn("=== Report", report)
        self.assertIn("Rust 2024 shipped in 1.85", report)
        self.assertEqual({r["card"] for r in claims.records(con)}, {"CARD"})


class SyncTest(unittest.TestCase):
    def test_a_round_looks_up_then_delivers_through_the_sink(self):
        con = _con()
        _add(con)
        out = os.path.join(tempfile.mkdtemp(), "out.jsonl")
        conf = {"plugins": {"claims": {**ON["plugins"]["claims"],
                                       "sink": "notes", "send": True}},
                "sinks": {"notes": {"kind": "command",
                                    "command": f'printf "%s\\n" "$UMBRA_ITEM" >> {out}'}}}
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf), \
                mock.patch("umbra.providers.lookup", return_value=Asker()), \
                mock.patch("umbra.providers.resolve", return_value=lambda p: "THE CARD"), \
                mock.patch.object(claims, "scan", return_value={
                    "fresh": [], "fresh_calls": 0, "reused": 0, "failed": 0}), \
                mock.patch.object(claims.time, "strftime", return_value=TODAY):
            claims.sync(con, [DAY], log=lambda *_: None)
        with open(out, encoding="utf-8") as f:
            sent = [json.loads(line) for line in f]
        self.assertEqual([i["body"] for i in sent], ["THE CARD"])
        self.assertEqual(_one(con)["status"], "checked")


if __name__ == "__main__":
    unittest.main()
