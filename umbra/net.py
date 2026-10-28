"""HTTP helpers.

The one reason this module exists: a python.org framework build that never ran
Install Certificates.command has an empty CA store, so **every** https request
fails with CERTIFICATE_VERIFY_FAILED. That trap was hit twice in this project
(once on a Gemini call, once on the Butler API), so it is solved in exactly one
place — anything that goes to the network comes through here rather than
calling create_default_context on its own.
"""

import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

# urllib sends `Python-urllib/3.13` by default, and the Cloudflare in front of
# WHOOP blocks that signature outright: 403 with `error code: 1010`. The
# request never reaches the application, so everything the error says about
# OAuth, signatures and authorization codes is misdirection — easily an hour
# of looking in the wrong place.
#
# 2026-08-15, same request measured both ways: default UA → 403, any UA → 400
# invalid_grant. It lives here rather than at each call site so the next source
# does not rediscover it.
USER_AGENT = "Umbra/1.0 (personal timeline importer)"

_ctx: ssl.SSLContext | None = None


def _headers(extra: dict | None = None) -> dict:
    return {"User-Agent": USER_AGENT, **(extra or {})}


def ssl_context() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            # Order matters: try the system default first (Linux always has
            # one, and it is the correct one), and only fall back to macOS's
            # /etc/ssl/cert.pem when that comes up empty — the python.org mac
            # build ships an empty CA store. Pointing at cert.pem
            # unconditionally raises FileNotFoundError on Ubuntu, which is
            # what the first CI run actually did.
            _ctx = ssl.create_default_context()
            if not _ctx.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
                _ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return _ctx


# How long counts as "wait a moment" before failing loudly instead.
# A minute-scale window is worth sleeping through; if Reset reports a day-scale
# window (WHOOP allows 10000/day, so Reset can be tens of thousands of
# seconds), sleeping means hanging the import for a whole day.
MAX_RETRY_SLEEP = 120


def _retry_delay(err: urllib.error.HTTPError, attempt: int) -> float:
    """How long to wait after a 429. Zero means stop waiting and raise."""
    for h in ("Retry-After", "X-RateLimit-Reset"):
        raw = err.headers.get(h) if err.headers else None
        if not raw:
            continue
        try:
            secs = float(str(raw).strip())
        except ValueError:
            continue                      # Retry-After may also be an HTTP date
        if secs > MAX_RETRY_SLEEP:
            return 0
        # Two seconds of slack: landing exactly on the window boundary earns
        # another 429.
        return secs + 2
    return min(2 ** attempt, MAX_RETRY_SLEEP)     # no header, so back off


# Retrying a dropped connection. Not the same thing as a 429: a 429 is the server
# asking you to wait, this is a packet that never arrived.
#
# On a flaky uplink, TLS handshakes fail intermittently with
# `SSL: UNEXPECTED_EOF_WHILE_READING` — most handshakes to one host fail for a
# few minutes, then all of them succeed. Retrying only 429 meant one EOF threw
# away a whole PLAUD import (dozens of requests), a sink dispatch, or a round of
# model calls.
#
# 1 + 2 + 4 seconds (7 in total) was measured too short: an outage often runs
# past half a minute, and one of a PLAUD round's dozens of requests always hits
# it. Now 2 / 4 / 8 / 16 / 32 seconds, 62 in total.
NET_TRIES = 6          # the first attempt plus five retries
NET_BACKOFF_S = 2.0    # 2s … 32s — at most 62 seconds of extra waiting

# A whole retry chain failing means the line is down, not flaky (no network at
# all). Then every request would wait another minute, a round of dozens of
# requests would drag on for tens of minutes and crowd out the next one. So it
# is noted, and later requests in the same process try once; any success clears
# it and the long retry comes back.
_line_down = False


def _transient(e: BaseException, idempotent: bool) -> bool:
    """Is this "the line hiccuped, once more may work"?

    urlopen wraps an OSError in URLError only in the **connect / handshake /
    send** phase; an error while reading the response is raised as is. So
    "URLError but not HTTPError" ≈ the request never reached the server, and
    resending even a POST cannot run it twice. A drop while reading is
    different — the server may already have acted on it (a second model charge,
    a second card), so only a GET may try again.
    """
    if isinstance(e, urllib.error.HTTPError):
        return False                       # the server's answer, not the line
    if isinstance(e, urllib.error.URLError):
        return True
    return idempotent and isinstance(
        e, (ConnectionError, TimeoutError, http.client.HTTPException))


def _retrying(fn, idempotent: bool):
    global _line_down
    tries = 1 if _line_down else NET_TRIES
    for attempt in range(tries):
        try:
            out = fn()
            _line_down = False
            return out
        except Exception as e:             # noqa: BLE001
            if isinstance(e, urllib.error.HTTPError):
                _line_down = False         # the server answered; the line is up
            if not _transient(e, idempotent):
                raise
            if attempt == tries - 1:
                _line_down = True
                raise
            time.sleep(NET_BACKOFF_S * 2 ** attempt)
    raise AssertionError("unreachable")


def _read_json(req, timeout):
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return json.loads(r.read())


def _read_raw(req, timeout):
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return r.read()


def get_json(url: str, headers: dict | None = None, timeout: int = 60,
             retries: int = 4, sleep=time.sleep) -> dict:
    """GET JSON, retrying a 429 according to the window the server reports.

    Only 429 is retried: 401/403/500 return the same answer however many times
    they are asked, so retrying them only delays the error. sleep is injectable
    so tests do not actually sleep.
    """
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=_headers(headers))
        try:
            return _retrying(lambda: _read_json(req, timeout), idempotent=True)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == retries:
                raise
            delay = _retry_delay(e, attempt)
            if not delay:
                raise
            sleep(delay)
    raise AssertionError("unreachable")


def get_text(url: str, headers: dict | None = None, timeout: int = 60) -> str:
    """GET plain text. For presigned URLs — PLAUD keeps large transcripts on S3.

    Gzip has to be handled here: these links often carry
    Content-Encoding: gzip and urllib does **not** decompress automatically
    (requests does, so anyone reasoning from requests experience gets a screen
    of binary). Detected by magic number rather than by trusting the header,
    because some S3 objects are gzip content with no header saying so.
    """
    req = urllib.request.Request(url, headers=_headers(headers))
    raw = _retrying(lambda: _read_raw(req, timeout), idempotent=True)
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def post_form(url: str, body: dict, headers: dict | None = None,
              timeout: int = 60) -> dict:
    """Form-encoded POST.

    OAuth token endpoints take application/x-www-form-urlencoded per RFC 6749
    and reject JSON, so post_json cannot be used for them.
    """
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(body).encode(),
        headers=_headers({"Content-Type": "application/x-www-form-urlencoded",
                          **(headers or {})}))
    return _retrying(lambda: _read_json(req, timeout), idempotent=False)


def post_json(url: str, body: dict, headers: dict | None = None,
              timeout: int = 180) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers=_headers({"Content-Type": "application/json",
                          **(headers or {})}))
    return _retrying(lambda: _read_json(req, timeout), idempotent=False)
