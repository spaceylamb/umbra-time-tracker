"""The machinery every Analyzer shares.

These cover `umbra/analyzer.py`, which exists because three lanes had each built
the same scaffolding and two of them were reaching into other modules' private
names to do it.

The tests that matter most here are the negative ones. `parse_json` must raise
rather than return an empty list, because a caller cannot otherwise tell "the
model said nothing happened" from "we failed to read the model" — and it stores
the first while it needs to retry the second.
"""

import sqlite3
import unittest

from umbra import analyzer, db


class ParseJsonTest(unittest.TestCase):
    """The shapes a model actually returns."""

    def test_a_bare_array(self):
        self.assertEqual(analyzer.parse_json('[{"a": 1}]'), [{"a": 1}])

    def test_a_fenced_block(self):
        self.assertEqual(analyzer.parse_json('```json\n[{"a": 1}]\n```'),
                         [{"a": 1}])

    def test_a_fence_without_a_language(self):
        self.assertEqual(analyzer.parse_json('```\n[{"a": 1}]\n```'), [{"a": 1}])

    def test_prose_before_the_array(self):
        self.assertEqual(analyzer.parse_json('Sure, here you go:\n[{"a": 1}]'),
                         [{"a": 1}])

    def test_an_object_wrapping_the_array(self):
        self.assertEqual(analyzer.parse_json('{"items": [{"a": 1}]}'),
                         [{"a": 1}])

    def test_an_empty_array_is_a_real_answer(self):
        """Distinct from a parse failure: the model was asked and said nothing
        was there. That is a result to store, not an error to retry."""
        self.assertEqual(analyzer.parse_json("[]"), [])

    def test_unparseable_text_raises(self):
        """The rule this module exists to hold.

        Returning [] here would be stored as "nothing happened in this window",
        and the window would never be looked at again — while what actually
        happened is that the reply was unreadable and the call should be retried.
        """
        with self.assertRaises(ValueError):
            analyzer.parse_json("I could not do that.")

    def test_broken_json_raises(self):
        with self.assertRaises(ValueError):
            analyzer.parse_json('[{"a": 1},]}{')

    def test_an_object_with_no_array_raises(self):
        with self.assertRaises(ValueError):
            analyzer.parse_json('{"a": 1}')

    def test_a_bare_scalar_raises(self):
        with self.assertRaises(ValueError):
            analyzer.parse_json('"just a string"')


class GramsTest(unittest.TestCase):
    def test_whitespace_is_removed_before_slicing(self):
        """The two texts compared are usually a transcript and something typed,
        and where the spaces fall is exactly what differs between them."""
        self.assertEqual(analyzer.grams("ab c"), analyzer.grams("abc"))

    def test_a_string_shorter_than_n_yields_nothing(self):
        self.assertEqual(analyzer.grams("ab"), set())

    def test_empty_input_yields_nothing(self):
        self.assertEqual(analyzer.grams(""), set())

    def test_overlap_scores_between_zero_and_one(self):
        a, b = analyzer.grams("the quick brown fox"), analyzer.grams("the quick brown fox")
        self.assertEqual(len(a & b) / len(a), 1.0)
        self.assertEqual(analyzer.grams("aaaa") & analyzer.grams("bbbb"), set())


class LegacyDigestReadTest(unittest.TestCase):
    """The voice lanes' old cache tables, read once to carry their answers over.

    Nothing writes them any more. What is read must still be exact: the bridge
    marks a window answered only where the old text matches byte for byte and
    the old call succeeded.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(db.SCHEMA)

    def _put(self, table, day, model, a, text, ok=1):
        self.con.execute(f"INSERT INTO {table} (day, model, a_min, n_seg, ok, text)"
                         " VALUES (?,?,?,?,?,?)", (day, model, a, 1, ok, text))

    def test_a_stored_window_reads_back_with_its_outcome(self):
        self._put("task_digests", "2026-08-27", "m", 540, "hello")
        self._put("task_digests", "2026-08-27", "m", 570, "failed", ok=0)
        got = analyzer.load_digests(self.con, "task_digests", "2026-08-27", "m")
        self.assertEqual(got, {540: ("hello", 1), 570: ("failed", 0)})

    def test_models_days_and_lanes_do_not_mix(self):
        self._put("task_digests", "2026-08-27", "m1", 540, "a")
        self._put("claim_digests", "2026-08-27", "m1", 540, "b")
        self.assertEqual(analyzer.load_digests(
            self.con, "task_digests", "2026-08-27", "m2"), {})
        self.assertEqual(analyzer.load_digests(
            self.con, "task_digests", "2026-08-28", "m1"), {})
        self.assertEqual(analyzer.load_digests(
            self.con, "claim_digests", "2026-08-27", "m1")[540][0], "b")


class RunWindowsTest(unittest.TestCase):
    def _jobs(self, n):
        return [{"a": 540 + i * 30, "text": f"w{i}", "ok": False} for i in range(n)]

    def test_every_window_returns(self):
        jobs = self._jobs(4)
        got, failed = analyzer.run_windows(
            lambda p: '[{"x": 1}]', jobs, lambda r: r["text"])
        self.assertEqual(len(got), 4)
        self.assertEqual(failed, 0)

    def test_one_failing_window_does_not_take_the_batch_down(self):
        """The other windows of that day cost money too. Losing all of them
        because one reply was malformed is the expensive failure."""
        jobs = self._jobs(4)

        def call(prompt):
            if prompt == "w2":
                return "not json"
            return '[{"x": 1}]'

        got, failed = analyzer.run_windows(call, jobs, lambda r: r["text"])
        self.assertEqual(len(got), 3)
        self.assertEqual(failed, 1)

    def test_a_raising_call_counts_as_failed_rather_than_propagating(self):
        jobs = self._jobs(2)

        def call(prompt):
            raise RuntimeError("network")

        got, failed = analyzer.run_windows(call, jobs, lambda r: r["text"])
        self.assertEqual((got, failed), ([], 2))

    def test_success_marks_the_job_and_failure_leaves_it_alone(self):
        """A failed window keeps ok=0 so the next round retries it. Caching a
        failure as a success means that stretch is never looked at again."""
        jobs = self._jobs(2)

        def call(prompt):
            return '[{"x": 1}]' if prompt == "w0" else "not json"

        analyzer.run_windows(call, jobs, lambda r: r["text"])
        self.assertTrue(jobs[0]["ok"])
        self.assertFalse(jobs[1]["ok"])

    def test_no_jobs_makes_no_calls(self):
        calls = []
        got, failed = analyzer.run_windows(
            lambda p: calls.append(p) or "[]", [], lambda r: r["text"])
        self.assertEqual((got, failed, calls), ([], 0, []))

    def test_the_prompt_builder_receives_the_job(self):
        seen = []
        analyzer.run_windows(lambda p: "[]", self._jobs(2),
                             lambda r: seen.append(r["a"]) or r["text"])
        self.assertEqual(sorted(seen), [540, 570])


class NoPrivateCrossModuleReachTest(unittest.TestCase):
    """The coupling this module was created to remove.

    Before it, `claims` called `actions._grams` and both lanes called
    `infer._json` — two modules documented as independent of each other reaching
    through a third one's private surface. A leading underscore is the author
    saying "this is mine"; reading it from another module means the boundary is
    decoration.
    """

    def test_no_module_reads_another_modules_private_name(self):
        import glob
        import os
        import re
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for f in glob.glob(os.path.join(here, "umbra", "**", "*.py"),
                           recursive=True):
            mod = os.path.splitext(os.path.basename(f))[0]
            with open(f, encoding="utf-8") as fh:
                lines = fh.readlines()
            for i, line in enumerate(lines, 1):
                code = line.split("#")[0]
                for m in re.finditer(r"\b([a-z_]+)\.(_[a-z][a-z0-9_]*)\s*\(", code):
                    owner, name = m.group(1), m.group(2)
                    if owner in ("self", "cls", "os", "np", mod):
                        continue
                    bad.append(f"{os.path.relpath(f, here)}:{i} {owner}.{name}")
        self.assertEqual(bad, [], f"private names reached across modules: {bad}")


if __name__ == "__main__":
    unittest.main()
