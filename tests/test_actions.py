"""Tasks from speech: things said or implied in recordings → proposals → one
tap at the destination → done.

Reworked 2026-08-21. The chain:

    scan      a day's plaud transcript, **window by 30-minute window**, asks the
              model → proposals
    dispatch  proposal → the destination (status='proposed'; the daemon cannot
              take it)
    you tap   ▶ run / ✕ not a task / ✓ already done       ← the gate is over there
    verdict   the next import pulls what you pressed back and keeps it

## Why from "one prompt a day" to windows

Before 8-16 a day held 200–500 characters of speech; one prompt for the day was
fine. From 8-16 recording ran all day: 123,385 characters / 1,963 segments on
8-18 — a 120k-character prompt, where recall in the middle drops noticeably, and
a passing half-sentence like "no idea either" is exactly what the middle swallows.

Cut by digest.windows(), measured: 34 of 48 windows a day have speech, the
largest 12,724 characters. 34 cheap calls, each with comfortable context.

## With the gate at the destination, is "approve is the only road to a sink"
## still true?

Yes; the gate moved. Butler's daemon takes a task when
`status == 'queued'`, and dispatch writes `'proposed'` — it is never taken
until you tap it in the PWA. umbra no longer holds a second gate locally; two
gates would only make you say the same "yes" twice.
"""

import json
import contextlib
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from umbra.plugins import actions
from umbra import analyze, db

DAY = "2026-08-19"


def _con():
    con = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    con.executescript(db.SCHEMA)
    return con


def _speak(con, minute, text, uid=None):
    lo, _ = analyze.day_bounds(DAY)
    con.execute("INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
                " VALUES (?,?,?,?,?,?)",
                (uid or f"p:{minute}", lo + minute * 60000, 2000,
                 "plaud", "speech",
                 json.dumps({"text": text, "speaker": "Speaker 1"})))
    con.commit()


ANSWER = ('[{"time":"09:10","type":"errand",'
          '"task":"订周五去 LA 的机票",'
          '"quote":"回头帮我订一下周五去 LA 的机票","confidence":"high"}]')


SEND_ON = {"plugins": {"actions": {"sink": "butler", "send": True}}}


@contextlib.contextmanager
def _sending(conf=None):
    """Credentials plus the send gate, for tests that drive a send path.

    The gate now sits inside each sending function rather than only at the ops
    layer that calls them: a function with an external effect should not have
    that effect when sending is off, whoever called it. So a test driving one
    turns sending on, the same as a person would.

    conf merges extra keys in, for the tests that also configure a threshold or
    a domain filter.
    """
    merged = dict(SEND_ON)
    if conf:
        merged = {**conf, "plugins": {**SEND_ON["plugins"],
                                      **(conf.get("plugins") or {})}}
    with mock.patch.dict(os.environ, {"UMBRA_BUTLER_URL": "https://b.example",
                                      "UMBRA_BUTLER_PAT": "but_x"}), \
            mock.patch.object(actions, "_conf", return_value=merged):
        yield




def _q(con, sql, args=()):
    """Run a query written against the old proposals table over every proposal,
    old and new — actions.records() materialised as a temporary table.

    These tests were written when the lane had a table of its own, and their
    assertions are the evidence that moving it onto the core store changed no
    behaviour. Rewriting each query would rewrite the evidence; this keeps every
    assertion exactly as it was and points it at the view the lane now serves.
    """
    rows = actions.records(con)
    cols = [c.strip() for c in actions._LEGACY_COLS.split(",")] + ["src"]
    con.execute("DROP TABLE IF EXISTS temp._p")
    con.execute(f"CREATE TEMP TABLE _p ({', '.join(cols)})")
    con.executemany(f"INSERT INTO temp._p VALUES ({','.join('?' * len(cols))})",
                    [[r.get(c) for c in cols] for r in rows])
    return con.execute(sql.replace("FROM proposals", "FROM temp._p"), args)

class ScanTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订一下周五去 LA 的机票")

    def test_scanning_yields_proposals_with_quote_and_time(self):
        r = actions.scan(self.con, DAY, call=lambda p: ANSWER)
        self.assertEqual(len(r["fresh"]), 1)
        rows = list(_q(self.con, 
            "SELECT t, task, quote, status, type FROM proposals"))
        self.assertEqual(rows[0][0], "09:10")
        self.assertIn("机票", rows[0][1])
        self.assertIn("回头帮我", rows[0][2])
        self.assertEqual(rows[0][3], "proposed")
        self.assertEqual(rows[0][4], "errand")

    def test_the_prompt_has_the_transcript_and_times(self):
        seen = []
        actions.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        self.assertIn("回头帮我订一下周五去 LA 的机票", seen[0])
        self.assertIn("09:10", seen[0])

    def test_no_recordings_no_model_call(self):
        con = _con()
        called = []
        r = actions.scan(con, DAY, call=lambda p: called.append(p) or "[]")
        self.assertEqual(r["fresh"], [])
        self.assertEqual(called, [])

    def test_rescanning_does_not_duplicate(self):
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        actions.scan(self.con, DAY, fresh=True, call=lambda p: ANSWER)
        n = list(_q(self.con, "SELECT COUNT(*) FROM proposals"))[0][0]
        self.assertEqual(n, 1)

    def test_a_dismissed_one_does_not_come_back_on_rescan(self):
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        pid = list(_q(self.con, "SELECT id FROM proposals"))[0][0]
        actions.set_status(self.con, pid, "dismissed")
        actions.scan(self.con, DAY, fresh=True, call=lambda p: ANSWER)
        rows = list(_q(self.con, "SELECT status FROM proposals"))
        self.assertEqual(rows, [("dismissed",)])


class WindowTest(unittest.TestCase):
    """Cut into windows rather than one prompt a day."""

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")       # 09:10 → window 540
        _speak(self.con, 800, "这个我也不知道为什么")   # 13:20 → window 780

    def test_only_windows_with_speech_are_called(self):
        seen = []
        r = actions.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        # 48 windows a day, only 2 with speech
        self.assertEqual(len(seen), 2)
        self.assertEqual(r["windows"], 2)

    def test_each_window_sees_only_its_own_stretch_with_the_rest_as_context(self):
        seen = []
        actions.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        # The two are 250 minutes apart, far past the context radius; neither sees
        # the other. The assertion is on the line-start time, not content — the
        # prompt template's own examples contain "no idea either", and asserting
        # on content would be fooled by the template.
        early = [p for p in seen if "09:10 [Speaker 1] 回头帮我订机票" in p]
        self.assertEqual(len(early), 1)
        self.assertNotIn("13:20 [", early[0])

    def test_a_proposal_records_its_window(self):
        actions.scan(self.con, DAY, call=lambda p: ANSWER if "订机票" in p else "[]")
        win = list(_q(self.con, "SELECT win_a FROM proposals"))[0][0]
        self.assertEqual(win, 540)


class RenderTest(unittest.TestCase):
    """Window rendering. A pure function, its structure pinned here."""

    def setUp(self):
        self.lo, _ = analyze.day_bounds(DAY)

    def _ev(self, minute, text):
        return {"source": "plaud", "ts": self.lo + minute * 60000,
                "text": text, "speaker": "Speaker 1"}

    def test_context_before_the_divider_target_after(self):
        evs = [self._ev(535, "上一个窗口说的话"), self._ev(550, "目标段里说的话")]
        out = actions.render(evs, 540, 570, self.lo)
        self.assertIn(actions.TARGET_MARK, out)
        # Context must come **before** the divider, or the model cannot tell
        # which part to judge
        self.assertLess(out.index("上一个窗口说的话"),
                        out.index(actions.TARGET_MARK))
        self.assertGreater(out.index("目标段里说的话"),
                           out.index(actions.TARGET_MARK))

    def test_following_context_is_marked_separately(self):
        evs = [self._ev(550, "目标段里说的话"), self._ev(585, "下一个窗口说的话")]
        out = actions.render(evs, 540, 570, self.lo)
        self.assertLess(out.index(actions.AFTER_MARK),
                        out.index("下一个窗口说的话"))

    def test_no_speech_in_the_target_returns_empty_and_no_call(self):
        """With only context and an empty target, the model will always force
        something out of the context."""
        evs = [self._ev(535, "只有上下文")]
        self.assertEqual(actions.render(evs, 540, 570, self.lo), "")

    def test_beyond_the_context_radius_is_invisible(self):
        evs = [self._ev(550, "目标"), self._ev(505, "太远了")]
        out = actions.render(evs, 540, 570, self.lo)
        self.assertNotIn("太远了", out)

    def test_non_speech_sources_are_left_out(self):
        """This lane is fed only speech — chrome titles do nothing for "what did
        they say needs doing", and measured they took half the all-source
        rendering."""
        evs = [self._ev(550, "目标"),
               {"source": "chrome", "ts": self.lo + 551 * 60000,
                "title": "某个网页"}]
        self.assertNotIn("某个网页", actions.render(evs, 540, 570, self.lo))


class IncrementalTest(unittest.TestCase):
    """An unchanged window text is not called again — infer's discipline; the
    cache key is the text fed in."""

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")

    def test_an_unchanged_window_is_not_called_again(self):
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        n = []
        r = actions.scan(self.con, DAY, call=lambda p: n.append(p) or ANSWER)
        self.assertEqual(n, [])
        self.assertEqual(r["reused"], 1)
        self.assertEqual(r["fresh_calls"], 0)

    def test_an_empty_answer_counts_as_answered(self):
        """This one is **deliberately different from infer**.

        infer decides whether last round answered by "did any span land in the
        window", at the cost of re-calling every window the model really said
        "nothing this half hour" about. infer can afford it: its windows nearly
        always yield spans. Task extraction is the reverse — most windows have no
        tasks, and that rule would re-call everything every round.

        So "the call succeeded" is recorded explicitly (analyzer_runs), not
        inferred from proposals. An empty answer and no answer are different.
        """
        actions.scan(self.con, DAY, call=lambda p: "[]")
        n = []
        r = actions.scan(self.con, DAY, call=lambda p: n.append(p) or "[]")
        self.assertEqual(n, [])
        self.assertEqual(r["reused"], 1)

    def test_a_failed_window_is_retried_next_round(self):
        def boom(p):
            raise RuntimeError("429")
        r = actions.scan(self.con, DAY, call=boom)
        self.assertEqual(r["failed"], 1)
        n = []
        actions.scan(self.con, DAY, call=lambda p: n.append(p) or ANSWER)
        self.assertEqual(len(n), 1)          # a failure must not be cached as success

    def test_changed_speech_is_called_again(self):
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        _speak(self.con, 552, "另外记得交那个表", uid="p:552")
        n = []
        actions.scan(self.con, DAY, call=lambda p: n.append(p) or "[]")
        self.assertEqual(len(n), 1)

    def test_fresh_bypasses_the_cache(self):
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        n = []
        actions.scan(self.con, DAY, fresh=True,
                     call=lambda p: n.append(p) or ANSWER)
        self.assertEqual(len(n), 1)

    def test_every_window_s_digest_is_stored(self):
        """Storing only the ones called deletes the reused windows' cache, and
        incremental decays into full. (infer.run_day fell into this; carried
        over as is.)"""
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        # The cache is the core's now (analyzer_runs); the invariant is the same.
        rows = list(self.con.execute(
            "SELECT COUNT(*) FROM analyzer_runs WHERE plugin='actions' AND day=?",
            (DAY,)))[0][0]
        self.assertEqual(rows, 1)            # only windows with speech are stored


class TypeTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "随便说点什么")

    def _scan(self, answer):
        actions.scan(self.con, DAY, call=lambda p: answer)
        return list(_q(self.con, "SELECT type, task FROM proposals"))

    def test_all_four_kinds_are_collected(self):
        ans = json.dumps([
            {"time": "09:10", "type": t, "task": f"t{i}",
             "quote": f"q{i}", "confidence": "high"}
            for i, t in enumerate(actions.TYPES)])
        got = self._scan(ans)
        self.assertEqual({r[0] for r in got}, set(actions.TYPES))

    def test_an_unseen_kind_is_not_guessed_but_recorded_as_unknown(self):
        """Guessing a kind invents information. Better recorded as unknown — the
        destination ignores kinds anyway, and a wrong guess only pollutes later
        statistics."""
        ans = ('[{"time":"09:10","type":"派对","task":"t",'
               '"quote":"q","confidence":"high"}]')
        self.assertEqual(self._scan(ans)[0][0], "unknown")

    def test_a_proposal_without_a_quote_is_not_collected(self):
        ans = '[{"time":"09:10","type":"errand","task":"t","confidence":"high"}]'
        self.assertEqual(self._scan(ans), [])


class ButlerSinkTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        self.pid = list(_q(self.con, "SELECT id FROM proposals"))[0][0]

    def _sink(self, posts, ret=None):
        def post_json(url, body, headers=None, timeout=180):
            posts.append((url, body, headers))
            # status must be returned — see DispatchSafetyTest: silence is read
            # as "an old version over there, already running", and sending stops
            return ret or {"taskId": "t-1", "sessionKey": "t-1",
                           "status": "proposed"}
        return post_json

    def test_sent_to_butler_with_an_idempotency_key_and_the_quote(self):
        posts = []
        with _sending(), \
             mock.patch("umbra.net.post_json", self._sink(posts)):
            n = actions.dispatch(self.con)
        self.assertEqual(n, 1)
        url, body, headers = posts[0]
        self.assertEqual(url, "https://b.example/api/external/dispatch")
        self.assertEqual(body["externalEventKey"], f"umbra:{DAY}:{self.pid}")
        self.assertTrue(body["proposed"])
        self.assertIn("机票", body["prompt"])
        self.assertIn("回头帮我", body["prompt"])   # the quote must go with it
        self.assertEqual(headers["Authorization"], "Bearer but_x")
        # none of the three project fields — let Butler route (it knows which
        # projects exist)
        self.assertNotIn("projectId", body)
        self.assertNotIn("projectName", body)

    def test_a_successful_send_records_the_task_id_and_is_not_resent(self):
        posts = []
        with _sending(), \
             mock.patch("umbra.net.post_json", self._sink(posts)):
            actions.dispatch(self.con)
            again = actions.dispatch(self.con)
        self.assertEqual(again, 0)
        self.assertEqual(len(posts), 1)
        row = list(_q(self.con, 
            "SELECT status, dispatch_id FROM proposals WHERE id=?",
            (self.pid,)))[0]
        self.assertEqual(row, ("dispatched", "t-1"))

    def test_a_failed_send_leaves_status_alone(self):
        """Marked dispatched means "said sent, wasn't" — the same discipline as
        approve."""
        def boom(url, body, headers=None, timeout=180):
            raise RuntimeError("503")
        with _sending(), \
             mock.patch("umbra.net.post_json", boom):
            n = actions.dispatch(self.con)
        self.assertEqual(n, 0)
        st = list(_q(self.con, 
            "SELECT status FROM proposals WHERE id=?", (self.pid,)))[0][0]
        self.assertEqual(st, "proposed")

    def test_butler_saying_duplicate_counts_as_sent(self):
        """A colliding idempotency key → Butler returns duplicate + the original
        taskId. Not a failure: "last round did send, the receipt was lost"."""
        posts = []
        dup = {"taskId": "t-old", "duplicate": True}
        with _sending(), \
             mock.patch("umbra.net.post_json", self._sink(posts, dup)):
            actions.dispatch(self.con)
        row = list(_q(self.con, 
            "SELECT status, dispatch_id FROM proposals WHERE id=?",
            (self.pid,)))[0]
        self.assertEqual(row, ("dispatched", "t-old"))

    def test_no_butler_configured_says_so_plainly(self):
        """Missing credentials with sending switched on.

        Sending off is the other case and it is not an error — nothing is
        attempted. This one is: sending was asked for and cannot happen, so the
        message has to name what to set. It is Fatal rather than per-item
        because every remaining proposal would fail identically, and a loop
        logging the same line twenty times buries the one thing to do.
        """
        with _sending(), mock.patch.dict(os.environ,
                                         {"UMBRA_BUTLER_URL": "",
                                          "UMBRA_BUTLER_PAT": ""}):
            with self.assertRaisesRegex(RuntimeError, "UMBRA_BUTLER"):
                actions.dispatch(self.con)


class VerdictTest(unittest.TestCase):
    """What you pressed at the destination has to make it back here."""

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        self.pid = list(_q(self.con, "SELECT id FROM proposals"))[0][0]
        actions._write(self.con, self.pid, "dispatched", dispatch_id="t-1")

    def test_not_a_task_comes_back_and_is_stored(self):
        n = actions.record_verdicts(self.con, [
            {"externalEventKey": f"umbra:{DAY}:{self.pid}",
             "verdict": "not_a_task", "verdictNote": "这是在跟人开玩笑"}])
        self.assertEqual(n, 1)
        row = list(_q(self.con, 
            "SELECT status, verdict, verdict_note FROM proposals WHERE id=?",
            (self.pid,)))[0]
        self.assertEqual(row[0], "dismissed")
        self.assertEqual(row[1], "not_a_task")
        self.assertIn("开玩笑", row[2])

    def test_run_is_recorded_as_approved(self):
        actions.record_verdicts(self.con, [
            {"externalEventKey": f"umbra:{DAY}:{self.pid}", "verdict": "ran"}])
        st = list(_q(self.con, 
            "SELECT status, verdict FROM proposals WHERE id=?",
            (self.pid,)))[0]
        self.assertEqual(st, ("approved", "ran"))

    def test_already_done_is_not_a_negative_example(self):
        """The extraction was right, just stale. In the negative pool it would
        teach the model to miss such tasks."""
        actions.record_verdicts(self.con, [
            {"externalEventKey": f"umbra:{DAY}:{self.pid}",
             "verdict": "already_done"}])
        self.assertEqual(actions.negatives(self.con), [])

    def test_only_not_a_task_goes_into_the_negative_pool(self):
        actions.record_verdicts(self.con, [
            {"externalEventKey": f"umbra:{DAY}:{self.pid}",
             "verdict": "not_a_task", "verdictNote": "开玩笑"}])
        neg = actions.negatives(self.con)
        self.assertEqual(len(neg), 1)
        self.assertIn("回头帮我", neg[0]["quote"])
        self.assertIn("机票", neg[0]["task"])

    def test_unrecognised_keys_are_skipped_quietly(self):
        """Butler holds tasks others sent too, with externalEventKey not in our
        format."""
        n = actions.record_verdicts(self.con, [
            {"externalEventKey": "steward:xyz", "verdict": "ran"},
            {"verdict": "ran"}])
        self.assertEqual(n, 0)


class LegacySinkTest(unittest.TestCase):
    """Ways out other than butler stay — not everyone runs Butler."""

    def test_a_command_sink_gets_the_task_in_environment_variables(self):
        conf = {"actions": {"sink": "command",
                            "command": 'printf %s "$UMBRA_TASK"'}}
        with mock.patch.object(actions, "_conf", return_value=conf):
            sink = actions.make_sink()
            out = sink({"task": "订机票", "quote": "q", "day": DAY, "t": "09:10"})
        self.assertIn("订机票", out)

    def test_no_sink_configured_says_so_plainly(self):
        with mock.patch.object(actions, "_conf", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "config.toml"):
                actions.make_sink()


class AnalyzerInterfaceTest(unittest.TestCase):
    """This lane declared as a plugin, run by the core rather than by itself.

    Moving it found the last two gaps in the interface. parse needed to see
    params, because a proposal that merely echoes something dictated nearby
    cannot be judged from the model's array alone. And params had to be asked
    per window rather than per day: this prompt carries the device buttons
    pressed during *this* half hour, and the day's full set would be wrong in it
    — a mark means "look harder right here", and pointing it at the wrong
    stretch is worse than not having it.
    """

    DAY = "2026-09-16"
    QUOTE = "remind me to book the flights before Friday"
    REPLY = json.dumps([{"task": "Book the flights", "quote": QUOTE,
                         "type": "errand", "confidence": "high",
                         "domain": "personal", "time": "09:05"}])

    def _con(self, extra=()):
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        lo, _hi = analyze.day_bounds(self.DAY)
        rows = [("p:0", lo + 541 * 60000, 0, "plaud", "speech",
                 {"text": "we were planning the trip", "speaker": "S1",
                  "file_id": "aa"}),
                ("p:1", lo + 545 * 60000, 0, "plaud", "speech",
                 {"text": self.QUOTE, "speaker": "S1", "file_id": "aa"})]
        db.insert(con, rows + [(u, lo + m * 60000, 0, src, k, pl)
                               for u, m, src, k, pl in extra])
        return con

    def _run(self, con, reply=None):
        from umbra import analyzer
        return analyzer.run(con, "actions", actions, self.DAY, "m",
                            call=lambda p: reply or self.REPLY)

    def test_it_declares_the_interface(self):
        for name in ("INPUT", "WINDOW", "PROMPT", "parse", "params",
                     "render_window", "KEY"):
            with self.subTest(name):
                self.assertTrue(hasattr(actions, name))
        self.assertEqual(actions.INPUT, "events:plaud")

    def test_the_core_runs_it_and_stores_rows(self):
        from umbra import analyzer
        con = self._con()
        r = self._run(con)
        self.assertEqual((r["windows"], r["called"], r["rows"]), (48, 1, 1))
        row = analyzer.load_items(con, "actions", self.DAY)[0]
        self.assertEqual(row["task"], "Book the flights")
        self.assertEqual(row["type"], "errand")

    def test_an_unchanged_rerun_calls_nothing(self):
        con = self._con()
        self._run(con)
        self.assertEqual(self._run(con)["called"], 0)

    def test_something_dictated_nearby_scores_as_an_echo(self):
        """At the moment he says it out loud, the work is already under way.
        Extracting it and pushing it back turns finished work into a card —
        measured 8-17, 31 of 32 proposals were that."""
        from umbra import analyzer
        con = self._con(extra=[("b:1", 546, "butler", "task",
                                {"text": self.QUOTE})])
        self._run(con)
        row = analyzer.load_items(con, "actions", self.DAY)[0]
        self.assertGreater(row["echo_score"], 0.9)

    def test_an_echo_is_recorded_not_dropped(self):
        """"It found this and judged it an echo" and "it found nothing" need
        different fixes, and only one of them is a bug."""
        from umbra import analyzer
        con = self._con(extra=[("b:1", 546, "butler", "task",
                                {"text": self.QUOTE})])
        self._run(con)
        rows = analyzer.load_items(con, "actions", self.DAY)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["echo_text"])

    def test_nothing_nearby_means_no_echo(self):
        from umbra import analyzer
        con = self._con()
        self._run(con)
        self.assertEqual(analyzer.load_items(con, "actions", self.DAY)[0]
                         ["echo_score"], 0.0)

    def test_a_proposal_with_no_quote_is_dropped(self):
        from umbra import analyzer
        con = self._con()
        self._run(con, reply=json.dumps([{"task": "unbacked"}]))
        self.assertEqual(analyzer.load_items(con, "actions", self.DAY), [])

    def test_context_is_thirty_minutes_not_zero(self):
        """A task revealed in passing usually needs the conversation around it
        to be a task at all. The claims lane runs at zero for the opposite
        reason, which is why this is per-plugin."""
        self.assertEqual(actions.CONTEXT_MIN, 30)


if __name__ == "__main__":
    unittest.main()


class MigrateTest(unittest.TestCase):
    """Old databases already have a proposals table. CREATE TABLE IF NOT EXISTS
    does nothing to an existing table — new code meeting an old database would
    raise OperationalError, and that database is the user's only data."""

    OLD = """
        CREATE TABLE proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL, t TEXT, task TEXT NOT NULL, quote TEXT NOT NULL,
            model TEXT, status TEXT NOT NULL DEFAULT 'proposed',
            result TEXT, created_ms INTEGER);
        CREATE TABLE imports (
            source TEXT PRIMARY KEY, last_run INTEGER NOT NULL,
            ok INTEGER NOT NULL, added INTEGER, error TEXT);
        INSERT INTO proposals (day, task, quote, status)
             VALUES ('2026-08-19', '订机票', '回头帮我订机票', 'approved');
    """

    def _old(self):
        con = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "old.db"))
        con.executescript(self.OLD)
        return con

    def test_an_old_database_gains_the_new_columns(self):
        con = self._old()
        db.migrate(con)
        cols = {c[1] for c in con.execute("PRAGMA table_info(proposals)")}
        for c in ("type", "win_a", "dispatch_id", "verdict", "verdict_note"):
            self.assertIn(c, cols)

    def test_migrating_leaves_existing_proposals_alone(self):
        con = self._old()
        db.migrate(con)
        self.assertEqual(
            _q(con, "SELECT task, status FROM proposals").fetchone(),
            ("订机票", "approved"))

    def test_migrating_can_run_again(self):
        con = self._old()
        db.migrate(con)
        db.migrate(con)                  # connect() calls it every time; must not raise
        self.assertEqual(
            _q(con, "SELECT COUNT(*) FROM proposals").fetchone()[0], 1)

    def test_an_old_database_gains_the_window_cache_table_too(self):
        """task_digests came after proposals and must exist after migrate — the
        cache bridge reads it, and a missing table would crash scan after the
        model calls were already paid for."""
        con = self._old()
        db.migrate(con)
        con.execute("INSERT INTO task_digests (day, model, a_min, n_seg, ok, text)"
                    " VALUES ('2026-08-19','m',540,1,1,'x')")


class PullVerdictsTest(unittest.TestCase):
    """How what you pressed at the destination gets back here.

    Through /api/export/tasks (the endpoint the butler importer already uses)
    rather than one GET per proposal — N requests for N states, on a lane in no
    hurry.
    """

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")
        actions.scan(self.con, DAY, call=lambda p: ANSWER)
        self.pid = list(_q(self.con, "SELECT id FROM proposals"))[0][0]
        actions._write(self.con, self.pid, "dispatched", dispatch_id="t-1")

    def _env(self):
        return _sending()

    def test_pulled_back_and_written_locally(self):
        gets = []

        def get_json(url, headers=None, **kw):
            gets.append(url)
            return {"tasks": [{"externalEventKey": f"umbra:{DAY}:{self.pid}",
                               "verdict": "not_a_task",
                               "verdictNote": "开玩笑的"}]}
        with self._env(), mock.patch("umbra.net.get_json", get_json):
            n = actions.pull_verdicts(self.con)
        self.assertEqual(n, 1)
        self.assertIn("/api/export/tasks", gets[0])
        row = list(_q(self.con, 
            "SELECT status, verdict FROM proposals WHERE id=?", (self.pid,)))[0]
        self.assertEqual(row, ("dismissed", "not_a_task"))

    def test_pagination(self):
        pages = [
            {"tasks": [{"externalEventKey": "umbra:2026-08-19:999",
                        "verdict": "ran"}], "nextCursor": "c2"},
            {"tasks": [{"externalEventKey": f"umbra:{DAY}:{self.pid}",
                        "verdict": "ran"}]},
        ]
        seen = []

        def get_json(url, headers=None, **kw):
            seen.append(url)
            return pages[len(seen) - 1]
        with self._env(), mock.patch("umbra.net.get_json", get_json):
            n = actions.pull_verdicts(self.con)
        self.assertEqual(n, 1)          # proposal 999 is not local; it does not count
        self.assertEqual(len(seen), 2)
        self.assertIn("cursor=c2", seen[1])

    def test_not_yet_pressed_is_left_alone(self):
        """The card is still lying there — verdict is empty. That is not "pressed
        nothing"; the local status must stay dispatched."""
        with self._env(), mock.patch(
                "umbra.net.get_json",
                lambda url, headers=None, **kw: {
                    "tasks": [{"externalEventKey": f"umbra:{DAY}:{self.pid}"}]}):
            n = actions.pull_verdicts(self.con)
        self.assertEqual(n, 0)
        st = list(_q(self.con, 
            "SELECT status FROM proposals WHERE id=?", (self.pid,)))[0][0]
        self.assertEqual(st, "dispatched")


class ConfidenceTest(unittest.TestCase):
    """If the prompt asks for confidence it has to be stored.

    Asked for and thrown away, "the model didn't say" and "we didn't keep it"
    become indistinguishable — and this field is the only knob on volume:
    a day with a recorder on yields twenty or so, and without a knob the system would be
    switched off as spam in its first week.
    """

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")

    def _ans(self, conf):
        return json.dumps([{"time": "09:10", "type": "errand", "task": "订机票",
                            "quote": "回头帮我订机票", "confidence": conf}])

    def test_it_is_stored(self):
        actions.scan(self.con, DAY, call=lambda p: self._ans("medium"))
        self.assertEqual(
            _q(self.con, "SELECT confidence FROM proposals").fetchone()[0],
            "medium")

    def test_missing_is_recorded_unknown_not_passed_off_as_high(self):
        ans = ('[{"time":"09:10","type":"errand","task":"订机票",'
               '"quote":"回头帮我订机票"}]')
        actions.scan(self.con, DAY, call=lambda p: ans)
        self.assertEqual(
            _q(self.con, "SELECT confidence FROM proposals").fetchone()[0],
            "unknown")

    def test_below_the_bar_is_not_sent_but_still_stored(self):
        """Stored so you can check "it did find it, just did not dare send" —
        dropped, a miss and a never-found could not be told apart."""
        actions.scan(self.con, DAY, call=lambda p: self._ans("low"))
        with _sending({"actions": {"min_confidence": "medium"}}), \
             mock.patch("umbra.net.post_json",
                        lambda *a, **k: {"taskId": "t-1", "status": "proposed"}):
            self.assertEqual(actions.dispatch(self.con), 0)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM proposals").fetchone()[0], 1)

    def test_no_bar_by_default_everything_is_sent(self):
        actions.scan(self.con, DAY, call=lambda p: self._ans("low"))
        with mock.patch.object(actions, "_conf", return_value={}), \
             _sending(), \
             mock.patch("umbra.net.post_json",
                        lambda *a, **k: {"taskId": "t-1", "status": "proposed"}):
            self.assertEqual(actions.dispatch(self.con), 1)

    def test_unknown_is_not_stopped_by_the_bar(self):
        """No stated confidence is not low confidence. Treating silence as a low
        score would swallow a whole class (say, a model that will not emit the
        field) silently."""
        ans = '[{"time":"09:10","type":"errand","task":"t","quote":"回头帮我订机票"}]'
        actions.scan(self.con, DAY, call=lambda p: ans)
        with _sending({"actions": {"min_confidence": "high"}}), \
             mock.patch("umbra.net.post_json",
                        lambda *a, **k: {"taskId": "t-1", "status": "proposed"}):
            self.assertEqual(actions.dispatch(self.con), 1)


class DispatchSafetyTest(unittest.TestCase):
    """Sending is an **outward** action, and the gate is over there. Two
    safeguards.

    Background: `proposed` is a newer Butler status. **Older Butler silently
    ignores the field** — an unknown body field raises nothing, the task lands
    as 'queued' and the daemon takes it at once. One sync before Butler is
    upgraded would run a whole day's twenty-odd extractions unattended. Not a
    theoretical risk — the default path.
    """

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")
        _speak(self.con, 800, "还要记得交那个表")
        actions.scan(self.con, DAY, call=lambda p: json.dumps([
            {"time": "09:10", "type": "errand", "task": "订机票",
             "quote": "回头帮我订机票", "confidence": "high"},
            {"time": "13:20", "type": "errand", "task": "交表",
             "quote": "还要记得交那个表", "confidence": "high"}]))

    def _env(self):
        return _sending()

    # ── Safeguard one: nothing goes out by default ─────────────
    def test_nothing_is_sent_by_default(self):
        """Extraction in sync is local, free and side-effect free; sending is
        not. It goes out only when switched on."""
        with mock.patch.object(actions, "_conf", return_value={}):
            self.assertFalse(actions.dispatch_enabled())

    def test_sent_when_switched_on(self):
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": {"dispatch": "butler"}}):
            self.assertTrue(actions.dispatch_enabled())

    # ── Safeguard two: stop at once if the field was not honoured ───
    def test_a_proposal_queued_directly_stops_sending_the_next(self):
        posts = []

        def old_butler(url, body, headers=None, timeout=180):
            posts.append(body)
            return {"taskId": f"t-{len(posts)}", "status": "queued"}

        with self._env(), mock.patch("umbra.net.post_json", old_butler):
            with self.assertRaisesRegex(RuntimeError, "proposed"):
                actions.dispatch(self.con)
        # The first cannot be stopped (already accepted), but the second must
        # not go out
        self.assertEqual(len(posts), 1)

    def test_it_continues_only_when_proposed_comes_back(self):
        posts = []

        def new_butler(url, body, headers=None, timeout=180):
            posts.append(body)
            return {"taskId": f"t-{len(posts)}", "status": "proposed"}

        with self._env(), mock.patch("umbra.net.post_json", new_butler):
            self.assertEqual(actions.dispatch(self.con), 2)

    def test_no_status_at_all_counts_as_not_honoured(self):
        """Older versions have no such field in the response. Silence is not
        consent — it is exactly the version this guards against."""
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda *a, **k: {"taskId": "t-1"}):
            with self.assertRaisesRegex(RuntimeError, "proposed"):
                actions.dispatch(self.con)

    def test_a_duplicate_delivery_does_not_trip_the_safeguard(self):
        """On an idempotency hit Butler returns duplicate + the original taskId,
        without status — that task was accepted as proposed last round; it is
        not a sign of an old version."""
        with self._env(), mock.patch(
                "umbra.net.post_json",
                lambda *a, **k: {"taskId": "t-old", "duplicate": True}):
            self.assertEqual(actions.dispatch(self.con), 2)


class BacklogTest(unittest.TestCase):
    """The moment sending is switched on, the saved-up batch must not pour
    out at once.

    It happened: the real database held 32 proposed from the v1 era, across 44
    days of speech. "Send everything proposed" meant the first sync after the
    switch dropped dozens of cards in the inbox at once — an inbox nobody would
    read again.

    Two gates, one job each:
      dispatch_days  too old is not sent. Something said three weeks ago and
                     never acted on is mostly dead; reviving it as a card is noise.
      max_per_run    at most this many a round. Oldest first, the backlog
                     drains slowly rather than in one flood.
    """

    def setUp(self):
        self.con = _con()
        self.now = "2026-08-21"

    def _add(self, day, task, conf="high", status="proposed"):
        self.con.execute(
            "INSERT INTO proposals (day, t, task, quote, status, type, confidence)"
            " VALUES (?,?,?,?,?,?,?)",
            (day, "09:10", task, f"quote-{task}", status, "errand", conf))
        self.con.commit()

    def _names(self, **conf):
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": conf} if conf else {}):
            return [r[2] for r in actions.dispatchable(self.con, today=self.now)]

    def test_capped_by_default_never_all_at_once(self):
        for i in range(30):
            self._add(self.now, f"t{i}")
        self.assertLessEqual(len(self._names()), actions.MAX_PER_RUN)

    def test_oldest_first_the_backlog_drains_slowly(self):
        self._add("2026-08-20", "旧的")
        self._add("2026-08-21", "新的")
        self.assertEqual(self._names(max_per_run=1), ["旧的"])

    def test_too_old_is_not_revived(self):
        self._add("2026-07-30", "三周前说的")
        self._add("2026-08-21", "今天说的")
        self.assertEqual(self._names(dispatch_days=3), ["今天说的"])

    def test_no_day_limit_by_default_only_the_cap(self):
        """The day limit is optional. By default everything gets in line, just
        slowly — silently losing something you said is far worse than sending
        it three days late."""
        self._add("2026-07-30", "三周前说的")
        self.assertEqual(self._names(), ["三周前说的"])

    def test_the_confidence_bar_still_applies(self):
        self._add(self.now, "低的", conf="low")
        self._add(self.now, "高的", conf="high")
        self.assertEqual(self._names(min_confidence="medium"), ["高的"])

    def test_sent_ones_do_not_queue_again(self):
        self._add(self.now, "发过了", status="dispatched")
        self._add(self.now, "还没发", status="proposed")
        self.assertEqual(self._names(), ["还没发"])

    def test_dispatch_sends_only_those_that_qualify(self):
        for i in range(5):
            self._add(self.now, f"t{i}")
        posts = []
        with _sending({"actions": {"max_per_run": 2}}), \
             mock.patch("umbra.net.post_json",
                        lambda u, b, h=None, **k: posts.append(b) or
                        {"taskId": f"t-{len(posts)}", "status": "proposed"}):
            self.assertEqual(actions.dispatch(self.con, today=self.now), 2)
        self.assertEqual(len(posts), 2)
        # the rest stay queued for the next round — not dropped
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM proposals WHERE status='proposed'")
            .fetchone()[0], 3)


class PreviewTest(unittest.TestCase):
    """Look before sending. **Not one byte goes out.**"""

    def setUp(self):
        self.con = _con()
        _speak(self.con, 550, "回头帮我订机票")
        actions.scan(self.con, DAY, call=lambda p: ANSWER)

    def test_preview_makes_no_network_request(self):
        def boom(*a, **k):
            raise AssertionError("preview must not touch the network")
        with mock.patch("umbra.net.post_json", boom), \
             mock.patch.dict(os.environ, {"UMBRA_BUTLER_URL": "", "UMBRA_BUTLER_PAT": ""}):
            got = actions.preview(self.con)
        self.assertEqual(len(got), 1)

    def test_preview_gives_exactly_the_body_that_would_be_sent(self):
        """What is seen and what is sent must be one thing. Two separate
        construction paths mean the one you looked at and the one sent can
        differ — and then the preview is a lie."""
        with _sending():
            shown = actions.preview(self.con)
            posts = []
            with mock.patch("umbra.net.post_json",
                            lambda u, b, h=None, **k: posts.append(b) or
                            {"taskId": "t-1", "status": "proposed"}):
                actions.dispatch(self.con)
        # Preview shows the item; the sink translates that item into its own
        # wire shape. What has to match is the item, which is why both build it
        # through _item rather than each assembling one.
        self.assertEqual(shown[0]["body"]["body"], posts[0]["prompt"])
        self.assertEqual(shown[0]["body"]["key"], posts[0]["externalEventKey"])

    def test_preview_changes_no_state(self):
        with mock.patch.dict(os.environ, {"UMBRA_BUTLER_URL": "", "UMBRA_BUTLER_PAT": ""}):
            actions.preview(self.con)
        self.assertEqual(
            _q(self.con, "SELECT status FROM proposals").fetchone()[0], "proposed")

    def test_preview_works_without_butler_configured(self):
        """"Look before sending" that needs credentials first is a step too late."""
        with mock.patch.dict(os.environ, {"UMBRA_BUTLER_URL": "", "UMBRA_BUTLER_PAT": ""}):
            self.assertEqual(len(actions.preview(self.con)), 1)


class EchoTest(unittest.TestCase):
    """Echo: what was recorded is really dictating a task to an agent.

    Tasks are often dispatched by voice dictation. The moment it is said the
    work **is already under way** — extracting it again turns the day's
    finished work into cards. Measured, 31 of 32 proposals on 8-17 were this;
    10 of 19 on 8-20.

    ## Why not check "is this sentence inside that text"

    Dictation garbles: a product name comes out as something else (say,
    "Kestrel" as "custard"). Literal containment catches almost none. So character
    3-gram overlap is compared.

    ## Why coverage, not Jaccard

    Butler prompts are often much longer than the sentence (links, context,
    templates). Jaccard is diluted by length and real echoes fail to match. The
    question is "how much of the quote landed in that text" — one direction.
    """

    def setUp(self):
        self.con = _con()

    def _agent(self, minute, text, source="butler", kind="task"):
        lo, _ = analyze.day_bounds(DAY)
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"{source}:{minute}", lo + minute * 60000, None, source, kind,
             json.dumps({"text": text})))
        self.con.commit()

    def _scan(self, answer):
        actions.scan(self.con, DAY, call=lambda p: answer)
        return list(_q(self.con, 
            "SELECT status, echo_score FROM proposals"))

    QUOTE = "帮我查一下这个 kernel task 进程是谁在搞，为什么占用这么高的 CPU"

    def _ans(self, quote=None):
        return json.dumps([{"time": "09:10", "type": "question",
                            "task": "排查 kernel_task 占用",
                            "quote": quote or self.QUOTE,
                            "confidence": "high"}])

    # ── Scoring ─────────────────────────────────────────────
    def test_identical_text_scores_full(self):
        q = "帮我订一下周五去 LA 的机票"
        self.assertEqual(actions.echo_score(q, [(0, q)]), 1.0)

    def test_garbled_dictation_is_still_recognised(self):
        """Like "Kestrel" recorded as "kes 球": literal containment misses,
        3-grams catch it."""
        sc = actions.echo_score(
            "那你帮我导入吧，导入到kes那边去",
            [(0, "好的，那你帮我导入吧，导入到 Kestrel 里面去，然后我去 Kestrel 看看结果")])
        self.assertGreater(sc, 0.45)

    def test_long_text_does_not_dilute_the_score(self):
        """With a candidate ten times the quote, Jaccard drops low while coverage
        does not — Butler prompts often carry links and context; that is normal."""
        q = "帮我订一下周五去 LA 的机票"
        sc = actions.echo_score(q, [(0, q + "别的内容" * 200)])
        self.assertEqual(sc, 1.0)

    def test_unrelated_is_near_zero(self):
        self.assertLess(
            actions.echo_score("去打流感疫苗", [(0, "排查生产环境日志中断")]), 0.2)

    def test_outside_the_window_does_not_count(self):
        self.assertEqual(
            actions.echo_score("帮我订一下周五去 LA 的机票", []), 0.0)

    def test_too_short_a_quote_is_never_an_echo(self):
        """Three characters make few grams, and any long text may cover them by
        chance. Better miss an echo than kill something real."""
        self.assertEqual(actions.echo_score("好的", [(0, "好的呀好的呀")]), 0.0)

    # ── Wired into scan ─────────────────────────────────────
    def test_dictation_to_an_agent_is_marked_echo_and_not_queued(self):
        self._agent(551, "你可以帮我查一下，是谁在搞这个 kernel task 进程吗？"
                         "怎么触发的？为什么会占用这么高的 CPU？")
        _speak(self.con, 550, self.QUOTE)
        result = actions.scan(self.con, DAY, call=lambda p: self._ans())
        rows = list(_q(self.con, 
            "SELECT status, echo_score FROM proposals"))
        self.assertEqual(rows[0][0], "echo")
        self.assertGreater(rows[0][1], 0.35)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["echo_filtered"], 1)

    def test_the_clipboard_counts_as_a_candidate_too(self):
        """Measured, stronger than the agent-side prompt: 5 agent-side matches
        scored only 4%, the clipboard 61–90% — near-verbatim text left by the
        dictation tool."""
        self._agent(551, "你可以帮我查一下，是谁在搞这个 kernel task 进程吗？"
                         "怎么触发的？为什么会占用这么高的 CPU？",
                    source="live", kind="clip")
        _speak(self.con, 550, self.QUOTE)
        self.assertEqual(self._scan(self._ans())[0][0], "echo")

    def test_no_echo_goes_into_the_queue_as_usual(self):
        _speak(self.con, 550, "我们出发之前尽快打一个流感疫苗")
        rows = self._scan(self._ans("我们出发之前尽快打一个流感疫苗"))
        self.assertEqual(rows[0][0], "proposed")

    def test_a_time_mismatch_is_not_an_echo(self):
        """Saying something similar to an agent two hours ago does not make this
        sentence an echo."""
        self._agent(430, self.QUOTE)
        _speak(self.con, 550, self.QUOTE)
        self.assertEqual(self._scan(self._ans())[0][0], "proposed")

    def test_echo_takes_no_part_in_sending(self):
        self._agent(551, self.QUOTE)
        _speak(self.con, 550, self.QUOTE)
        self._scan(self._ans())
        with mock.patch.object(actions, "_conf", return_value={}):
            self.assertEqual(actions.dispatchable(self.con), [])

    def test_the_evidence_for_an_echo_is_kept(self):
        """"Why is this an echo" must be answerable — the inference chain's
        discipline. A bare score cannot tell afterwards whether it was wrong."""
        self._agent(551, "你可以帮我查一下这个 kernel task 进程是谁在搞的，"
                         "为什么占用这么高的 CPU？")
        _speak(self.con, 550, self.QUOTE)
        self._scan(self._ans())
        note = _q(self.con, 
            "SELECT echo_text FROM proposals").fetchone()[0]
        self.assertIn("kernel task", note)

    def test_the_echo_filter_can_be_turned_off(self):
        self._agent(551, self.QUOTE)
        _speak(self.con, 550, self.QUOTE)
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": {"echo_threshold": 0}}):
            actions.scan(self.con, DAY, call=lambda p: self._ans())
        self.assertEqual(
            _q(self.con, "SELECT status FROM proposals").fetchone()[0],
            "proposed")


class DomainSpeakerTest(unittest.TestCase):
    """Two new fields, each from its own source.

    domain    judged by the model (work / personal). "Work things need no
              reminder" holds now and may not later, so it is a line of
              sending configuration, not a criterion in the prompt — extracted
              all the same, just not sent.
    speakers  **counted locally**, never asked of the model. How many distinct
              speakers a window has is a fact; asking a model costs money and
              gets it wrong.

    Why speakers: measured on 8-20, 8 of 9 proposals from one-speaker windows
    were echoes (dictating to an agent); of 11 with two or more, only 4. Recorded
    without filtering at first — the echo filter already catches most solo cases,
    and another layer risks double counting; enough days must be measured to
    know what independent information is left.
    """

    def setUp(self):
        self.con = _con()

    def _speak_as(self, minute, text, speaker):
        lo, _ = analyze.day_bounds(DAY)
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{minute}:{speaker}", lo + minute * 60000, 2000,
             "plaud", "speech", json.dumps({"text": text, "speaker": speaker})))
        self.con.commit()

    ANS = ('[{"time":"09:10","type":"followup","domain":"work",'
           '"task":"把文档发给同事","quote":"这个我到时候总结一个文档发给你",'
           '"confidence":"high"}]')

    def test_domain_is_stored(self):
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        self.assertEqual(
            _q(self.con, "SELECT domain FROM proposals").fetchone()[0], "work")

    def test_missing_domain_is_recorded_unknown_not_guessed(self):
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        ans = self.ANS.replace('"domain":"work",', '')
        actions.scan(self.con, DAY, call=lambda p: ans)
        self.assertEqual(
            _q(self.con, "SELECT domain FROM proposals").fetchone()[0], "unknown")

    def test_speakers_are_counted_locally_not_asked(self):
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        self._speak_as(552, "好的没问题", "Speaker 2")
        self._speak_as(553, "我也觉得可以", "Speaker 3")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        self.assertEqual(
            _q(self.con, "SELECT speakers FROM proposals").fetchone()[0], 3)

    def test_talking_alone_is_one(self):
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        self.assertEqual(
            _q(self.con, "SELECT speakers FROM proposals").fetchone()[0], 1)

    def test_speakers_reported_by_the_model_are_ignored(self):
        """It has no such information; whatever it answers is invented. What can
        be counted locally is not asked of the model."""
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        ans = self.ANS.replace('"confidence":"high"', '"speakers":99,"confidence":"high"')
        actions.scan(self.con, DAY, call=lambda p: ans)
        self.assertEqual(
            _q(self.con, "SELECT speakers FROM proposals").fetchone()[0], 1)

    def test_skip_domains_stops_sending_but_not_storing(self):
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": {"skip_domains": ["work"]}}):
            result = actions.scan(self.con, DAY, call=lambda p: self.ANS)
            self.assertEqual(actions.dispatchable(self.con), [])
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["domain_filtered"], 1)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM proposals "
                             "WHERE status='proposed'").fetchone()[0], 1)

    def test_unknown_is_not_stopped_by_skip_domains(self):
        """The model not saying work or personal does not make it work. Treating
        silence as a hit would swallow a whole day from a model that will not
        emit the field."""
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        actions.scan(self.con, DAY,
                     call=lambda p: self.ANS.replace('"domain":"work",', ''))
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": {"skip_domains": ["work"]}}):
            self.assertEqual(len(actions.dispatchable(self.con)), 1)

    def test_no_domain_gate_by_default(self):
        self._speak_as(550, "这个我到时候总结一个文档发给你", "X")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        with mock.patch.object(actions, "_conf", return_value={}):
            self.assertEqual(len(actions.dispatchable(self.con)), 1)


class MinSpeakersTest(unittest.TestCase):
    """When only one person is talking, it is most likely talking to an AI.

    Measured over a week of proposals, the echo rate falls with speakers:
        1 person 68%  ·  2: 52%  ·  3: 34%  ·  4 or more: 37%

    The echo filter already removes most solo cases — few of the survivors
    were solo. So this gate is **off by default**: what it could catch is the
    little the echo filter misses, while its mistakes are "remembering something
    real while alone" — the kind most worth keeping.
    """

    def setUp(self):
        self.con = _con()

    def _add(self, task, speakers):
        self.con.execute(
            "INSERT INTO proposals (day,t,task,quote,status,type,confidence,"
            " domain,speakers) VALUES (?,?,?,?,'proposed','errand','high',"
            "'personal',?)",
            (DAY, "09:10", task, f"quote-{task}", speakers))
        self.con.commit()

    def _names(self, **conf):
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": conf} if conf else {}):
            return [r[2] for r in actions.dispatchable(self.con)]

    def test_no_speaker_gate_by_default(self):
        self._add("自言自语", 1)
        self.assertEqual(self._names(), ["自言自语"])

    def test_switched_on_it_stops_solo_ones(self):
        self._add("自言自语", 1)
        self._add("跟人说的", 3)
        self.assertEqual(self._names(min_speakers=2), ["跟人说的"])

    def test_an_unknown_count_is_not_stopped(self):
        """Old proposals lack the field. Treating NULL as 1 would silently swallow
        everything from before the migration."""
        self.con.execute(
            "INSERT INTO proposals (day,t,task,quote,status) "
            "VALUES (?,?,?,?,'proposed')", (DAY, "09:10", "老提案", "q-old"))
        self.con.commit()
        self.assertEqual(self._names(min_speakers=2), ["老提案"])


class ContextTest(unittest.TestCase):
    """With only the quote on the card, the reader cannot recognise what they
    were talking about.

    Two pieces of context, from entirely different places:

    "what was going on"    **reuses the spans infer already computed** — no new
                           model call. That day's activity judgement is in the
                           inferences table (activity, with whom, category);
                           asking again buys a second answer that may contradict
                           the timeline.
    "the transcript around" **computed at send time**, not stored. The events
                           are there; a stored copy would make two truths of one
                           piece of data, and a new window size needs no rescan.
    """

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text, speaker="X"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{minute}", self.lo + minute * 60000, 2000, "plaud", "speech",
             json.dumps({"text": text, "speaker": speaker})))
        self.con.commit()

    def _span(self, a, b, cat, activity, who=None, model="m"):
        from umbra import tz
        # A row as stored: keyed by the day's cut (see inference.py's schema).
        lo, hi = tz.bounds(self.con, DAY)
        self.con.execute(
            "INSERT INTO inferences (day, model, a_min, b_min, cat, payload,"
            " day_lo, day_n) VALUES (?,?,?,?,?,?,?,?)",
            (DAY, model, a, b, cat,
             json.dumps({"activity": activity, "with": who}),
             lo, (hi - lo + 1) // 60_000))
        self.con.commit()

    # ── What was going on ───────────────────────────────────
    def test_the_span_covering_that_moment_is_taken(self):
        self._span(600, 630, "chore", "在超市买菜")
        self._span(630, 660, "chore", "和朋友测试露营设备", who="朋友")
        got = actions.span_context(self.con, DAY, 642)
        self.assertIn("露营", got["activity"])
        self.assertEqual(got["with"], "朋友")

    def test_a_day_infer_never_ran_on_returns_none(self):
        """None, not an empty string — "never inferred" and "nothing in that half
        hour" are different, and the card has to say which."""
        self.assertIsNone(actions.span_context(self.con, DAY, 642))

    def test_outside_any_span_returns_none_too(self):
        self._span(600, 630, "chore", "在超市买菜")
        self.assertIsNone(actions.span_context(self.con, DAY, 700))

    # ── The transcript around it ────────────────────────────
    def test_the_excerpt_has_lines_around_with_the_one_marked(self):
        self._speak(636, "你这个 cooler 叫什么？")
        self._speak(642, "我们到时候买两条绳")
        self._speak(648, "行，那我记一下")
        out = actions.transcript_excerpt(self.con, DAY, 642, "我们到时候买两条绳", win=10)
        self.assertIn("你这个 cooler 叫什么？", out)
        self.assertIn("行，那我记一下", out)
        # The line must be findable at a glance, or it is lost among dozens
        line = [l for l in out.splitlines() if "买两条绳" in l][0]
        self.assertIn(actions.quote_mark(), line)

    def test_outside_the_window_is_left_out(self):
        self._speak(600, "很久以前说的")
        self._speak(642, "我们到时候买两条绳")
        out = actions.transcript_excerpt(self.con, DAY, 642, "我们到时候买两条绳", win=10)
        self.assertNotIn("很久以前说的", out)

    def test_speakers_shown_to_tell_conversation_from_talking_alone(self):
        self._speak(640, "你要买几条？", speaker="Speaker 2")
        self._speak(642, "我们到时候买两条绳")
        out = actions.transcript_excerpt(self.con, DAY, 642, "我们到时候买两条绳", win=10)
        self.assertIn("Speaker 2", out)

    # ── Into the prompt sent ────────────────────────────────
    def test_the_prompt_has_both_activity_and_transcript(self):
        self._span(630, 660, "chore", "和朋友测试露营设备", who="朋友")
        self._speak(636, "你这个 cooler 叫什么？")
        self._speak(642, "我们到时候买两条绳")
        p = actions.item_prompt(self.con, DAY, "10:42", "买绳子", "我们到时候买两条绳")
        self.assertIn("和朋友测试露营设备", p)
        self.assertIn("你这个 cooler 叫什么？", p)
        self.assertIn("我们到时候买两条绳", p)

    def test_without_inference_the_transcript_still_goes_and_says_so(self):
        """A missing activity line must not look like "nothing was happening"."""
        self._speak(642, "我们到时候买两条绳")
        p = actions.item_prompt(self.con, DAY, "10:42", "买绳子", "我们到时候买两条绳")
        self.assertIn("我们到时候买两条绳", p)
        self.assertIn("not been inferred", p)

    def test_the_prompt_warns_transcription_may_be_wrong(self):
        """The **first reason** the reader wants this context — to tell whether
        it was misheard."""
        self._speak(642, "我们到时候买两条绳")
        p = actions.item_prompt(self.con, DAY, "10:42", "买绳子", "我们到时候买两条绳")
        self.assertIn("transcription", p)

    def test_preview_and_dispatch_see_the_same_thing(self):
        self._speak(642, "我们到时候买两条绳")
        self.con.execute(
            "INSERT INTO proposals (day,t,task,quote,status,type,confidence,domain)"
            " VALUES (?,?,?,?,'proposed','errand','high','personal')",
            (DAY, "10:42", "买绳子", "我们到时候买两条绳"))
        self.con.commit()
        with _sending():
            shown = actions.preview(self.con)
            posts = []
            with mock.patch("umbra.net.post_json",
                            lambda u, b, h=None, **k: posts.append(b) or
                            {"taskId": "t-1", "status": "proposed"}):
                actions.dispatch(self.con)
        # Preview shows the item; the sink translates it into its own wire
        # shape. What must match is the item — two construction paths would let
        # what you reviewed and what went out drift apart, invisibly.
        self.assertEqual(shown[0]["body"]["body"], posts[0]["prompt"])
        self.assertIn("我们到时候买两条绳", posts[0]["prompt"])


class ExcerptBoundTest(unittest.TestCase):
    """The excerpt has **two** limits.

    Cutting by time alone fails: alone, ten minutes may be 3 lines; four people
    putting up a tent, 40 lines and 2,500 characters. Measured, one 8-18 item
    had 39 lines within ±5 minutes — the line itself buried in the middle, as
    good as no context.

    Lines are the unit people really read (a few either side); the time window
    is only the outer guard, keeping a line after half an hour of silence out.

    What is left out **is said**: silent truncation suggests "that was all
    that was said".
    """

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)
        for i in range(60):
            self.con.execute(
                "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
                " VALUES (?,?,?,?,?,?)",
                (f"p:{i}", self.lo + (600 + i) * 60000 // 4, 2000,
                 "plaud", "speech",
                 json.dumps({"text": f"第{i}句", "speaker": "X"})))
        self.con.commit()

    def _ex(self, **kw):
        return actions.transcript_excerpt(
            self.con, DAY, (600 + 30) // 4, "第30句", **kw)

    def test_lines_are_capped(self):
        out = self._ex(win=600, max_lines=5)
        body = [l for l in out.splitlines() if l.startswith("2") or "第" in l]
        # 5 before + the line + 5 after, plus the omission marks
        self.assertLessEqual(sum(1 for l in out.splitlines() if "第" in l), 11)

    def test_the_line_itself_is_always_in(self):
        out = self._ex(win=600, max_lines=3)
        self.assertIn(actions.quote_mark(), out)
        self.assertIn("第30句", out)

    def test_what_is_left_out_is_said(self):
        """Silent truncation suggests "that was all that was said"."""
        out = self._ex(win=600, max_lines=3)
        self.assertIn("omitted", out)

    def test_no_omission_mark_under_the_limit(self):
        out = self._ex(win=600, max_lines=100)
        self.assertNotIn("omitted", out)

    def test_the_time_window_is_still_the_outer_guard(self):
        """A line after half an hour of silence is not context, even with lines
        to spare."""
        out = self._ex(win=1, max_lines=100)
        self.assertNotIn("第0句", out)


class ContextBleedTest(unittest.TestCase):
    """Lines in the context are not this window's output.

    Measured: a line at 16:45 on 8-18 sat in window 990–1020, but window
    960–990's ±30-minute context saw it too, so it was extracted twice — two
    identical cards to press.

    The `(day, quote)` unique index cannot stop it: the two quotes differed by
    a few characters (one kept a trailing particle), so the keys differed. The
    right place is **ownership**, not dedup: a line belongs only to its own
    window; the context is there to help the model understand, not to hand in
    an answer.
    """

    def setUp(self):
        self.con = _con()

    def _speak(self, minute, text):
        lo, _ = analyze.day_bounds(DAY)
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{minute}", lo + minute * 60000, 2000, "plaud", "speech",
             json.dumps({"text": text, "speaker": "X"})))
        self.con.commit()

    def test_a_quote_only_in_the_context_is_not_collected(self):
        # minute 555 is in window 540–570; window 510–540's context sees it too
        self._speak(520, "先随便说点什么当锚")
        self._speak(555, "我们到时候去接一下他")
        # both windows are called, and both extract 555 as a task
        ans = json.dumps([{"time": "09:15", "type": "errand",
                           "task": "去接人", "quote": "我们到时候去接一下他",
                           "confidence": "high"}])
        actions.scan(self.con, DAY, call=lambda p: ans)
        rows = list(_q(self.con, "SELECT win_a FROM proposals"))
        self.assertEqual(len(rows), 1, f"the same line was extracted {len(rows)} times")
        self.assertEqual(rows[0][0], 540)      # owned by the window it is really in

    def test_a_quote_in_the_target_is_collected(self):
        self._speak(555, "我们到时候去接一下他")
        ans = json.dumps([{"time": "09:15", "type": "errand", "task": "去接人",
                           "quote": "我们到时候去接一下他", "confidence": "high"}])
        actions.scan(self.con, DAY, call=lambda p: ans)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM proposals").fetchone()[0], 1)

    def test_a_quote_not_matching_the_transcript_is_still_collected(self):
        """Models sometimes tidy a quote (dropping fillers). Better let in one of
        doubtful ownership than lose something real over a character — a miss
        costs more than a duplicate."""
        self._speak(555, "呃我们，我们到时候去接一下他嘛")
        ans = json.dumps([{"time": "09:15", "type": "errand", "task": "去接人",
                           "quote": "我们到时候去接一下他", "confidence": "high"}])
        actions.scan(self.con, DAY, call=lambda p: ans)
        self.assertEqual(
            _q(self.con, "SELECT COUNT(*) FROM proposals").fetchone()[0], 1)


class MarkTest(unittest.TestCase):
    """Hardware marks: the **only direct human input** in this chain.

    confidence is the model grading itself, speakers is inferred from the
    surroundings, echo_score is text comparison — all guesses. A mark was
    pressed by hand, so it outranks them rather than sitting beside them.

    But it means "probably something here, look twice", **not** "something
    is here", still less "only here". Implemented as "scan only marked
    windows", the system would decay into a manual notepad.
    """

    # The prompt's language comes from user config. Several assertions here pin
    # the Chinese wording, so the language must be pinned — or they pass on a
    # development machine configured for Chinese and fail on CI with no config
    # (measured 2026-08-23; the same hole once blew up test_infer_incremental).
    LANG = "zh"

    def setUp(self):
        from umbra import infer
        p = mock.patch.object(infer, "lang", return_value=self.LANG)
        p.start()
        self.addCleanup(p.stop)
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{minute}", self.lo + minute * 60000, 2000, "plaud", "speech",
             json.dumps({"text": text, "speaker": "X"})))
        self.con.commit()

    def _mark(self, minute, title, content=""):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"m:{minute}", self.lo + minute * 60000, None, "plaud", "mark",
             json.dumps({"title": title, "content": content,
                         "mark_source": "hardware"})))
        self.con.commit()

    ANS = ('[{"time":"09:10","type":"errand","domain":"work",'
           '"task":"办护照续期","quote":"护照的还没弄，回头去办",'
           '"confidence":"low"}]')

    # ── Into the prompt ─────────────────────────────────────
    def test_a_mark_goes_into_the_prompt_asking_for_more_care(self):
        self._speak(550, "护照的还没弄，回头去办")
        self._mark(551, "护照情况", "• 护照续期还没处理")
        seen = []
        actions.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        p = [x for x in seen if "护照的还没弄" in x][0]
        self.assertIn("按了标记", p)
        self.assertIn("护照续期还没处理", p)   # the device's own summary goes along
        self.assertIn("更仔细", p)

    def test_no_mark_no_such_section(self):
        self._speak(550, "护照的还没弄，回头去办")
        seen = []
        actions.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        self.assertNotIn("按了标记", seen[0])

    def test_the_prompt_says_do_not_invent_one(self):
        """A mark means "look twice", not "must hand one in". Without that line
        the model forces one out to justify the mark — and what it invents is the
        hardest to catch."""
        self._speak(550, "随便说点什么")
        self._mark(551, "某个标记")
        seen = []
        actions.scan(self.con, DAY, call=lambda p: seen.append(p) or "[]")
        self.assertIn("不要", [x for x in seen if "按了标记" in x][0])

    # ── Applied to proposals ────────────────────────────────
    def test_a_proposal_near_a_mark_is_marked(self):
        self._speak(550, "护照的还没弄，回头去办")
        self._mark(551, "护照情况")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        self.assertEqual(
            _q(self.con, "SELECT marked FROM proposals").fetchone()[0], 1)

    def test_far_from_the_mark_does_not_count(self):
        """A mark is "this moment", not "this half hour". Counting the whole
        window would dilute the mark into a rough 30-minute label."""
        self._speak(550, "护照的还没弄，回头去办")
        self._mark(530, "二十分钟前的另一个标记")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        self.assertEqual(
            _q(self.con, "SELECT marked FROM proposals").fetchone()[0], 0)

    def test_a_day_without_marks_is_extracted_as_usual(self):
        """Other windows are unchanged — marks are not the only source."""
        self._speak(550, "护照的还没弄，回头去办")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        rows = list(_q(self.con, "SELECT status, marked FROM proposals"))
        self.assertEqual(rows, [("proposed", 0)])

    # ── Outranking the other gates ──────────────────────────
    def test_a_marked_one_is_not_killed_by_the_echo_filter(self):
        """Talking to someone while pressing the button — not an echo but
        emphasis."""
        self._speak(550, "护照的还没弄，回头去办")
        self._mark(551, "护照情况")
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES ('b:1',?,NULL,'butler','task',?)",
            (self.lo + 551 * 60000,
             json.dumps({"text": "护照的还没弄，回头去办"})))
        self.con.commit()
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        st = _q(self.con, "SELECT status FROM proposals").fetchone()[0]
        self.assertEqual(st, "proposed")

    def _dispatchable(self, **conf):
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": conf} if conf else {}):
            return [r[2] for r in actions.dispatchable(self.con)]

    def test_a_marked_one_skips_the_confidence_bar(self):
        self._speak(550, "护照的还没弄，回头去办")
        self._mark(551, "护照情况")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)   # confidence=low
        self.assertEqual(self._dispatchable(min_confidence="high"), ["办护照续期"])

    def test_a_marked_one_skips_the_work_filter(self):
        """Nobody presses the button for something they do not want."""
        self._speak(550, "护照的还没弄，回头去办")
        self._mark(551, "护照情况")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)   # domain=work
        self.assertEqual(self._dispatchable(skip_domains=["work"]), ["办护照续期"])

    def test_marked_ones_come_first(self):
        """When the cap cuts, the one a person pressed must not be pushed out by
        one the model guessed."""
        self._speak(500, "早一点说的普通事")
        self._speak(550, "护照的还没弄，回头去办")
        self._mark(551, "护照情况")
        early = ('[{"time":"08:20","type":"errand","domain":"personal",'
                 '"task":"普通事","quote":"早一点说的普通事","confidence":"high"}]')
        actions.scan(self.con, DAY,
                     call=lambda p: self.ANS if "护照" in p else early)
        self.assertEqual(self._dispatchable(max_per_run=1), ["办护照续期"])

    def test_unmarked_ones_are_still_gated(self):
        self._speak(550, "护照的还没弄，回头去办")
        actions.scan(self.con, DAY, call=lambda p: self.ANS)
        self.assertEqual(self._dispatchable(skip_domains=["work"]), [])


class ExplicitRecordTest(unittest.TestCase):
    """"Note this down" — an explicit request to record counts too.

    Missed on 2026-08-22 13:05: "ah, that's a good idea, note this down", with
    the hardware button pressed on the spot — the strongest signal in the chain,
    and none of the four kinds covered it — they are all about something to
    **do**, while this asks for something to be **recorded**.

    No model judgement of whether it is worth doing is needed: the speaker
    already judged.
    """

    def setUp(self):
        self.con = _con()

    def test_the_prompt_counts_explicit_requests_to_record(self):
        p = actions.PROMPT_ZH
        self.assertIn("记一下", p)

    def test_the_english_prompt_covers_it_too(self):
        self.assertIn("note this down", actions.PROMPT_EN.lower())


class MultiDeviceSpeakerTest(unittest.TestCase):
    """With two devices recording one meeting, the speaker count is **the
    maximum within any one recording**, not the union.

    segment.py says it plainly: "Speaker 1/2 are numbers within each
    recording"; nobody is recognised across recordings. That rules out two
    approaches:

      labels only        each track's "Speaker 1" collides → four people counted
                         as two (too few)
      (file, label)      one person counted once per track → nine people counted
                         as eighteen (too many)

    The first meeting with both tracks landed, 2026-08-24, the 20:00 window:

        two recordings, 9 speaker labels each
        union = 18     max = 9

    A meeting of 9 cannot have 18 speakers. Why max: the two tracks are
    independent diarisations of **one scene**, neither recording an extra person,
    so there are at least as many people as the best-hearing track heard.

    In two common cases max is right and the union wrong:
      one person dictating to an AI, both tracks hear 1 → max=1 (the union gives
                                         2, and min_speakers' "one person is
                                         probably an echo" signal breaks)
      a four-person Zoom with only you in the room: the wearable hears 1, the
      software 4 → max=4 (union 5)

    ⚠️ claims.py's MIN_SPEAKERS gate counts the same thing; change both together.
    """

    @staticmethod
    def _e(file_id, speaker, text="说了句话"):
        return {"ts": 0, "file_id": file_id, "speaker": speaker, "text": text}

    def test_two_tracks_of_one_meeting_do_not_double_the_count(self):
        """The measured shape: two recordings with 2 speakers each — the same 2
        people, not 4."""
        evs = [self._e("A", "Speaker 1"), self._e("A", "Speaker 2"),
               self._e("B", "Speaker 1"), self._e("B", "Speaker 2")]
        self.assertEqual(actions._speakers_in(evs, 0, 0, 30), 2)

    def test_the_best_hearing_track_decides(self):
        """The wearable hears only you (on headphones), the software hears four
        remote people — 4 people."""
        evs = [self._e("pin", "Speaker 1")] + [
            self._e("desk", f"Speaker {i}") for i in range(1, 5)]
        self.assertEqual(actions._speakers_in(evs, 0, 0, 30), 4)

    def test_talking_alone_is_still_one_person(self):
        """Both tracks record just you — the union would give 2, breaking
        min_speakers' "one person is probably talking to an AI" signal."""
        evs = [self._e("pin", "Speaker 1"), self._e("desk", "Speaker 1")]
        self.assertEqual(actions._speakers_in(evs, 0, 0, 30), 1)

    def test_one_person_speaking_often_in_one_recording_is_one_person(self):
        evs = [self._e("A", "Speaker 1"), self._e("A", "Speaker 1")]
        self.assertEqual(actions._speakers_in(evs, 0, 0, 30), 1)

    def test_counting_works_without_speaker_labels(self):
        evs = [self._e("A", ""), self._e("B", "")]
        self.assertEqual(actions._speakers_in(evs, 0, 0, 30), 1)


class ExcerptDedupeTest(unittest.TestCase):
    """In a card's excerpt, one sentence captured by two recordings becomes one.

    A wearable and Plaud Desktop both run during meetings, so one sentence has
    two transcripts worded slightly differently (two independent ASR passes,
    different punctuation and slips).

    The real cost is not looks but **half the context budget eaten**: a card
    shows only CONTEXT_MAX_LINES=8 lines either side, and the config says "lines
    are the unit people read". With four of eight repeated you see only four.

    Merging **is reported** — the same discipline as "N lines omitted" in this
    file: deleting silently suggests that was all that was said.

    ⚠️ The threshold was picked, not measured. When this was written there was
    no real two-track meeting yet (the only Desktop recording was a 41-second
    self-test with one speaker). So it is conservative, merging only what is
    almost certainly one sentence — better miss a merge than merge two different
    things.
    """

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text, file_id="A", speaker="Speaker 1"):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{file_id}:{minute}", self.lo + minute * 60000, 2000,
             "plaud", "speech",
             json.dumps({"text": text, "speaker": speaker,
                         "file_id": file_id})))
        self.con.commit()

    # Two versions of one sentence — shaped after a real pair of ASR differences
    LONG = "那个 API 的问题我们回头再看一下，先把这周的进度过完"
    SHORT = "那个 API 的问题我们回头再看一下 先把这周的进度过完"

    def _out(self, quote, **kw):
        return actions.transcript_excerpt(self.con, DAY, 642, quote,
                                          win=10, **kw)

    def test_one_sentence_from_two_recordings_keeps_one(self):
        self._speak(642, self.LONG, file_id="notepin")
        self._speak(642, self.SHORT, file_id="desktop")
        out = self._out(self.LONG)
        n = sum(1 for l in out.splitlines() if "回头再看一下" in l)
        self.assertEqual(n, 1, out)

    def test_the_fuller_one_is_kept(self):
        """Both are the same sentence; keep the longer — the shorter has usually
        been cut."""
        self._speak(642, self.SHORT, file_id="desktop")
        self._speak(642, self.LONG, file_id="notepin")
        self.assertIn(self.LONG, self._out(self.LONG))

    def test_the_quoted_line_is_never_dropped(self):
        """The card's anchor is "this sentence". Merged away, the excerpt has no
        centre — with no match for the quote transcript_excerpt falls back to the
        middle stretch."""
        self._speak(642, self.LONG, file_id="notepin")
        self._speak(642, self.SHORT, file_id="desktop")
        out = self._out(self.SHORT)          # the quote is the **short** one
        line = [l for l in out.splitlines() if "回头再看一下" in l][0]
        self.assertIn(self.SHORT, line)
        self.assertIn(actions.quote_mark(), line)

    def test_said_twice_in_one_recording_is_not_merged(self):
        """It really was said twice. Merging would rewrite the facts."""
        self._speak(642, self.LONG, file_id="notepin")
        self._speak(643, self.LONG, file_id="notepin")
        out = self._out(self.LONG)
        self.assertEqual(sum(1 for l in out.splitlines()
                             if "回头再看一下" in l), 2, out)

    def test_far_apart_is_not_merged(self):
        """Two tracks capturing one sentence differ by seconds. Five minutes
        apart are two things."""
        self._speak(642, self.LONG, file_id="notepin")
        self._speak(647, self.SHORT, file_id="desktop")
        out = self._out(self.LONG)
        self.assertEqual(sum(1 for l in out.splitlines()
                             if "回头再看一下" in l), 2, out)

    def test_a_merge_is_reported(self):
        self._speak(642, self.LONG, file_id="notepin")
        self._speak(642, self.SHORT, file_id="desktop")
        self.assertRegex(self._out(self.LONG), r"1 lines captured by both recordings merged")

    def test_no_merge_no_extra_line(self):
        self._speak(642, self.LONG, file_id="notepin")
        self.assertNotRegex(self._out(self.LONG), r"merged")

    def test_threshold_zero_turns_it_off(self):
        with mock.patch.object(actions, "_conf",
                               return_value={"actions": {"dedupe_threshold": 0}}):
            self._speak(642, self.LONG, file_id="notepin")
            self._speak(642, self.SHORT, file_id="desktop")
            out = self._out(self.LONG)
        self.assertEqual(sum(1 for l in out.splitlines()
                             if "回头再看一下" in l), 2, out)

    def test_two_different_sentences_are_not_merged(self):
        """Better miss a merge than turn two different sentences into one —
        that rewrites the facts."""
        self._speak(642, "我们到时候买两条绳", file_id="notepin")
        self._speak(642, "你这个 cooler 叫什么", file_id="desktop")
        out = self._out("我们到时候买两条绳")
        self.assertIn("买两条绳", out)
        self.assertIn("cooler", out)


class DedupeFalsePositiveTest(unittest.TestCase):
    """A short sentence must not be absorbed by a long one — a false merge seen on
    the real database on 2026-08-24.

    The first version divided by how much of the shorter was covered
    (min(len(a), len(b))), reasoning "a truncated copy should not count as
    different". But that opened a hole: any short sentence that is a substring
    of a long one scores near 1. A real case, similarity 0.75:

        A: Er, I'm doing it.
        B: While doing the export feature I came across a permissions problem in
           an endpoint

    Two entirely different sentences; merging them erases B's problem from the
    card.

    Switched to symmetric Jaccard: two tracks capturing one sentence should be
    of similar length. Far-apart lengths are penalised by the denominator, with
    no extra length gate. Better miss a merge.
    """

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text, file_id):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{file_id}:{minute}", self.lo + minute * 60000, 2000,
             "plaud", "speech",
             json.dumps({"text": text, "speaker": "Speaker 1",
                         "file_id": file_id})))
        self.con.commit()

    # Structure copied from the real pair (text replaced, as the repo does): the
    # short sentence has 4 3-grams, 3 of them scattered through the long one —
    # "我在做", "呃，我这个", "呃，我在测试环境". Dividing by min gives
    # 3/4 = 0.75, comfortably through.
    SHORT = "呃，我在做。"
    LONG = ("我在做那个页面的时候，顺便又扫到一个小问题。现在是可以呃，"
            "我这个呃，我在测试环境已经试过了啊，你看一下，没问题就合了。")

    def test_a_short_sentence_is_not_absorbed_by_a_long_one(self):
        self._speak(642, self.SHORT, "notepin")
        self._speak(642, self.LONG, "desktop")
        out = actions.transcript_excerpt(self.con, DAY, 642, self.SHORT, win=10)
        self.assertIn("呃，我在做。", out)
        self.assertIn("我在测试环境", out)      # the long sentence must stay whole
        self.assertNotIn("合并", out)

    def test_what_stops_it_is_the_length_floor_not_similarity(self):
        """With two-track data in hand on 2026-08-24 the measure changed:
        same_utterance went back to containment (immune to differing sentence
        boundaries), so this pair **scores high**.

        What stops it now is the length floor — "呃，我在做。" has only four
        3-grams, short of DEDUPE_MIN_CHARS. More precise than stopping it by
        similarity: the real problem was never that they are alike but that
        **the sample is too short for alikeness to be trusted**.
        """
        self.assertGreater(actions.same_utterance(self.SHORT, self.LONG), 0.7)
        self.assertLess(len(self.SHORT.replace(" ", "")),
                        actions.DEDUPE_MIN_CHARS)

    def test_two_transcriptions_of_one_sentence_still_match(self):
        """The fix must not miss what should merge."""
        a = "那个 API 的问题我们回头再看一下，先把这周的进度过完"
        b = "那个 API 的问题我们回头再看一下 先把这周的进度过完"
        self.assertGreater(actions.same_utterance(a, b), 0.6)


class DedupeCalibrationTest(unittest.TestCase):
    """Where the measure and threshold come from — measured on 2026-08-24, the
    first meeting with both tracks landed.

    Sample: a 20:02–20:34 Planning Meeting, 160 lines from the wearable / 158
    from the software, one meeting recorded twice.
      positive = a wearable line vs the most similar software line within ±1
                 minute (two transcriptions of one sentence)
      negative = vs lines more than 3 minutes away (certainly different),
                 worst case taken

                     duplicates merged   false merges
        Jaccard@0.6      21.9%        0.0%     ← the old setting, missing 78%
        Jaccard@0.4      50.0%        0.0%
        containment@0.7  68.1%        0.6%
        containment@0.7 + 8-char floor  68.8%   0.0%   ← the current one

    **Why Jaccard lost**: the low scores were not mainly different ASR wording
    but **different sentence boundaries** — one track splits a sentence, the
    other does not, so the longer has an extra stretch that Jaccard's union
    denominator penalises hard. A typical measured pair:

        wearable: I think it might be hard to recruit people we don't know.
        software: I think it might be hard to recruit people we don't know. Yeah…

    Plainly the same sentence: Jaccard only 0.595 (under 0.6, missed),
    containment near 1.

    **The length floor is what makes containment usable**. Without it any short
    sentence whose grams scatter through a long one scores near 1 (see the real
    false merge in DedupeFalsePositiveTest). Eight characters, the same source
    as ECHO_MIN_CHARS: too short a sample, and alikeness cannot be trusted.
    """

    # Shape copied from the real pair ("We're doing the omakase." wholly inside
    # "We're doing the omakase. I just found out what the omakase was.",
    # containment 1.00): A sits inside B as is; B carries an extra half sentence.
    A = "我觉得这个可能有点难 得再想想"
    B = "我觉得这个可能有点难 得再想想 对啊他们也不一定愿意来"

    def test_one_sentence_with_different_boundaries_merges(self):
        """The bulk of the duplicates — the tracks split sentences differently.
        A sits inside B as is; merging loses nothing."""
        self.assertGreaterEqual(actions.same_utterance(self.A, self.B),
                                actions.DEDUPE_THRESHOLD)

    def test_different_boundaries_and_punctuation_deliberately_do_not_merge(self):
        """Different boundaries and different punctuation — containment 0.77,
        short of 0.9.

        **Missed on purpose**. The threshold is 0.9 because going lower raises
        lost content from 0.2% to 2.5% (0.8) and 4.0% (0.7). Compression and
        safety trade off, and "better miss a merge" is this chain's discipline.
        """
        a = "我觉得这个可能有点难，得再想想"
        self.assertLess(actions.same_utterance(a, self.B),
                        actions.DEDUPE_THRESHOLD)

    def test_two_transcriptions_differing_only_in_punctuation_merge(self):
        """containment 0.913 — the missing 8.7% are all grams broken by
        punctuation, not content. Merging these loses no meaning."""
        a = "那个 API 的问题我们回头再看一下，先把这周的进度过完"
        b = "那个 API 的问题我们回头再看一下 先把这周的进度过完"
        self.assertGreaterEqual(actions.same_utterance(a, b),
                                actions.DEDUPE_THRESHOLD)

    def test_two_different_sentences_still_do_not_merge(self):
        self.assertLess(actions.same_utterance("我们到时候买两条绳",
                                               "你这个 cooler 叫什么"), 0.3)

    def test_anything_too_short_never_merges(self):
        """The length floor lives in dedupe_recordings, not the measure — the
        measure only answers "alike?"; "is this sample worth trusting" is
        another question."""
        rows = [{"m": 10, "text": "嗯对", "file_id": "A"},
                {"m": 10, "text": "嗯对啊我在做这个事情呢你等我一下", "file_id": "B"}]
        kept, merged = actions.dedupe_recordings(rows)
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 2)


class DedupeTimeGateTest(unittest.TestCase):
    """The time gate is in **seconds**, not minutes.

    Measured on 2026-08-24: the tracks align within 1.4 seconds (7 identical
    anchors, median +0.8 s). Both sides are start_at + segment offset, and the
    clocks really are that close.

    The first version gated on "the same minute", where 918 cross-track
    candidate pairs could form and real duplicates were a sliver. A confirmed
    false merge was 16.4 seconds apart — the same minute, but never the same
    sentence:

        A: You know kind of wrap it up and we'll we'll take over…
        B: Um above the noise themselves so, um I feel like it'll be…

    containment 0.83; similarity cannot stop it, time stops it at once. ±5
    seconds leaves 3.5× margin over the measured 1.4.
    """

    @staticmethod
    def _row(sec, text, file_id):
        return {"ts": sec * 1000, "m": sec // 60, "text": text,
                "file_id": file_id}

    SAME = "我觉得这个可能有点难 得再想想"
    ALSO = "我觉得这个可能有点难 得再想想 对啊他们也不一定愿意来"

    def test_a_second_apart_merges(self):
        rows = [self._row(600, self.SAME, "pin"),
                self._row(601, self.ALSO, "desk")]
        _, merged = actions.dedupe_recordings(rows)
        self.assertEqual(merged, 1)

    def test_same_minute_but_sixteen_seconds_apart_does_not_merge(self):
        """The confirmed false merge has exactly this shape."""
        rows = [self._row(600, self.SAME, "pin"),
                self._row(616, self.ALSO, "desk")]
        _, merged = actions.dedupe_recordings(rows)
        self.assertEqual(merged, 0)


class ExcerptBudgetTest(unittest.TestCase):
    """When two tracks overlap, the card's line budget scales with the number of
    recordings.

    The original problem was "half of 8 context lines are repeats". Dedup cannot
    fix it — measured on that 2026-08-24 meeting: a 0.9 threshold removes only 7%
    of lines (losing 0.2% of content); reaching 26% loses 4%. Compression and
    safety trade off, and "better miss a merge" is this chain's discipline.

    So the budget is **not solved by dedup** but by a deterministic fact: with
    one stretch recorded N times, seeing as much **distinct content** takes N
    times the lines. No risk; no text compared.

    The context_max_lines note says "lines are the unit people read" — this
    keeps that unit's meaning: 8 lines means 8 **different things said**, not
    8 rows.
    """

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _speak(self, minute, text, file_id):
        self.con.execute(
            "INSERT INTO events (uid, ts, dur_ms, source, kind, payload)"
            " VALUES (?,?,?,?,?,?)",
            (f"p:{file_id}:{minute}", self.lo + minute * 60000, 2000,
             "plaud", "speech",
             json.dumps({"text": text, "speaker": "S", "file_id": file_id})))
        self.con.commit()

    def _fill(self, tracks):
        """Fill each track with 40 mutually different lines (none merge)."""
        for fid in tracks:
            for i in range(40):
                self._speak(630 + i, f"{fid} 第 {i} 句 这是一段够长的内容用来占位",
                            fid)

    def _lines(self):
        out = actions.transcript_excerpt(self.con, DAY, 642, "", win=60)
        return [l for l in out.splitlines() if not l.startswith("…")]

    def test_one_track_keeps_the_budget(self):
        self._fill(["pin"])
        self.assertEqual(len(self._lines()), actions.CONTEXT_MAX_LINES * 2 + 1)

    def test_two_tracks_double_it(self):
        self._fill(["pin", "desk"])
        self.assertEqual(len(self._lines()),
                         actions.CONTEXT_MAX_LINES * 2 * 2 + 1)


class ScanRenderDedupeTest(unittest.TestCase):
    """The copy proposals are extracted from is deduplicated too — or one
    sentence becomes two proposals.

    It happened on 2026-08-24; both reached the database, both needed a press:

        20:26 [dismissed] contact two friends to try setting up a Meshtastic
                          channel
        20:26 [dismissed] contact two friends to try building a Meshtastic
                          emergency channel

    The dedup key is quote, not task (see the SCHEMA note: task is the model's
    rewording and changes every round; quote is the transcript, stable). That
    design is right, but it assumes "one sentence, one transcript" — two tracks
    transcribe it twice, the quotes differ, and the key does not catch it.

    Fixed upstream: the model never sees the repeat. The measured pair was 0.14
    seconds apart with containment 0.941, through both gates.

    ⚠️ A single-track window must not change by one byte — actions.scan caches
    on the rendered text too; changed text means calling the model again.
    """

    def setUp(self):
        self.con = _con()
        self.lo, _ = analyze.day_bounds(DAY)

    def _ev(self, minute, sec, text, file_id):
        return {"ts": self.lo + minute * 60000 + sec * 1000, "dur_ms": 2000,
                "source": "plaud", "kind": "speech", "text": text,
                "speaker": "Speaker 1", "file_id": file_id}

    # Shape copied from the real pair: the short one sits almost whole in the
    # long one
    SHORT = "所以我可能会去找 小林和阿周 试试能不能把那个频道搭起来"
    LONG = ("所以我可能会去找 小林和阿周 试试能不能把那个频道搭起来 "
            "东西周三才到 到了再看效果怎么样")

    def test_one_sentence_on_two_tracks_is_fed_to_the_model_once(self):
        evs = [self._ev(550, 0, self.SHORT, "notepin"),
               self._ev(550, 0, self.LONG, "desktop")]
        out = actions.render(evs, 540, 570, self.lo)
        self.assertEqual(sum(1 for l in out.splitlines()
                             if "小林" in l), 1, out)

    def test_a_single_track_window_does_not_change_by_one_byte(self):
        """scan's cache hangs on this text; a change means calling the model again."""
        evs = [self._ev(550, 0, self.SHORT, "notepin"),
               self._ev(551, 0, "另一句完全不同的话在这里", "notepin")]
        out = actions.render(evs, 540, 570, self.lo)
        self.assertIn(f"[Speaker 1] {self.SHORT}", out)
        self.assertIn("[Speaker 1] 另一句完全不同的话在这里", out)

    def test_said_twice_on_one_track_is_not_merged(self):
        evs = [self._ev(550, 0, self.SHORT, "notepin"),
               self._ev(550, 2, self.LONG, "notepin")]
        out = actions.render(evs, 540, 570, self.lo)
        self.assertEqual(sum(1 for l in out.splitlines() if "小林" in l), 2)


class SaidTwiceTest(unittest.TestCase):
    """One task, said twice in two windows, is listed once. The model judges
    which are the same (titles' text similarity could not: a restatement
    scored lower than two tasks differing only by a name); this pins what is
    done with its answer."""

    def setUp(self):
        from umbra import analyzer
        self.con = _con()
        analyzer.ensure_tables(self.con, None)
        self.analyzer = analyzer

    def add(self, t, task):
        h, m = map(int, t.split(":"))
        a = (h * 60 + m) // 30 * 30
        return self.analyzer.save_items(
            self.con, "actions", DAY, "m", a, f"d{a}", [{
                "a": a, "b": a + 30, "t": t, "task": task, "quote": f"q {t}",
                "type": "errand", "state": "proposed"}], "quote", now_ms=1)[0]

    def status(self, pid):
        return actions.record(self.con, pid)["status"]

    def test_a_repeat_the_model_names_is_held_and_undo_brings_it_back(self):
        first = self.add("09:10", "Book the car service")
        again = self.add("09:50", "Get the car serviced before the 20th")
        asked = []
        call = lambda p: asked.append(p) or json.dumps([[again, first]])
        self.assertEqual(actions.mark_duplicates(self.con, DAY, [first, again], call=call), 1)
        self.assertIn(f"{again} 09:50 NEW", asked[0])
        self.assertEqual((self.status(first), self.status(again)), ("proposed", "duplicate"))
        self.assertTrue(actions.reopen(self.con, again))
        self.assertEqual(self.status(again), "proposed")

    def test_the_rule_beats_the_reply(self):
        """Only a new one, and only as a repeat of an earlier one."""
        old = self.add("09:10", "Book the car service")
        new = self.add("09:50", "Get the car serviced")
        call = lambda p: json.dumps([[old, new], [new, 9999], ["x", 1], {"a": 1}])
        actions_ids = [new]                          # old was listed before
        self.assertEqual(actions.mark_duplicates(self.con, DAY, actions_ids, call=call), 0)
        self.assertEqual(self.status(old), "proposed")

    def test_a_failed_check_holds_nothing(self):
        a = self.add("09:10", "Book the car service")
        b = self.add("09:50", "Get the car serviced")

        def boom(_):
            raise TimeoutError("down")
        self.assertEqual(actions.mark_duplicates(self.con, DAY, [a, b], call=boom), 0)
        self.assertEqual(self.status(b), "proposed")

    def test_nothing_new_asks_nothing(self):
        a = self.add("09:10", "Book the car service")
        b = self.add("09:50", "Get the car serviced")
        call = mock.Mock()
        actions.mark_duplicates(self.con, DAY, [], call=call)
        call.assert_not_called()
