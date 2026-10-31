"""Fact-checking: claims said aloud in conversation → verified online → one
daily card.

The second lane beside actions (tasks from speech). It shares digest.windows()'s
30-minute grid and actions.render()'s window rendering, and **nothing else** —
its own table, prompt and calls. For the same reason task extraction never
rode along with infer: one call doing "find claims" and "find tasks" makes both
worse, and leaves no way to cache just one half incrementally.

## Two stages, because 27 claims a day cannot all be checked online

    ①  extract claims + the model's own first verdict from memory   cheap, local
    ②  send only what the filter keeps for online verification      costly, ~7 a day

## How the filter was set (measured 2026-08-16 to 22, 99 claims verified online)

Claims with a threshold number / effective date / exception clause / changing
price (absolute) judged fine offline were **wrong 33%** of the time; definitions,
physical constants and settled law were 100% right.
So it is not "trust the model or not" but **trust it by category**.

Wrong the other way too: the offline model called all news from the weeks before
a recording false, a 22% false-alarm rate (Stripe buying OpenRouter, SpaceX
buying Cursor, Apple launching leasing — all true).
So the prompt must carry today's date and a reminder that knowledge has a
cutoff, so "never heard of it" lands on unsure rather than likely_wrong.
"""

import json
import re
import contextlib
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from umbra.plugins import claims
from umbra import analyze, db

DAY = "2026-08-19"


def _con():
    con = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    con.executescript(db.SCHEMA)
    return con


ANS = json.dumps([{
    "time": "09:10", "speaker": "Speaker 2",
    "claim": "持有效美国驾照在新西兰可以直接租车，不需要国际驾照",
    "quote": "美国驾照在新西兰直接能租车，不用办国际驾照",
    "kind": "rule", "verdict": "confident_right", "absolute": True,
    "why": "新西兰承认英文驾照",
}])


SEND_ON = {"plugins": {"claims": {"sink": "butler", "send": True}}}


@contextlib.contextmanager
def _sending():
    """Credentials plus the send gate, for tests that exercise a send path.

    Sending is off unless a destination is named and sending is switched on, and
    that gate now sits inside each sending function rather than only at the ops
    layer that calls them. A function with an external effect should not have
    that effect when sending is off, whoever called it — so a test driving one
    turns sending on, the same as a person would.
    """
    with mock.patch.dict(os.environ, {"UMBRA_BUTLER_URL": "https://b.example",
                                      "UMBRA_BUTLER_PAT": "but_x"}), \
            mock.patch.object(claims, "_conf", return_value=SEND_ON):
        yield




def _q(con, sql, args=()):
    """Run a query written against the old claims table over every claim, old
    and new — claims.records() materialised as a temporary table.

    These tests were written when the lane had a table of its own, and their
    assertions are the evidence that moving it onto the core store changed no
    behaviour. This keeps every assertion as it was and points it at the view
    the lane now serves.
    """
    rows = claims.records(con)
    cols = [c.strip() for c in claims._LEGACY_COLS.split(",")] + ["src", "delivered"]
    con.execute("DROP TABLE IF EXISTS temp._c")
    con.execute(f"CREATE TEMP TABLE _c ({', '.join(cols)})")
    con.executemany(f"INSERT INTO temp._c VALUES ({','.join('?' * len(cols))})",
                    [[r.get(c) for c in cols] for r in rows])
    return con.execute(re.sub(r"FROM claims\b", "FROM temp._c", sql), args)

class ScanTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text, speaker="X"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{minute}:{speaker}", self.lo + minute * 60000, 2000,
             "plaud", "speech", json.dumps({"text": text, "speaker": speaker})))
        self.con.commit()

    def _two(self):
        self._speak(550, "美国驾照在新西兰直接能租车，不用办国际驾照", "Speaker 2")
        self._speak(551, "是吗？我以为要办的", "X")

    def test_extracts_claims_with_kind_and_own_verdict(self):
        self._two()
        claims.scan(self.con, DAY, call=lambda p: ANS)
        row = _q(self.con, 
            "SELECT kind, verdict, absolute, speaker, claim FROM claims").fetchone()
        self.assertEqual(row[0], "rule")
        self.assertEqual(row[1], "confident_right")
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], "Speaker 2")
        self.assertIn("新西兰", row[4])

    def test_only_multi_speaker_windows_are_scanned(self):
        """A stretch of one person dictating to an agent has no "claims made to
        others"; scanning it is waste — measured, such windows yield only travel
        plans."""
        self._speak(550, "美国驾照在新西兰直接能租车", "X")   # one speaker only
        seen = []
        claims.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        self.assertEqual(seen, [])

    def test_the_prompt_has_today_s_date_and_a_cutoff_reminder(self):
        """Measured, the offline model called all news from the weeks before a
        recording false (22% false alarms). The most telling: it worked out
        "Burning Man's 40th is in 2026" without knowing it already was 2026."""
        self._two()
        seen = []
        claims.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        self.assertIn(DAY, seen[0])
        self.assertIn("knowledge has a cutoff", seen[0])
        self.assertIn("unsure", seen[0])

    def test_no_quote_is_not_collected(self):
        ans = json.dumps([{"time": "09:10", "claim": "某个断言",
                           "kind": "rule", "verdict": "unsure"}])
        self._two()
        claims.scan(self.con, DAY, call=lambda p: ans)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM claims").fetchone()[0], 0)

    def test_context_lines_are_not_this_window_s_output(self):
        """The same discipline as actions, reusing its ownership check."""
        self._speak(520, "早先说的一句", "X")
        self._speak(521, "嗯", "Speaker 2")
        self._speak(555, "美国驾照在新西兰直接能租车，不用办国际驾照", "Speaker 2")
        self._speak(556, "是吗", "X")
        claims.scan(self.con, DAY, call=lambda p: ANS)
        rows = list(_q(self.con, "SELECT win_a FROM claims"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 540)

    def test_rescanning_does_not_duplicate(self):
        self._two()
        claims.scan(self.con, DAY, call=lambda p: ANS)
        claims.scan(self.con, DAY, fresh=True, call=lambda p: ANS)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM claims").fetchone()[0], 1)

    def test_incremental_unchanged_windows_are_not_called_again(self):
        self._two()
        claims.scan(self.con, DAY, call=lambda p: ANS)
        n = []
        r = claims.scan(self.con, DAY, call=lambda p: n.append(p) or ANS)
        self.assertEqual(n, [])
        self.assertEqual(r["reused"], 1)

    def test_failed_windows_are_retried_next_round(self):
        self._two()
        def boom(p):
            raise RuntimeError("429")
        r = claims.scan(self.con, DAY, call=boom)
        self.assertEqual(r["failed"], 1)
        n = []
        claims.scan(self.con, DAY, call=lambda p: n.append(p) or ANS)
        self.assertEqual(len(n), 1)

    def test_unrecognised_kinds_and_verdicts_are_not_guessed(self):
        ans = json.dumps([{"time": "09:10", "claim": "c", "quote": "美国驾照在新西兰直接能租车",
                           "kind": "外星", "verdict": "大概吧", "speaker": "X"}])
        self._two()
        claims.scan(self.con, DAY, call=lambda p: ans)
        row = _q(self.con, "SELECT kind, verdict FROM claims").fetchone()
        self.assertEqual(row, ("other", "unsure"))


class GateTest(unittest.TestCase):
    """The filter. Two rules set by seven days of measurement, not picked."""

    def setUp(self):
        self.con = _con()

    def _add(self, claim, verdict="confident_right", absolute=0, kind="rule"):
        self.con.execute(
            "INSERT INTO claims (day, t, speaker, claim, quote, kind, verdict,"
            " absolute, status) VALUES (?,?,?,?,?,?,?,?,'new')",
            (DAY, "09:10", "X", claim, f"q-{claim}", kind, verdict, absolute))
        self.con.commit()

    def _names(self):
        return [r["claim"] for r in claims.checkable(self.con)]

    def test_doubtful_own_verdicts_are_checked(self):
        self._add("可疑的", verdict="likely_wrong")
        self._add("拿不准的", verdict="unsure")
        self.assertEqual(set(self._names()), {"可疑的", "拿不准的"})

    def test_absolute_ones_are_checked_even_when_the_model_says_fine(self):
        """Measured, 33% of these judged fine offline were wrong — and 10 of 12
        errors were in this category."""
        self._add("满 8 年之前注销就不算长期居民", absolute=1)
        self.assertEqual(self._names(), ["满 8 年之前注销就不算长期居民"])

    def test_definitions_and_constants_pass(self):
        """Things like "1 kWh is one unit of electricity": 100% right over seven
        days; checking them is waste."""
        self._add("1 度电等于 1 千瓦时", absolute=0)
        self.assertEqual(self._names(), [])

    def test_checked_ones_are_not_checked_again(self):
        self._add("查过了", verdict="likely_wrong")
        self.con.execute("UPDATE claims SET status='checked'")
        self.con.commit()
        self.assertEqual(self._names(), [])


class DispatchTest(unittest.TestCase):
    """One task per batch, not per claim — it ends as one daily card, and checking
    separately means N agents each writing a report unaware of the others."""

    def setUp(self):
        self.con = _con()
        for i in range(3):
            self.con.execute(
                "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
                " absolute,status) VALUES (?,?,?,?,?,?,?,?,'new')",
                (DAY, "09:10", "X", f"断言{i}", f"原话{i}", "rule",
                 "likely_wrong", 0))
        self.con.commit()

    def _env(self):
        return _sending()

    def test_a_batch_goes_out_as_one_read_only_task(self):
        posts = []
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda u, b, h=None, **k: posts.append(b) or {"taskId": "t-1"}):
            tid = claims.dispatch(self.con)
        self.assertEqual(tid, "t-1")
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0]["readonly"])      # a read-only discussion, never merged
        for i in range(3):
            self.assertIn(f"断言{i}", posts[0]["prompt"])

    def test_sent_ones_are_marked_checking_with_the_task_id(self):
        with self._env(), mock.patch(
                "umbra.net.post_json", lambda *a, **k: {"taskId": "t-1"}):
            claims.dispatch(self.con)
        rows = list(_q(self.con, 
            "SELECT DISTINCT status, check_task FROM claims"))
        self.assertEqual(rows, [("checking", "t-1")])

    def test_a_failed_send_leaves_status_alone(self):
        def boom(*a, **k):
            raise RuntimeError("503")
        with self._env(), mock.patch("umbra.net.post_json", boom):
            self.assertIsNone(claims.dispatch(self.con))
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM claims WHERE status='new'")
            .fetchone()[0], 3)

    def test_nothing_to_check_sends_nothing(self):
        self.con.execute("UPDATE claims SET status='checked'")
        self.con.commit()
        with self._env():
            self.assertIsNone(claims.dispatch(self.con))

    def test_the_prompt_does_not_treat_offline_reasons_as_findings(self):
        """Measured, 2 offline "wrong" verdicts were right, but the correct answer
        they gave was itself wrong. So the first verdict is a lead, never part of
        the final report."""
        posts = []
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda u, b, h=None, **k: posts.append(b) or {"taskId": "t-1"}):
            claims.dispatch(self.con)
        self.assertIn("a lead, not a finding", posts[0]["prompt"])

    def test_a_duplicate_reply_is_not_counted_as_sent(self):
        """Butler dedupes on externalEventKey and on a hit **returns the old taskId
        as it is** plus duplicate: true
        (apps/web/app/api/external/dispatch/route.ts).

        Reading only taskId, an old task that already failed is taken as "just
        sent" and attached to the claims again — pull judges it failed, back to
        new, sent again, the same corpse returned. The 15 claims of 2026-08-25
        stuck forever that way: no error, logs green, status bouncing between new
        and checking, never settling.

        The same discipline as "a failed send leaves status alone": not really
        sent, status unchanged."""
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda *a, **k: {"taskId": "t-dead", "duplicate": True}):
            self.assertIsNone(claims.dispatch(self.con))
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM claims WHERE status='new'")
            .fetchone()[0], 3)
        self.assertEqual(
            _q(self.con, 
                "SELECT COUNT(*) FROM claims WHERE check_task IS NOT NULL")
            .fetchone()[0], 0)

    def test_a_resend_uses_a_different_idempotency_key(self):
        """Recognising duplicate is not enough — with the same key every time, a
        resend is **bound** to be a duplicate and that day can never be checked.
        So the key must carry the attempt number.

        digest() learned this long ago (see the note in claims.py on the v3/v1
        versions in the key); dispatch() had not caught up."""
        keys = []

        def grab(u, b, h=None, **k):
            keys.append(b["externalEventKey"])
            return {"taskId": f"t-{len(keys)}"}

        with self._env(), mock.patch("umbra.net.post_json", grab):
            claims.dispatch(self.con)
            # the state after pull() claims a failure: back to new, task id cleared
            self.con.execute(
                "UPDATE claims SET status='new', check_task=NULL")
            self.con.commit()
            claims.dispatch(self.con)
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])


class OneWaySinkTest(unittest.TestCase):
    """A destination that cannot answer still gets the claims; nothing waits.

    The first cut refused such a sink outright: sending had moved every claim to
    `checking`, and pull looks for a receipt that a command or a webhook never
    gives, so the day sat there forever. Refusing fixed that by sending nothing
    at all — which left anyone without a round-trip destination with no way to
    get the claims out of Umbra, to a channel to read or a script of their own
    that does the lookup.

    So a one-way send ends at `sent`. Nothing is pending, nothing is retried and
    no digest is built, because no report is coming to build it from.
    """

    TODAY = "2026-08-20"

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.out = os.path.join(tempfile.mkdtemp(), "out.jsonl")
        self._add(DAY, "09:00", "a claim", "the words")

    def _add(self, day, t, claim, quote, verdict="likely_wrong"):
        self.con.execute(
            "INSERT INTO claims (day,t,speaker,claim,quote,why,verdict,absolute,"
            " status) VALUES (?,?,?,?,?,?,?,?,'new')",
            (day, t, "Speaker 2", claim, quote, "it has a date in it", verdict, 1))
        self.con.commit()

    def _conf(self, command=None):
        cmd = command or f'printf "%s\\n" "$UMBRA_ITEM" >> {self.out}'
        return {"plugins": {"claims": {"sink": "oneway", "send": True}},
                "sinks": {"oneway": {"kind": "command", "command": cmd}}}

    def _dispatch(self, conf=None, msgs=None):
        conf = conf or self._conf()
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf):
            return claims.dispatch(self.con, log=(msgs if msgs is not None
                                                  else []).append,
                                   today=self.TODAY)

    def _sent(self):
        if not os.path.exists(self.out):
            return []
        with open(self.out, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _status(self):
        return [r[0] for r in _q(self.con, "SELECT status FROM claims ORDER BY day")]

    def test_the_claims_go_out_readable_and_structured(self):
        self._dispatch()
        sent = self._sent()
        self.assertEqual(len(sent), 1)
        item = sent[0]
        self.assertIn(DAY, item["title"])
        self.assertIn("1 claim from", item["body"])
        # For a person reading it in a channel: the drawer's row, in text.
        for part in ("09:00", "a claim", "the words", "likely wrong",
                     "it has a date in it"):
            self.assertIn(part, item["body"])
        # For a script that does its own lookup: the instructions and the rows.
        self.assertIn("verify each one online", item["prompt"])
        self.assertEqual([c["claim"] for c in item["claims"]], ["a claim"])
        self.assertEqual(item["claims"][0]["quote"], "the words")
        self.assertTrue(item["visible"])

    def test_the_body_reads_in_the_order_things_were_said(self):
        """The batch is ordered for the lookup (absolute ones first); a person
        reading it in a channel follows the day."""
        self._add(DAY, "08:30", "an earlier claim", "earlier words")
        self.con.execute("UPDATE claims SET absolute=0 WHERE t='08:30'")
        self.con.commit()
        self._dispatch()
        body = self._sent()[0]["body"]
        self.assertLess(body.index("08:30"), body.index("09:00"))

    def test_sent_is_final_and_never_resent(self):
        self._dispatch()
        self.assertEqual(self._status(), ["sent"])
        self._dispatch()
        self.assertEqual(len(self._sent()), 1)
        # pull has nothing to ask about, and leaves it alone.
        with mock.patch.object(claims, "_conf", return_value=self._conf()):
            self.assertEqual(claims.pull(self.con), 0)
        self.assertEqual(self._status(), ["sent"])

    def test_a_failed_send_leaves_them_new(self):
        msgs = []
        self._dispatch(self._conf("exit 3"), msgs)
        self.assertEqual(self._status(), ["new"])
        self.assertTrue(any("✗" in m for m in msgs), msgs)

    def test_each_recent_day_goes_as_its_own_item_in_one_round(self):
        self._add("2026-08-20", "10:00", "today's claim", "today's words")
        self._dispatch()
        self.assertEqual([i["day"] for i in self._sent()], [DAY, "2026-08-20"])
        self.assertEqual(self._status(), ["sent", "sent"])

    def test_a_backlog_older_than_a_week_stays_in_the_drawer(self):
        """Switching sending on must not flood the destination with weeks of
        stale claims: only the last seven days go."""
        self._add("2026-08-13", "11:00", "eight days old", "old words")   # out
        self._add("2026-08-14", "11:00", "seven days old", "old words")   # in
        self._dispatch()
        bodies = " ".join(i["body"] for i in self._sent())
        self.assertNotIn("eight days old", bodies)
        self.assertIn("seven days old", bodies)
        self.assertEqual(self._status(), ["new", "sent", "sent"])

    def test_the_cli_says_what_went_out(self):
        """`umbra claims check` printed "nothing needs checking" right under the
        line saying the claims had gone: a one-way send returns no receipt to
        follow up on, and the CLI read that as nothing sent."""
        import io
        import types
        conf = self._conf()
        out = io.StringIO()
        with mock.patch.object(claims, "_conf", return_value=conf), \
                mock.patch("umbra.config.load", return_value=conf), \
                mock.patch("umbra.db.connect", return_value=self.con), \
                mock.patch("time.strftime", return_value=self.TODAY), \
                contextlib.redirect_stdout(out):
            claims.cli(types.SimpleNamespace(action="check", id=None))
        self.assertIn("sent 1", out.getvalue())
        self.assertNotIn("nothing needs checking", out.getvalue())

    def test_a_round_trip_sink_still_waits_for_its_answer(self):
        with _sending(), mock.patch("umbra.net.post_json",
                                    lambda *a, **k: {"taskId": "t-1"}):
            self.assertEqual(claims.dispatch(self.con), "t-1")
        self.assertEqual(self._status(), ["checking"])


class PullRecoveryTest(unittest.TestCase):
    """A failed task goes back to the queue. Marking it checked is "said it was
    checked, wasn't"."""

    def setUp(self):
        self.con = _con()
        for i in range(3):
            self.con.execute(
                "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
                " absolute,status,check_task) VALUES (?,?,?,?,?,?,?,?,"
                "'checking','t-dead')",
                (DAY, "09:10", "X", f"断言{i}", f"原话{i}", "rule",
                 "likely_wrong", 0))
        self.con.commit()

    def test_the_reported_count_is_this_task_s_claims_not_every_change(self):
        """The number in that log line has to come from cur.rowcount.

        con.total_changes counts every change since the connection opened, so a
        digest that wrote claim_card_items earlier in the same round gets folded
        in and the line reports a number far larger than the truth. A person
        reads it to decide how many claims need rechecking.
        """
        msgs = []
        with _sending(), mock.patch("umbra.net.get_json",
                                    lambda *a, **k: {"status": "failed"}):
            # unrelated changes first, to inflate the cumulative counter
            self.con.execute("UPDATE claims SET why='x'")
            self.con.commit()
            claims.pull(self.con, log=msgs.append)
        self.assertTrue(any("3 claims" in m for m in msgs), msgs)


class AnalyzerInterfaceTest(unittest.TestCase):
    """This lane declared as a plugin, run by the core rather than by itself.

    The declaration is the open-source deliverable: what a community lane has to
    write is a prompt and a parser, and everything this one needed beyond that
    turned out to be a gap in the interface rather than a special case — parse
    could not see its own window, PROMPT could not be chosen at run time, and
    {today} had nowhere to come from.
    """

    DAY = "2026-09-15"
    LONG = "the visa waiver is ninety days for most countries"
    REPLY = json.dumps([{"claim": "The visa waiver is 90 days",
                         "quote": LONG, "kind": "number", "verdict": "unsure",
                         "absolute": True, "time": "09:05", "speaker": "S2"}])

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        lo, _hi = analyze.day_bounds(self.DAY)
        db.insert(self.con, [
            (f"p:{i}", lo + m * 60000, 0, "plaud", "speech",
             {"text": t, "speaker": sp, "file_id": "aa"})
            for i, (m, t, sp) in enumerate(
                [(541, "we were talking about travel", "S1"),
                 (545, self.LONG, "S2")])])

    def _run(self, reply=None, **kw):
        from umbra import analyzer
        return analyzer.run(self.con, "claims", claims, self.DAY, "m",
                            call=lambda p: reply or self.REPLY, **kw)

    def test_it_declares_the_interface(self):
        for name in ("INPUT", "WINDOW", "PROMPT", "parse", "render_window"):
            with self.subTest(name):
                self.assertTrue(hasattr(claims, name))
        self.assertEqual(claims.INPUT, "events:plaud")

    def test_the_core_runs_it_and_stores_rows(self):
        from umbra import analyzer
        r = self._run()
        self.assertEqual((r["windows"], r["called"], r["rows"]), (48, 1, 1))
        row = analyzer.load_items(self.con, "claims", self.DAY)[0]
        self.assertEqual(row["claim"], "The visa waiver is 90 days")
        self.assertEqual(row["absolute"], 1)

    def test_an_unchanged_rerun_calls_nothing(self):
        self._run()
        self.assertEqual(self._run()["called"], 0)

    def test_a_quote_from_a_neighbouring_window_is_dropped(self):
        """At zero context, "this quote is nowhere in the window" means the
        claim belongs to the next one. The tasks lane runs at thirty minutes of
        context, where the same rule would drag in every neighbour's output —
        which is why this lives in the plugin and not the core."""
        from umbra import analyzer
        stray = json.dumps([{"claim": "x", "quote": "nobody said this at all",
                             "kind": "other", "verdict": "unsure"}])
        self._run(reply=stray)
        self.assertEqual(analyzer.load_items(self.con, "claims", self.DAY), [])

    def test_a_claim_with_no_quote_is_dropped(self):
        """Nothing to check it against."""
        from umbra import analyzer
        self._run(reply=json.dumps([{"claim": "unbacked", "kind": "other"}]))
        self.assertEqual(analyzer.load_items(self.con, "claims", self.DAY), [])

    def test_an_unrecognised_verdict_becomes_unsure_not_fine(self):
        """Better to pay for one more check than to wave something through
        because the model mis-spelled a field."""
        from umbra import analyzer
        odd = json.dumps([{"claim": "c", "quote": self.LONG, "kind": "number",
                           "verdict": "totally-made-up"}])
        self._run(reply=odd)
        row = analyzer.load_items(self.con, "claims", self.DAY)[0]
        self.assertEqual(row["verdict"], "unsure")

    def test_a_single_speaker_window_is_never_sent(self):
        """One person dictating to an agent is not addressing anyone. Measured,
        those windows yield nothing but travel plans."""
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        lo, _hi = analyze.day_bounds(self.DAY)
        db.insert(con, [("p:1", lo + 545 * 60000, 0, "plaud", "speech",
                         {"text": self.LONG, "speaker": "S1", "file_id": "aa"})])
        from umbra import analyzer
        calls = []
        analyzer.run(con, "claims", claims, self.DAY, "m",
                     call=lambda p: calls.append(p) or self.REPLY)
        self.assertEqual(calls, [])

    def test_the_prompt_is_chosen_at_run_time(self):
        """A module-level string would pin whichever language was configured
        when the process first loaded."""
        self.assertTrue(callable(claims.PROMPT))
        self.assertIsInstance(claims.PROMPT(), str)



class CrossRoundDupTest(unittest.TestCase):
    """The same thing said once, extracted again next round in other words, is
    not sent to be checked a second time.

    A window is re-extracted whole whenever its text changes — a late recording,
    or PLAUD rewriting the transcript between two rounds. The model then picks
    the same assertion again, in other words, and the (day, quote) key only
    stops an identical quote: the two rounds often quote **different parts of
    the same passage**. Before this, one assertion was checked twice and showed
    up twice on the card.

    Calibrated across a month of rounds, every pair within two minutes: quote
    containment >= 0.8 **or** claim containment >= 0.75 marked only real repeats,
    and two different things said in the same minute stayed apart.

    A repeat is kept, as `duplicate` — findings are never deleted — and it is
    out of the checking queue and out of the drawer's count.
    """

    PASSAGE = ("the lease lets you leave after twelve months with two months "
               "notice, and the landlord has to return the deposit within thirty "
               "days of moving out, that part is in the state law")

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text, speaker):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{minute}:{speaker}", self.lo + minute * 60000, 2000,
             "plaud", "speech", json.dumps({"text": text, "speaker": speaker})))
        self.con.commit()

    def _old(self, t, claim, quote, status="checked", verdict="confident_right",
             absolute=1):
        """The one an earlier round already extracted (likely already sent)."""
        self.con.execute(
            "INSERT INTO claims (day,t,win_a,claim,quote,verdict,absolute,"
            " status,created_ms) VALUES (?,?,?,?,?,?,?,?,?)",
            (DAY, t, 540, claim, quote, verdict, absolute, status, 1))
        self.con.commit()

    def _scan(self, *items):
        ans = json.dumps([dict({"speaker": "Speaker 1", "kind": "rule",
                                "verdict": "confident_right", "absolute": True},
                               **x) for x in items])
        return claims.scan(self.con, DAY, call=lambda p: ans)

    def _live(self):
        return [r for r in claims.records(self.con) if r["status"] != "duplicate"]

    def test_another_cut_of_the_same_passage_is_not_kept(self):
        self._speak(550, self.PASSAGE, "Speaker 1")
        self._speak(551, "right, makes sense", "Speaker 2")
        self._old("09:10", "A lease may be ended after twelve months with two "
                  "months notice.", self.PASSAGE)
        r = self._scan({
            "time": "09:10",
            "claim": "State law requires a landlord to return a deposit within "
                     "thirty days.",
            "quote": "the landlord has to return the deposit within thirty days of "
                     "moving out, that part is in the state law"})
        self.assertEqual(len(self._live()), 1,
                         "the same passage, quoted from another point, was kept again")
        self.assertEqual(r["dup"], 1)
        self.assertEqual(r["fresh"], [])

    def test_different_quotes_but_nearly_the_same_claim_are_not_kept(self):
        self._speak(550, "the editor is not open source, it is closed, you cannot "
                    "read the code", "Speaker 1")
        self._speak(551, "oh really", "Speaker 2")
        self._old("09:10", "The editor is not open source.",
                  "the editor is not open source")
        self._scan({"time": "09:11",
                    "claim": "The note-taking editor is not open source.",
                    "quote": "it is closed, you cannot read the code"})
        self.assertEqual(len(self._live()), 1)

    def test_different_things_in_the_same_minute_are_both_kept(self):
        """Two assertions about one company in one breath are two things — the
        claim threshold must stay above where they overlap."""
        self._speak(550, "they went public last year, and then the founder stepped "
                    "down in january", "Speaker 1")
        self._speak(551, "yeah", "Speaker 2")
        self._old("09:10", "The company went public last year.",
                  "they went public last year")
        self._scan({"time": "09:10",
                    "claim": "The company's founder stepped down as chief "
                             "executive in January.",
                    "quote": "the founder stepped down in january"})
        self.assertEqual(len(self._live()), 2)

    def test_said_again_much_later_is_really_said_again(self):
        """The same topic in two conversations hours apart — both stay."""
        self._speak(550, self.PASSAGE, "Speaker 1")
        self._speak(551, "right, makes sense", "Speaker 2")
        self._old("07:40", "A lease may be ended after twelve months with two "
                  "months notice.", self.PASSAGE)
        self._scan({"time": "09:10",
                    "claim": "A lease may be ended after twelve months with two "
                             "months notice.",
                    "quote": "the lease lets you leave after twelve months with two "
                             "months notice"})
        self.assertEqual(len(self._live()), 2)

    def test_items_of_one_round_are_not_compared(self):
        """Two items the model split in one round are split on purpose — the
        check only guards across rounds."""
        self._speak(550, "that documentary was shot in twenty seventeen and it is "
                    "streaming now", "Speaker 1")
        self._speak(551, "mm", "Speaker 2")
        self._scan(
            {"time": "09:10", "claim": "The documentary was shot in 2017.",
             "quote": "that documentary was shot in twenty seventeen"},
            {"time": "09:10", "claim": "The documentary is streaming now.",
             "quote": "that documentary was shot in twenty seventeen and it is "
                      "streaming now"})
        self.assertEqual(len(self._live()), 2)

    def test_a_more_doubtful_new_round_raises_the_old_one(self):
        """The first round called it confident_right and not absolute, so it was
        not checked; the reworded second round is unsure. Holding back the
        repeat must not hold back the check too — better one check more."""
        self._speak(550, "and then the cofounder left the board, so now the "
                    "reserves are low", "Speaker 1")
        self._speak(551, "what?", "Speaker 2")
        self._old("09:10", "The cofounder left the nonprofit's board.",
                  "and then the cofounder left the board", status="new", absolute=0)
        self._scan({"time": "09:10",
                    "claim": "The cofounder left the nonprofit's board.",
                    "quote": "and then the cofounder left the board, so now the "
                             "reserves are low",
                    "verdict": "unsure"})
        self.assertEqual(len(self._live()), 1)
        self.assertEqual(len(claims.checkable(self.con)), 1,
                         "the second round doubts it, so it belongs in the queue")

    def test_one_already_sent_is_not_changed(self):
        """Checking or checked keeps its first verdict — the report was written
        against it."""
        self._speak(550, "and then the cofounder left the board, so now the "
                    "reserves are low", "Speaker 1")
        self._speak(551, "what?", "Speaker 2")
        self._old("09:10", "The cofounder left the nonprofit's board.",
                  "and then the cofounder left the board", status="checked", absolute=0)
        self._scan({"time": "09:10", "claim": "The cofounder left the nonprofit's board.",
                    "quote": "and then the cofounder left the board, so", "verdict":
                    "likely_wrong"})
        self.assertEqual(self.con.execute(
            "SELECT verdict, absolute, status FROM claims").fetchone(),
            ("confident_right", 0, "checked"))

    def test_a_repeat_across_two_rounds_of_the_new_store(self):
        """The path as it runs now: round one writes a finding, the transcript is
        rewritten (the window's text changes, so it is asked again), round two
        words the same assertion differently."""
        self._speak(550, self.PASSAGE, "Speaker 1")
        self._speak(551, "right, makes sense", "Speaker 2")
        self._scan({"time": "09:10",
                    "claim": "A landlord must return a deposit within thirty days.",
                    "quote": self.PASSAGE})
        self._speak(552, "is that true everywhere though", "Speaker 2")
        r = self._scan({"time": "09:10",
                        "claim": "State law gives a landlord thirty days to return "
                                 "a deposit.",
                        "quote": "the landlord has to return the deposit within "
                                 "thirty days of moving out"})
        self.assertEqual(r["dup"], 1)
        self.assertEqual(len(self._live()), 1)
        self.assertEqual(len(claims.checkable(self.con)), 1)


class EarliestDayIsNotStarvedTest(unittest.TestCase):
    """pending_day picks the earliest day, but dispatch used to take the global
    top BATCH (absolute ones first) and only then keep that day. Once later days
    held more than BATCH absolute claims, the earliest day's ordinary ones never
    made it in: dispatch returned None with no error, pending_day returned the
    same day next round, and the whole lane stopped."""

    def setUp(self):
        self.con = _con()
        for i in range(3):
            self.con.execute(
                "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
                " absolute,status) VALUES (?,?,?,?,?,?,?,?,'new')",
                ("2026-08-18", "09:10", "X", f"claim{i}", f"quote{i}", "rule",
                 "likely_wrong", 0))
        self.con.executemany(
            "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
            " absolute,status) VALUES (?,?,?,?,?,?,?,?,'new')",
            [(DAY, "10:00", "X", f"later{i}", f"lq{i}", "rule",
              "confident_right", 1) for i in range(claims.BATCH + 5)])
        self.con.commit()

    def test_the_earliest_day_goes_out_even_outside_the_global_top_batch(self):
        posts = []
        with _sending(), mock.patch(
                "umbra.net.post_json",
                lambda u, b, h=None, **k: posts.append(b) or {"taskId": "t-1"}):
            tid = claims.dispatch(self.con)
        self.assertEqual(tid, "t-1")
        self.assertIn("2026-08-18", posts[0]["title"])
        for i in range(3):
            self.assertIn(f"claim{i}", posts[0]["prompt"])


class OneCardPerDayTest(unittest.TestCase):
    """A day's card has one summary and one set of wrong / right.

    Two verification reports for one day were each compressed into a card and
    joined end to end: one full "3 of 15 wrong…" set, then another "4 of 10
    wrong…" — wrong and right twice over.

    Now a day's undelivered reports are compressed together, once. When a report
    arrives late the one already rendered is rendered again with it (one more
    cheap local call); reports already delivered stay out.
    """

    def setUp(self):
        self.con = _con()

    def _checked(self, claim, task, report, card=None):
        self.con.execute(
            "INSERT INTO claims (day,t,claim,quote,verdict,absolute,status,"
            " check_task,report,card,created_ms) VALUES "
            "(?,?,?,?,'unsure',1,'checked',?,?,?,?)",
            (DAY, "09:10", claim, claim + " quote", task, report, card,
             int(time.time() * 1000)))
        self.con.commit()

    def _digest(self):
        posts = []
        with _sending(), mock.patch(
                "umbra.net.post_json",
                lambda u, b, h=None, **k: posts.append(b) or {
                    "taskId": f"d-{len(posts)}", "status": "proposed"}):
            claims.digest(self.con)
        return posts

    def test_two_reports_are_compressed_once(self):
        self._checked("Lease", "t-1", "report one: the lease notice period")
        self._checked("Deposit", "t-2", "report two: the deposit deadline")
        seen = []
        n = claims.render_cards(self.con, call=lambda p: seen.append(p) or "merged card")
        self.assertEqual(len(seen), 1, "two reports for one day became two cards")
        self.assertIn("report one", seen[0])
        self.assertIn("report two", seen[0])
        self.assertEqual(n, 1)
        self.assertEqual(self._digest()[0]["prompt"], "merged card")

    def test_a_late_report_re_renders_the_one_already_rendered(self):
        """The first report was rendered on arrival and the digest waited for the
        second; when it came only the new one was rendered, and the two cards
        were joined."""
        self._checked("Lease", "t-1", "report one: the lease notice period",
                      card="a card of report one only")
        self._checked("Deposit", "t-2", "report two: the deposit deadline")
        seen = []
        claims.render_cards(self.con, call=lambda p: seen.append(p) or "merged card")
        self.assertEqual(len(seen), 1)
        self.assertIn("report one", seen[0])
        self.assertIn("report two", seen[0])
        self.assertEqual(self._digest()[0]["prompt"], "merged card")

    def test_a_delivered_report_stays_out_of_the_supplement(self):
        self._checked("Lease", "t-1", "report one: the lease notice period")
        claims.render_cards(self.con, call=lambda p: "first card")
        self.assertEqual(self._digest()[0]["prompt"], "first card")
        self._checked("Deposit", "t-2", "report two: the deposit deadline")
        seen = []
        claims.render_cards(self.con, call=lambda p: seen.append(p) or "supplement")
        self.assertNotIn("report one", seen[0])
        self.assertEqual(self._digest()[0]["prompt"], "supplement")

    def test_the_prompt_says_several_reports_make_one_card(self):
        for lang in ("en", "zh"):
            with mock.patch.object(claims, "_lang", return_value=lang, create=True):
                p = claims.card_prompt()
            self.assertIn("{report}", p)
        self.assertIn("more than one", claims.CARD_PROMPT_EN)
        self.assertIn("once", claims.CARD_PROMPT_EN)
        self.assertIn("不止一份", claims.CARD_PROMPT_ZH)


class LateClaimSameMillisecondTest(unittest.TestCase):
    """A late claim created in the same millisecond as the digest must still go
    out as a supplement. On main the legacy backfill ran every time and `<=`
    counted such a claim as covered by the original card. Here new digests are
    recorded in sink_dispatch and never write claim_cards, which that backfill
    joins on — this pins that it stays so."""

    def setUp(self):
        self.con = _con()
        self.con.execute(
            "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
            " absolute,status,check_task,report,card) VALUES "
            "(?,?,?,?,?,?,?,?,'checked','t-1',?,?)",
            (DAY, "09:10", "X", "claim", "quote", "rule", "likely_wrong", 1,
             "report", "card body"))
        self.con.commit()

    def test_a_late_claim_in_the_same_millisecond_goes_out(self):
        posts = []
        post = lambda u, b, h=None, **k: posts.append(b) or {
            "taskId": f"d-{len(posts)}", "status": "proposed"}
        with _sending(), mock.patch("umbra.net.post_json", post), \
                mock.patch("time.time", return_value=1_800_000_000.0):
            self.assertEqual(claims.digest(self.con), 1)
            self.con.execute(
                "INSERT INTO claims (day,t,claim,quote,status,verdict,absolute,"
                " report,card,created_ms) VALUES "
                "(?,?,?,?,'checked','likely_wrong',1,?,?,?)",
                (DAY, "10:20", "late claim", "late quote", "late report",
                 "late card body", int(time.time() * 1000)))
            self.con.commit()
            self.assertEqual(claims.digest(self.con), 1)
        self.assertEqual(posts[1]["prompt"], "late card body")

if __name__ == "__main__":
    unittest.main()


class CliWiringTest(unittest.TestCase):
    """Wired into the CLI and sync. Extraction is free, sending costs — the same
    discipline as the actions lane."""

    def test_the_cli_has_a_claims_subcommand(self):
        from umbra import __main__ as cli
        for act in ("scan", "list", "check", "pull"):
            args = cli.build_parser().parse_args(["claims", act])
            self.assertEqual((args.cmd, args.action), ("claims", act))

    def test_in_sync_extraction_runs_by_default_sending_does_not(self):
        """Extraction is local and costs cents; sending is $3–4 a day and creates
        cards elsewhere. The same switch shape as actions.dispatch_enabled."""
        from umbra import ops
        import inspect
        src = inspect.getsource(ops)
        self.assertIn("claims", src)

    def test_sending_has_to_be_switched_on(self):
        """Looking claims up is what the plugin does once it is on (see
        test_claims_lookup.ModeTest). Sending the day's card into somebody
        else's system is the one thing still behind its own switch."""
        from umbra.plugins import claims as C
        with mock.patch.object(C, "_conf", return_value={}):
            self.assertIsNone(C._sink_name())
        with mock.patch.object(C, "_conf", return_value={
                "plugins": {"claims": {"sink": "notes"}}}):
            self.assertIsNone(C._sink_name())
        with mock.patch.object(C, "_conf", return_value={
                "plugins": {"claims": {"sink": "notes", "send": True}}}):
            self.assertEqual(C._sink_name(), "notes")

class PullTest(unittest.TestCase):
    """Pulls Butler's finished results back.

    Not **to** reprocess and resend — that card is the final product: opening
    it is the answer, replying is the follow-up through Butler's own continue.
    Pulling back is only for the record: to count "how often I was wrong this
    month", and to see whether the filter is right.
    """

    def setUp(self):
        self.con = _con()
        self.con.execute(
            "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
            " absolute,status,check_task) VALUES "
            "(?,?,?,?,?,?,?,?,'checking','t-1')",
            (DAY, "09:10", "X", "断言", "原话", "rule", "likely_wrong", 1))
        self.con.commit()

    def _env(self):
        return _sending()

    def test_finished_results_are_stored(self):
        with self._env(), mock.patch(
                "umbra.net.get_json",
                lambda u, h=None, **k: {"status": "done",
                                        "finalText": "# 核查结果\n1. 错误…"}):
            n = claims.pull(self.con)
        self.assertEqual(n, 1)
        row = _q(self.con, 
            "SELECT status, report FROM claims").fetchone()
        self.assertEqual(row[0], "checked")
        self.assertIn("核查结果", row[1])

    def test_still_running_is_left_alone(self):
        with self._env(), mock.patch(
                "umbra.net.get_json",
                lambda u, h=None, **k: {"status": "running"}):
            self.assertEqual(claims.pull(self.con), 0)
        self.assertEqual(
            _q(self.con, "SELECT status FROM claims").fetchone()[0], "checking")

    def test_a_failed_task_goes_back_to_the_queue(self):
        """Marking it checked is "said it was checked, wasn't" — the same
        discipline as a failed send."""
        with self._env(), mock.patch(
                "umbra.net.get_json",
                lambda u, h=None, **k: {"status": "failed"}):
            claims.pull(self.con)
        row = _q(self.con, "SELECT status, check_task FROM claims").fetchone()
        self.assertEqual(row, ("new", None))


class PerDayTest(unittest.TestCase):
    """One check task a day — one report maps to one day, so the daily card
    needs no splitting."""

    def setUp(self):
        self.con = _con()
        for day in ("2026-08-19", "2026-08-20"):
            self.con.execute(
                "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
                " absolute,status) VALUES (?,?,?,?,?,?,?,?,'new')",
                (day, "09:10", "X", f"{day} 的断言", f"{day} 原话",
                 "rule", "likely_wrong", 0))
        self.con.commit()

    def _env(self):
        return _sending()

    def test_one_day_at_a_time_earliest_first(self):
        posts = []
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda u, b, h=None, **k: posts.append(b) or {"taskId": "t-1"}):
            claims.dispatch(self.con)
        self.assertIn("2026-08-19", posts[0]["prompt"])
        self.assertNotIn("2026-08-20", posts[0]["prompt"])
        left = _q(self.con, 
            "SELECT day FROM claims WHERE status='new'").fetchone()[0]
        self.assertEqual(left, "2026-08-20")


class DigestTest(unittest.TestCase):
    """The daily card: one **visible** card a day.

    Checking uses readonly → a read-only source in Butler, which is hidden in
    the inbox (page.tsx excludes it). So the daily card is a separate
    reviewGated one.

    And proposed: it is something to read, not a task, and should not run by
    itself. Pressing ▶ makes it a conversation — with the report already in
    context to ask about.
    """

    def setUp(self):
        self.con = _con()
        self.con.execute(
            "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
            " absolute,status,check_task,report) VALUES "
            "(?,?,?,?,?,?,?,?,'checked','t-1',?)",
            (DAY, "09:10", "X", "断言", "原话", "rule", "likely_wrong", 1,
             "# 核查结果\n1. 惠州不属于广州 —— 错误"))
        # digest uses the rendered card, not the report itself (see CardTest)
        self.con.execute("UPDATE claims SET card=?",
                         ("**1 条里 1 条说错了**\n\n❌ 惠州不属于广州",))
        self.con.commit()

    def _env(self):
        return _sending()

    def _post(self, posts):
        return lambda u, b, h=None, **k: posts.append(b) or {
            "taskId": "d-1", "status": "proposed"}

    def test_one_visible_card_that_does_not_run_itself(self):
        posts = []
        with self._env(), mock.patch("umbra.net.post_json", self._post(posts)):
            n = claims.digest(self.con)
        self.assertEqual(n, 1)
        self.assertTrue(posts[0]["reviewGated"])   # visible in the inbox
        self.assertTrue(posts[0]["proposed"])      # to read, does not run itself
        self.assertIn("惠州", posts[0]["prompt"])

    def test_sent_once_a_day(self):
        posts = []
        with self._env(), mock.patch("umbra.net.post_json", self._post(posts)):
            claims.digest(self.con)
            self.assertEqual(claims.digest(self.con), 0)
        self.assertEqual(len(posts), 1)

    def test_late_claims_after_the_card_go_separately_without_repeating(self):
        posts = []
        with self._env(), mock.patch("umbra.net.post_json", self._post(posts)):
            self.assertEqual(claims.digest(self.con), 1)
            self.con.execute(
                "INSERT INTO claims (day,t,claim,quote,status,verdict,absolute,"
                " report,card,created_ms) VALUES "
                "(?,?,?,?,'checked','likely_wrong',1,?,?,?)",
                (DAY, "10:20", "迟到断言", "迟到原话", "迟到报告",
                 "迟到卡片正文", int(time.time() * 1000) + 1_000))
            self.con.commit()
            self.assertEqual(claims.digest(self.con), 1)
            self.assertEqual(claims.digest(self.con), 0)

        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[1]["prompt"], "迟到卡片正文")
        self.assertNotIn("惠州", posts[1]["prompt"])
        self.assertEqual(posts[1]["title"], f"{DAY} fact check, supplement (1)")
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM claim_card_items").fetchone()[0],
            2)

    def test_with_an_old_card_late_items_are_found_by_send_time(self):
        con = _con()
        con.execute(
            "INSERT INTO claims (day,t,claim,quote,status,verdict,absolute,"
            " report,card,created_ms) VALUES "
            "(?,?,?,?,'checked','likely_wrong',1,?,?,?)",
            (DAY, "09:10", "原日报断言", "q1", "r1", "原日报正文", 100))
        con.execute(
            "INSERT INTO claims (day,t,claim,quote,status,verdict,absolute,"
            " report,card,created_ms) VALUES "
            "(?,?,?,?,'checked','likely_wrong',1,?,?,?)",
            (DAY, "10:20", "迟到断言", "q2", "r2", "迟到正文", 300))
        con.execute(
            "INSERT INTO claim_cards (day,task,created_ms) VALUES (?,?,?)",
            (DAY, "old-task", 200))
        con.commit()
        posts = []
        with self._env(), mock.patch("umbra.net.post_json", self._post(posts)):
            self.assertEqual(claims.digest(con), 1)
            self.assertEqual(claims.digest(con), 0)

        self.assertEqual([p["prompt"] for p in posts], ["迟到正文"])
        delivered = list(con.execute(
            "SELECT c.claim, i.task FROM claim_card_items i "
            "JOIN claims c ON c.id=i.claim_id ORDER BY c.id"))
        self.assertEqual(delivered, [("原日报断言", "old-task"),
                                     ("迟到断言", "d-1")])

    def test_a_failed_supplementary_card_can_be_retried(self):
        posts = []
        with self._env(), mock.patch("umbra.net.post_json", self._post(posts)):
            claims.digest(self.con)
        self.con.execute(
            "INSERT INTO claims (day,t,claim,quote,status,verdict,absolute,"
            " report,card,created_ms) VALUES "
            "(?,?,?,?,'checked','likely_wrong',1,?,?,?)",
            (DAY, "10:20", "迟到断言", "迟到原话", "迟到报告", "迟到卡片",
             int(time.time() * 1000) + 1_000))
        self.con.commit()

        with self._env(), mock.patch(
                "umbra.net.post_json", side_effect=RuntimeError("503")):
            self.assertEqual(claims.digest(self.con), 0)
        with self._env(), mock.patch("umbra.net.post_json", self._post(posts)):
            self.assertEqual(claims.digest(self.con), 1)

        self.assertEqual(posts[-1]["prompt"], "迟到卡片")

    def test_unfinished_days_are_not_sent(self):
        self.con.execute(
            "INSERT INTO claims (day,t,claim,quote,status) "
            "VALUES (?,?,?,?,'checking')", (DAY, "10:00", "还在查", "q2"))
        self.con.commit()
        with self._env(), mock.patch("umbra.net.post_json", self._post([])):
            self.assertEqual(claims.digest(self.con), 0)

    def test_a_day_with_nothing_to_check_sends_no_empty_card(self):
        con = _con()
        con.execute("INSERT INTO claims (day,t,claim,quote,status,verdict,absolute)"
                    " VALUES (?,?,?,?,'new','confident_right',0)",
                    (DAY, "09:10", "定义类，不用查", "q"))
        con.commit()
        with self._env(), mock.patch("umbra.net.post_json", self._post([])):
            self.assertEqual(claims.digest(con), 0)

    def test_the_body_is_only_the_card(self):
        """Instructions for the agent **must not** go in the body — the reader
        sees them too and reads past them as noise. The first version opened with
        "this is … the daily card, already verified" and closed with "this card is
        for a person to read; if they ask…", and both were read.

        The cost: pressing ▶, the agent may verify again. Acceptable — the card is
        proposed and nobody expects a press; one sentence less buys a clean body.
        """
        posts = []
        with self._env(), mock.patch("umbra.net.post_json", self._post(posts)):
            claims.digest(self.con)
        self.assertEqual(posts[0]["prompt"], "**1 条里 1 条说错了**\n\n❌ 惠州不属于广州")

    def test_a_failed_send_records_no_state(self):
        def boom(*a, **k):
            raise RuntimeError("503")
        with self._env(), mock.patch("umbra.net.post_json", boom):
            self.assertEqual(claims.digest(self.con), 0)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM claim_cards").fetchone()[0], 0)


class CardTest(unittest.TestCase):
    """A report ≠ a card.

    Measured: the daily card used the raw research report as its body — 8,854
    characters, 99 lines, 37 of them reference links — unreadable on a phone.
    The only way out was pressing ▶ for the agent to summarise, while the prompt
    still said "do nothing", so the first reply was "got it, the card is in
    place", then typing "summarise this". Three steps, all designed in by me.

    The report is kept (fully sourced, worth having); the card is a **local
    rendering** of it — one cheap model call pressed into something scannable.
    Kept apart, days already sent can be re-rendered without checking online
    again.
    """

    REPORT = """# 核查结果
## 1. 惠州属于广州 —— 错误
惠州是广东省地级市，与广州平级。
出处：[维基](https://zh.wikipedia.org/wiki/惠州市)
## 2. 木星最大 —— 正确
出处：[NASA](https://science.nasa.gov/jupiter/)
"""

    def setUp(self):
        self.con = _con()
        self.con.execute(
            "INSERT INTO claims (day,t,speaker,claim,quote,kind,verdict,"
            " absolute,status,check_task,report) VALUES "
            "(?,?,?,?,?,?,?,?,'checked','t-1',?)",
            (DAY, "09:10", "X", "断言", "原话", "rule", "likely_wrong", 1,
             self.REPORT))
        self.con.commit()

    def _env(self):
        return _sending()

    CARD = "**8-19：2 条里 1 条说错了**\n\n❌ 惠州不属于广州（是省辖地级市）"

    def test_rendered_into_a_card_and_stored(self):
        n = claims.render_cards(self.con, call=lambda p: self.CARD)
        self.assertEqual(n, 1)
        self.assertIn("惠州", _q(self.con, 
            "SELECT card FROM claims WHERE day=?", (DAY,)).fetchone()[0])

    def test_rendered_ones_are_not_rendered_again(self):
        claims.render_cards(self.con, call=lambda p: self.CARD)
        seen = []
        claims.render_cards(self.con, call=lambda p: seen.append(p) or self.CARD)
        self.assertEqual(seen, [])

    def test_rendering_is_fed_the_whole_report(self):
        seen = []
        claims.render_cards(self.con, call=lambda p: seen.append(p) or self.CARD)
        self.assertIn("惠州是广东省地级市", seen[0])

    def test_the_daily_card_uses_the_rendering_not_the_report(self):
        claims.render_cards(self.con, call=lambda p: self.CARD)
        posts = []
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda u, b, h=None, **k: posts.append(b) or {"taskId": "d-1"}):
            claims.digest(self.con)
        self.assertIn("1 条说错了", posts[0]["prompt"])
        self.assertNotIn("science.nasa.gov", posts[0]["prompt"])  # no wall of links on the card

    def test_days_not_yet_rendered_are_not_sent(self):
        """Better a round late than another unreadable card."""
        with self._env(), mock.patch(
                "umbra.net.post_json", lambda *a, **k: {"taskId": "d-1"}):
            self.assertEqual(claims.digest(self.con), 0)

    def test_a_failed_rendering_is_not_stored(self):
        def boom(p):
            raise RuntimeError("rate limited")
        self.assertEqual(claims.render_cards(self.con, call=boom), 0)
        self.assertIsNone(_q(self.con, 
            "SELECT card FROM claims WHERE day=?", (DAY,)).fetchone()[0])

    def test_the_card_prompt_no_longer_says_nothing_to_do(self):
        """That sentence is what produced the filler reply. Now: answer if they ask."""
        claims.render_cards(self.con, call=lambda p: self.CARD)
        posts = []
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda u, b, h=None, **k: posts.append(b) or {"taskId": "d-1"}):
            claims.digest(self.con)
        for phrase in ("什么都不用做", "nothing to do"):
            self.assertNotIn(phrase, posts[0]["prompt"])


class PlainTextCardTest(unittest.TestCase):
    """The card must be plain text — Butler does not render markdown in a prompt.

    Turn.tsx:142 is `{promptText}`: bare text interpolation + CSS white-space:
    pre-wrap; only agent output goes through MarkdownBody. On purpose: a prompt
    the user typed keeps its line breaks.

    So **bold**, [text](link) and --- rules in a card are all shown raw.
    Measured, the screenshot was a screen of asterisks and bare link syntax.
    """

    def test_the_rendering_prompt_forbids_markdown(self):
        for token in ("纯文本", "不要", "markdown"):
            self.assertIn(token, claims.CARD_PROMPT_ZH.lower())
        for token in ("plain text only", "markdown"):
            self.assertIn(token, claims.CARD_PROMPT_EN.lower())

    def test_the_card_body_has_no_markdown_syntax(self):
        con = _con()
        con.execute(
            "INSERT INTO claims (day,t,claim,quote,status,verdict,absolute,"
            " report,card) VALUES (?,?,?,?,'checked','likely_wrong',1,?,?)",
            (DAY, "09:10", "c", "q", "报告",
             "8-19：2 条说错了\n\n错  惠州不属于广州\n    出处 https://x.example"))
        con.commit()
        posts = []
        with _sending(), \
             mock.patch("umbra.net.post_json",
                        lambda u, b, h=None, **k: posts.append(b) or {"taskId": "d-1"}):
            claims.digest(con)
        body = posts[0]["prompt"]
        self.assertNotIn("**", body)
        self.assertNotIn("](", body)      # the [text](link) kind
        self.assertNotIn("\n---", body)

    def test_no_more_preamble_for_the_agent(self):
        """"This is … the daily card, already verified" and "this card is for a
        person to read…" are seen by the reader too — pure noise; the title
        already has the date and the count."""
        con = _con()
        con.execute(
            "INSERT INTO claims (day,t,claim,quote,status,verdict,absolute,"
            " report,card) VALUES (?,?,?,?,'checked','likely_wrong',1,?,?)",
            (DAY, "09:10", "c", "q", "报告", "正文"))
        con.commit()
        posts = []
        with _sending(), \
             mock.patch("umbra.net.post_json",
                        lambda u, b, h=None, **k: posts.append(b) or {"taskId": "d-1"}):
            claims.digest(con)
        body = posts[0]["prompt"]
        self.assertTrue(body.startswith("正文"), f"it begins: {body[:40]!r}")
        self.assertEqual(body, "正文")


class MultiDeviceGateTest(unittest.TestCase):
    """Multi-speaker detection must not be fooled by numbering when two tracks
    record at once.

    claims scans only multi-speaker windows (someone dictating to an agent makes
    no "claims to others"). But when a wearable and Plaud Desktop record one
    meeting, each track numbers from "Speaker 1", and both naive counts are
    wrong:

      labels only        "Speaker 1" collides across tracks → however many
                         people, counted as one
      (file, label)      one person counted once per track → one person
                         dictating counts as two,
                         and the gate means nothing

    Take **the maximum within any one recording**: the tracks are diarised
    independently of one scene, so there are at least as many people as the
    best-hearing track heard. Reasoning in actions._speakers_in.
    """

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text, speaker, file_id):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{file_id}:{minute}", self.lo + minute * 60000, 2000,
             "plaud", "speech",
             json.dumps({"text": text, "speaker": speaker,
                         "file_id": file_id})))
        self.con.commit()

    def test_the_better_track_lets_the_window_through(self):
        """A meeting on headphones: the wearable hears only you, the software
        hears both — a multi-speaker window."""
        self._speak(550, "美国驾照在新西兰直接能租车，不用办国际驾照",
                    "Speaker 1", "notepin")
        self._speak(550, "美国驾照在新西兰直接能租车", "Speaker 1", "desktop")
        self._speak(551, "是吗？我以为要办的", "Speaker 2", "desktop")
        claims.scan(self.con, DAY, call=lambda p: ANS)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM claims").fetchone()[0], 1,
            "the software heard two people; this window is not talking to oneself")

    def test_one_person_on_two_tracks_is_still_one_person(self):
        """Exactly what the gate is for: dictating to an AI while two devices
        record. Counting (file_id, label) makes two people and the gate useless."""
        self._speak(550, "美国驾照在新西兰直接能租车", "Speaker 1", "notepin")
        self._speak(550, "美国驾照在新西兰直接能租车", "Speaker 1", "desktop")
        claims.scan(self.con, DAY, call=lambda p: ANS)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM claims").fetchone()[0], 0)

    def test_one_speaker_on_one_track_is_still_skipped(self):
        """The gate itself has to stay — this fixes disambiguation, it does not
        remove the gate."""
        self._speak(550, "美国驾照在新西兰直接能租车", "Speaker 1", "notepin")
        self._speak(551, "嗯我记一下", "Speaker 1", "notepin")
        claims.scan(self.con, DAY, call=lambda p: ANS)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM claims").fetchone()[0], 0)
