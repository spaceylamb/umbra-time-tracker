"""Tests for WHOOP token storage and refresh.

Run: python3 -m unittest discover -s tests -t . -v

They target what only breaks once live — an access token lasts an hour, so
development never reaches the refresh branch; refresh logic has to be covered
by tests, not by trying it.
"""

import json
import os
import stat
import tempfile
import unittest
import urllib.error
from unittest import mock

from umbra import whoop_auth as wa

HOUR_MS = 3_600_000
NOW = 1_755_000_000_000          # a fixed time, so tests do not depend on the clock


def _tok(**over) -> dict:
    base = {
        "access_token": "at_old",
        "refresh_token": "rt_old",
        "expires_at": NOW + HOUR_MS,
        "scope": "read:sleep offline",
    }
    base.update(over)
    return base


def _http_error(status: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        wa.TOKEN_URL, status, "err", {}, __import__("io").BytesIO(body))


class TokenStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "whoop_token.json")

    def test_roundtrip(self):
        wa.save(_tok(), self.path)
        self.assertEqual(wa.load(self.path), _tok())

    def test_load_missing_returns_none(self):
        self.assertIsNone(wa.load(os.path.join(self.dir, "nope.json")))

    def test_permissions_are_owner_only(self):
        """A token is as good as account access; it must not be 644."""
        wa.save(_tok(), self.path)
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600, f"expected 600, got {oct(mode)}")

    def test_write_is_atomic(self):
        """A write failing halfway must leave the old file intact — not half a JSON."""
        wa.save(_tok(access_token="at_v1"), self.path)
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                wa.save(_tok(access_token="at_v2"), self.path)
        self.assertEqual(wa.load(self.path)["access_token"], "at_v1")

    def test_no_temp_file_left_behind(self):
        wa.save(_tok(), self.path)
        self.assertEqual(os.listdir(self.dir), ["whoop_token.json"])


class EnsureAccessTokenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "whoop_token.json")
        env = mock.patch.dict(os.environ, {
            "WHOOP_CLIENT_ID": "cid", "WHOOP_CLIENT_SECRET": "sec"})
        env.start()
        self.addCleanup(env.stop)

    def test_missing_file_raises_auth_expired(self):
        with self.assertRaises(wa.WhoopAuthExpired):
            wa.ensure_access_token(self.path, now_ms=NOW)

    def test_valid_token_is_reused_without_network(self):
        wa.save(_tok(), self.path)
        with mock.patch.object(wa.net, "post_form") as post:
            got = wa.ensure_access_token(self.path, now_ms=NOW)
        self.assertEqual(got, "at_old")
        post.assert_not_called()

    def test_expired_token_triggers_refresh(self):
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at_new", "refresh_token": "rt_new",
                "expires_in": 3600}) as post:
            got = wa.ensure_access_token(self.path, now_ms=NOW)
        self.assertEqual(got, "at_new")
        post.assert_called_once()

    def test_refresh_fires_within_margin(self):
        """Thirty seconds left is time to refresh, not to wait for expiry — or a
        request 401s on the way."""
        wa.save(_tok(expires_at=NOW + 30_000), self.path)
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at_new", "expires_in": 3600}) as post:
            wa.ensure_access_token(self.path, now_ms=NOW)
        post.assert_called_once()

    def test_offline_scope_sent_on_refresh(self):
        """Refreshing without offline may drop refresh_token from the response."""
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at_new", "expires_in": 3600}) as post:
            wa.ensure_access_token(self.path, now_ms=NOW)
        body = post.call_args[0][1]
        self.assertIn("offline", body["scope"])
        self.assertEqual(body["grant_type"], "refresh_token")

    def test_new_refresh_token_is_persisted(self):
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at_new", "refresh_token": "rt_new",
                "expires_in": 3600}):
            wa.ensure_access_token(self.path, now_ms=NOW)
        self.assertEqual(wa.load(self.path)["refresh_token"], "rt_new")

    def test_old_refresh_token_kept_when_response_omits_it(self):
        """WHOOP does not always return a new refresh_token.

        Overwriting the old one with None when it is absent makes the next
        refresh fail — "worked for a few days, then wants authorising again", the
        most common live failure of integrations like this.
        """
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at_new", "expires_in": 3600}):
            wa.ensure_access_token(self.path, now_ms=NOW)
        self.assertEqual(wa.load(self.path)["refresh_token"], "rt_old")

    def test_expires_at_is_absolute_ms(self):
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at_new", "expires_in": 3600}):
            wa.ensure_access_token(self.path, now_ms=NOW)
        self.assertEqual(wa.load(self.path)["expires_at"], NOW + HOUR_MS)

    def test_persist_failure_does_not_return_token(self):
        """If it cannot be saved, it must not be returned.

        Returning an access token that was not saved means the old refresh token
        was revoked by the server and the new one never stored — both lost.
        Better to fail this once.
        """
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at_new", "refresh_token": "rt_new",
                "expires_in": 3600}):
            with mock.patch.object(wa, "save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    wa.ensure_access_token(self.path, now_ms=NOW)

    def test_invalid_grant_raises_auth_expired(self):
        """The refresh token was revoked (you withdrew access in WHOOP, say)."""
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form",
                               side_effect=_http_error(400, b'{"error":"invalid_grant"}')):
            with self.assertRaises(wa.WhoopAuthExpired):
                wa.ensure_access_token(self.path, now_ms=NOW)

    def test_401_raises_auth_expired(self):
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", side_effect=_http_error(401)):
            with self.assertRaises(wa.WhoopAuthExpired):
                wa.ensure_access_token(self.path, now_ms=NOW)

    def test_server_error_is_not_auth_expired(self):
        """A WHOOP 500 is temporary; it must not push re-authorisation or stop the
        whole import."""
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.object(wa.net, "post_form", side_effect=_http_error(503)):
            with self.assertRaises(urllib.error.HTTPError):
                wa.ensure_access_token(self.path, now_ms=NOW)

    def test_missing_creds_is_config_error_not_auth_expired(self):
        wa.save(_tok(expires_at=NOW - 1), self.path)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as cm:
                wa.ensure_access_token(self.path, now_ms=NOW)
        self.assertNotIsInstance(cm.exception, wa.WhoopAuthExpired)


class AuthorizeUrlTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {
            "WHOOP_CLIENT_ID": "cid", "WHOOP_CLIENT_SECRET": "sec"})
        env.start()
        self.addCleanup(env.stop)

    def test_contains_required_params(self):
        import urllib.parse as up
        url = wa.authorize_url("http://localhost:8899/whoop/callback",
                               "state_long_enough")
        q = up.parse_qs(up.urlparse(url).query)
        self.assertEqual(q["client_id"], ["cid"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["state"], ["state_long_enough"])
        self.assertEqual(q["redirect_uri"], ["http://localhost:8899/whoop/callback"])
        self.assertIn("offline", q["scope"][0])

    def test_short_state_is_rejected_locally(self):
        """WHOOP requires state ≥ 8 characters, or it redirects with

        invalid_state (found 2026-08-15, undocumented). Catching it locally beats
        the person seeing an error_description after clicking through the browser.
        """
        with self.assertRaises(ValueError):
            wa.authorize_url("http://localhost:8899/whoop/callback", "short")

    def test_generated_state_is_long_enough(self):
        import secrets
        self.assertGreaterEqual(len(secrets.token_urlsafe(16)), 8)

    def test_exchange_code_stores_expires_at(self):
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at", "refresh_token": "rt", "expires_in": 3600}):
            tok = wa.exchange_code("thecode", "http://localhost:8899/whoop/callback")
        self.assertEqual(tok["refresh_token"], "rt")
        self.assertIn("expires_at", tok)

    def test_exchange_code_without_refresh_token_is_rejected(self):
        """No refresh token means the offline scope did not take — fail now, not
        an hour later when the access token expires and cannot be refreshed."""
        with mock.patch.object(wa.net, "post_form", return_value={
                "access_token": "at", "expires_in": 3600}):
            with self.assertRaises(RuntimeError):
                wa.exchange_code("thecode", "http://localhost:8899/whoop/callback")


if __name__ == "__main__":
    unittest.main()
