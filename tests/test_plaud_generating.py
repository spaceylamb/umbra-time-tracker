"""A file halfway through re-transcription must not be imported.

The case that showed it: the app said `Generating…` while the API already
returned hundreds of transcript segments — **a half-finished transcript is
readable**. Importing it would:

  · store an intermediate version and trigger a full infer + model calls
    (money spent for nothing)
  · import again once generation finishes, and pay again
  · leave the page wrong in between, with every step exiting 0

The API has no status field (none of the listing's six fields), so "generating"
can only be recognised from the shape of the blocks. Measured:

    generating   source_list=[transaction, …]   note_list=[]
    normal       source_list=[transaction, …]   note_list=[auto_sum_note, …]

Every normal file sampled had an auto_sum_note; the one generating had none,
and it filled in within half an hour — so "skip and wait for the next round"
costs little.

Both lists empty **does not** count as generating: that is "should have been
transcribed and was not". The two need different handling — one wants a person
to re-generate in the app, the other only waits. Mixing them up would let a
really broken file be skipped forever as "still generating".
"""

import unittest
from unittest import mock

from umbra.importers import plaud


def detail(source_types, note_types, content=1):
    return {
        "id": "of_" + "a" * 32,
        "start_at": "2026-09-14T14:45:53",
        "source_list": [{"data_type": t, "data_content": "[]" if content else ""}
                        for t in source_types],
        "note_list": [{"data_type": t, "data_content": "x"} for t in note_types],
    }


class IsGeneratingTest(unittest.TestCase):
    def test_a_transcript_with_an_empty_note_list_is_generating(self):
        self.assertTrue(plaud.is_generating(
            detail(["transaction", "outline"], [])))

    def test_a_summary_in_note_list_means_done(self):
        self.assertFalse(plaud.is_generating(
            detail(["transaction", "outline"], ["auto_sum_note"])))

    def test_both_empty_is_not_generating(self):
        """That is "never transcribed" — it must be reported, not quietly
        skipped as "still generating"."""
        self.assertFalse(plaud.is_generating(detail([], [])))

    def test_only_a_placeholder_block_is_not_generating(self):
        """Before transcription finishes source_list may hold only a mark_memo
        placeholder.

        The test is pinned to transaction rather than "source_list non-empty":
        a placeholder says someone pressed the button, nothing about
        transcription.
        """
        self.assertFalse(plaud.is_generating(detail(["mark_memo"], [])))


class CollectSkipsGeneratingTest(unittest.TestCase):
    """The wiring: a correct test that collect forgets to use is no test."""

    def _collect(self, det, gen_since=None):
        def fake(url, *a, **k):
            if "files/?" in url:
                return {"data": [{"id": det["id"],
                                  "start_at": det["start_at"],
                                  "created_at": det["start_at"]}]}
            return det

        with mock.patch.object(plaud.plaud_auth, "authed_get_json", fake), \
             mock.patch.object(plaud.plaud_auth, "ensure_access_token",
                               lambda *a, **k: "t"), \
             mock.patch.object(plaud, "_known_file_ids", lambda con: set()), \
             mock.patch.object(plaud, "_known_serials", lambda con: {}), \
             mock.patch.object(plaud, "_checked_map", lambda con: {}), \
             mock.patch.object(plaud, "_generating_map",
                               lambda con: dict(gen_since or {})), \
             mock.patch.object(plaud.db, "connect", mock.MagicMock()):
            return plaud.collect()

    def test_a_generating_file_imports_no_rows(self):
        det = detail(["transaction"], [])
        det["source_list"][0]["data_content"] = (
            '[{"start_time":0,"end_time":1000,"content":"half done",'
            '"speaker":"Speaker 1"}]')
        self.assertEqual(self._collect(det), [])

    def test_a_finished_file_imports_as_usual(self):
        """The counter-anchor: the test must not become "skip everything"."""
        det = detail(["transaction"], ["auto_sum_note"])
        det["source_list"][0]["data_content"] = (
            '[{"start_time":0,"end_time":1000,"content":"done",'
            '"speaker":"Speaker 1"}]')
        self.assertEqual(len(self._collect(det)), 1)

    def test_empty_for_too_long_is_imported_anyway(self):
        """An empty note_list has a cause that **is not temporary**: automatic
        summaries switched off in the app.

        Then every recording matches forever. Skipping without a limit means
        silently losing everything, with not one log line — every step exits 0.

        So after the grace period it is imported anyway. Better a doubtful
        transcript in the store than quietly nothing.
        """
        import time
        det = detail(["transaction"], [])
        det["source_list"][0]["data_content"] = (
            '[{"start_time":0,"end_time":1000,"content":"stuck",'
            '"speaker":"Speaker 1"}]')
        long_ago = int(time.time() * 1000) - plaud.GENERATING_GRACE_MS - 1000
        self.assertEqual(
            len(self._collect(det, {"a" * 32: long_ago})), 1)

    def test_just_started_generating_is_still_held(self):
        """The counter-anchor: a grace of 0 would mean the feature does nothing."""
        import time
        det = detail(["transaction"], [])
        det["source_list"][0]["data_content"] = (
            '[{"start_time":0,"end_time":1000,"content":"half done",'
            '"speaker":"Speaker 1"}]')
        recent = int(time.time() * 1000) - 2 * 3600_000
        self.assertEqual(self._collect(det, {"a" * 32: recent}), [])


if __name__ == "__main__":
    unittest.main()
