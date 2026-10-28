"""The drawer is what the enabled plugins put in it.

    said(con, date, conf) -> dict       this plugin's part of the day's drawer
    SAID_KIND = "claim"                  the kind of item the page can act on
    said_mark(con, item_id, body) -> dict
                                         the page's ✓ / ✕ / Seen on one of them;
                                         LookupError when there is no such item

Before, api.said built the tasks and the claims itself, importing both bundled
plugins by name — so a plugin of the same name in your own directory was never
the one the drawer read, a switched-off plugin still filled its tab, and no other
plugin could put anything in the drawer at all.
"""

import types
import unittest
from unittest import mock

from umbra import api, db, devseed, registry

DAY = devseed.DAY


def _plugin(**kw):
    return types.SimpleNamespace(**kw)


class AssemblyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.con = db.connect(devseed.seed(tempfile.mkdtemp()))

    def _said(self, mods, conf):
        with mock.patch.object(registry, "plugins", return_value=(mods, {})), \
                mock.patch("umbra.config.load", return_value=conf):
            return api.said(DAY, self.con)

    def test_only_enabled_plugins_contribute(self):
        mods = {"a": _plugin(said=lambda con, d, conf: {"a_items": [1]}),
                "b": _plugin(said=lambda con, d, conf: {"b_items": [2]})}
        conf = {"plugins": {"a": {"enabled": True}}}
        self.assertEqual(self._said(mods, conf), {"date": DAY, "a_items": [1]})

    def test_a_plugin_without_a_drawer_adds_nothing(self):
        mods = {"g": _plugin(ENABLED_BY_DEFAULT=True)}
        self.assertEqual(self._said(mods, {}), {"date": DAY})

    def test_the_bundled_ones_through_the_registry(self):
        """Nothing enabled: no tabs. Claims alone: its part and nothing of tasks."""
        found, _ = registry.plugins()
        self.assertEqual(self._said(found, {}), {"date": DAY})
        d = self._said(found, {"plugins": {"claims": {"enabled": True}}})
        self.assertIn("claims", d)
        self.assertNotIn("tasks", d)

    def test_a_bad_date_is_still_refused(self):
        with self.assertRaises(api.NotFound):
            api.said("not-a-date", self.con)


class MarkTest(unittest.TestCase):
    def _mark(self, kind, item, body=None, mods=None):
        with mock.patch.object(registry, "plugins", return_value=(mods or self.mods, {})):
            return api.said_mark(kind, item, body or {}, con=object())

    def setUp(self):
        self.calls = []

        def mark(con, item_id, body):
            if item_id == 404:
                raise LookupError(f"no claim {item_id}")
            self.calls.append((item_id, body))
            return {"ok": True}
        self.mods = {"claims": _plugin(SAID_KIND="claim", said_mark=mark)}

    def test_goes_to_the_plugin_that_owns_the_kind(self):
        self.assertEqual(self._mark("claim", 7, {"seen": True}), {"ok": True})
        self.assertEqual(self.calls, [(7, {"seen": True})])

    def test_a_kind_nobody_owns_is_not_found(self):
        with self.assertRaises(api.NotFound):
            self._mark("mood", 1)

    def test_an_item_the_plugin_does_not_have_is_not_found(self):
        with self.assertRaises(api.NotFound):
            self._mark("claim", 404)


if __name__ == "__main__":
    unittest.main()
