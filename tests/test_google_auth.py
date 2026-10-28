"""Google service-account auth — signing the JWT with openssl, no third-party
package.

Umbra has no pip dependency at all: clone it and run it, and the fixed
interpreter in launchd needs nothing maintained. The official google-auth
brings a dozen transitive dependencies to do one thing — RS256 signing — and
macOS ships openssl.

Not fastidiousness, but a failure mode: a scheduled job runs unattended, and
every dependency is one more thing that can die silently at 3am and be noticed
three weeks later. It has happened once (conda taking over the interpreter).
"""

import base64
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from umbra import google_auth

NOW = 1786900000


def _keypair():
    """Really generate an RSA key pair. Signing cannot be verified with a mock."""
    d = tempfile.mkdtemp()
    priv, pub = os.path.join(d, "k.pem"), os.path.join(d, "k.pub")
    subprocess.run(["openssl", "genrsa", "-out", priv, "2048"],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "rsa", "-in", priv, "-pubout", "-out", pub],
                   check=True, capture_output=True)
    with open(priv, encoding="utf-8") as f:
        return f.read(), pub


def _sa(pem):
    return {"type": "service_account", "project_id": "proj-1",
            "client_email": "bot@proj-1.iam.gserviceaccount.com",
            "private_key": pem, "private_key_id": "kid-1"}


def _unb64(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


class AssertionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pem, cls.pub = _keypair()
        cls.tok = google_auth.assertion(_sa(cls.pem), NOW)

    def test_it_has_three_segments(self):
        self.assertEqual(len(self.tok.split(".")), 3)

    def test_the_header_declares_rs256(self):
        h = json.loads(_unb64(self.tok.split(".")[0]))
        self.assertEqual(h["alg"], "RS256")
        self.assertEqual(h["typ"], "JWT")

    def test_the_claims_name_the_service_account(self):
        c = json.loads(_unb64(self.tok.split(".")[1]))
        self.assertEqual(c["iss"], "bot@proj-1.iam.gserviceaccount.com")
        self.assertEqual(c["aud"], google_auth.TOKEN_URL)
        self.assertEqual(c["scope"], google_auth.SCOPE)
        self.assertEqual(c["iat"], NOW)
        self.assertGreater(c["exp"], NOW)

    def test_it_expires_within_an_hour(self):
        """Google refuses assertions valid for longer than an hour."""
        c = json.loads(_unb64(self.tok.split(".")[1]))
        self.assertLessEqual(c["exp"] - c["iat"], 3600)

    def test_the_signature_actually_verifies(self):
        """The crux. Whether a signature is right is known only by verifying one."""
        head, claims, sig = self.tok.split(".")
        with tempfile.TemporaryDirectory() as d:
            sigf = os.path.join(d, "sig")
            with open(sigf, "wb") as f:
                f.write(_unb64(sig))
            r = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", self.pub,
                 "-signature", sigf],
                input=f"{head}.{claims}".encode(), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_a_tampered_payload_fails_verification(self):
        """Make sure the one above is not vacuous — one changed byte must fail."""
        head, claims, sig = self.tok.split(".")
        with tempfile.TemporaryDirectory() as d:
            sigf = os.path.join(d, "sig")
            with open(sigf, "wb") as f:
                f.write(_unb64(sig))
            r = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", self.pub,
                 "-signature", sigf],
                input=f"{head}.{claims}x".encode(), capture_output=True)
        self.assertNotEqual(r.returncode, 0)


class PrivateKeyHygieneTest(unittest.TestCase):
    """The private key has to reach openssl through the file system. Then leave
    no trace."""

    def test_no_key_file_survives_signing(self):
        pem, _ = _keypair()
        before = set(os.listdir(tempfile.gettempdir()))
        google_auth.sign(b"payload", pem)
        leaked = set(os.listdir(tempfile.gettempdir())) - before
        self.assertEqual(leaked, set(), f"files left behind after signing: {leaked}")

    def test_the_key_file_is_owner_only_while_it_exists(self):
        pem, _ = _keypair()
        seen = []
        real = os.chmod

        def spy(path, mode, *a, **kw):
            seen.append(mode)
            return real(path, mode, *a, **kw)

        with mock.patch.object(google_auth.tempfile, "mkstemp",
                               side_effect=google_auth.tempfile.mkstemp) as mk:
            google_auth.sign(b"payload", pem)
        # mkstemp creates 0600 by itself; what is pinned is that it must be used,
        # not open(path, 'w'), which goes through the umask
        self.assertTrue(mk.called, "the private key was not written through mkstemp")


class CredentialsTest(unittest.TestCase):
    def test_no_credentials_is_not_a_crash(self):
        """Not configured is "Google not set up yet", not a fault; it must not stop
        the import."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(google_auth.credentials())

    def test_a_missing_file_is_not_a_crash(self):
        with mock.patch.dict(os.environ,
                             {google_auth.CREDS_ENV: "/nope/nothing.json"}):
            self.assertIsNone(google_auth.credentials())

    def test_a_corrupt_file_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{not json")
            with mock.patch.dict(os.environ, {google_auth.CREDS_ENV: p}):
                self.assertIsNone(google_auth.credentials())

    def test_a_file_without_a_private_key_is_rejected(self):
        """A user account's JSON looks much the same but cannot sign — say so early."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "user.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"type": "authorized_user", "client_id": "x"}, f)
            with mock.patch.dict(os.environ, {google_auth.CREDS_ENV: p}):
                self.assertIsNone(google_auth.credentials())


class TokenCacheTest(unittest.TestCase):
    """An hour-long token must not be exchanged on every call — that is free quota
    and latency given away."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "google_token.json")

    def test_a_fresh_token_is_reused(self):
        google_auth.save({"access_token": "abc",
                          "expires_at_ms": (NOW + 3000) * 1000}, self.path)
        with mock.patch.object(google_auth, "_fetch") as fetch:
            got = google_auth.ensure_access_token(
                path=self.path, now_ms=NOW * 1000, sa={"x": 1})
        self.assertEqual(got, "abc")
        self.assertFalse(fetch.called, "exchanged a new one before expiry")

    def test_an_expiring_token_is_refreshed(self):
        """Two seconds left counts as expired: passing the check and expiring on
        the way = a random 401."""
        google_auth.save({"access_token": "old",
                          "expires_at_ms": (NOW + 2) * 1000}, self.path)
        with mock.patch.object(google_auth, "_fetch",
                               return_value={"access_token": "new",
                                             "expires_in": 3600}) as fetch:
            got = google_auth.ensure_access_token(
                path=self.path, now_ms=NOW * 1000, sa={"x": 1})
        self.assertEqual(got, "new")
        self.assertTrue(fetch.called)

    def test_the_token_file_is_owner_only(self):
        google_auth.save({"access_token": "abc", "expires_at_ms": 1}, self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_a_corrupt_cache_is_not_a_crash(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertIsNone(google_auth.load(self.path))


if __name__ == "__main__":
    unittest.main()
