"""The review plugin as a whole: one card a month, through a sink you name.

The gates on the card come from the voice-dispatch lesson (82% "already done")
and from the first real run, where one judging pass produced over four hundred
suggestions:

    needs_you = 0       only what the model says can go on without you
    not already over    a next date in the past means it probably stopped
                        (nearly half of the first four hundred)
    six per lever       one kind had 166, another 16; without a cap per group
                        the first fills the card
    24 in all           a card read on one screen is a card read
    most frequent first so a ×3 never pushes out a ×51
    the rest wait       for next month's card, not dropped
"""

import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from umbra import db, providers, registry, sink
from umbra.plugins import review as RV
from umbra.plugins.review import card, detect, judge

TODAY = "2026-09-15"


def _con():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    judge.ensure(con)
    return con


def _sugg(con, akey, lever="push", n=10, needs_you=0, next_day="2026-09-20",
          title=None, period="2026-M09"):
    con.execute(
        "INSERT INTO review_suggestions (period, akey, kind, title, lever, needs_you, why, "
        "cadence, n, median_gap, jitter, last_day, next_day, evidence, model, created_ms) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (period, akey, "page", title or f"you open {akey}", lever, needs_you,
         "nobody needs to", "weekly", n, 7, 0, "2026-09-13", next_day,
         json.dumps([{"day": "2026-09-13", "text": f"evidence for {akey}"}]), "m", 0))
    con.commit()


def _conf(**spec):
    return {"plugins": {"review": {"enabled": True, **spec}}}


class DeclarationsTest(unittest.TestCase):
    def test_off_until_switched_on_and_it_sends(self):
        self.assertFalse(getattr(RV, "ENABLED_BY_DEFAULT", False))
        self.assertTrue(RV.SEND_ONLY)
        self.assertEqual(RV.CLI_NAME, "review")

    def test_a_round_takes_it_when_on(self):
        self.assertIn("review", registry.rounds(only_enabled=True, conf=_conf()))


class BlockedTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(providers, "status", return_value={"key_set": True})
        p.start()
        self.addCleanup(p.stop)

    def test_needs_a_sink(self):
        self.assertIn("sink", RV.blocked(_conf()))

    def test_a_sink_that_is_not_set_up(self):
        with mock.patch.object(sink, "answers", return_value=None):
            self.assertIn("notes", RV.blocked(_conf(sink="notes")))

    def test_a_model_without_its_key(self):
        with mock.patch.object(sink, "answers", return_value=False), \
             mock.patch.object(providers, "status",
                               return_value={"key_set": False, "key_env": "GEMINI_API_KEY"}):
            self.assertIn("GEMINI_API_KEY", RV.blocked(_conf(sink="notes")))

    def test_ready(self):
        with mock.patch.object(sink, "answers", return_value=False):
            self.assertIsNone(RV.blocked(_conf(sink="notes")))


class GatesTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()

    def ids(self):
        return [r["akey"] for r in card.dispatchable(self.con, today=TODAY)]

    def test_only_what_does_not_need_you(self):
        _sugg(self.con, "a")
        _sugg(self.con, "b", needs_you=1)
        self.assertEqual(self.ids(), ["a"])

    def test_one_whose_next_date_passed_waits(self):
        _sugg(self.con, "stale", next_day="2026-08-01")
        _sugg(self.con, "burst", next_day=None)
        self.assertEqual(self.ids(), ["burst"])

    def test_six_per_lever_and_most_frequent_first(self):
        for i in range(9):
            _sugg(self.con, f"push{i}", n=i)
        _sugg(self.con, "mail", lever="unsubscribe", n=1)
        got = self.ids()
        self.assertEqual(got[:6], [f"push{i}" for i in range(8, 2, -1)])
        self.assertIn("mail", got)
        self.assertEqual(len(got), 7)

    def test_twenty_four_in_all(self):
        for lev in detect.LEVERS:
            for i in range(6):
                _sugg(self.con, f"{lev}{i}", lever=lev)
        self.assertEqual(len(self.ids()), card.MAX_ITEMS)

    def test_ruled_or_already_sent_is_out(self):
        _sugg(self.con, "a")
        _sugg(self.con, "b")
        judge.verdict(self.con, judge.load(self.con)[0]["id"], "useful")
        self.con.execute("UPDATE review_suggestions SET card='2026-M08' WHERE akey='b'")
        self.assertEqual(self.ids(), [])


class CardTest(unittest.TestCase):
    def test_the_card_says_what_and_how_to_rule(self):
        con = _con()
        _sugg(con, "page:grocery.example.com", title="You reorder groceries by hand")
        _sugg(con, "mail:receipts", lever="unsubscribe", title="Receipts you never open")
        prev = card.preview(con, today=TODAY)
        it = prev["item"]
        self.assertEqual(it["key"], "umbra:review:v1:2026-M09")
        self.assertIn("2 things", it["title"])
        self.assertIn("Make it come to you", it["body"])
        self.assertIn("Unsubscribe or mute", it["body"])
        sid = judge.load(con)[0]["id"]
        self.assertIn(f"#{sid}", it["body"])
        self.assertIn("umbra review verdict", it["body"])
        self.assertTrue(it["visible"])

    def test_nothing_to_send_is_none(self):
        self.assertIsNone(card.preview(_con(), today=TODAY))


class FakeSink:
    def __init__(self):
        self.sent, self.fail = [], None

    def send(self, item):
        if self.fail:
            raise self.fail
        self.sent.append(item)
        return f"r{len(self.sent)}"


class SyncTest(unittest.TestCase):
    """A round: judge once a day at most, send one card a month."""

    def setUp(self):
        self.con = _con()
        self.fake = FakeSink()
        self.calls = []
        self.logs = []
        self.today = TODAY
        self.cands = [{"akey": "page:dash.example.com", "kind": "page", "n": 12,
                       "name": "weekly", "median_gap": 7, "jitter": 0,
                       "last": "2026-09-13", "next_expected": "2026-09-20",
                       "evidence": [{"day": "2026-09-13", "text": "Dashboard"}]}]

        def model(prompt):
            self.calls.append(prompt)
            return json.dumps([{"akey": c["akey"], "title": f"you check {c['akey']}",
                                "lever": "push", "needs_you": False, "why": "a feed would do"}
                               for c in self.cands if c["akey"] in prompt])
        for p in (mock.patch.object(sink, "resolve",
                                    side_effect=lambda n, conf=None: {"send": self.fake.send}),
                  mock.patch.object(sink, "answers", return_value=False),
                  mock.patch.object(providers, "status", return_value={"key_set": True}),
                  mock.patch.object(providers, "resolve", return_value=model),
                  mock.patch.object(detect, "history_weeks", side_effect=lambda *a, **k: self.weeks),
                  mock.patch.object(detect, "candidates", side_effect=self.count),
                  mock.patch.object(RV, "_today", side_effect=lambda: self.today)):
            p.start()
            self.addCleanup(p.stop)
        self.weeks = 16.0

    counted = 0

    def count(self, *a, **k):
        self.counted += 1
        return self.cands

    def round(self, conf=None):
        with mock.patch.object(RV, "_conf", return_value=conf or _conf(sink="notes")):
            RV.sync(self.con, [], self.logs.append)

    def test_one_card_a_month(self):
        self.round()
        self.today = "2026-09-16"
        self.round()
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("2026-M09", self.fake.sent[0]["key"])

    def test_next_month_brings_what_is_new(self):
        self.round()
        self.today = "2026-10-02"
        self.cands = self.cands + [dict(self.cands[0], akey="mail:receipts", kind="mail",
                                        last="2026-09-30", next_expected="2026-10-07")]
        self.round()
        self.assertEqual(len(self.fake.sent), 2)
        self.assertIn("mail:receipts", self.calls[-1])
        self.assertNotIn("page:dash", self.calls[-1])       # asked once, ever

    def test_too_little_history_waits_without_a_call(self):
        self.weeks = 4.2
        self.round()
        self.assertEqual((self.fake.sent, self.calls), ([], []))
        self.assertTrue(any("4.2 weeks" in m for m in self.logs))

    def test_counted_once_a_day_at_most(self):
        """Sixteen weeks of history is read at most once a day, whatever the
        round's interval. (Nothing judged worth a card, so none goes out and
        the next day counts again.)"""
        self.cands = []
        self.round(_conf(sink="notes"))
        self.round(_conf(sink="notes"))
        self.assertEqual(self.counted, 1)
        self.today = "2026-09-16"
        self.round(_conf(sink="notes"))
        self.assertEqual(self.counted, 2)

    def test_on_with_a_sink_is_sending(self):
        """The monthly list is the plugin: there is no second switch to forget."""
        self.round(_conf(sink="notes"))
        self.assertEqual(len(self.fake.sent), 1)

    def test_a_failed_send_is_tried_again_the_next_day(self):
        self.fake.fail = OSError("down")
        self.round()
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(any("down" in m for m in self.logs))
        self.fake.fail = None
        self.round()
        self.assertEqual(self.fake.sent, [])          # same day: not again
        self.today = "2026-09-16"
        self.round()
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(len(self.calls), 1)          # and no second judging

    def test_a_destination_that_has_it_counts_as_sent(self):
        self.fake.fail = sink.Duplicate("seen", receipt="old")
        self.round()
        self.fake.fail = None
        self.today = "2026-09-16"
        self.round()
        self.assertEqual(self.fake.sent, [])

    def test_blocked_does_nothing(self):
        self.round(_conf())
        self.assertEqual((self.fake.sent, self.calls), ([], []))
        self.assertTrue(any("not running" in m for m in self.logs))


class CliTest(unittest.TestCase):
    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        real = db.connect
        self.con = real(path)
        judge.ensure(self.con)
        p = mock.patch.object(db, "connect", side_effect=lambda *a, **k: real(path))
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv):
        import argparse
        ap = argparse.ArgumentParser()
        RV.cli_args(ap)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             mock.patch.object(RV, "_conf", return_value=_conf()), \
             mock.patch.object(RV, "_today", return_value=TODAY):
            RV.cli(ap.parse_args(list(argv)))
        return out.getvalue()

    def test_verdict(self):
        _sugg(self.con, "a")
        sid = judge.load(self.con)[0]["id"]
        self.run_cli("verdict", str(sid), "already", "did", "it")
        self.assertEqual(judge.by_id(self.con, sid)["verdict_note"], "did it")

    def test_preview_sends_nothing(self):
        _sugg(self.con, "page:grocery.example.com")
        with mock.patch.object(sink, "send") as send:
            text = self.run_cli("preview")
        send.assert_not_called()
        self.assertIn("page:grocery.example.com", text)
        self.assertIn("Nothing was sent", text)

    def test_the_report_needs_no_model(self):
        with mock.patch.object(providers, "resolve", side_effect=AssertionError("no")):
            text = self.run_cli()
        self.assertIn("Coverage", text)

    def test_hits(self):
        _sugg(self.con, "a")
        judge.verdict(self.con, judge.load(self.con)[0]["id"], "useful")
        self.assertIn("useful 1", self.run_cli("hits"))


if __name__ == "__main__":
    unittest.main()
