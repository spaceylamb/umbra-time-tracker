"""python -m umbra.devseed --site: the public demo.

Three made-up weeks exported as a static site, flagged so the page can say it
is a demo and keep its clock still. Built in a throwaway home, so it can never
touch a real database and spells no path of the machine that built it.
"""

import json
import os
import re
import shutil
import tempfile
import unittest

from umbra import db, devseed


def _flag(out: str) -> dict:
    with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
        m = re.search(r"window\.__UMBRA_STATIC__=(\{.*?\})</script>", f.read())
    return json.loads(m.group(1))


class DemoSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.home = os.path.join(cls.dir, "home")
        cls.out = os.path.join(cls.dir, "site")
        devseed.site(cls.out, home=cls.home)
        with open(os.path.join(cls.out, "api", "meta.json"), encoding="utf-8") as f:
            cls.meta = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_page_is_flagged_as_the_demo(self):
        demo = _flag(self.out)["demo"]
        self.assertTrue(demo["link"].startswith("https://github.com/spaceylamb/umbra-time-tracker"))

    def test_its_clock_stands_at_one_fixed_moment(self):
        """Not the moment it was built: two builds must read the same, and the
        last import must sit at the same instant whenever the page is opened."""
        self.assertEqual(_flag(self.out)["demo"]["now"], devseed.DEMO_NOW)
        for f in self.meta["fresh"]:
            with self.subTest(f["source"]):
                self.assertEqual(f["last_run"], devseed.DEMO_NOW)

    def test_nothing_needs_you(self):
        """A demo of made-up days has nothing to fix: no source is failing."""
        self.assertTrue(self.meta["fresh"])
        for f in self.meta["fresh"]:
            with self.subTest(f["source"]):
                self.assertFalse(f["bad"])
                self.assertEqual(f["fails"], 0)

    def test_no_path_of_the_building_machine(self):
        real = os.path.realpath(self.dir)
        for root, _, names in os.walk(os.path.join(self.out, "api")):
            for n in names:
                with open(os.path.join(root, n), encoding="utf-8") as f:
                    text = f.read()
                with self.subTest(n):
                    self.assertNotIn(self.dir, text)
                    self.assertNotIn(real, text)

    def test_it_leaves_nothing_in_its_home(self):
        self.assertEqual(os.listdir(self.home), [])


class SafetyTest(unittest.TestCase):
    def test_never_writes_over_an_existing_database(self):
        d = tempfile.mkdtemp()
        try:
            data = os.path.join(d, "Umbra")
            os.makedirs(data)
            with open(os.path.join(data, "events.db"), "w") as f:
                f.write("precious")
            with self.assertRaises(RuntimeError):
                devseed._write_site(os.path.join(d, "site"), home=d,
                                    db_path=os.path.join(data, "events.db"),
                                    config_dir=os.path.join(d, "config"))
            with open(os.path.join(data, "events.db")) as f:
                self.assertEqual(f.read(), "precious")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_never_writes_outside_its_home(self):
        d = tempfile.mkdtemp()
        try:
            with self.assertRaises(RuntimeError):
                devseed._write_site(os.path.join(d, "site"), home=os.path.join(d, "home"),
                                    db_path=db.DB_PATH, config_dir=os.path.join(d, "config"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class DeveloperSeedTest(unittest.TestCase):
    def test_the_developers_seed_keeps_its_expired_login(self):
        """Without demo, devseed still shows the Sources page's "Needs you"."""
        d = tempfile.mkdtemp()
        try:
            con = db.connect(devseed.seed(d))
            fails = con.execute("SELECT fails FROM imports WHERE source = 'plaud'").fetchone()[0]
            con.close()
            self.assertEqual(fails, 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
