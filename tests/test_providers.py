"""Model providers: changing models is configuration, not code.

From the 2026-08-19 open-source refactor. Before it, Gemini's endpoint was
hard-coded in infer.py and switching providers meant editing source. With
providers:

    resolve("gemini-3.7-flash")  → a call(prompt) → text function

Two built-in kinds cover nearly everyone:

    gemini   the native generativelanguage API
    openai   OpenAI-compatible endpoints — one base_url covers
             OpenAI / Ollama / vLLM / llama.cpp / Fireworks;
             "use an open model" = run a local server and change a URL

Configured under [providers.*] in ~/.config/umbra/config.toml, matched by
model-name prefix. With nothing configured gemini* works directly (as before);
any other prefix gets an error that says what to do — "unknown" and "one step
of configuration away" must be told apart.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import config, providers


class ZeroConfigTest(unittest.TestCase):
    def test_the_gemini_prefix_works_without_configuration(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
             mock.patch.object(providers.net, "post_json") as post:
            call = providers.resolve("gemini-3.7-flash", conf={})
            post.return_value = {"candidates": [{"content": {"parts": [
                {"text": "答"}]}}]}
            self.assertEqual(call("问"), "答")
        url = post.call_args.args[0]
        self.assertIn("gemini-3.7-flash", url)
        self.assertIn("key=k", url)

    def test_gemini_without_a_key_says_so_plainly_and_once(self):
        """At resolve, before any window is asked — not once per window."""
        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                providers.resolve("gemini-3.7-flash", conf={})

    def test_an_unknown_prefix_explains_how_to_configure_it(self):
        with self.assertRaisesRegex(RuntimeError, r"config\.toml"):
            providers.resolve("llama3-70b", conf={})


class OpenAICompatibleTest(unittest.TestCase):
    CONF = {"providers": {"ollama": {
        "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "models": ["llama", "qwen"],
    }}}

    def _call(self, model="llama3-70b", conf=None, resp="答"):
        call = providers.resolve(model, conf=conf or self.CONF)
        with mock.patch.object(providers.net, "post_json") as post:
            post.return_value = {"choices": [{"message": {"content": resp}}]}
            out = call("问")
        return out, post

    def test_the_prefix_matches_a_configured_provider(self):
        out, post = self._call()
        self.assertEqual(out, "答")
        self.assertEqual(post.call_args.args[0],
                         "http://localhost:11434/v1/chat/completions")

    def test_the_request_body_is_shaped_like_chat_completions(self):
        _, post = self._call()
        body = post.call_args.args[1]
        self.assertEqual(body["model"], "llama3-70b")
        self.assertEqual(body["messages"], [{"role": "user", "content": "问"}])

    def test_a_local_server_without_a_key_gets_no_authorization(self):
        _, post = self._call()
        headers = post.call_args.kwargs.get("headers") or {}
        self.assertNotIn("Authorization", headers)

    def test_an_api_key_env_sends_bearer(self):
        conf = {"providers": {"fw": {
            "kind": "openai", "base_url": "https://api.fireworks.ai/v1",
            "models": ["gpt", "accounts/"], "api_key_env": "FW_KEY"}}}
        with mock.patch.dict(os.environ, {"FW_KEY": "秘密"}):
            _, post = self._call(model="gpt-5.6-sol", conf=conf)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"],
                         "Bearer 秘密")

    def test_a_configured_key_missing_from_the_environment_says_so(self):
        conf = {"providers": {"fw": {
            "kind": "openai", "base_url": "https://x/v1",
            "models": ["gpt"], "api_key_env": "FW_KEY"}}}
        env = {k: v for k, v in os.environ.items() if k != "FW_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "FW_KEY"):
                providers.resolve("gpt-5.6-sol", conf=conf)

    def test_configuration_can_override_the_built_in_gemini_prefix(self):
        """To route gemini* through your own proxy, say — your configuration
        always wins."""
        conf = {"providers": {"proxy": {
            "kind": "openai", "base_url": "https://proxy/v1",
            "models": ["gemini"]}}}
        _, post = self._call(model="gemini-3.7-flash", conf=conf)
        self.assertIn("proxy", post.call_args.args[0])

    def test_an_unknown_kind_is_refused(self):
        conf = {"providers": {"x": {"kind": "grpc", "models": ["m"]}}}
        with self.assertRaisesRegex(RuntimeError, "grpc"):
            providers.resolve("m1", conf=conf)


class ConfigFileTest(unittest.TestCase):
    def test_no_config_file_means_empty_configuration(self):
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()):
            self.assertEqual(config.load(), {})

    def test_reads_toml(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "config.toml"), "w") as f:
            f.write('[providers.o]\nkind = "openai"\nmodels = ["m"]\n')
        with mock.patch.object(config, "CONFIG_DIR", d):
            got = config.load()
        self.assertEqual(got["providers"]["o"]["kind"], "openai")

    def test_bad_toml_names_the_file_rather_than_reading_as_empty(self):
        """Silently empty, a configuration change would not take effect and
        nobody could tell why."""
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "config.toml"), "w") as f:
            f.write("[[[\n")
        with mock.patch.object(config, "CONFIG_DIR", d):
            with self.assertRaisesRegex(RuntimeError, "config.toml"):
                config.load()


if __name__ == "__main__":
    unittest.main()
