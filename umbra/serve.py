"""`umbra serve`: the API and the built frontend, on localhost.

ADR 0001: the browser talks HTTP to Python and no JavaScript process ever
touches the database. This is stdlib only — ThreadingHTTPServer, one SQLite
connection per request (a connection cannot cross threads).

It binds 127.0.0.1 unless told otherwise. What it serves is the same raw
history `umbra viz` refuses to put in a repo — browsing, clipboard, agent
transcripts — so listening on the network is something a person has to ask for
by name, not something a default does.
"""

import gzip
import json
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import api, db

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PORT = 5170

_WEEKP = re.compile(r"^/api/week/([^/]+)(/goals)?$")
_DAY = re.compile(r"^/api/day/([^/]+)(/detail|/raw|/said)?$")
_CORRECT = re.compile(r"^/api/day/([^/]+)/correct$")
_SAID = re.compile(r"^/api/said/([a-z_]+)/(\d+)$")

# Nothing the page is built from belongs in a shared cache: it is one person's
# history, and a stale day is worse than a slow one.
_NO_STORE = "no-store"

_NOT_BUILT = """<!doctype html><meta charset=utf-8><title>Umbra</title>
<body style="font:14px system-ui;margin:40px;max-width:560px;line-height:1.5">
<h1 style="font-size:18px">The web page has not been built</h1>
<p>The API is running at <code>/api/meta</code>. Build the page with
<code>bun run --cwd web build</code>, or run <code>bun run --cwd web dev</code>
and open the address it prints.</p>"""


def _route(path: str, con):
    if path == "/api/meta":
        return api.meta(con)
    if path == "/api/setup":
        return api.setup()
    if path == "/api/import":
        return api.import_progress(con)
    if path == "/api/config":
        return api.config_get()
    if path == "/api/settings":
        from . import settings
        return settings.get()
    w = _WEEKP.match(path)
    if w and not w.group(2):
        return api.week(w.group(1), con)
    m = _DAY.match(path)
    if m:
        date, part = m.group(1), m.group(2)
        if part == "/detail":
            return api.detail(date, con)
        if part == "/raw":
            return api.raw(date, con)
        if part == "/said":
            return api.said(date, con)
        return api.day(date, con)
    raise api.NotFound(f"no route: {path}")


class Handler(BaseHTTPRequestHandler):
    api_only = False
    server_version = "umbra"

    def log_message(self, format, *args):  # noqa: A002 — the base class's name
        pass                                  # quiet: errors print themselves

    def _send(self, status: int, body: bytes, ctype: str, cache: str = _NO_STORE):
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 6)
            self.send_response(status)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False,
                                      separators=(",", ":")).encode(),
                   "application/json; charset=utf-8")

    def do_POST(self):
        """The writes the page can make: start an import, change a setting,
        set a week's priorities, run one of settings.ACTIONS.

        This server listens on localhost, and any web page the person has open
        can send a request to localhost. A plain form POST cannot set custom
        headers or a JSON content type without a CORS preflight — which this
        server never answers — so requiring both, plus a same-host Origin,
        keeps other sites from starting jobs here."""
        path = urlparse(self.path).path
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if (self.headers.get("X-Umbra") != "1"
                or "application/json" not in (self.headers.get("Content-Type") or "")
                or (origin and urlparse(origin).netloc != host)):
            self._json(403, {"error": "refused: not from this page"})
            return
        if path == "/api/action":
            from . import settings
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                self._json(200, settings.act(str(body.get("name"))))
            except KeyError as e:
                self._json(404, {"error": str(e).strip("'")})
            except (OSError, ValueError, AttributeError) as e:
                self._json(400, {"error": str(e)})
            return
        w = _WEEKP.match(path)
        if w and w.group(2):
            con = db.connect()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                self._json(200, api.set_goals(w.group(1), body.get("goals"), con))
            except api.NotFound as e:
                self._json(404, {"error": str(e)})
            except (ValueError, AttributeError) as e:
                self._json(400, {"error": str(e)})
            finally:
                con.close()
            return
        c = _CORRECT.match(path)
        if c:
            con = db.connect()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected an object")
                self._json(200, api.correct(c.group(1), body, con))
            except api.NotFound as e:
                self._json(404, {"error": str(e)})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            finally:
                con.close()
            return
        s = _SAID.match(path)
        if s:
            con = db.connect()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected an object")
                out = api.said_mark(s.group(1), int(s.group(2)), body, con)
                self._json(200, out)
            except api.NotFound as e:
                self._json(404, {"error": str(e)})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            finally:
                con.close()
            return
        if path == "/api/config":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected an object of settings")
                self._json(200, api.config_set(body))
            except ValueError as e:              # configedit.Refused included
                self._json(400, {"error": str(e)})
            return
        if path != "/api/import":
            self._json(404, {"error": f"no route: {path}"})
            return
        con = db.connect()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
            only = body.get("only") if isinstance(body, dict) else None
            self._json(202, api.start_import(con, only=only if isinstance(only, str) else None))
        except api.NotFound as e:
            self._json(404, {"error": str(e)})
        except Exception as e:                       # noqa: BLE001
            self._json(500, {"error": f"{type(e).__name__}: {e}"})
        finally:
            con.close()

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            con = db.connect()
            try:
                self._json(200, _route(path, con))
            except api.NotFound as e:
                self._json(404, {"error": str(e)})
            except Exception as e:                   # noqa: BLE001
                print(f"  ✗ {path}: {type(e).__name__}: {e}")
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
            finally:
                con.close()
            return
        if self.api_only:
            self._json(404, {"error": "api-only: the page is served by the dev server"})
            return
        self._static(path)

    def _static(self, path: str):
        index = os.path.join(WEB, "index.html")
        if not os.path.exists(index):
            self._send(200, _NOT_BUILT.encode(), "text/html; charset=utf-8")
            return
        # The build uses relative asset paths (so the export works from any
        # directory); from a deep link like /day/2026-09-08 the browser asks
        # for /day/assets/x.js. Whatever precedes /assets/ is the route.
        if "/assets/" in path:
            path = "/assets/" + path.rsplit("/assets/", 1)[1]
        rel = os.path.normpath(path.lstrip("/")) if path != "/" else "index.html"
        full = os.path.join(WEB, rel)
        # Stay inside WEB; anything else, and every client-side route, gets the
        # app shell — the router decides what /day/2026-09-08 means.
        if (rel.startswith("..") or not os.path.isfile(full)):
            full = index
        with open(full, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        # Hashed asset names can be cached forever; the shell never.
        cache = ("public, max-age=31536000, immutable"
                 if "/assets/" in full.replace(os.sep, "/") else _NO_STORE)
        self._send(200, body, ctype, cache)


def serve(host: str = "127.0.0.1", port: int = PORT, open_browser: bool = True,
          api_only: bool = False) -> None:
    Handler.api_only = api_only
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"umbra serve  {url}" + ("  (API only)" if api_only else ""))
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("⚠️  listening beyond this machine — anyone who can reach it "
              "can read your full history")
    if open_browser and not api_only:
        import webbrowser
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
