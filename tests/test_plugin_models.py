"""Each plugin can run on its own model.

    [plugins.claims]
    model = "gpt-6-luna"

Before, every plugin ran on the one model inference uses. A fact-check lane
wants a cheap model for extraction and a model that searches for lookups, and
neither need be the model that writes the day. Without a `model` the plugin
runs on inference's model, as it always has.

The model is part of every cache key, so changing it re-asks the windows it
covers: what one model answered is not what another would.
"""

import os
import unittest
from unittest import mock

from umbra import analyzer, infer
from umbra.plugins import claims


class ModelForTest(unittest.TestCase):
    def test_a_plugin_without_a_model_runs_on_inference_model(self):
        self.assertEqual(analyzer.model_for("claims", conf={}), infer.MODEL)

    def test_a_plugin_names_its_own(self):
        conf = {"plugins": {"claims": {"model": "gpt-6-luna"}}}
        self.assertEqual(analyzer.model_for("claims", conf=conf), "gpt-6-luna")
        self.assertEqual(analyzer.model_for("goals", conf=conf), infer.MODEL)

    def test_a_round_runs_each_plugin_on_its_model(self):
        conf = {"plugins": {"goals": {"model": "claude-haiku-5-5"}}}
        seen = []
        with mock.patch("umbra.config.load", return_value=conf), \
                mock.patch.object(analyzer, "run",
                                  side_effect=lambda con, n, mod, day, model, **k:
                                  seen.append(model) or {}):
            analyzer.run_days(None, "goals", object(), ["2026-10-01"])
        self.assertEqual(seen, ["claude-haiku-5-5"])


class ClaimsModelTest(unittest.TestCase):
    def test_extraction_uses_the_lanes_model(self):
        conf = {"plugins": {"claims": {"model": "gpt-6-luna"}}}
        seen = []
        with mock.patch("umbra.config.load", return_value=conf), \
                mock.patch.object(claims.analyzer, "run",
                                  side_effect=lambda con, n, mod, day, model, **k:
                                  seen.append(model) or dict.fromkeys(
                                      ("called", "reused", "failed"), 0)), \
                mock.patch.object(claims, "_bridge_cache", return_value=0), \
                mock.patch.object(claims, "records", return_value=[]), \
                mock.patch("umbra.db.items_after_legacy"):
            claims.scan(None, "2026-10-01")
        self.assertEqual(seen, ["gpt-6-luna"])


if __name__ == "__main__":
    unittest.main()
