"""A source that has stopped: one card per outage, through a sink.

The case that started it: location stopped arriving for over three weeks and
nobody noticed. Every round the log said the import was fine — pulled 42,
added 42 — because the same old stretch was being rewritten. Success with
nothing new is exactly what a stopped source looks like, so the test is the
newest record itself (max(ts)), never whether the import succeeded.

Only sources that say they should have something every normal day are watched,
and each says how many quiet days mean "stopped" (STALE_DAYS) and where to look
first (STALE_HINT). A reading app or git is quiet for a week for no reason at
all; alerting on them would teach people to ignore the card.
"""

import contextlib
import io
import os
import tempfile
import types
import unittest
from unittest import mock

from umbra import config, db, ops, registry, settings, sink
from umbra.importers import ALL
from umbra.plugins import staleness as ST

DAY_MS = 86_400_000
NOW = 1_790_000_000_000


def _con():
    return db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))


def _event(con, source, ts, n=0):
    con.execute("INSERT INTO events (uid, ts, source, kind, payload) VALUES (?,?,?,?,?)",
                (f"{source}:{ts}:{n}", ts, source, "x", "{}"))
    con.commit()


def _conf(**plugin):
    return {"plugins": {"staleness": {"enabled": True, **plugin}}}


class DeclarationsTest(unittest.TestCase):
    """The sources say it, not a table in the plugin."""

    def test_the_every_day_sources_declare_how_long_is_too_long(self):
        want = {"location": 2, "plaud": 2, "whoop": 2, "iphone": 2,
                "chrome": 4, "knowledgec": 4}
        for name, days in want.items():
            with self.subTest(name):
                self.assertEqual(ALL[name].STALE_DAYS, days)
                self.assertTrue(ALL[name].STALE_HINT.strip())

    def test_the_sometimes_sources_are_not_watched(self):
        for name in ("weread", "git", "zsh", "claude", "media", "intent", "butler"):
            with self.subTest(name):
                self.assertIsNone(getattr(ALL[name], "STALE_DAYS", None))

    def test_hints_name_nothing_personal(self):
        for name, mod in ALL.items():
            hint = getattr(mod, "STALE_HINT", "") or ""
            for bad in ("spaceylamb", "sherwin", "gps.", "workers.dev"):
                self.assertNotIn(bad, hint.lower(), name)

    def test_limits_are_keyed_by_the_name_events_are_written_under(self):
        """knowledgec writes source "app"; a limit keyed "knowledgec" would
        never match a row."""
        with mock.patch.object(ST.sources, "aside", return_value=(set(), set())):
            lim = ST.limits({})
        self.assertEqual(lim["app"], 4)
        self.assertNotIn("knowledgec", lim)
        self.assertEqual(lim["location"], 2)


class LimitsTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ST.sources, "aside", return_value=(set(), set()))
        p.start()
        self.addCleanup(p.stop)

    def test_config_changes_one_source(self):
        lim = ST.limits(_conf(days={"location": 5}))
        self.assertEqual(lim["location"], 5)
        self.assertEqual(lim["whoop"], 2)

    def test_zero_stops_watching_one(self):
        self.assertNotIn("location", ST.limits(_conf(days={"location": 0})))

    def test_config_can_watch_a_source_that_did_not_ask(self):
        self.assertEqual(ST.limits(_conf(days={"git": 7}))["git"], 7)

    def test_a_source_switched_off_is_not_watched(self):
        """Turning location off and then being told it stopped is noise."""
        with mock.patch.object(ST.sources, "aside", return_value=({"location"}, set())):
            self.assertNotIn("location", ST.limits({}))

    def test_a_source_that_lost_its_setup_is_still_watched(self):
        """A token file that went missing is exactly the kind of stop to report."""
        with mock.patch.object(ST.sources, "aside", return_value=(set(), {"whoop"})):
            self.assertIn("whoop", ST.limits({}))


class StaleTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ST.sources, "aside", return_value=(set(), set()))
        p.start()
        self.addCleanup(p.stop)
        self.con = _con()

    def test_quiet_past_its_limit_is_stopped(self):
        _event(self.con, "location", NOW - 3 * DAY_MS)
        got = ST.stale(self.con, now_ms=NOW, conf={})
        self.assertEqual([(s["source"], s["days"]) for s in got], [("location", 3)])
        self.assertEqual(got[0]["last_ms"], NOW - 3 * DAY_MS)

    def test_quiet_within_its_limit_is_not(self):
        _event(self.con, "location", NOW - 1 * DAY_MS)
        _event(self.con, "chrome", NOW - 3 * DAY_MS)        # chrome waits four
        self.assertEqual(ST.stale(self.con, now_ms=NOW, conf={}), [])

    def test_the_newest_record_is_what_counts(self):
        _event(self.con, "location", NOW - 9 * DAY_MS)
        _event(self.con, "location", NOW - 1 * DAY_MS, n=1)
        self.assertEqual(ST.stale(self.con, now_ms=NOW, conf={}), [])

    def test_a_source_that_never_had_data_is_not_stopped(self):
        """Never connected is not stopped. Settings › Sources says what it needs."""
        self.assertEqual(ST.stale(self.con, now_ms=NOW, conf={}), [])

    def test_each_finding_says_where_to_look(self):
        _event(self.con, "whoop", NOW - 5 * DAY_MS)
        s = ST.stale(self.con, now_ms=NOW, conf={})[0]
        self.assertEqual(s["hint"], ALL["whoop"].STALE_HINT)
        self.assertEqual(s["limit"], 2)


class CardTest(unittest.TestCase):
    S = {"source": "location", "last_ms": NOW - 3 * DAY_MS, "days": 3,
         "limit": 2, "hint": "Open the phone app."}

    def test_one_key_per_outage(self):
        """The key is the source and its last record. While stopped it does not
        change, so a destination that dedupes by key gets one card; once data
        comes back and stops again, the last record moved and it is a new one."""
        a = ST.item(self.S)
        b = ST.item({**self.S, "days": 4})
        c = ST.item({**self.S, "last_ms": NOW})
        self.assertEqual(a["key"], b["key"])
        self.assertNotEqual(a["key"], c["key"])

    def test_the_card_says_what_and_where_to_look(self):
        it = ST.item(self.S)
        self.assertIn("location", it["title"])
        self.assertIn("3 days", it["title"])
        self.assertIn("Open the phone app.", it["body"])
        self.assertTrue(it["visible"])

    def test_the_card_says_success_is_not_proof(self):
        self.assertIn("succeed", ST.item(self.S)["body"])


class FakeSink:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def send(self, item):
        if self.fail:
            raise self.fail
        self.sent.append(item)
        return f"r{len(self.sent)}"


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.con = _con()
        self.logs = []
        self.fake = FakeSink()
        for p in (mock.patch.object(ST.sources, "aside", return_value=(set(), set())),
                  mock.patch.object(sink, "resolve",
                                    side_effect=lambda name, conf=None: {"send": self.fake.send})):
            p.start()
            self.addCleanup(p.stop)

    def run_round(self, conf, now=NOW):
        with mock.patch.object(ST, "_conf", return_value=conf), \
             mock.patch.object(ST, "_now", return_value=now):
            ST.sync(self.con, [], self.logs.append)

    def test_sends_one_card_per_stopped_source(self):
        _event(self.con, "location", NOW - 3 * DAY_MS)
        _event(self.con, "whoop", NOW - 4 * DAY_MS)
        self.run_round(_conf(sink="me"))
        titles = " | ".join(i["title"] for i in self.fake.sent)
        self.assertEqual(len(self.fake.sent), 2)
        self.assertIn("location", titles)
        self.assertIn("whoop", titles)

    def test_the_same_outage_is_sent_once(self):
        _event(self.con, "location", NOW - 3 * DAY_MS)
        self.run_round(_conf(sink="me"))
        self.run_round(_conf(sink="me"), now=NOW + DAY_MS)
        self.assertEqual(len(self.fake.sent), 1)

    def test_a_new_outage_after_recovery_is_sent_again(self):
        _event(self.con, "location", NOW - 3 * DAY_MS)
        self.run_round(_conf(sink="me"))
        _event(self.con, "location", NOW + DAY_MS, n=1)
        self.run_round(_conf(sink="me"), now=NOW + 5 * DAY_MS)
        self.assertEqual(len(self.fake.sent), 2)

    def test_a_failed_send_is_tried_again_next_round(self):
        _event(self.con, "location", NOW - 3 * DAY_MS)
        self.fake.fail = OSError("down")
        self.run_round(_conf(sink="me"))
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(any("down" in m for m in self.logs))
        self.fake.fail = None
        self.run_round(_conf(sink="me"))
        self.assertEqual(len(self.fake.sent), 1)

    def test_a_destination_that_already_has_it_counts_as_sent(self):
        _event(self.con, "location", NOW - 3 * DAY_MS)
        self.fake.fail = sink.Duplicate("seen", receipt="old")
        self.run_round(_conf(sink="me"))
        self.fake.fail = None
        self.run_round(_conf(sink="me"))
        self.assertEqual(self.fake.sent, [])

    def test_on_with_a_sink_is_sending(self):
        """The alert is the plugin: there is no second switch to forget."""
        _event(self.con, "location", NOW - 3 * DAY_MS)
        self.run_round(_conf(sink="me"))
        self.assertEqual(len(self.fake.sent), 1)

    def test_without_a_sink_it_does_not_run(self):
        _event(self.con, "location", NOW - 3 * DAY_MS)
        self.run_round(_conf())
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(any("not running" in m for m in self.logs))

    def test_nothing_stopped_says_nothing(self):
        _event(self.con, "location", NOW - DAY_MS // 2)
        self.run_round(_conf(sink="me"))
        self.assertEqual((self.fake.sent, self.logs), ([], []))


class BlockedTest(unittest.TestCase):
    def test_needs_a_sink(self):
        self.assertIn("sink", ST.blocked(_conf()))

    def test_a_sink_that_does_not_resolve_is_named(self):
        with mock.patch.object(sink, "answers", return_value=None):
            self.assertIn("nowhere", ST.blocked(_conf(sink="nowhere")))

    def test_a_sink_that_resolves_is_enough(self):
        with mock.patch.object(sink, "answers", return_value=False):
            self.assertIsNone(ST.blocked(_conf(sink="me")))

    def test_declarations(self):
        self.assertFalse(getattr(ST, "ENABLED_BY_DEFAULT", False))
        self.assertTrue(ST.SEND_ONLY)
        self.assertIsNone(getattr(ST, "INPUT", None))


class RoundTest(unittest.TestCase):
    """A plugin with a round and no INPUT is a plugin, not a sink."""

    def test_it_is_a_step_of_the_round_when_on(self):
        on = registry.rounds(only_enabled=True, conf=_conf(sink="me"))
        self.assertIn("staleness", on)
        self.assertNotIn("staleness", registry.rounds(only_enabled=True, conf={}))

    def test_a_sink_only_plugin_is_not(self):
        self.assertNotIn("butler", registry.rounds())

    def test_the_round_runs_it(self):
        ran = []
        mod = types.SimpleNamespace(sync=lambda con, days, log: ran.append(days))
        with mock.patch.object(registry, "plugins", return_value=({"x": mod}, {})), \
             mock.patch.object(registry, "enabled", return_value=True), \
             mock.patch.object(ops, "_sync_one",
                               side_effect=lambda n, m, d, log: m.sync(None, d, log)):
            ops._run_plugins(lambda name, fn: fn(), ["2026-09-01"])
        self.assertEqual(ran, [["2026-09-01"]])

    def test_settings_lists_it_and_still_hides_the_sink(self):
        with mock.patch.object(ST.sources, "aside", return_value=(set(), set())):
            names = [p["name"] for p in settings._plugins({})]
        self.assertIn("staleness", names)
        self.assertNotIn("butler", names)

    def test_the_plugins_command_shows_its_switch(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             mock.patch.object(config, "load", return_value={}):
            from umbra import __main__ as cli
            cli.cmd_plugins(types.SimpleNamespace(action="list", name=None))
        line = next(x for x in out.getvalue().splitlines() if "staleness" in x)
        self.assertIn("off", line)


class CliTest(unittest.TestCase):
    def test_lists_every_watched_source(self):
        con = _con()
        _event(con, "location", NOW - 3 * DAY_MS)
        _event(con, "whoop", NOW - DAY_MS // 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             mock.patch.object(ST.sources, "aside", return_value=(set(), set())), \
             mock.patch.object(ST, "_conf", return_value={}), \
             mock.patch.object(ST, "_now", return_value=NOW):
            ST.show(con)
        text = out.getvalue()
        loc = next(x for x in text.splitlines() if "location" in x)
        whoop = next(x for x in text.splitlines() if "whoop" in x)
        self.assertIn("⚠", loc)
        self.assertIn("✓", whoop)
        self.assertIn("never", next(x for x in text.splitlines() if "plaud" in x))
        self.assertIn("Overland", text)        # the stopped one says where to look

    def test_the_command_is_named_stale(self):
        self.assertEqual(ST.CLI_NAME, "stale")


if __name__ == "__main__":
    unittest.main()
