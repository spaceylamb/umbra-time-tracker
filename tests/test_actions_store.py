"""The tasks lane on the core's findings store, read together with its old table.

Proposals found before the move are in `proposals`, which gains no rows; new
ones are in analyzer_items. The lane serves both as one list. What is pinned
here is what the move could break without any older test noticing:

* the first round after upgrading must not ask the model again for windows the
  old cache had answered — that pays to find the same tasks reworded and sends
  them twice;
* a quote the old table already holds is not found again;
* a proposal queued before the move is still sent, and your verdict on it still
  lands on it;
* ids name one proposal whichever store it is in, because they are what the
  destination dedupes on.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import analyze, analyzer, db, sink
from umbra.plugins import actions

DAY = "2026-08-19"
QUOTE = "回头帮我订一下周五去 LA 的机票"
ANSWER = json.dumps([{"time": "09:10", "type": "errand",
                      "task": "给妈妈订周五去 LA 的机票", "quote": QUOTE,
                      "confidence": "high"}], ensure_ascii=False)


def _con():
    con = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    con.executescript(db.SCHEMA)
    return con


def _speak(con, minute, text, uid=None, file_id=None, speaker="Speaker 1"):
    lo, _ = analyze.day_bounds(DAY)
    db.insert(con, [(uid or f"p:{minute}", lo + minute * 60000, 2000, "plaud",
                     "speech", {"text": text, "speaker": speaker,
                                "file_id": file_id})])


def _legacy(con, quote=QUOTE, status="proposed", **kw):
    cols = {"day": DAY, "t": "09:10", "task": "旧的提案", "quote": quote,
            "status": status, "type": "errand", "confidence": "high", **kw}
    cur = con.execute(f"INSERT INTO proposals ({', '.join(cols)}) "
                      f"VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    con.commit()
    return cur.lastrowid


class CacheBridgeTest(unittest.TestCase):
    def test_the_rendered_text_is_byte_identical_to_the_old_lane(self):
        """The bridge is only sound if the window reads exactly as it used to:
        the old cache answered *that* text. Two recordings of one sentence, a
        second speaker and context on both sides — the cases the old renderer
        had special handling for."""
        con = _con()
        _speak(con, 540, "我们先把行程定下来")
        _speak(con, 550, QUOTE, uid="p:550a", file_id="a")
        _speak(con, 550, QUOTE + "。", uid="p:550b", file_id="b",
               speaker="Speaker 2")
        _speak(con, 575, "然后再说酒店", speaker="Speaker 2")
        lo, hi = analyze.day_bounds(DAY)
        evs = [e for e in db.load_range(con, lo - 1800000, hi + 1800000)
               if e.get("source") == "plaud" and e.get("kind") != "mark"]
        old = {a: actions.render(evs, a, a + 30, lo)
               for a in range(0, 1440, 30)}
        new = {w["a"]: w["text"]
               for w in analyzer.windows(con, actions, DAY, "m")}
        self.assertEqual({a: t for a, t in old.items() if t}, new)

    def test_windows_the_old_cache_answered_are_not_asked_again(self):
        con = _con()
        _speak(con, 550, QUOTE)
        lo, hi = analyze.day_bounds(DAY)
        evs = [e for e in db.load_range(con, lo - 1800000, hi + 1800000)
               if e.get("source") == "plaud"]
        con.execute("INSERT INTO task_digests (day, model, a_min, n_seg, ok, text)"
                    " VALUES (?,?,?,?,?,?)",
                    (DAY, "m", 540, 1, 1, actions.render(evs, 540, 570, lo)))
        con.commit()
        calls = []
        r = actions.scan(con, DAY, model="m", call=lambda p: calls.append(p) or ANSWER)
        self.assertEqual(calls, [])
        self.assertEqual(r["reused"], 1)

    def test_a_window_the_old_cache_failed_on_is_asked(self):
        """ok=0 means the call failed. Carrying that over as answered would mean
        the stretch is never looked at again."""
        con = _con()
        _speak(con, 550, QUOTE)
        lo, hi = analyze.day_bounds(DAY)
        evs = [e for e in db.load_range(con, lo - 1800000, hi + 1800000)
               if e.get("source") == "plaud"]
        con.execute("INSERT INTO task_digests (day, model, a_min, n_seg, ok, text)"
                    " VALUES (?,?,?,?,?,?)",
                    (DAY, "m", 540, 1, 0, actions.render(evs, 540, 570, lo)))
        con.commit()
        calls = []
        actions.scan(con, DAY, model="m", call=lambda p: calls.append(p) or ANSWER)
        self.assertEqual(len(calls), 1)

    def test_a_window_whose_text_changed_is_asked(self):
        con = _con()
        _speak(con, 550, QUOTE)
        con.execute("INSERT INTO task_digests (day, model, a_min, n_seg, ok, text)"
                    " VALUES (?,?,?,?,?,?)", (DAY, "m", 540, 1, 1, "older text"))
        con.commit()
        calls = []
        actions.scan(con, DAY, model="m", call=lambda p: calls.append(p) or ANSWER)
        self.assertEqual(len(calls), 1)


class TwoStoresTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, QUOTE)

    def test_a_quote_the_old_table_holds_is_not_found_again(self):
        _legacy(self.con, status="dispatched")
        r = actions.scan(self.con, DAY, model="m", call=lambda p: ANSWER)
        self.assertEqual(r["inserted"], 0)
        self.assertEqual(len(actions.records(self.con)), 1)

    def test_new_ids_never_collide_with_old_ones(self):
        """umbra:<day>:<id> is what the destination dedupes on. A new proposal
        reusing an old id would be answered with the old task's receipt and
        never sent."""
        old = _legacy(self.con, quote="另一句话")
        self.con.execute("UPDATE sqlite_sequence SET seq=500 WHERE name='proposals'")
        self.con.commit()
        actions.scan(self.con, DAY, model="m", call=lambda p: ANSWER)
        new = [r for r in actions.records(self.con) if r["src"] == "item"][0]
        self.assertGreater(new["id"], 500)
        self.assertNotEqual(new["id"], old)

    def test_both_are_listed_as_one(self):
        _legacy(self.con, quote="另一句话")
        actions.scan(self.con, DAY, model="m", call=lambda p: ANSWER)
        self.assertEqual(sorted(r["src"] for r in actions.records(self.con)),
                         ["item", "legacy"])
        self.assertEqual(len(actions.pending(self.con)), 2)


class SendTest(unittest.TestCase):
    """Sending and verdicts, for a proposal in each store."""

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, QUOTE)
        self.old = _legacy(self.con, quote="上周说的另一件事", t="08:00")
        actions.scan(self.con, DAY, model="m", call=lambda p: ANSWER)
        self.new = [r for r in actions.records(self.con) if r["src"] == "item"][0]["id"]
        self.sent = []
        conf = {"plugins": {"actions": {"sink": "test", "send": True}}}
        self.patches = [
            mock.patch.object(actions, "_conf", return_value=conf),
            mock.patch.object(sink, "resolve", return_value={
                "send": lambda item: self.sent.append(item) or f"t-{len(self.sent)}"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_both_go_out_and_both_are_marked_sent(self):
        n = actions.dispatch(self.con, log=lambda *_: None)
        self.assertEqual(n, 2)
        st = {r["id"]: (r["status"], r["dispatch_id"]) for r in actions.records(self.con)}
        self.assertEqual(st[self.old][0], "dispatched")
        self.assertEqual(st[self.new][0], "dispatched")
        self.assertTrue(st[self.new][1])

    def test_a_new_send_is_recorded_where_sends_live(self):
        actions.dispatch(self.con, log=lambda *_: None)
        got = sink.state(self.con, "actions", str(self.new), "test")
        self.assertEqual(got["status"], sink.SENT)

    def test_the_key_names_each_by_its_own_id(self):
        actions.dispatch(self.con, log=lambda *_: None)
        self.assertEqual(sorted(i["key"] for i in self.sent),
                         sorted([f"umbra:{DAY}:{self.old}", f"umbra:{DAY}:{self.new}"]))

    def test_a_verdict_lands_on_each(self):
        actions.dispatch(self.con, log=lambda *_: None)
        n = actions.record_verdicts(self.con, [
            {"externalEventKey": f"umbra:{DAY}:{self.old}", "verdict": "ran"},
            {"externalEventKey": f"umbra:{DAY}:{self.new}", "verdict": "not_a_task",
             "verdictNote": "开玩笑"}])
        self.assertEqual(n, 2)
        st = {r["id"]: (r["status"], r["verdict"]) for r in actions.records(self.con)}
        self.assertEqual(st[self.old], ("approved", "ran"))
        self.assertEqual(st[self.new], ("dismissed", "not_a_task"))
        self.assertEqual([x["note"] for x in actions.negatives(self.con)], ["开玩笑"])

    def test_a_verdict_for_another_day_is_ignored(self):
        n = actions.record_verdicts(self.con, [
            {"externalEventKey": f"umbra:2026-01-01:{self.new}", "verdict": "ran"}])
        self.assertEqual(n, 0)


class LateRecordingTest(unittest.TestCase):
    def test_a_sent_proposal_survives_its_window_being_judged_again(self):
        """The case the old replace-on-rejudge store would have broken: a late
        recording changes the window, and the model this time does not mention
        the task that has already gone out."""
        con = _con()
        _speak(con, 550, QUOTE)
        actions.scan(con, DAY, model="m", call=lambda p: ANSWER)
        pid = actions.records(con)[0]["id"]
        actions._write(con, pid, "dispatched", dispatch_id="t-1")
        _speak(con, 560, "顺便把酒店也看一下")
        r = actions.scan(con, DAY, model="m", call=lambda p: "[]")
        self.assertEqual(r["windows"], 1)
        self.assertEqual([(x["id"], x["status"]) for x in actions.records(con)],
                         [(pid, "dispatched")])


if __name__ == "__main__":
    unittest.main()


class CardLanguageTest(unittest.TestCase):
    """The card sent with each proposal was Chinese whatever the language. It now
    follows [infer] lang; the zh wording is what the lane always sent."""

    def _card(self, lg):
        from umbra import infer
        con = _con()
        _speak(con, 548, "先把行程定下来")
        _speak(con, 550, QUOTE)
        with mock.patch.object(infer, "lang", return_value=lg):
            return actions.item_prompt(con, DAY, "09:10", "Book the flights", QUOTE)

    def test_english_by_default_shape(self):
        card = self._card("en")
        self.assertIn("Their words:", card)
        self.assertIn(actions.QUOTE_MARK_EN, card)
        self.assertIn("not been inferred", card)
        body = card.replace(QUOTE, "").replace("先把行程定下来", "")
        self.assertNotRegex(body, r"[一-鿿]")

    def test_chinese_keeps_the_old_wording(self):
        card = self._card("zh")
        self.assertIn("他的原话：「", card)
        self.assertIn("── 出处 ", card)
        self.assertIn(actions.QUOTE_MARK_ZH, card)
