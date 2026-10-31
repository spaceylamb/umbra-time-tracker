"""Where Umbra puts things, on each platform.

The bug these pin down is not "wrong directory" — it is "plausible-looking
directory that nobody on this OS uses". os.path.expanduser will cheerfully
build ~/Library/Application Support/Umbra on Linux; nothing raises, the
database is created, and the user simply never finds it. Every assertion here
is about a path being *native to the platform*, not merely non-empty.
"""

import os
import unittest
from unittest import mock

from umbra import paths


def _clean(**over):
    """A pristine environment plus whatever the case wants set.

    clear=True matters: the developer machine has XDG_* and UMBRA_* set from
    real use, and an inherited value would silently make a case assert the
    thing it was written to rule out.
    """
    return mock.patch.dict(os.environ, over, clear=True)


class OverrideTest(unittest.TestCase):
    """UMBRA_* beats the platform default everywhere."""

    def test_data_dir_override_wins_on_every_platform(self):
        for plat in ("darwin", "linux", "win32"):
            with self.subTest(plat), _clean(UMBRA_DATA_DIR="/tmp/x"), \
                    mock.patch("sys.platform", plat):
                self.assertEqual(paths.data_dir(), "/tmp/x")

    def test_config_dir_override_wins(self):
        with _clean(UMBRA_CONFIG_DIR="/tmp/c"):
            self.assertEqual(paths.config_dir(), "/tmp/c")

    def test_log_dir_override_wins(self):
        with _clean(UMBRA_LOG_DIR="/tmp/l"):
            self.assertEqual(paths.log_dir(), "/tmp/l")

    def test_override_expands_a_tilde(self):
        """People write ~/... in env files; an unexpanded ~ becomes a
        directory literally named "~" in the working directory."""
        with _clean(UMBRA_DATA_DIR="~/umbra-data"):
            self.assertEqual(paths.data_dir(),
                             os.path.expanduser("~/umbra-data"))


class MacOSTest(unittest.TestCase):
    def test_data_is_application_support(self):
        with _clean(), mock.patch("sys.platform", "darwin"):
            self.assertTrue(paths.data_dir().endswith(
                "Library/Application Support/Umbra"))

    def test_data_ignores_xdg(self):
        """An existing install's database lives in Application Support.

        Honouring XDG_DATA_HOME here would relocate the only copy of a user's
        data the first time they upgrade, and show them an empty timeline
        rather than an error.
        """
        with _clean(XDG_DATA_HOME="/tmp/xdg"), \
                mock.patch("sys.platform", "darwin"):
            self.assertNotIn("/tmp/xdg", paths.data_dir())

    def test_config_does_follow_xdg(self):
        """Config is the opposite case: ~/.config is where it already is, and
        it is what people symlink out of a dotfiles repo."""
        with _clean(XDG_CONFIG_HOME="/tmp/xdg"), \
                mock.patch("sys.platform", "darwin"):
            self.assertEqual(paths.config_dir(), "/tmp/xdg/umbra")

    def test_logs_are_library_logs(self):
        with _clean(), mock.patch("sys.platform", "darwin"):
            self.assertTrue(paths.log_dir().endswith("Library/Logs/Umbra"))


class LinuxTest(unittest.TestCase):
    def test_data_honours_xdg_data_home(self):
        with _clean(XDG_DATA_HOME="/tmp/xdg"), \
                mock.patch("sys.platform", "linux"):
            self.assertEqual(paths.data_dir(), "/tmp/xdg/umbra")

    def test_data_defaults_to_local_share(self):
        with _clean(), mock.patch("sys.platform", "linux"):
            self.assertEqual(paths.data_dir(),
                             os.path.expanduser("~/.local/share/umbra"))

    def test_config_defaults_to_dot_config(self):
        with _clean(), mock.patch("sys.platform", "linux"):
            self.assertEqual(paths.config_dir(),
                             os.path.expanduser("~/.config/umbra"))

    def test_logs_default_to_local_state(self):
        with _clean(), mock.patch("sys.platform", "linux"):
            self.assertEqual(paths.log_dir(),
                             os.path.expanduser("~/.local/state/umbra/logs"))

    def test_no_macos_shaped_path_anywhere(self):
        """The exact failure this module exists to prevent."""
        with _clean(), mock.patch("sys.platform", "linux"):
            for got in (paths.data_dir(), paths.config_dir(), paths.log_dir()):
                self.assertNotIn("Library", got)

    def test_relative_xdg_value_is_ignored(self):
        """The XDG spec says a relative base dir must be ignored.

        Resolving it against the cwd would put the database wherever the
        process was started from — and for a scheduled job that is a directory
        nobody would think to look in.
        """
        with _clean(XDG_DATA_HOME="relative/path"), \
                mock.patch("sys.platform", "linux"):
            self.assertEqual(paths.data_dir(),
                             os.path.expanduser("~/.local/share/umbra"))


class WindowsTest(unittest.TestCase):
    def test_data_is_localappdata(self):
        with _clean(LOCALAPPDATA=r"C:\Users\x\AppData\Local"), \
                mock.patch("sys.platform", "win32"):
            self.assertEqual(paths.data_dir(),
                             os.path.join(r"C:\Users\x\AppData\Local", "Umbra"))

    def test_config_is_roaming_appdata(self):
        with _clean(APPDATA=r"C:\Users\x\AppData\Roaming"), \
                mock.patch("sys.platform", "win32"):
            self.assertEqual(paths.config_dir(),
                             os.path.join(r"C:\Users\x\AppData\Roaming", "Umbra"))

    def test_falls_back_when_appdata_is_unset(self):
        """A missing LOCALAPPDATA must not produce a path starting with
        "None" — that creates a real directory with a nonsense name."""
        with _clean(), mock.patch("sys.platform", "win32"):
            self.assertNotIn("None", paths.data_dir())
            self.assertNotIn("None", paths.config_dir())


class WiringTest(unittest.TestCase):
    """The modules that own a path constant must read it from here.

    Each of these was its own hardcoded os.path.expanduser call before; a new
    one added later would go unnoticed until a Linux user reported an empty
    database.
    """

    def test_consumers_agree_with_paths(self):
        from umbra import config, db, ops, whoop_auth
        self.assertEqual(db.DB_DIR, paths.data_dir())
        self.assertEqual(config.CONFIG_DIR, paths.config_dir())
        self.assertEqual(ops.LOG_DIR, paths.log_dir())
        self.assertEqual(os.path.dirname(whoop_auth.TOKEN_PATH),
                         paths.data_dir())

    def test_no_module_hardcodes_a_platform_path(self):
        """A module that runs everywhere must not name one OS's directories.

        Modules that declare PLATFORM are exempt, and the exemption is the
        point rather than a loophole: a source reading ~/Library/Biome is
        reading macOS's own data, which is the entire reason that source
        exists, and sources.skip_reason() already steps it aside elsewhere.
        What must never happen is a module with no PLATFORM quietly assuming
        macOS — that one does not get skipped, it just points at nothing.
        """
        import glob
        import re
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for f in glob.glob(os.path.join(here, "umbra", "**", "*.py"),
                           recursive=True):
            if os.path.basename(f) in ("paths.py", "doctor.py"):
                continue        # paths.py owns them; commands/doctor.py reads macOS TCC
            body = open(f, encoding="utf-8").read()
            if re.search(r"^PLATFORM\s*=", body, re.M):
                continue
            for i, line in enumerate(body.splitlines(), 1):
                if line.lstrip().startswith("#") or '"""' in line:
                    continue
                if re.search(r'expanduser\(\s*["\']~/(Library|\.local|AppData)',
                             line):
                    bad.append(f"{os.path.relpath(f, here)}:{i}")
        self.assertEqual(bad, [], f"hardcoded platform paths: {bad}")


if __name__ == "__main__":
    unittest.main()
