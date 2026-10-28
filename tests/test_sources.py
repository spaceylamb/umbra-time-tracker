"""The source plugin protocol: a Source is a module, its conventions module
attributes.

From the 2026-08-19 open-source refactor. The 13 importers already looked
alike (collect(since_ms) → rows); this only makes the implicit contract explicit:

    collect(since_ms)   required — without it, not a source
    SOURCE              the name written into events.source; defaults to the module name
    PLATFORM            "darwin" / "linux" / None (runs anywhere)
    REQUIRES            ["env:KEY", "cli:name", "file:path", "fda"]
    check()             optional, a custom health check

Why a module and not a class: all 13 importers are modules, and a class would be
ceremony — the protocol (a few attributes and one function) reads just as
clearly as a module, with one less indent.

Your own sources go in ~/.config/umbra/sources/*.py; the file name is the source
name.
A broken file is **skipped and recorded**, never taking other sources down — but
the error must be visible: skipped silently, "why didn't my source run" has no
answer.
"""

import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from umbra import sources
from umbra.importers import ALL


class BuiltInSourcesFollowTheProtocolTest(unittest.TestCase):
    def test_every_built_in_source_has_collect(self):
        for name, mod in ALL.items():
            self.assertTrue(callable(getattr(mod, "collect", None)), name)

    def test_platform_takes_only_known_values(self):
        for name, mod in ALL.items():
            self.assertIn(getattr(mod, "PLATFORM", None),
                          (None, "darwin", "linux"), name)

    def test_sources_on_apple_private_stores_are_marked_darwin(self):
        """knowledgeC and Biome exist only on macOS. Unmarked, a Linux import
        would show four sources failing with "no such file" — the wrong
        environment, not a fault."""
        for name in ("knowledgec", "iphone", "media", "intent"):
            self.assertEqual(ALL[name].PLATFORM, "darwin", name)

    def test_sources_needing_credentials_declare_them(self):
        self.assertIn("env:WHOOP_CLIENT_ID", ALL["whoop"].REQUIRES)
        self.assertIn("env:UMBRA_BUTLER_PAT", ALL["butler"].REQUIRES)
        self.assertIn("env:WEREAD_API_KEY", ALL["weread"].REQUIRES)
        self.assertIn("env:UMBRA_LOCATION_TOKEN", ALL["location"].REQUIRES)

    def test_requires_entries_are_all_in_a_known_form(self):
        for name, mod in ALL.items():
            for r in getattr(mod, "REQUIRES", []):
                kind = r.split(":", 1)[0]
                self.assertIn(kind, ("env", "cli", "file", "fda"),
                              f"{name}: {r}")


class RequirementChecksTest(unittest.TestCase):
    def test_env_set_is_satisfied(self):
        with mock.patch.dict(os.environ, {"X_TOKEN": "1"}):
            self.assertEqual(sources.probe("env:X_TOKEN")[0], True)

    def test_env_unset_is_not(self):
        env = {k: v for k, v in os.environ.items() if k != "X_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            ok, hint = sources.probe("env:X_TOKEN")
            self.assertEqual(ok, False)
            self.assertIn("X_TOKEN", hint)

    def test_file_present_is_satisfied(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(sources.probe(f"file:{f.name}")[0], True)

    def test_file_expands_a_tilde(self):
        """REQUIRES holds things like ~/.plaud/tokens.json — unexpanded it is
        always False, while the path it reports looks right, which is very hard
        to track down."""
        with mock.patch.object(os.path, "exists", return_value=True) as p:
            sources.probe("file:~/x")
            p.assert_called_once_with(os.path.expanduser("~/x"))

    def test_cli_is_looked_up_on_path(self):
        self.assertEqual(sources.probe("cli:sh")[0], True)
        self.assertEqual(sources.probe("cli:绝不存在的命令")[0], False)

    def test_fda_that_cannot_be_answered_says_so(self):
        """Full Disk Access has no reliable probe. None = don't know — passing
        it off as True
        shows no grant as ready, and as False shows ready as broken."""
        ok, hint = sources.probe("fda")
        self.assertIsNone(ok)
        self.assertTrue(hint)


class PlatformGateTest(unittest.TestCase):
    def _mod(self, platform):
        m = mock.Mock(spec=["collect", "PLATFORM"])
        m.PLATFORM = platform
        return m

    def test_no_platform_runs_anywhere(self):
        self.assertIsNone(sources.skip_reason(self._mod(None)))

    def test_this_platform_runs(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIsNone(sources.skip_reason(self._mod("darwin")))

    def test_another_platform_gives_a_plain_reason(self):
        with mock.patch.object(sys, "platform", "linux"):
            r = sources.skip_reason(self._mod("darwin"))
            self.assertIn("macOS", r)


class UserSourceDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _write(self, name, body):
        with open(os.path.join(self.dir, name), "w") as f:
            f.write(textwrap.dedent(body))

    def test_a_good_file_is_found_named_by_its_file(self):
        self._write("mysrc.py", """
            def collect(since_ms=None):
                return []
        """)
        found, errors = sources.discover(user_dir=self.dir)
        self.assertIn("mysrc", found)
        self.assertEqual(errors, {})

    def test_a_bad_file_is_skipped_but_its_error_recorded(self):
        """One bad source does not take the others down (the same philosophy as
        import's fault isolation), but it must be visible — "why didn't my
        source run" needs an answer."""
        self._write("bad.py", "def collect(:\n")
        self._write("good.py", "def collect(since_ms=None):\n    return []\n")
        found, errors = sources.discover(user_dir=self.dir)
        self.assertIn("good", found)
        self.assertNotIn("bad", found)
        self.assertIn("bad", errors)

    def test_a_module_without_collect_is_not_a_source(self):
        self._write("notasrc.py", "X = 1\n")
        found, errors = sources.discover(user_dir=self.dir)
        self.assertNotIn("notasrc", found)
        self.assertIn("notasrc", errors)   # most likely a mistake; it must be visible

    def test_a_user_source_overrides_a_built_in_of_the_same_name(self):
        """A feature: unhappy with the built-in chrome filter, drop a file of the
        same name in your directory and it is replaced, no repository change."""
        self._write("zsh.py", "def collect(since_ms=None):\n    return []\n")
        found, _ = sources.discover(user_dir=self.dir)
        self.assertIsNot(found["zsh"], ALL["zsh"])

    def test_a_missing_directory_means_no_user_sources(self):
        found, errors = sources.discover(user_dir=os.path.join(self.dir, "无"))
        self.assertEqual(set(found), set(ALL))
        self.assertEqual(errors, {})

    def test_underscore_files_do_not_count(self):
        """__pycache__ / _helpers.py and the like."""
        self._write("_util.py", "def collect(since_ms=None):\n    return []\n")
        found, errors = sources.discover(user_dir=self.dir)
        self.assertNotIn("_util", found)
        self.assertEqual(errors, {})


class SourceStatusTest(unittest.TestCase):
    def test_status_answers_every_requirement(self):
        m = mock.Mock(spec=["collect", "REQUIRES"])
        m.REQUIRES = ["env:不存在的KEY", "cli:sh"]
        st = sources.status(m)
        self.assertEqual(len(st), 2)
        self.assertEqual(st[0], ("env:不存在的KEY", False, mock.ANY))
        self.assertEqual(st[1][:2], ("cli:sh", True))

    def test_a_module_s_own_check_wins(self):
        m = mock.Mock(spec=["collect", "REQUIRES", "check"])
        m.REQUIRES = ["env:X"]
        m.check.return_value = [("custom", True, "")]
        self.assertEqual(sources.status(m), [("custom", True, "")])


if __name__ == "__main__":
    unittest.main()
