"""umbra export: the static, read-only page the phone reads.

Every file it writes is a GET the page would otherwise make, so each must
validate against the same schema the server answers with; raw records go only
into the last raw_days; and it never clears a directory it did not make.
"""

import datetime as dt
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from umbra import config, db, devseed, export
from umbra.schema import validate as V

KIND = [(r"^api/meta\.json$", "Meta"), (r"^api/day/[^/]+\.json$", "Day"),
        (r"^api/day/[^/]+/detail\.json$", "DayDetail"), (r"^api/day/[^/]+/raw\.json$", "Raw"),
        (r"^api/day/[^/]+/said\.json$", "Said"), (r"^api/week/[^/]+\.json$", "Week"),
        (r"^api/setup\.json$", "Setup"), (r"^api/settings\.json$", "Settings"),
        (r"^api/import\.json$", "ImportProgress")]


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        path = devseed.seed(os.path.join(cls.dir, "data"))
        real = db.connect
        cls.patches = [mock.patch.object(db, "connect", side_effect=lambda *a, **k: real(path)),
                       mock.patch.object(config, "load", return_value={})]
        for p in cls.patches:
            p.start()
        cls.out = os.path.join(cls.dir, "site")
        cls.result = export.write(cls.out, raw_days=3)

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def files(self):
        for root, _, names in os.walk(os.path.join(self.out, "api")):
            for n in names:
                yield os.path.relpath(os.path.join(root, n), self.out).replace(os.sep, "/")

    def test_every_file_validates(self):
        seen = 0
        for rel in self.files():
            kind = next(k for pat, k in KIND if re.match(pat, rel))
            with open(os.path.join(self.out, rel), encoding="utf-8") as f:
                V.validate(json.load(f), kind)
            seen += 1
        self.assertEqual(seen, self.result["files"])

    def test_raw_only_for_the_last_days(self):
        raws = sorted(r.split("/")[2] for r in self.files() if r.endswith("/raw.json"))
        last = dt.date.fromisoformat(devseed.DAY)
        self.assertEqual(raws, [(last - dt.timedelta(days=i)).isoformat() for i in (2, 1, 0)])

    def test_index_is_flagged_static(self):
        with open(os.path.join(self.out, "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("window.__UMBRA_STATIC__={}", html)
        self.assertTrue(os.path.isdir(os.path.join(self.out, "assets")))

    def test_rerun_replaces_its_own_export(self):
        export.write(self.out, raw_days=3)

    def test_refuses_to_clear_a_directory_it_did_not_make(self):
        other = os.path.join(self.dir, "precious")
        os.makedirs(other)
        with open(os.path.join(other, "notes.txt"), "w") as f:
            f.write("mine")
        with self.assertRaises(FileExistsError):
            export.write(other)
        self.assertTrue(os.path.exists(os.path.join(other, "notes.txt")))

    def test_paths_on_this_mac_are_not_spelled_out(self):
        home = os.path.expanduser("~")
        for name in ("meta", "setup", "settings"):
            with open(os.path.join(self.out, "api", name + ".json"), encoding="utf-8") as f:
                self.assertNotIn(json.dumps(home)[1:-1] + "/", f.read(), name)

    def test_single_file_carries_everything(self):
        p = os.path.join(self.dir, "one.html")
        export.write_single(p, raw_days=1)
        with open(p, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn('src="./assets/', html)
        self.assertNotIn('href="./assets/', html)
        self.assertIn('"/api/meta"', html)
        self.assertNotIn("</script><", html.split("__UMBRA_STATIC__=", 1)[1].split("</script>", 1)[0])

    def test_charset_comes_before_the_data(self):
        """file:// sends no charset; the meta tag only counts in the first
        1024 bytes. With the payload in front, CJK and dashes in the data
        came out as mojibake (an em dash in a source error read â€\")."""
        p = os.path.join(self.dir, "one.html")
        export.write_single(p, raw_days=1)
        with open(p, "rb") as f:
            head = f.read(1024)
        self.assertIn(b"<meta charset=", head)


if __name__ == "__main__":
    unittest.main()
