"""umbra serve over real HTTP: routes, status codes, the static fallback.

The payload shapes are test_api_contract's job; this is only about the server
putting them on the wire correctly and refusing what it should refuse.
"""

import gzip
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from umbra import config, db, devseed, serve


class ServeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        path = devseed.seed(cls.dir)
        real = db.connect
        cls.patches = [
            mock.patch.object(serve.db, "connect", side_effect=lambda *a, **k: real(path)),
            mock.patch.object(config, "load", return_value={}),
            # No built page in the test tree: the shell must still answer.
            mock.patch.object(serve, "WEB", cls.dir),
        ]
        for p in cls.patches:
            p.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def get(self, path, gz=False):
        req = urllib.request.Request(self.base + path,
                                     headers={"Accept-Encoding": "gzip"} if gz else {})
        try:
            with urllib.request.urlopen(req) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return r.status, dict(r.headers), body
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_meta(self):
        status, h, body = self.get("/api/meta")
        self.assertEqual(status, 200)
        self.assertIn("application/json", h["Content-Type"])
        self.assertEqual(json.loads(body)["order"][-1], devseed.DAY)

    def test_day_detail_raw(self):
        for suffix in ("", "/detail", "/raw", "/said"):
            status, _, body = self.get(f"/api/day/{devseed.DAY}{suffix}")
            self.assertEqual(status, 200, suffix)
            self.assertEqual(json.loads(body)["date"], devseed.DAY)

    def test_gzip_when_asked(self):
        status, h, body = self.get(f"/api/day/{devseed.DAY}/detail", gz=True)
        self.assertEqual(h.get("Content-Encoding"), "gzip")
        self.assertIn("ai", json.loads(body))

    def test_history_is_never_cached(self):
        _, h, _ = self.get("/api/meta")
        self.assertEqual(h["Cache-Control"], "no-store")

    def test_missing_day_is_404_json(self):
        status, _, body = self.get("/api/day/2020-01-01")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    def test_unknown_api_path_is_404(self):
        self.assertEqual(self.get("/api/nope")[0], 404)

    def test_no_page_built_still_answers(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"has not been built", body)

    def post(self, path, headers):
        req = urllib.request.Request(self.base + path, data=b"{}", method="POST",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_a_bare_post_is_refused(self):
        """What a form on some other site could send."""
        status, _ = self.post("/api/import", {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 403)

    def test_a_foreign_origin_is_refused(self):
        status, _ = self.post("/api/import", {"X-Umbra": "1", "Content-Type": "application/json",
                                              "Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_the_page_can_start_an_import(self):
        with mock.patch.object(serve.api, "start_import", return_value={"started": True}) as s:
            status, body = self.post("/api/import", {"X-Umbra": "1", "Content-Type": "application/json",
                                                     "Origin": self.base})
        self.assertEqual((status, body), (202, {"started": True}))
        s.assert_called_once()

    def test_the_page_can_close_a_task(self):
        with mock.patch.object(serve.api, "said_mark", return_value={"ok": True}) as v:
            req = urllib.request.Request(
                self.base + "/api/said/task/7", method="POST",
                data=json.dumps({"verdict": "not_a_task"}).encode(),
                headers={"X-Umbra": "1", "Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True})
        self.assertEqual(v.call_args.args[:3], ("task", 7, {"verdict": "not_a_task"}))

    def test_the_page_can_correct_an_hour(self):
        with mock.patch.object(serve.api, "correct", return_value={"ok": True}) as c:
            req = urllib.request.Request(
                self.base + "/api/day/2026-09-12/correct", method="POST",
                data=json.dumps({"span": "18:00-19:00", "cat": "read"}).encode(),
                headers={"X-Umbra": "1", "Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True})
        self.assertEqual(c.call_args.args[:2], ("2026-09-12", {"span": "18:00-19:00", "cat": "read"}))

    def test_config_read_and_write(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(config, "CONFIG_DIR", d), \
             mock.patch.object(config, "load", side_effect=lambda: __import__("tomllib").load(open(d + "/config.toml", "rb")) if os.path.exists(d + "/config.toml") else {}):
            status, body = self.post("/api/config", {"X-Umbra": "1", "Content-Type": "application/json",
                                                     "Origin": self.base})
            self.assertEqual(status, 200)
            req = urllib.request.Request(self.base + "/api/config", method="POST",
                                         data=json.dumps({"ui.lang": "zh"}).encode(),
                                         headers={"X-Umbra": "1", "Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                keys = {k["key"]: k["value"] for k in json.loads(r.read())["keys"]}
            self.assertEqual(keys["ui.lang"], "zh")
            req = urllib.request.Request(self.base + "/api/config", method="POST",
                                         data=json.dumps({"providers.x": 1}).encode(),
                                         headers={"X-Umbra": "1", "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 400)

    def test_default_binds_loopback(self):
        """The server carries someone's whole history; the network is opt-in."""
        import inspect
        self.assertEqual(inspect.signature(serve.serve).parameters["host"].default,
                         "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
