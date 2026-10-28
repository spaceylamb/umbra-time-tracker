""""Cannot read" must not look like "nothing there".

All three Biome sources (iphone / media / intent) used to do this:

    try:
        names = sorted(os.listdir(root))
    except (OSError, PermissionError):
        return []

So when TCC refused access, the importer reported "✓ 0 collected" — exactly
like "the iPhone has not synced yet". knowledgec got it right from the start:
it raises PermissionError, and the report shows
a glaring ✗.

This failure mode lies in a self-healing way: the database already holds
thousands of rows, the freshness report shows "covered up to today", and it
looks perfectly healthy. With permission really revoked you would see weeks of
"✓ 0 new" while the iPhone half of the timeline quietly stopped — exactly what
the imports table was designed to prevent (see the comment in db.py).
"""

import os
import unittest
from unittest import mock

from umbra import segb
from umbra.importers import intent, iphone, media


class SubdirsTest(unittest.TestCase):
    def test_a_missing_directory_is_empty(self):
        """No Biome directory is a normal state — this Mac may never have synced
        an iPhone."""
        self.assertEqual(segb.subdirs("/nope/not/here"), [])

    def test_a_forbidden_directory_raises(self):
        with mock.patch("os.listdir", side_effect=PermissionError(1, "nope")):
            with self.assertRaises(PermissionError):
                segb.subdirs("/anywhere")

    def test_the_error_says_what_to_do_about_it(self):
        """The error must point at **the host GUI app** — TCC attributes access
        to the responsible process, not to python itself. Granting python is
        wasted effort, learned the hard way.

        Don't check only for "Full Disk Access": the words also appear later in
        the message, and deleting the actionable first sentence would still pass
        — a mutation test caught exactly that once.
        """
        with mock.patch("os.listdir", side_effect=PermissionError(1, "nope")):
            with self.assertRaises(PermissionError) as ctx:
                segb.subdirs("/some/blocked/path")
        msg = str(ctx.exception)
        self.assertIn("HOST", msg)                    # whom to grant
        self.assertIn("/some/blocked/path", msg)      # which directory is unreadable

    def test_it_lists_only_directories(self):
        with mock.patch("os.listdir", return_value=["a", "b.txt"]), \
             mock.patch("os.path.isdir", side_effect=lambda p: p.endswith("a")):
            self.assertEqual(segb.subdirs("/root"), [os.path.join("/root", "a")])


class ImportersSurfaceItTest(unittest.TestCase):
    """All three sources must raise permission errors to the import loop, so it
    records a failure."""

    def test_iphone(self):
        with mock.patch("os.listdir", side_effect=PermissionError(1, "nope")):
            with self.assertRaises(PermissionError):
                iphone.collect()

    def test_media(self):
        with mock.patch("os.listdir", side_effect=PermissionError(1, "nope")):
            with self.assertRaises(PermissionError):
                media.collect()

    def test_intent(self):
        with mock.patch("os.listdir", side_effect=PermissionError(1, "nope")):
            with self.assertRaises(PermissionError):
                intent.collect()


class StillToleratesAbsenceTest(unittest.TestCase):
    """The reverse must hold too: never having synced an iPhone is not a fault and
    must not stop the import."""

    def test_iphone(self):
        with mock.patch("os.listdir",
                        side_effect=FileNotFoundError(2, "no such")):
            self.assertEqual(iphone.collect(), [])

    def test_media(self):
        with mock.patch("os.listdir",
                        side_effect=FileNotFoundError(2, "no such")):
            self.assertEqual(media.collect(), [])

    def test_intent(self):
        with mock.patch("os.listdir",
                        side_effect=FileNotFoundError(2, "no such")):
            self.assertEqual(intent.collect(), [])


if __name__ == "__main__":
    unittest.main()
