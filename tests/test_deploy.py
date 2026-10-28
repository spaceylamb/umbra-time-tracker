"""Publishing the built page to your own host.

This cannot go through GitHub: the page is built from your local events.db,
and data never goes into a repository — the cloud has no database and nothing
to build. So two separate pipelines:

    code   your machine → git push → GitHub → CI runs tests    code only
    data   your machine → umbra viz → upload → your host       the page only

The command comes from configuration, with no vendor hard-coded: Cloudflare
Pages / Netlify / rsync / S3 are each one command, and the choice is yours.

⚠️ The page embeds complete raw records (a month of it holds thousands of
   email addresses and everything said to an AI). So it is **off** by
   default and needs authentication first — a static file taken once cannot be
   taken back.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import config, ops


def with_conf(text):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    return mock.patch.object(config, "CONFIG_DIR", d)


def _days_ignored(fn):
    """goals' round picks its own days; the old _attrib stand-in took none."""
    return lambda days: fn()


class Base(unittest.TestCase):
    """Make a fake page. A clean CI machine never ran viz, and deploy now checks
    that the page exists — without one these tests are green locally and red
    on CI."""

    def setUp(self):
        from umbra import viz
        d = tempfile.mkdtemp()
        page = os.path.join(d, "umbra-viz.html")
        site = os.path.join(d, "umbra-site")
        with open(page, "w", encoding="utf-8") as f:
            f.write("<html>" + "x" * 2000 + "</html>")
        for p in (mock.patch.object(viz, "OUT", page),
                  mock.patch.object(viz, "SITE_DIR", site)):
            p.start()
            self.addCleanup(p.stop)


class OffByDefaultTest(Base):
    def test_not_configured_means_skipped(self):
        with with_conf(""):
            ran = []
            self.assertFalse(ops.deploy(run=lambda c, **k: ran.append(c)))
            self.assertEqual(ran, [])

    def test_skipped_is_not_a_failure(self):
        """Unconfigured is the normal case in sync; it must not dirty the
        round's exit code."""
        with with_conf(""):
            self.assertIs(ops.deploy(run=lambda *a, **k: None), False)


class ConfiguredTest(Base):
    CONF = '[deploy]\ncommand = "echo pushing {dir}"\n'

    def test_the_configured_command_runs(self):
        with with_conf(self.CONF):
            got = []
            ops.deploy(run=lambda c, **k: got.append(c) or type("R", (), {"returncode": 0})())
            self.assertEqual(len(got), 1)
            self.assertIn("echo pushing", got[0])

    def test_dir_becomes_a_real_directory(self):
        """A directory rather than a file: Pages, Netlify and the like take a
        whole directory. (This used to assert "the data directory" — exactly
        what the "page only" tests below prove dangerous: it holds events.db
        and tokens.)"""
        with with_conf(self.CONF):
            got = []
            # Checked **inside** the callback: by the time deploy returns, the
            # staging directory has been deleted by design
            ops.deploy(run=lambda c, **k: got.append(os.path.isdir(c.rsplit(" ", 1)[-1]))
                       or type("R", (), {"returncode": 0})())
            self.assertEqual(got, [True])

    def test_a_failed_command_is_reported_as_such(self):
        """A silent failure = you believe the site is new while it stopped three
        days ago."""
        with with_conf(self.CONF):
            r = ops.deploy(run=lambda c, **k: type("R", (), {"returncode": 1})())
            self.assertIs(r, None)      # None = ran but failed; False = not configured


if __name__ == "__main__":
    unittest.main()


class PageOnlyTest(Base):
    """{dir} must never be the data directory.

    Besides the page it holds events.db, google_token.json and
    whoop_token.json — handing it all to `pages deploy` publishes the database
    and the tokens with it. So deploy builds a clean staging directory holding
    only the page, and hands that over.
    """

    CONF = '[deploy]\ncommand = "ship {dir}"\n'

    def test_dir_is_not_the_data_directory(self):
        from umbra import viz
        with with_conf(self.CONF):
            got = []
            ops.deploy(run=lambda c, **k: got.append(c) or type("R", (), {"returncode": 0})())
            self.assertNotIn(os.path.dirname(viz.OUT), got[0])

    def _stage(self):
        """Run deploy once and capture the staging directory's full contents (in
        the callback — by the time it returns, the directory is deleted by
        design)."""
        seen = {}

        def run(cmd, **kw):
            d = cmd.rsplit(" ", 1)[-1]
            seen["top"] = sorted(os.listdir(d))
            seen["all"] = sorted(
                os.path.relpath(os.path.join(r, f), d)
                for r, _, fs in os.walk(d) for f in fs)
            seen["size"] = os.path.getsize(os.path.join(d, "index.html"))
            return type("R", (), {"returncode": 0})()

        with with_conf(self.CONF):
            ops.deploy(run=run)
        return seen

    def test_the_staging_directory_holds_only_the_page(self):
        """A host looks for index.html and nothing else is wanted here.

        This used to also carry functions/ — the Cloudflare Pages backend that
        set goals and ran attribution. Both now run locally, so what gets
        published is a page again, and anything else appearing in this directory
        is an accident.
        """
        seen = self._stage()
        self.assertEqual(seen["top"], ["index.html"])
        self.assertGreater(seen["size"], 1000)

    def test_no_database_or_token_anywhere_in_the_tree(self):
        """**The most important test in this file.**

        A near miss on 2026-08-23: {dir} expanded to the data directory, where
        events.db, google_token.json and whoop_token.json sat — one `pages
        deploy` would have published all of them.
        """
        bad = [f for f in self._stage()["all"]
               if f.endswith((".db", ".db-wal", ".db-shm", ".sqlite"))
               or "token" in f.lower() or "secret" in f.lower()]
        self.assertEqual(bad, [], f"these must never be published: {bad}")

    def test_the_staging_directory_is_deleted_afterwards(self):
        """The page is the complete raw record; no second copy on disk."""
        holder = {}
        with with_conf(self.CONF):
            ops.deploy(run=lambda c, **k: holder.setdefault("d", c.rsplit(" ", 1)[-1])
                       or type("R", (), {"returncode": 0})())
        self.assertFalse(os.path.exists(holder["d"]))


class InSyncTest(Base):
    """deploy must be the last step of sync.

    Lost once on 2026-08-23: another branch added a tasks step to sync, and the
    merge overwrote the deploy line — **with no test going red**; the page simply
    stopped updating, and someone noticed days later.
    """

    def test_sync_deploys_at_the_end(self):
        from unittest import mock
        called = []
        with mock.patch.object(ops, "deploy",
                               side_effect=lambda **k: called.append(1)):
            with with_conf(""):
                ops.sync(_tz=lambda: None, _import=lambda: None, _infer=lambda d: None,
                         _viz=lambda: None,
                         _plugins={"actions": lambda d: None,
                                   "claims": lambda d: None,
                                   "goals": _days_ignored(lambda: None)},
                         log=lambda *a: None)
        self.assertEqual(len(called), 1, "sync did not call deploy")

    def test_sync_can_take_an_injected_deploy(self):
        """Unit tests must be able to isolate a real Cloudflare configuration
        completely."""
        called = []
        with with_conf(""):
            ops.sync(_tz=lambda: None, _import=lambda: None, _infer=lambda d: None,
                     _viz=lambda: None,
                     _plugins={"actions": lambda d: None,
                               "claims": lambda d: None,
                               "goals": _days_ignored(lambda: None)},
                     _deploy=lambda **k: called.append(1), log=lambda *a: None)
        self.assertEqual(called, [1])

    def test_deploy_comes_after_the_page_is_built(self):
        """There is nothing to publish before the page exists — the other order
        publishes last round's page."""
        from unittest import mock
        order = []
        with mock.patch.object(ops, "deploy",
                               side_effect=lambda **k: order.append("deploy")):
            with with_conf(""):
                ops.sync(_tz=lambda: None, _import=lambda: None, _infer=lambda d: None,
                         _viz=lambda: order.append("viz"),
                         _plugins={"actions": lambda d: None,
                                   "claims": lambda d: None,
                                   "goals": _days_ignored(lambda: None)},
                         log=lambda *a: None)
        self.assertEqual(order, ["viz", "deploy"])
