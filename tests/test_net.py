"""Tests for the HTTP layer.

One thing above all, and it was the live failure that took longest to find
(2026-08-15): the CDN in front of WHOOP blocks Python's default User-Agent,
`Python-urllib/x.y`,
with 403 and `error code: 1010`. The request never reaches the application, so
everything the error seems to say about OAuth, signatures or codes misleads.
"""

import unittest
import urllib.error
from unittest import mock

from umbra import net


class UserAgentTest(unittest.TestCase):
    def _captured_request(self, fn, *a, **kw):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=cm) as op:
            fn(*a, **kw)
        return op.call_args[0][0]

    def test_get_json_sets_user_agent(self):
        req = self._captured_request(net.get_json, "https://x.test/a")
        self.assertEqual(req.get_header("User-agent"), net.USER_AGENT)

    def test_post_form_sets_user_agent(self):
        req = self._captured_request(net.post_form, "https://x.test/a", {"k": "v"})
        self.assertEqual(req.get_header("User-agent"), net.USER_AGENT)

    def test_post_json_sets_user_agent(self):
        req = self._captured_request(net.post_json, "https://x.test/a", {"k": "v"})
        self.assertEqual(req.get_header("User-agent"), net.USER_AGENT)

    def test_user_agent_is_not_the_python_default(self):
        """`Python-urllib/*` is exactly the signature the CDN blocks with 1010."""
        self.assertNotIn("Python-urllib", net.USER_AGENT)
        self.assertTrue(net.USER_AGENT.strip())

    def test_caller_can_override_user_agent(self):
        req = self._captured_request(
            net.get_json, "https://x.test/a", {"User-Agent": "Custom/9"})
        self.assertEqual(req.get_header("User-agent"), "Custom/9")

    def test_post_form_is_form_encoded_not_json(self):
        """OAuth token endpoints take a form body per RFC 6749; JSON is refused."""
        req = self._captured_request(net.post_form, "https://x.test/a",
                                     {"grant_type": "refresh_token", "s": "a b"})
        self.assertEqual(req.get_header("Content-type"),
                         "application/x-www-form-urlencoded")
        self.assertIn(b"grant_type=refresh_token", req.data)
        self.assertIn(b"s=a+b", req.data)          # spaces encoded, never sent raw


class RateLimitRetryTest(unittest.TestCase):
    """Retrying on 429.

    Hit on the first full import (2026-08-15): 4 endpoints × 25 a page, about
    100 requests of two years' history sent back to back, straight through
    WHOOP's 100 requests a minute.
    """

    def _err(self, code, headers=None):
        return urllib.error.HTTPError(
            "https://x.test/a", code, "err", headers or {}, None)

    def _run(self, side_effect, **kw):
        slept = []
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = b'{"ok":1}'
        seq = [cm if x is None else x for x in side_effect]
        with mock.patch.object(net.urllib.request, "urlopen", side_effect=seq):
            out = net.get_json("https://x.test/a", sleep=slept.append, **kw)
        return out, slept

    def test_retries_after_429_then_succeeds(self):
        out, slept = self._run([self._err(429, {"X-RateLimit-Reset": "13"}), None])
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(len(slept), 1)

    def test_sleep_comes_from_ratelimit_reset(self):
        _, slept = self._run([self._err(429, {"X-RateLimit-Reset": "13"}), None])
        # A little extra sleep; landing exactly on the window edge earns another 429
        self.assertGreaterEqual(slept[0], 13)
        self.assertLess(slept[0], 20)

    def test_retry_after_header_is_honored(self):
        _, slept = self._run([self._err(429, {"Retry-After": "7"}), None])
        self.assertGreaterEqual(slept[0], 7)

    def test_falls_back_when_no_headers(self):
        _, slept = self._run([self._err(429), None])
        self.assertGreater(slept[0], 0)

    def test_daily_window_is_not_slept_through(self):
        """At the 10,000-a-day limit, Reset may be tens of thousands of seconds.

        Sleeping through it would hang the import for a day; failing loudly is
        better.
        """
        with self.assertRaises(urllib.error.HTTPError):
            self._run([self._err(429, {"X-RateLimit-Reset": "80000"}), None])

    def test_gives_up_after_retries(self):
        errs = [self._err(429, {"X-RateLimit-Reset": "1"})] * 5
        with self.assertRaises(urllib.error.HTTPError):
            self._run(errs, retries=2)

    def test_non_429_is_not_retried(self):
        """A 401 stays a 401 however often it is retried; retrying only delays
        the error."""
        with self.assertRaises(urllib.error.HTTPError):
            self._run([self._err(401), None])
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[self._err(401)]) as op:
            with self.assertRaises(urllib.error.HTTPError):
                net.get_json("https://x.test/a", sleep=lambda s: None)
        self.assertEqual(op.call_count, 1)

    def test_retries_zero_means_no_retry(self):
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[self._err(429)]) as op:
            with self.assertRaises(urllib.error.HTTPError):
                net.get_json("https://x.test/a", retries=0, sleep=lambda s: None)
        self.assertEqual(op.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class SslContextFallbackTest(unittest.TestCase):
    """With no certifi and no macOS /etc/ssl/cert.pem (= bare Ubuntu).

    Found on the first CI run (2026-08-20) on an Ubuntu runner: with certifi
    absent the old code pointed at /etc/ssl/cert.pem — a macOS path, which is
    FileNotFoundError on Linux. The right order: certifi → the system's default
    store → the macOS fallback.
    """

    def setUp(self):
        self._saved = net._ctx
        net._ctx = None
        self.addCleanup(lambda: setattr(net, "_ctx", self._saved))

    def test_bare_linux_uses_the_system_default_store(self):
        import sys
        with mock.patch.dict(sys.modules, {"certifi": None}), \
             mock.patch.object(net.os.path, "exists", return_value=False):
            ctx = net.ssl_context()
        self.assertIsInstance(ctx, net.ssl.SSLContext)


class TransientNetworkRetryTest(unittest.TestCase):
    """A dropped connection is retried a few times instead of failing the step.

    On a flaky uplink a TLS handshake fails intermittently with
    `SSL: UNEXPECTED_EOF_WHILE_READING`: most handshakes to one host fail for a
    few minutes, then all of them succeed. Retrying only 429 meant one EOF threw
    away a whole PLAUD import (dozens of requests), a sink dispatch, or a round
    of model calls.

    Two classes, because a POST cannot be resent freely:
      URLError (not HTTPError)   urlopen wraps errors in the connect / handshake /
                                 send phase this way — the request never reached
                                 the server, so GET and POST can both retry
      dropped while reading      the server may already have acted on it, so only
                                 GET retries
    """

    def setUp(self):
        net._line_down = False
        self.addCleanup(setattr, net, "_line_down", False)

    def _ok(self):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = b'{"ok":1}'
        return cm

    def _eof(self):
        import ssl
        return urllib.error.URLError(ssl.SSLEOFError(
            8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred"))

    def test_get_json_retries_a_dropped_handshake(self):
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[self._eof(), self._eof(), self._ok()]) as op, \
             mock.patch.object(net.time, "sleep"):
            out = net.get_json("https://x.test/a", sleep=lambda s: None)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(op.call_count, 3)

    def test_post_json_retries_a_dropped_handshake(self):
        """The request was never sent, so resending cannot run it twice."""
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[self._eof(), self._ok()]) as op, \
             mock.patch.object(net.time, "sleep"):
            out = net.post_json("https://x.test/a", {"q": 1})
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(op.call_count, 2)

    def test_post_form_retries_a_dropped_handshake(self):
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[self._eof(), self._ok()]) as op, \
             mock.patch.object(net.time, "sleep"):
            out = net.post_form("https://x.test/a", {"q": 1})
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(op.call_count, 2)

    def test_get_text_retries_a_dropped_handshake(self):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = b"hello"
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[self._eof(), cm]), \
             mock.patch.object(net.time, "sleep"):
            self.assertEqual(net.get_text("https://x.test/a"), "hello")

    def test_post_dropped_while_reading_is_not_retried(self):
        """The server may already have acted: a second model charge, a second card."""
        import http.client
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[http.client.RemoteDisconnected("x"),
                                            self._ok()]) as op, \
             mock.patch.object(net.time, "sleep"):
            with self.assertRaises(http.client.RemoteDisconnected):
                net.post_json("https://x.test/a", {"q": 1})
        self.assertEqual(op.call_count, 1)

    def test_get_dropped_while_reading_is_retried(self):
        import http.client
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[http.client.RemoteDisconnected("x"),
                                            self._ok()]) as op, \
             mock.patch.object(net.time, "sleep"):
            self.assertEqual(net.get_json("https://x.test/a",
                                          sleep=lambda s: None), {"ok": 1})
        self.assertEqual(op.call_count, 2)

    def test_gives_up_after_a_bounded_number_of_tries(self):
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[self._eof()] * 20) as op, \
             mock.patch.object(net.time, "sleep"):
            with self.assertRaises(urllib.error.URLError):
                net.post_json("https://x.test/a", {"q": 1})
        self.assertEqual(op.call_count, net.NET_TRIES)

    def _sleeps(self, side_effect, call=None):
        slept = []
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=side_effect) as op, \
             mock.patch.object(net.time, "sleep", side_effect=slept.append):
            try:
                (call or (lambda: net.post_json("https://x.test/a", {"q": 1})))()
            except urllib.error.URLError:
                pass
        return op.call_count, slept

    def test_retries_wait_out_a_minute_long_outage(self):
        """1 + 2 + 4 seconds was measured too short: an outage often runs past
        half a minute, and one of dozens of PLAUD requests always hit it."""
        _, slept = self._sleeps([self._eof()] * 20)
        self.assertGreaterEqual(sum(slept), 60)

    def test_after_a_whole_chain_fails_later_requests_do_not_wait_long(self):
        """With the line really down, a minute per request turns one round into
        tens of minutes and crowds out the next."""
        self._sleeps([self._eof()] * 20)
        calls, slept = self._sleeps([self._eof()] * 20)
        self.assertEqual(calls, 1)
        self.assertEqual(slept, [])

    def test_one_success_restores_the_long_retry(self):
        self._sleeps([self._eof()] * 20)                    # judged down
        self._sleeps([self._ok()])                          # a success = back up
        _, slept = self._sleeps([self._eof()] * 20)
        self.assertGreaterEqual(sum(slept), 60)

    def test_an_http_error_is_not_a_network_blip(self):
        """401 / 500 are the server's answer, not a line problem."""
        err = urllib.error.HTTPError("https://x.test/a", 500, "e", {}, None)
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=[err, self._ok()]) as op, \
             mock.patch.object(net.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                net.post_json("https://x.test/a", {"q": 1})
        self.assertEqual(op.call_count, 1)
