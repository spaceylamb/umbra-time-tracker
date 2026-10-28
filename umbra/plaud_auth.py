"""PLAUD token management.

The difference from whoop_auth: **no separate copy of the token is kept; it
reads and writes `~/.plaud/tokens.json` directly** — the file belonging to the
official `@plaud-ai/cli`.

Why share rather than each keep their own: a refresh response may carry a **new**
refresh_token (server-side rotation). With two independent holders refreshing,
whichever refreshes invalidates the other's copy — which presents as "worked for
a few days, then suddenly demanded re-authorisation", the most common production
failure in integrations of this shape. A single holder is the only arrangement
that cannot fight itself, at the cost of coupling to the CLI's file format (four
fields, and stable).

A side benefit is not having to implement OAuth at all: authorising and
re-authorising are both `plaud login` (a browser PKCE flow calling back to
localhost:8199). Umbra only handles renewal.

The refresh endpoint needs no client_id or secret; the body carries only
refresh_token — read out of the CLI's published package and confirmed by testing.
"""

import json
import os
import tempfile
import time
import urllib.error

from . import net

TOKEN_PATH = os.path.expanduser("~/.plaud/tokens.json")

REFRESH_URL = ("https://platform.plaud.ai/developer/api"
               "/oauth/third-party/access-token/refresh")

# How early to consider a token due for refresh. The access token measurably
# lives only about 24 hours, and with no margin a token with two seconds left
# passes the check and then expires in flight — showing up as random 401s. Same
# reasoning and same number as whoop_auth.
REFRESH_MARGIN_MS = 120_000

_UNSET = object()


class PlaudAuthExpired(RuntimeError):
    """The refresh token is dead and `plaud login` must be run again.

    A separate type from a network blip: this one hard-fails and aborts the whole
    import (the policy the user set on 2026-08-15, same as WHOOP) because no
    number of retries will help, while network errors keep the per-source fault
    isolation.
    """


def load(path: str = TOKEN_PATH) -> dict | None:
    """Read the token. A missing file returns None — before `plaud login` has
    ever been run, that is the normal state."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save(tok: dict, path: str = TOKEN_PATH) -> None:
    """Atomic write.

    Atomic is required: crashing mid-write leaves half a JSON file, at which
    point both the old and new tokens are gone and re-authorisation is the only
    way out — and it breaks the CLI along with it, because this is the same file.
    The temp file goes in the same directory: a rename across file systems is not
    atomic.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tok, f, ensure_ascii=False, indent=2)
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


def refresh(refresh_token: str) -> dict:
    """Trade a refresh token for a new access token."""
    try:
        return net.post_form(REFRESH_URL, {"refresh_token": refresh_token})
    except urllib.error.HTTPError as e:
        # 400/401/403 means this refresh token is dead and retrying cannot help.
        # Everything else (500/503/timeouts) is transient, re-raised as-is into
        # the per-source fault isolation.
        if e.code in (400, 401, 403):
            raise PlaudAuthExpired(
                f"PLAUD refresh token expired (HTTP {e.code}). "
                "Re-authorize: plaud login") from e
        raise


def _merge(old: dict, resp: dict, now_ms: int) -> dict:
    """Merge a refresh response back into the stored token.

    refresh_token is overwritten **only when present** rather than through a blind
    update: when the response omits the field, a blind update writes None and the
    next refresh is guaranteed to fail.
    """
    tok = dict(old)
    tok["access_token"] = resp["access_token"]
    tok["token_type"] = resp.get("token_type") or tok.get("token_type") or "Bearer"
    tok["expires_at"] = now_ms + int(resp.get("expires_in", 86400)) * 1000
    if resp.get("refresh_token"):
        tok["refresh_token"] = resp["refresh_token"]
    return tok


# The parameter names have to match the module functions (so call sites read
# naturally), so aliases are taken first — otherwise the body would reference the
# names shadowed by the parameters.
_refresh, _save = refresh, save


def ensure_access_token(path: str = TOKEN_PATH, now_ms: int | None = None,
                        tok=_UNSET, refresh=None, save=None,
                        force: bool = False) -> str:
    """Return a usable access token, refreshing and persisting when needed.

    tok / refresh / save are injectable so tests neither read files nor go out to
    the network.

    Persisting happens before returning — returning a freshly obtained token
    without storing it throws it away, and the next run refreshes with the old
    refresh token, which has already been invalidated.

    force=True skips the local expiry check and refreshes unconditionally. Used
    by authed_get_json after a 401 — at that point the server has already
    falsified the local expires_at.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if tok is _UNSET:
        tok = load(path)
    # isinstance rather than a truthiness test: when tokens.json has been
    # mangled into a list, the .get() below raises AttributeError, while what
    # belongs here is "go and re-authorise".
    if not isinstance(tok, dict) or not tok.get("refresh_token"):
        raise PlaudAuthExpired(
            f"no PLAUD token yet ({path}). Authorize first: "
            "npm i -g @plaud-ai/cli && plaud login")

    if (not force and tok.get("access_token")
            and tok.get("expires_at", 0) - REFRESH_MARGIN_MS > now):
        return tok["access_token"]

    new = _merge(tok, (refresh or _refresh)(tok["refresh_token"]), now)
    if save:
        save(new)
    else:
        _save(new, path)
    return new["access_token"]


# ── Authenticated requests ────────────────────────────────────────

def authed_get_json(url: str, extra: dict | None = None,
                    path: str = TOKEN_PATH, get=None, ensure=None) -> dict:
    """A GET with a Bearer token. **On 401 the server wins**: force one refresh
    and retry.

    The lesson of 2026-08-18: the local tokens.json said expiry was at 12:00, the
    server returned ACCESS_TOKEN_INVALID at 09:17, and a whole day of speech
    never arrived.

    `expires_at` is only what the server expected at issue time — a password
    change, a login elsewhere, or a background revocation all invalidate it early,
    and **we are not notified**. Using it as an admission test asks a cache to
    answer a question only the server can. Using it optimistically to save a
    request is fine; once refused, the server has to be believed.

    Without this retry path the only trigger for a refresh is "the local clock
    said so", so early server-side invalidation can never be caught up with and
    every re-run is another 401.

    The retry happens **once**. A second 401 means the refresh token is dead too,
    at which point PlaudAuthExpired lets cmd_import's AUTH_EXPIRED hard-abort —
    otherwise 900 file lookups would hit the refresh endpoint 900 times, rotating
    the refresh token on each one.

    Only 401 counts. 403 is insufficient permission, 429 is rate limiting, 5xx is
    a server fault, and treating any of them as expired authorisation sends
    someone to run `plaud login` for nothing while hiding the real cause.
    """
    get = get or net.get_json
    ensure = ensure or ensure_access_token
    head = lambda t: {**(extra or {}), "Authorization": f"Bearer {t}"}  # noqa: E731
    try:
        return get(url, head(ensure(path=path)))
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
    try:
        return get(url, head(ensure(path=path, force=True)))
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        raise PlaudAuthExpired(
            "PLAUD rejected a freshly refreshed access token (HTTP 401) — "
            "the refresh token has also expired. Re-authorize: plaud login") from e
