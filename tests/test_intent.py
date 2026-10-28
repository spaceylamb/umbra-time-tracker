"""The App.Intent importer — "you sent a message", "you made a call".

⚠️ **This source has no byte-level fixture, on purpose.**

Every record in a real file carries a 2–3 KB `NSKeyedArchiver` blob holding
the whole serialized intent: the contact's name, conversation ids,
the phone number in several spellings, and what look like fragments of messages.
Scrubbing is whack-a-mole — remove one spelling of a number and another
survives; clear one blob and the others remain.

The container format itself is already checked by two real fixtures (iphone
and media share the parser and the layout). What needs checking here is **the
field mapping and filtering**, and a dict does that — no contact names need to
be in the repository.
"""

import unittest
from unittest import mock

from umbra import segb
from umbra.importers import intent

BASE = 807401272.0          # 2026-07-30, a realistic magnitude


def rec(apple_s, bundle="com.apple.MobileSMS", cls="INSendMessageIntent",
        verb="SendMessage", phone="+18186121884", blob=b"\x00" * 2400):
    """One parsed App.Intent record (field numbers from real files)."""
    r = {1: [apple_s], 2: [bundle.encode()], 3: [b"intents"],
         4: [cls.encode()], 5: [verb.encode()], 6: [1], 7: [4]}
    if phone:
        r[12] = [phone.encode()]
    if blob:
        r[8] = [blob]
    return r


class MappingTest(unittest.TestCase):
    def test_becomes_a_point_event(self):
        rows = intent.to_rows([rec(BASE)])
        self.assertEqual(len(rows), 1)
        uid, ts, dur, src, kind, payload = rows[0]
        self.assertEqual(src, "intent")
        self.assertEqual(ts, segb.apple_ms(BASE))
        self.assertIsNone(dur, "an intent is an instant; it has no duration")

    def test_verb_and_app_are_kept(self):
        rows = intent.to_rows([rec(BASE)])
        self.assertEqual(rows[0][5]["verb"], "SendMessage")
        self.assertEqual(rows[0][5]["bundle"], "com.apple.MobileSMS")

    def test_call_intent(self):
        rows = intent.to_rows([rec(BASE, bundle="com.apple.InCallService",
                                   cls="INStartCallIntent", verb="StartCall")])
        self.assertEqual(rows[0][5]["verb"], "StartCall")

    def test_uid_is_stable(self):
        evs = [rec(BASE)]
        self.assertEqual(intent.to_rows(evs)[0][0], intent.to_rows(evs)[0][0])

    def test_distinct_events_get_distinct_uids(self):
        rows = intent.to_rows([rec(BASE), rec(BASE + 60)])
        self.assertEqual(len({r[0] for r in rows}), 2)

    def test_records_without_a_verb_are_dropped(self):
        r = rec(BASE)
        del r[5]
        del r[4]
        self.assertEqual(intent.to_rows([r]), [])


class PrivacyTest(unittest.TestCase):
    """f12 is the other party's phone number; f8 is a serialized blob with names
    and conversation ids.

    The same judgement as media's f8: knowing "you sent a message" already says
    what you were doing; knowing to whom and what is another order of
    sensitivity. An allowlist, not a blocklist.
    """

    def test_phone_number_is_never_stored(self):
        rows = intent.to_rows([rec(BASE, phone="+18186121884")])
        self.assertNotIn("8186121884", repr(rows))

    def test_blob_is_never_stored(self):
        rows = intent.to_rows([rec(BASE, blob=b"Nic Dikon" + b"\x00" * 100)])
        self.assertNotIn("Nic Dikon", repr(rows))

    def test_kept_fields_exclude_the_sensitive_ones(self):
        self.assertNotIn(intent.F_HANDLE, intent.KEEP_FIELDS)
        self.assertNotIn(intent.F_BLOB, intent.KEEP_FIELDS)

    def test_payload_has_no_unexpected_keys(self):
        """New fields stay out of the payload by default — that is what an
        allowlist is for."""
        rows = intent.to_rows([rec(BASE)])
        self.assertEqual(set(rows[0][5]), {"bundle", "app", "verb", "intent"})


class SinceTest(unittest.TestCase):
    def test_filters_old_events(self):
        rows = intent.to_rows([rec(BASE), rec(BASE + 10_000)],
                              since_ms=segb.apple_ms(BASE + 5_000))
        self.assertEqual(len(rows), 1)


class CollectTest(unittest.TestCase):
    def test_missing_dir_is_not_an_error(self):
        with mock.patch.object(intent, "stream_dirs", return_value=[]):
            self.assertEqual(intent.collect(), [])

    def test_registered(self):
        from umbra.importers import ALL, SOURCE_OF
        self.assertIn("intent", ALL)
        self.assertEqual(SOURCE_OF["intent"], "intent")

    def test_collect_takes_since_ms_first(self):
        import inspect
        self.assertEqual(
            list(inspect.signature(intent.collect).parameters)[0], "since_ms")

    def test_reads_local_and_remote(self):
        """Intents happen on both the Mac and the iPhone; both are read."""
        import os
        import tempfile
        root = tempfile.mkdtemp()
        for side in ("local", "remote/PHONE"):
            os.makedirs(os.path.join(root, intent.STREAM, side))
        with mock.patch.object(intent, "BIOME", root):
            got = [d.replace(root, "") for d in intent.stream_dirs()]
        self.assertEqual(len(got), 2)


if __name__ == "__main__":
    unittest.main()
