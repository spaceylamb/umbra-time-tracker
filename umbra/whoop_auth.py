"""WHOOP OAuth 2.0 token management.

Storage, decided 2026-08-15: a JSON file next to the database, chmod 600.

Why not the env file: a refresh token is **state the program rewrites**, not
configuration. The env file is hand-maintained, often sourced by a shell rc too
— letting the program edit it means the next manual edit collides, and every
new terminal exports a value that has already been invalidated.

Why not the Keychain: once Umbra runs resident under launchd, non-interactive
access to the login keychain raises an "allow access" dialog, and there is nobody
in a daemon environment to click it. This machine has already been bitten once by
exactly this class of problem with TCC (see `umbra doctor`).

Why not inside events.db: that table is an append-only point-event store which
deliberately holds no mutable state, so that changing the segmentation algorithm
needs no data migration. Putting a token in it breaks precisely that property.

The trade-off of plaintext on disk: the client secret is already plaintext in
the env file, so this introduces no new class of exposure, and FileVault covers
encryption at rest.
"""

import json
import os
import tempfile
import time
import urllib.error
import urllib.parse

from . import net, paths

TOKEN_PATH = os.path.join(paths.data_dir(), "whoop_token.json")

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"

# offline is not one of the Dashboard checkboxes — it is a scope supplied at
# request time, and it has to be supplied again on refresh or the response may
# stop returning a refresh_token.
SCOPES = [
    "read:recovery", "read:cycles", "read:sleep",
    "read:workout", "read:profile", "read:body_measurement",
    "offline",
]

# How early to consider a token due for refresh. With no margin, a token with
# two seconds left passes the check and then expires in flight, which shows up as
# random 401s.
REFRESH_MARGIN_MS = 120_000


class WhoopAuthExpired(RuntimeError):
    """The refresh token is dead and re-authorisation is required.

    A distinct type because it is handled the opposite way from a network blip:
    this one hard-fails and aborts the whole import (the user's choice), while
    network errors keep the per-source fault isolation.
    """


def _creds() -> tuple[str, str]:
    cid = os.environ.get("WHOOP_CLIENT_ID")
    sec = os.environ.get("WHOOP_CLIENT_SECRET")
    if not cid or not sec:
        raise RuntimeError(
            "WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET are not set. They live in "
            "your env file; umbra loads it itself, or export them by hand.")
    return cid, sec


def load(path: str = TOKEN_PATH) -> dict | None:
    """Read the token. A missing file returns None rather than raising — before
    the first authorisation that is the normal state."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save(tok: dict, path: str = TOKEN_PATH) -> None:
    """Write the token atomically, mode 600.

    Atomic is required: crashing halfway through a refresh write leaves half a
    JSON file, at which point both the old and the new token are gone and
    re-authorisation is the only way out. Write a temp file then os.replace();
    rename is atomic on POSIX.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # The temp file must sit in the destination directory: a rename across file
    # systems is not atomic, and /tmp and the data directory may well be on
    # different volumes.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tok, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())     # losing power before metadata is flushed
                                     # yields an empty file
        os.chmod(tmp, 0o600)         # permissions before rename, so there is no
                                     # instant where it is world-readable
        os.replace(tmp, path)
    except BaseException:
        # On failure do not leave the temp file behind, or they accumulate
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def authorize_url(redirect_uri: str, state: str) -> str:
    """Build the authorisation URL (used by the one-off auth script)."""
    # WHOOP requires state to be at least 8 characters; shorter and it 302s back
    # with invalid_state (found by testing on 2026-08-15; undocumented). Caught
    # locally, which beats the user finding out from an error_description after
    # clicking through the browser.
    if len(state) < 8:
        raise ValueError(f"state must be at least 8 chars, got {len(state)}")
    cid, _ = _creds()
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
    })


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange the authorisation code for a token (one-off auth script)."""
    cid, sec = _creds()
    r = net.post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": cid,
        "client_secret": sec,
    })
    if not r.get("refresh_token"):
        # Fail now rather than discovering an hour later, when the access token
        # expires, that it can never be refreshed.
        raise RuntimeError(
            "WHOOP returned no refresh_token — the offline scope did not take "
            f"effect. Fields actually returned: {sorted(r)}")
    return _merge(None, r, int(time.time() * 1000))


def refresh(refresh_token: str) -> dict:
    """Trade a refresh token for a new access token.

    offline has to be supplied in scope again: OAuth permits narrowing scope on
    refresh, and without it WHOOP concludes you no longer need a refresh token
    and may stop returning a new one.
    """
    cid, sec = _creds()
    try:
        return net.post_form(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": cid,
            "client_secret": sec,
            "scope": "offline",
        })
    except urllib.error.HTTPError as e:
        # 400/401 means this refresh token is dead; retrying cannot help and
        # re-authorisation is required. Everything else (500/503/timeouts) is a
        # transient fault, re-raised as-is into the per-source fault isolation.
        if e.code in (400, 401):
            raise WhoopAuthExpired(
                f"WHOOP refresh token expired (HTTP {e.code}). "
                "Re-authorize: umbra auth whoop") from e
        raise


def _merge(old: dict | None, resp: dict, now_ms: int) -> dict:
    """Merge a refresh response back into the stored token.

    The crux is that refresh_token is overwritten **only when present** rather
    than through a blind update: when the response omits the field, a blind
    update writes None and the next refresh is guaranteed to fail — which
    presents as "worked for a few days, then suddenly demanded
    re-authorisation", the most common production failure in integrations of this
    shape.
    """
    tok = dict(old or {})
    tok["access_token"] = resp["access_token"]
    tok["expires_at"] = now_ms + int(resp.get("expires_in", 3600)) * 1000
    if resp.get("refresh_token"):
        tok["refresh_token"] = resp["refresh_token"]
    if resp.get("scope"):
        tok["scope"] = resp["scope"]
    return tok


def ensure_access_token(path: str = TOKEN_PATH, now_ms: int | None = None) -> str:
    """Return a usable access token, refreshing and persisting when needed.

    Persisting happens before returning — returning a freshly obtained token
    without storing it throws it away, and the next run refreshes using the old
    refresh token, which has already been invalidated.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    tok = load(path)
    if not tok or not tok.get("refresh_token"):
        raise WhoopAuthExpired(
            f"no WHOOP token yet ({path}). Authorize first: umbra auth whoop")

    if tok.get("access_token") and tok.get("expires_at", 0) - REFRESH_MARGIN_MS > now:
        return tok["access_token"]

    new = _merge(tok, refresh(tok["refresh_token"]), now)
    save(new, path)      # before the return: unusable if it was not persisted
    return new["access_token"]
