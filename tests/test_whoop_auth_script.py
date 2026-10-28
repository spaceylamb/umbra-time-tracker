"""Tests for the authorisation script.

The real end to end (browser → WHOOP → callback) needs a person clicking, but
**everything after the callback** can be tested: a real HTTP server, a real
request carrying a code, the real state check and the write. Only the WHOOP
hop is a stub.
"""

import os
import socket
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from unittest import mock

from umbra import whoop_login as script


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CallbackServerTest(unittest.TestCase):
    def test_captures_code_and_state_from_real_request(self):
        port = _free_port()
        out = {}

        # Bind first, then start the thread. Waiting for the bind with a probe
        # connection is wrong — handle_request() takes one connection, and the
        # probe would use up the callback's turn.
        srv = script.make_server(port)
        t = threading.Thread(
            target=lambda: out.update(script.wait_for_callback(srv, timeout=10)),
            daemon=True)
        t.start()

        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/whoop/callback?code=abc&state=xyz",
                timeout=5) as r:
            self.assertEqual(r.status, 200)
        t.join(5)
        self.assertEqual(out.get("code"), "abc")
        self.assertEqual(out.get("state"), "xyz")


class FinishTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "whoop_token.json")
        p = mock.patch.object(script.whoop_auth, "TOKEN_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_state_mismatch_is_rejected(self):
        """Without the state check, someone could hand you their authorisation
        code and have your data written into their account."""
        with self.assertRaises(SystemExit):
            script.finish({"code": "c", "state": "attacker"}, "mine", "uri")

    def test_error_param_is_surfaced(self):
        with self.assertRaises(SystemExit):
            script.finish({"error": "access_denied"}, "s", "uri")

    def test_missing_code_is_rejected(self):
        with self.assertRaises(SystemExit):
            script.finish({"state": "s"}, "s", "uri")

    def test_happy_path_persists_token(self):
        fake = {"access_token": "at", "refresh_token": "rt", "expires_at": 1}
        with mock.patch.object(script.whoop_auth, "exchange_code",
                               return_value=fake) as ex:
            with mock.patch.object(script.whoop_auth, "save") as sv:
                script.finish({"code": "c", "state": "s"}, "s", "uri")
        ex.assert_called_once_with("c", "uri")
        sv.assert_called_once_with(fake)


class AuthorizeUrlIntegrationTest(unittest.TestCase):
    def test_url_uses_the_redirect_we_pass(self):
        with mock.patch.dict(os.environ, {"WHOOP_CLIENT_ID": "cid",
                                          "WHOOP_CLIENT_SECRET": "sec"}):
            url = script.whoop_auth.authorize_url(script.DEFAULT_REDIRECT,
                                                  "state_long_enough")
        self.assertIn(urllib.parse.quote(script.DEFAULT_REDIRECT, safe=""), url)


if __name__ == "__main__":
    unittest.main()
