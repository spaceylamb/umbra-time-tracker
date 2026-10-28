"""Looking something up: a model that searches the web and says where it looked.

providers.resolve gives call(prompt) -> text. A fact-check needs more than text:
which pages the answer rests on, and how many searches it cost. So beside it:

    lookup("gemini-3.8-flash")  → ask(prompt) → {"text", "sources", "searches"}

Each of the three big providers searches in its own way, and the response
shapes below were taken from real calls on 2026-10-24 (Anthropic's from its
documentation):

    gemini      generateContent + tools: [{"google_search": {}}]
                sources in groundingMetadata.groundingChunks[].web, as
                redirect links titled with the bare domain
    openai      the Responses API + tools: [{"type": "web_search"}]
                sources in the message's url_citation annotations
    anthropic   Messages + the web_search server tool
                sources in web_search_tool_result blocks and citations

**Sources come from the provider's own record of what it searched, never from
the answer text.** A model can write any URL into a sentence; the grounding
metadata only lists pages the search really returned.
"""

import os
import unittest
from unittest import mock

from umbra import providers

KEYS = {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "ANTHROPIC_API_KEY": "a"}


def _lookup(model, resp, conf=None):
    """One lookup against a canned response; returns (result, post mock)."""
    with mock.patch.dict(os.environ, KEYS), \
            mock.patch.object(providers.net, "post_json") as post, \
            mock.patch.object(providers, "_unredirect", side_effect=lambda u: u):
        post.side_effect = resp if isinstance(resp, list) else [resp]
        ask = providers.lookup(model, conf=conf or {})
        return ask("Did Postgres add MERGE in 15?"), post


GEMINI = {"candidates": [{
    "content": {"parts": [
        {"text": "thinking out loud", "thought": True},
        {"text": "Yes — MERGE arrived in PostgreSQL 15."}]},
    "groundingMetadata": {
        "groundingChunks": [
            {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA",
                     "title": "postgresql.org"}},
            {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB",
                     "title": "wikipedia.org"}}],
        "webSearchQueries": ["postgres merge version", "postgres 15 release notes"]}}]}


class GeminiTest(unittest.TestCase):
    def test_search_is_switched_on_in_the_request(self):
        _, post = _lookup("gemini-3.8-flash", GEMINI)
        body = post.call_args.args[1]
        self.assertEqual(body["tools"], [{"google_search": {}}])
        self.assertIn("gemini-3.8-flash:generateContent", post.call_args.args[0])

    def test_the_answer_leaves_out_the_models_thinking(self):
        r, _ = _lookup("gemini-3.8-flash", GEMINI)
        self.assertEqual(r["text"], "Yes — MERGE arrived in PostgreSQL 15.")

    def test_sources_and_search_count_come_from_the_grounding_record(self):
        r, _ = _lookup("gemini-3.8-flash", GEMINI)
        self.assertEqual([s["title"] for s in r["sources"]],
                         ["postgresql.org", "wikipedia.org"])
        self.assertEqual(r["searches"], 2)

    def test_redirect_links_are_followed_to_the_page_itself(self):
        """Gemini hands back vertexaisearch redirect links that stop working
        after a while. A source someone opens next week has to be the page."""
        real = {"https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA":
                "https://www.postgresql.org/docs/release/15.0/"}
        with mock.patch.dict(os.environ, KEYS), \
                mock.patch.object(providers.net, "post_json", return_value=GEMINI), \
                mock.patch.object(providers, "_unredirect",
                                  side_effect=lambda u: real.get(u, u)):
            r = providers.lookup("gemini-3.8-flash", conf={})("q")
        self.assertEqual(r["sources"][0]["url"], "https://www.postgresql.org/docs/release/15.0/")

    def test_a_redirect_that_cannot_be_followed_keeps_the_link_it_has(self):
        import urllib.error
        with mock.patch.object(providers, "_open_no_redirect",
                               side_effect=urllib.error.URLError("offline")):
            self.assertEqual(providers._unredirect("https://x/redirect/1"),
                             "https://x/redirect/1")


OPENAI = {"output": [
    {"type": "web_search_call", "action": {"type": "search", "queries": ["a", "b"]}},
    {"type": "web_search_call", "action": {"type": "search", "queries": ["c"]}},
    {"type": "message", "content": [{
        "type": "output_text",
        "text": "MERGE was added in PostgreSQL 15.",
        "annotations": [
            {"type": "url_citation", "title": "PostgreSQL: Release Notes",
             "url": "https://www.postgresql.org/docs/release/15.0/?utm_source=openai"},
            {"type": "url_citation", "title": "PostgreSQL: Release Notes",
             "url": "https://www.postgresql.org/docs/release/15.0/?utm_source=openai"}]}]}]}


class OpenAITest(unittest.TestCase):
    def test_gpt_works_with_only_a_key(self):
        """Like gemini*, the provider's own models need no [providers] entry."""
        r, post = _lookup("gpt-6-luna", OPENAI)
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer o")
        self.assertEqual(r["text"], "MERGE was added in PostgreSQL 15.")

    def test_search_is_the_web_search_tool_with_a_cap(self):
        _, post = _lookup("gpt-6-luna", OPENAI)
        body = post.call_args.args[1]
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        self.assertEqual(body["max_tool_calls"], providers.MAX_SEARCHES)

    def test_a_search_is_required_not_optional(self):
        """Measured 2026-10-24: asked "is this true?", gpt-6-luna answered a
        well-known myth from memory without searching at all."""
        _, post = _lookup("gpt-6-luna", OPENAI)
        self.assertEqual(post.call_args.args[1]["tool_choice"], "required")

    def test_without_citations_the_pages_it_searched_stand_in(self):
        """Measured: some answers carry no url_citation though the search ran.
        The pages the search returned are still where it looked."""
        resp = {"output": [
            {"type": "web_search_call", "action": {"type": "search", "sources": [
                {"type": "url", "url": "https://www.nzta.govt.nz/roads"},
                {"type": "url", "url": "https://www.police.govt.nz/driving"}]}},
            {"type": "message", "content": [{"type": "output_text", "text": "No.",
                                             "annotations": []}]}]}
        r, post = _lookup("gpt-6-luna", resp)
        self.assertEqual(post.call_args.args[1]["include"], ["web_search_call.action.sources"])
        self.assertEqual([s["url"] for s in r["sources"]],
                         ["https://www.nzta.govt.nz/roads", "https://www.police.govt.nz/driving"])

    def test_tracking_parameters_are_stripped_and_repeats_collapsed(self):
        r, _ = _lookup("gpt-6-luna", OPENAI)
        self.assertEqual(r["sources"], [{"url": "https://www.postgresql.org/docs/release/15.0/",
                                         "title": "PostgreSQL: Release Notes"}])
        self.assertEqual(r["searches"], 2)

    def test_plain_calls_still_use_chat_completions(self):
        with mock.patch.dict(os.environ, KEYS), \
                mock.patch.object(providers.net, "post_json") as post:
            post.return_value = {"choices": [{"message": {"content": "hi"}}]}
            self.assertEqual(providers.resolve("gpt-6-luna", conf={})("q"), "hi")
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/chat/completions")

    def test_a_local_openai_compatible_server_cannot_search_and_says_so(self):
        conf = {"providers": {"ollama": {"kind": "openai", "models": ["llama"],
                                         "base_url": "http://localhost:11434/v1"}}}
        with self.assertRaisesRegex(RuntimeError, "llama3.*search"):
            providers.lookup("llama3", conf=conf)


ANTHROPIC = {
    "content": [
        {"type": "text", "text": "I'll search for that."},
        {"type": "server_tool_use", "id": "srv1", "name": "web_search",
         "input": {"query": "postgres merge"}},
        {"type": "web_search_tool_result", "tool_use_id": "srv1", "content": [
            {"type": "web_search_result", "url": "https://www.postgresql.org/docs/15/sql-merge.html",
             "title": "PostgreSQL: MERGE", "encrypted_content": "x", "page_age": "2022"},
            {"type": "web_search_result", "url": "https://en.wikipedia.org/wiki/Merge_(SQL)",
             "title": "Merge (SQL)", "encrypted_content": "y"}]},
        {"type": "text", "text": "MERGE was added in PostgreSQL 15.",
         "citations": [{"type": "web_search_result_location",
                        "url": "https://www.postgresql.org/docs/15/sql-merge.html",
                        "title": "PostgreSQL: MERGE", "cited_text": "MERGE …",
                        "encrypted_index": "z"}]}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 6000, "output_tokens": 90,
              "server_tool_use": {"web_search_requests": 1}}}


class AnthropicTest(unittest.TestCase):
    def test_claude_works_with_only_a_key(self):
        r, post = _lookup("claude-sonnet-5-5", ANTHROPIC)
        self.assertEqual(post.call_args.args[0], "https://api.anthropic.com/v1/messages")
        h = post.call_args.kwargs["headers"]
        self.assertEqual(h["x-api-key"], "a")
        self.assertIn("anthropic-version", h)

    def test_search_is_the_web_search_server_tool_with_a_cap(self):
        _, post = _lookup("claude-sonnet-5-5", ANTHROPIC)
        tool = post.call_args.args[1]["tools"][0]
        self.assertEqual((tool["type"], tool["name"], tool["max_uses"]),
                         ("web_search_20250305", "web_search", providers.MAX_SEARCHES))

    def test_the_answer_is_the_text_after_the_last_search(self):
        """"I'll search for that." is narration, not the answer."""
        r, _ = _lookup("claude-sonnet-5-5", ANTHROPIC)
        self.assertEqual(r["text"], "MERGE was added in PostgreSQL 15.")

    def test_sources_are_every_page_the_search_returned(self):
        r, _ = _lookup("claude-sonnet-5-5", ANTHROPIC)
        self.assertEqual([s["url"] for s in r["sources"]],
                         ["https://www.postgresql.org/docs/15/sql-merge.html",
                          "https://en.wikipedia.org/wiki/Merge_(SQL)"])
        self.assertEqual(r["searches"], 1)

    def test_the_pages_the_answer_cites_come_first(self):
        """A caller keeps the first few; those should be what the answer rests
        on, not whatever the search ranked first."""
        content = [ANTHROPIC["content"][2], {**ANTHROPIC["content"][3], "citations": [
            {**ANTHROPIC["content"][3]["citations"][0],
             "url": "https://en.wikipedia.org/wiki/Merge_(SQL)", "title": "Merge (SQL)"}]}]
        r, _ = _lookup("claude-sonnet-5-5", {**ANTHROPIC, "content": content})
        self.assertEqual(r["sources"][0]["url"], "https://en.wikipedia.org/wiki/Merge_(SQL)")

    def test_a_search_error_is_a_result_not_a_crash(self):
        """The API reports a failed search as a 200 with an error object where
        the result list would be."""
        resp = {**ANTHROPIC, "content": [
            {"type": "web_search_tool_result", "tool_use_id": "s",
             "content": {"type": "web_search_tool_result_error", "error_code": "unavailable"}},
            {"type": "text", "text": "No firm answer found."}],
            "usage": {"server_tool_use": {"web_search_requests": 0}}}
        r, _ = _lookup("claude-sonnet-5-5", resp)
        self.assertEqual((r["text"], r["sources"], r["searches"]),
                         ("No firm answer found.", [], 0))

    def test_a_paused_turn_is_continued_and_the_searches_add_up(self):
        paused = {"content": ANTHROPIC["content"][:3], "stop_reason": "pause_turn",
                  "usage": {"server_tool_use": {"web_search_requests": 1}}}
        r, post = _lookup("claude-sonnet-5-5", [paused, ANTHROPIC])
        self.assertEqual(post.call_count, 2)
        second = post.call_args_list[1].args[1]["messages"]
        self.assertEqual(second[-1], {"role": "assistant", "content": paused["content"]})
        self.assertEqual(r["searches"], 2)
        self.assertEqual(r["text"], "MERGE was added in PostgreSQL 15.")

    def test_plain_calls_return_the_text(self):
        with mock.patch.dict(os.environ, KEYS), \
                mock.patch.object(providers.net, "post_json") as post:
            post.return_value = {"content": [{"type": "text", "text": "hi"}],
                                 "stop_reason": "end_turn"}
            self.assertEqual(providers.resolve("claude-haiku-5-5", conf={})("q"), "hi")

    def test_no_key_says_which_one_once(self):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
                providers.lookup("claude-sonnet-5-5", conf={})


class StatusTest(unittest.TestCase):
    def test_the_built_in_keys_are_named_for_the_page(self):
        with mock.patch.dict(os.environ, KEYS):
            self.assertEqual(providers.status("gpt-6-luna", conf={})["key_env"], "OPENAI_API_KEY")
            self.assertEqual(providers.status("claude-sonnet-5-5", conf={})["key_env"],
                             "ANTHROPIC_API_KEY")
        self.assertTrue(providers.can_search("claude-sonnet-5-5", conf={}))
        self.assertTrue(providers.can_search("gpt-6-luna", conf={}))
        local = {"providers": {"o": {"kind": "openai", "models": ["llama"],
                                     "base_url": "http://localhost:11434/v1"}}}
        self.assertFalse(providers.can_search("llama3", conf=local))


if __name__ == "__main__":
    unittest.main()
