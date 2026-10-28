"""The fact-checking lane on the core's findings store, read with its old tables.

Claims found before the move are in `claims` (with their digest deliveries in
claim_cards and claim_card_items); new ones are in analyzer_items, with the same
bookkeeping in each item's meta. The lane serves both as one list. Pinned here:
a new claim makes the whole trip — checked, carded, delivered — and a day that
mixes the two stores is still sent once, with late claims as a supplement.
"""

import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra import analyze, analyzer, db, sink
from umbra.plugins import claims

DAY = "2026-08-19"
QUOTE = "美国驾照在新西兰直接能租车，不用办国际驾照"
ANS = json.dumps([{"time": "09:10", "speaker": "Speaker 2",
                   "claim": "持有效美国驾照在新西兰可以直接租车",
                   "quote": QUOTE, "kind": "rule", "verdict": "unsure",
                   "absolute": True, "why": "x"}], ensure_ascii=False)


def _con():
    con = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    con.executescript(db.SCHEMA)
    return con


def _talk(con, minute=550):
    """Two people, so the window passes the multi-speaker gate."""
    lo, _ = analyze.day_bounds(DAY)
    db.insert(con, [
        (f"p:{minute}a", lo + minute * 60000, 2000, "plaud", "speech",
         {"text": "我们下个月去新西兰", "speaker": "Speaker 1", "file_id": "f"}),
        (f"p:{minute}b", lo + (minute + 1) * 60000, 2000, "plaud", "speech",
         {"text": QUOTE, "speaker": "Speaker 2", "file_id": "f"})])


def _legacy(con, quote="另一条旧断言", status="new", **kw):
    cols = {"day": DAY, "t": "08:00", "speaker": "X", "claim": "旧的", "quote": quote,
            "kind": "rule", "verdict": "likely_wrong", "absolute": 0,
            "status": status, **kw}
    cur = con.execute(f"INSERT INTO claims ({', '.join(cols)}) "
                      f"VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    con.commit()
    return cur.lastrowid


@contextlib.contextmanager
def _sending(posts, get=None):
    conf = {"plugins": {"claims": {"sink": "butler", "send": True}}}

    def post(u, b, h=None, **k):
        posts.append(b)
        return {"taskId": f"t-{len(posts)}", "status": "proposed"}

    with mock.patch.dict(os.environ, {"UMBRA_BUTLER_URL": "https://b.example",
                                      "UMBRA_BUTLER_PAT": "x"}), \
         mock.patch.object(claims, "_conf", return_value=conf), \
         mock.patch("umbra.net.post_json", post), \
         mock.patch("umbra.net.get_json", get or (lambda *a, **k: {})):
        yield


class ScanTest(unittest.TestCase):
    def test_a_new_claim_is_kept_as_a_finding(self):
        con = _con()
        _talk(con)
        r = claims.scan(con, DAY, model="m", call=lambda p: ANS)
        self.assertEqual(len(r["fresh"]), 1)
        got = claims.records(con)
        self.assertEqual((got[0]["src"], got[0]["status"]), ("item", "new"))

    def test_a_quote_the_old_table_holds_is_not_found_again(self):
        con = _con()
        _talk(con)
        _legacy(con, quote=QUOTE, status="checked")
        claims.scan(con, DAY, model="m", call=lambda p: ANS)
        self.assertEqual([r["src"] for r in claims.records(con)], ["legacy"])

    def test_windows_the_old_cache_answered_are_not_asked_again(self):
        con = _con()
        _talk(con)
        w = analyzer.windows(con, claims, DAY, "m")[0]
        con.execute("INSERT INTO claim_digests (day, model, a_min, n_seg, ok, text)"
                    " VALUES (?,?,?,?,?,?)", (DAY, "m", w["a"], 2, 1, w["text"]))
        con.commit()
        calls = []
        claims.scan(con, DAY, model="m", call=lambda p: calls.append(p) or ANS)
        self.assertEqual(calls, [])

    def test_the_rendering_is_the_one_the_old_lane_cached(self):
        """The old scan rendered through this same render_window at zero
        context, over the day's speech without marks. Same text, same cache."""
        con = _con()
        _talk(con)
        lo, hi = analyze.day_bounds(DAY)
        evs = [e for e in db.load_range(con, lo, hi)
               if e.get("source") == "plaud" and e.get("kind") != "mark"]
        new = {w["a"]: w["text"] for w in analyzer.windows(con, claims, DAY, "m")}
        old = {a: claims.render_window(evs, a, a + 30, lo, ctx=0)
               for a in range(0, 1440, 30)}
        self.assertEqual({a: t for a, t in old.items() if t.strip()}, new)


class LifecycleTest(unittest.TestCase):
    """A claim found after the move makes the whole trip."""

    def setUp(self):
        self.con = _con()
        _talk(self.con)
        claims.scan(self.con, DAY, model="m", call=lambda p: ANS)
        self.id = claims.records(self.con)[0]["id"]

    def _status(self):
        return claims.records(self.con)[0]

    def test_sent_checked_carded_delivered(self):
        posts = []
        with _sending(posts, get=lambda *a, **k: {"status": "done",
                                                  "finalText": "报告：说对了"}):
            tid = claims.dispatch(self.con, log=lambda *_: None)
            self.assertEqual(self._status()["status"], "checking")
            self.assertEqual(self._status()["check_task"], tid)
            self.assertEqual(claims.pull(self.con, log=lambda *_: None), 1)
            self.assertEqual(self._status()["report"], "报告：说对了")
            self.assertEqual(claims.render_cards(
                self.con, call=lambda p: "卡片正文", log=lambda *_: None), 1)
            self.assertEqual(self._status()["card"], "卡片正文")
            self.assertEqual(claims.digest(self.con, log=lambda *_: None), 1)
        self.assertTrue(self._status()["delivered"])
        self.assertEqual(posts[-1]["title"], f"{DAY} fact check (1)")
        with _sending(posts):
            self.assertEqual(claims.digest(self.con, log=lambda *_: None), 0,
                             "a delivered day is not sent again")

    def test_the_check_send_is_recorded_where_sends_live(self):
        posts = []
        with _sending(posts):
            claims.dispatch(self.con, log=lambda *_: None)
        self.assertEqual(sink.state(self.con, "claims", f"check:{DAY}:1",
                                    "butler")["status"], sink.SENT)

    def test_a_failed_check_returns_the_claim_to_the_queue(self):
        posts = []
        with _sending(posts, get=lambda *a, **k: {"status": "failed"}):
            claims.dispatch(self.con, log=lambda *_: None)
            claims.pull(self.con, log=lambda *_: None)
        got = self._status()
        self.assertEqual((got["status"], got["check_task"]), ("new", None))

    def test_a_resend_uses_a_new_attempt_in_the_key(self):
        posts = []
        with _sending(posts, get=lambda *a, **k: {"status": "failed"}):
            claims.dispatch(self.con, log=lambda *_: None)
            claims.pull(self.con, log=lambda *_: None)
            claims.dispatch(self.con, log=lambda *_: None)
        keys = [p["externalEventKey"] for p in posts]
        self.assertEqual(len(set(keys)), 2)


class MixedDayTest(unittest.TestCase):
    def test_a_day_carded_before_the_move_gets_new_claims_as_a_supplement(self):
        """The old ledger says the day had its card. A claim found since is late
        content for that day, not a second first digest."""
        con = _con()
        old = _legacy(con, quote="旧的", status="checked", card="旧卡")
        con.execute("INSERT INTO claim_cards (day,task,created_ms) VALUES (?,?,?)",
                    (DAY, "t-old", 1))
        con.execute("INSERT INTO claim_card_items (claim_id,task,created_ms) "
                    "VALUES (?,?,?)", (old, "t-old", 1))
        con.commit()
        _talk(con)
        claims.scan(con, DAY, model="m", call=lambda p: ANS)
        new = [r for r in claims.records(con) if r["src"] == "item"][0]
        claims._write(con, new, "checked", report="r", card="新卡")
        con.commit()
        posts = []
        with _sending(posts):
            self.assertEqual(claims.digest(con, log=lambda *_: None), 1)
        self.assertIn("supplement", posts[0]["title"])
        self.assertEqual(posts[0]["prompt"], "新卡")

    def test_a_day_with_claims_in_both_stores_waits_for_both(self):
        """Half a report misleads more than none: a day still checking in either
        store is not sent."""
        con = _con()
        _legacy(con, quote="旧的", status="checking", check_task="t-9")
        _talk(con)
        claims.scan(con, DAY, model="m", call=lambda p: ANS)
        new = [r for r in claims.records(con) if r["src"] == "item"][0]
        claims._write(con, new, "checked", report="r", card="新卡")
        con.commit()
        posts = []
        with _sending(posts):
            self.assertEqual(claims.digest(con, log=lambda *_: None), 0)


if __name__ == "__main__":
    unittest.main()


class EnglishPromptsTest(unittest.TestCase):
    """The lane shipped with PROMPT_EN = PROMPT_ZH: an English user's model was
    asked in Chinese, and the verification job and card answered in Chinese."""

    def _lang(self, lg):
        from umbra import infer
        return mock.patch.object(infer, "lang", return_value=lg)

    def test_the_extraction_prompt_is_english_with_the_same_placeholders(self):
        import re
        self.assertNotEqual(claims.PROMPT_EN, claims.PROMPT_ZH)
        self.assertNotRegex(claims.PROMPT_EN, r"[一-鿿]")
        find = lambda p: sorted(set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", p)))
        self.assertEqual(find(claims.PROMPT_EN), find(claims.PROMPT_ZH))
        claims.PROMPT_EN.format(day="d", today="t", text="x")

    def test_the_prompt_follows_the_language(self):
        with self._lang("en"):
            self.assertIs(claims._prompt(), claims.PROMPT_EN)
        with self._lang("zh"):
            self.assertIs(claims._prompt(), claims.PROMPT_ZH)

    def test_the_verification_job_follows_the_language(self):
        row = {"day": DAY, "t": "09:10", "speaker": "S1", "claim": "c",
               "quote": "q", "verdict": "unsure", "absolute": 1, "why": "w"}
        with self._lang("en"):
            en = claims.check_prompt([row])
        with self._lang("zh"):
            zh = claims.check_prompt([row])
        self.assertNotRegex(en, r"[一-鿿]")
        self.assertIn("Answer in English", en)
        self.assertIn("中文回答", zh)

    def test_the_card_prompt_follows_the_language_and_forbids_markdown(self):
        with self._lang("en"):
            p = claims.card_prompt()
        self.assertNotRegex(p, r"[一-鿿]")
        self.assertIn("plain text", p.lower())
        p.format(report="r")
