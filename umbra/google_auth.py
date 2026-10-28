"""Google service account auth — signing a JWT with openssl.

**Why google-auth is not installed.** Umbra has no third-party packages at all:
clone it and it runs, and the fixed interpreter under launchd needs no
maintenance. The official library would pull in a dozen transitive dependencies
to do one thing — an RS256 signature. The standard library genuinely has no RSA,
but macOS ships openssl.

The reasoning is not fastidiousness, it is **failure modes**: this is an
unattended scheduled job, and every added dependency is one more thing that can
fail silently at three in the morning and be discovered three weeks later. This
machine has already been bitten once, when conda took over the interpreter
launchd was using.

The flow is Google's standard JWT-bearer flow:

    {header}.{claims}  --openssl dgst -sha256 -sign-->  signature
    the three joined   --POST oauth2.googleapis.com-->  a 1-hour access token

The token is cached next to the database under the same rules as
whoop_token.json (mode 600, atomic write, refresh early) — reasoning in the
whoop_auth.py docstring.

Environment: GOOGLE_APPLICATION_CREDENTIALS points at the service account json.
"""

import base64
import json
import os
import subprocess
import tempfile
import time

from . import db, net

CREDS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
TOKEN_PATH = os.path.join(db.DB_DIR, "google_token.json")

TOKEN_URL = "https://oauth2.googleapis.com/token"
GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# Places API (New) sits on the standard Cloud auth stack and wants exactly this
# generic scope. (The older Geocoding API lives on maps.googleapis.com, accepts
# only an API key, and rejects a service account with REQUEST_DENIED — which is
# why Umbra uses Places only.)
SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Google rejects an assertion valid for more than an hour.
ASSERTION_TTL_S = 3600

# How early to consider a token due for replacement. With no margin, a token
# with two seconds left passes the check and then expires in flight, which shows
# up as random 401s.
REFRESH_MARGIN_MS = 120_000


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(payload: bytes, pem: str) -> bytes:
    """RS256 signature.

    openssl's -sign only accepts a **file path**, so the private key has to touch
    disk once. mkstemp rather than open(path, "w"): the former creates with 0600,
    the latter depends on umask — and on a machine with a loose umask that means
    other users can read it. Deleted immediately afterwards.
    """
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(pem)
        r = subprocess.run(["openssl", "dgst", "-sha256", "-sign", path],
                           input=payload, capture_output=True, check=True)
        return r.stdout
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def assertion(sa: dict, now_s: int | None = None) -> str:
    """Assemble the JWT Google expects."""
    now = int(now_s if now_s is not None else time.time())
    head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"},
                              separators=(",", ":")).encode())
    claims = _b64url(json.dumps({
        "iss": sa["client_email"],
        "scope": SCOPE,
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + ASSERTION_TTL_S,
    }, separators=(",", ":")).encode())
    signing_input = f"{head}.{claims}".encode("ascii")
    return f"{head}.{claims}.{_b64url(sign(signing_input, sa['private_key']))}"


def credentials(path: str | None = None) -> dict | None:
    """Read the service account json. None when it cannot be read.

    Unconfigured means "Google is not connected yet", which is a normal state,
    not a fault — it must not abort the import. A user-account json
    (type=authorized_user) looks very similar but has no private key and cannot
    sign, so it is rejected here rather than surfacing later as an
    incomprehensible openssl error.
    """
    p = path or os.environ.get(CREDS_ENV)
    if not p:
        return None
    try:
        with open(os.path.expanduser(p), encoding="utf-8") as f:
            sa = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(sa, dict) or not sa.get("private_key") \
            or not sa.get("client_email"):
        return None
    return sa


def load(path: str = TOKEN_PATH) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            tok = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return tok if isinstance(tok, dict) else None


def save(tok: dict, path: str = TOKEN_PATH) -> None:
    """Atomic write, mode 600."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tok, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _fetch(sa: dict, now_s: int) -> dict:
    return net.post_form(TOKEN_URL, {"grant_type": GRANT,
                                     "assertion": assertion(sa, now_s)})


def ensure_access_token(path: str = TOKEN_PATH, now_ms: int | None = None,
                        sa: dict | None = None) -> str | None:
    """An access token with some life left in it. None when no credentials are
    configured."""
    sa = sa if sa is not None else credentials()
    if sa is None:
        return None
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    tok = load(path)
    if tok and tok.get("access_token") \
            and (tok.get("expires_at_ms") or 0) - REFRESH_MARGIN_MS > now:
        return tok["access_token"]
    resp = _fetch(sa, now // 1000)
    fresh = {"access_token": resp["access_token"],
             "expires_at_ms": now + int(resp.get("expires_in") or 3600) * 1000}
    save(fresh, path)
    return fresh["access_token"]
