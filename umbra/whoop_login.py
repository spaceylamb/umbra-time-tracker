"""One-time WHOOP authorisation. Run it once; imports refresh the token after.

    umbra auth whoop

By default a local server on 127.0.0.1:8899 lives for a few minutes to receive
the OAuth callback. If your WHOOP app has no localhost redirect registered, use
the one it has:

    umbra auth whoop --redirect https://your.domain/whoop/callback

In that mode the browser lands on a 404 page — that is expected: the domain is
only a landing spot that does not redirect, chosen because it has a valid
certificate. Paste the whole URL from the address bar back in.

Inside the package rather than in scripts/: a script beside the repo does not
exist for anyone who installed with uv or pipx, and this is the first thing a
WHOOP owner has to run.
"""

import argparse
import http.server
import secrets
import threading
import urllib.parse

from . import whoop_auth

DEFAULT_REDIRECT = "http://localhost:8899/whoop/callback"


class _Handler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Handler.result = {k: v[0] for k, v in q.items()}
        ok = "code" in _Handler.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            ("<h2>✅ Authorised — back to the terminal</h2>" if ok else
             f"<h2>❌ Authorisation failed</h2><pre>{_Handler.result}</pre>").encode())

    def log_message(self, format, *args):                   # noqa: A002
        pass          # never print the callback URL (it carries the code) to the terminal


def make_server(port: int) -> http.server.HTTPServer:
    """Bind the port first, then build the authorisation URL.

    Two steps, and not only for testing: if the port is taken it must fail
    **before** the person is sent to the browser, or they come back authorised to
    find nobody listening and the code already spent.
    """
    _Handler.result = {}
    return http.server.HTTPServer(("127.0.0.1", port), _Handler)


def wait_for_callback(srv: http.server.HTTPServer, timeout: int = 300) -> dict:
    """Receive one callback and close. The socket is already listening;
    connections queue in the backlog and are not lost."""
    srv.timeout = timeout
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    t.join(timeout)
    srv.server_close()
    return _Handler.result


def finish(params: dict, state: str, redirect_uri: str) -> dict:
    """Check the callback parameters and exchange them for a token."""
    if params.get("error"):
        raise SystemExit(f"WHOOP refused authorisation: {params['error']} "
                         f"{params.get('error_description', '')}")
    if not params.get("code"):
        raise SystemExit(f"no code in the callback: {params}")
    # Without checking state, someone could hand you their authorisation code and
    # have your data written into their account.
    if params.get("state") != state:
        raise SystemExit("state does not match — possibly CSRF; refusing to continue.")

    tok = whoop_auth.exchange_code(params["code"], redirect_uri)
    whoop_auth.save(tok)
    return tok


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="umbra auth whoop")
    ap.add_argument("--redirect", default=DEFAULT_REDIRECT,
                    help="must match the one registered in the WHOOP dashboard exactly")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args(argv)

    state = secrets.token_urlsafe(16)
    local = urllib.parse.urlparse(args.redirect).hostname in ("localhost",
                                                              "127.0.0.1")
    # Take the port before sending the person to the browser — otherwise they
    # come back authorised to find nobody listening.
    srv = None
    if local:
        try:
            srv = make_server(args.port)
        except OSError as e:
            raise SystemExit(f"cannot start on port {args.port} ({e}). "
                             f"See who has it: lsof -nP -iTCP:{args.port}")

    url = whoop_auth.authorize_url(args.redirect, state)
    print("\nOpen this address in a browser to authorise:\n")
    print(f"  {url}\n")

    if srv is not None:
        print(f"(waiting for the callback on 127.0.0.1:{args.port}; 5-minute timeout)")
        try:
            import subprocess
            subprocess.run(["open", url], check=False, timeout=10)
        except Exception:                                   # noqa: BLE001
            pass
        params = wait_for_callback(srv)
        if not params:
            raise SystemExit("timed out without a callback.")
    else:
        print("After authorising the browser lands on a 404 page — that is expected.")
        got = input("\nPaste the full URL from the address bar here:\n> ").strip()
        params = {k: v[0] for k, v in
                  urllib.parse.parse_qs(urllib.parse.urlparse(got).query).items()}

    tok = finish(params, state, args.redirect)
    import datetime as dt
    exp = dt.datetime.fromtimestamp(tok["expires_at"] / 1000)
    print(f"\n✅ Token saved to {whoop_auth.TOKEN_PATH}")
    print(f"   access token valid until {exp:%Y-%m-%d %H:%M}")
    print(f"   scope: {tok.get('scope', '?')}")
    print("\nNow run: umbra import --only whoop")


if __name__ == "__main__":
    main()
