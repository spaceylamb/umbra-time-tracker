"""Findings accumulate; judgements are replaced.

An Analyzer that declares KEY produces findings — a task heard, a claim made —
and a finding, once found, has things happen to it: it is sent, dismissed,
answered. analyzer_rows replaces a window's output when the window is judged
again, which is right for a judgement and wrong for a finding: a late recording
re-judges the window, and a task that has already gone out would vanish, or come
back under a rewording and go out twice.

These tests pin the difference, and that the two voice lanes' oldest promise —
dismissed does not come back — holds in the generic store.
"""

import json
import sqlite3
import tempfile
import textwrap
import os
import unittest
from unittest import mock

from umbra import analyze, analyzer, config, db, registry

DAY = "2026-08-27"

FINDINGS = textwrap.dedent('''
    INPUT  = "events:zsh"
    WINDOW = 720
    PROMPT = "{text}"
    KEY    = "quote"

    def parse(items, window):
        return [dict(x) for x in items]
''')


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patch = mock.patch.object(config, "PLUGINS_DIR", self.dir)
        self.patch.start()
        registry.reload()
        with open(os.path.join(self.dir, "found.py"), "w") as f:
            f.write(FINDINGS)
        registry.reload()
        self.mod = registry.analyzers()["found"]
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)
        self.add(545)

    def tearDown(self):
        self.patch.stop()
        registry.reload()

    def add(self, *minutes):
        lo, _ = analyze.day_bounds(DAY)
        db.insert(self.con, [(f"zsh:{m}", lo + m * 60000, None, "zsh", "command",
                              {"cmd": f"cmd{m}"}) for m in minutes])

    def run_with(self, *found, fresh=False):
        return analyzer.run(self.con, "found", self.mod, DAY, "m",
                            call=lambda p: json.dumps(list(found)), fresh=fresh)

    def items(self, **kw):
        return analyzer.load_items(self.con, "found", **kw)


class AccumulateTest(_Base):
    def test_findings_are_kept_with_an_id(self):
        r = self.run_with({"quote": "book the flights", "a": 545, "b": 546})
        self.assertEqual(r["rows"], 1)
        got = self.items()
        self.assertEqual(got[0]["key"], "book the flights")
        self.assertEqual(r["ids"], [got[0]["id"]])
        self.assertEqual((got[0]["a"], got[0]["state"]), (545, "new"))

    def test_judging_the_window_again_does_not_delete_what_was_found(self):
        """A late recording re-judges the window. A task already sent must not
        vanish because this time the model did not mention it."""
        self.run_with({"quote": "book the flights"})
        self.add(600)
        r = self.run_with({"quote": "call the dentist"})
        self.assertEqual(r["called"], 1)
        self.assertEqual([i["key"] for i in self.items()],
                         ["book the flights", "call the dentist"])

    def test_the_same_finding_again_is_not_a_second_one(self):
        self.run_with({"quote": "book the flights"})
        r = self.run_with({"quote": "book the flights"}, fresh=True)
        self.assertEqual(r["rows"], 0)
        self.assertEqual(len(self.items()), 1)

    def test_dismissed_does_not_come_back(self):
        """The voice lanes' oldest promise, kept by the generic store."""
        self.run_with({"quote": "book the flights"})
        i = self.items()[0]["id"]
        analyzer.update_item(self.con, "found", i, state="dismissed")
        self.run_with({"quote": "book the flights"}, fresh=True)
        self.assertEqual([x["state"] for x in self.items()], ["dismissed"])

    def test_what_was_found_is_never_rewritten(self):
        self.run_with({"quote": "book the flights", "task": "Book flights"})
        self.run_with({"quote": "book the flights", "task": "Reworded"}, fresh=True)
        self.assertEqual(self.items()[0]["task"], "Book flights")

    def test_a_finding_with_no_key_is_dropped(self):
        """It could not be followed through being sent and answered, and would
        be found again, as new, every time."""
        r = self.run_with({"task": "no quote"}, {"quote": "  "})
        self.assertEqual((r["rows"], self.items()), (0, []))

    def test_a_finding_may_arrive_already_held(self):
        self.run_with({"quote": "book the flights", "state": "echo"})
        got = self.items()[0]
        self.assertEqual(got["state"], "echo")
        self.assertNotIn("state", json.loads(self.con.execute(
            "SELECT payload FROM analyzer_items").fetchone()[0]))

    def test_an_unchanged_window_is_not_asked_again(self):
        self.run_with({"quote": "book the flights"})
        self.assertEqual(self.run_with()["called"], 0)

    def test_an_empty_answer_is_still_an_answer(self):
        self.run_with()
        self.assertEqual(self.run_with()["called"], 0)

    def test_a_bad_key_declaration_is_refused(self):
        self.mod.KEY = 3
        try:
            with self.assertRaises(analyzer.BadDeclaration):
                self.run_with({"quote": "x"})
        finally:
            self.mod.KEY = "quote"


class UpdateTest(_Base):
    def setUp(self):
        super().setUp()
        self.run_with({"quote": "book the flights"})
        self.id = self.items()[0]["id"]

    def test_meta_is_merged_not_replaced(self):
        analyzer.update_item(self.con, "found", self.id, meta={"sent": "t-1"})
        analyzer.update_item(self.con, "found", self.id, meta={"verdict": "ran"})
        self.assertEqual(self.items()[0]["meta"], {"sent": "t-1", "verdict": "ran"})

    def test_expect_refuses_a_move_from_the_wrong_state(self):
        """A dismissal that lands first must not be undone by a late "sent"."""
        analyzer.update_item(self.con, "found", self.id, state="dismissed")
        self.assertFalse(analyzer.update_item(self.con, "found", self.id,
                                              state="sent", expect="new"))
        self.assertEqual(self.items()[0]["state"], "dismissed")

    def test_filter_by_state(self):
        self.assertEqual(len(self.items(state=("new", "echo"))), 1)
        self.assertEqual(self.items(state="dismissed"), [])

    def test_owned_fields_win_over_payload_fields(self):
        """A plugin cannot shadow its own bookkeeping by accident."""
        self.run_with({"quote": "x", "id": "spoof", "day": "1999-01-01"}, fresh=True)
        got = [i for i in self.items() if i["key"] == "x"][0]
        self.assertIsInstance(got["id"], int)
        self.assertEqual(got["day"], DAY)

    def test_an_unknown_id_moves_nothing(self):
        self.assertFalse(analyzer.update_item(self.con, "found", 999, state="x"))

    def test_drop_removes_findings_too(self):
        analyzer.drop(self.con, "found")
        self.assertEqual(self.items(), [])


class MarkRanTest(_Base):
    def test_a_marked_window_is_not_asked(self):
        """A plugin moving its cache from a table of its own: where the old
        cache says this exact input was answered, asking again only pays for
        the same findings worded slightly differently."""
        w = analyzer.windows(self.con, self.mod, DAY, "m")[0]
        analyzer.mark_ran(self.con, "found", DAY, "m", w["a"], w["digest"])
        self.assertEqual(self.run_with({"quote": "x"})["called"], 0)


class ContextAcrossMidnightTest(unittest.TestCase):
    def test_raw_events_are_read_across_the_day_edge(self):
        con = sqlite3.connect(":memory:")
        con.executescript(db.SCHEMA)
        lo, _ = analyze.day_bounds(DAY)
        db.insert(con, [("z:prev", lo - 10 * 60000, None, "zsh", "command",
                         {"cmd": "late"})])
        evs, _ = analyzer._gather(con, "events", "zsh", DAY, "m", pad_min=30)
        self.assertEqual(len(evs), 1)
        evs, _ = analyzer._gather(con, "events", "zsh", DAY, "m")
        self.assertEqual(evs, [])


if __name__ == "__main__":
    unittest.main()
