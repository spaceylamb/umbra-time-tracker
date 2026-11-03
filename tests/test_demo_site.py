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

    def week(self, wk: str) -> dict:
        with open(os.path.join(self.out, "api", "week", wk + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def test_three_whole_weeks_before_the_last(self):
        """Enough to page back through: three weeks of seven days each, and
        the reference week the page opens on."""
        order = self.meta["order"]
        for wk, mon in (("2026-W34", "2026-08-17"), ("2026-W35", "2026-08-24"),
                        ("2026-W36", "2026-08-31")):
            with self.subTest(wk):
                first = order.index(mon)
                self.assertEqual(len([d for d in order[first:first + 7]
                                      if d in self.week(wk)["days"]]), 7)
        self.assertEqual(order[-1], devseed.DAY)

    def test_every_week_has_its_priorities(self):
        for wk in ("2026-W34", "2026-W35", "2026-W36", "2026-W37"):
            with self.subTest(wk):
                self.assertEqual([p["text"] for p in self.week(wk)["priorities"]], devseed.GOALS)

    def test_the_weeks_get_better(self):
        """A trend to see: more nights asleep before midnight and more hours on
        the first priority, week after week."""
        pr = [self.week(wk)["priorities"] for wk in ("2026-W34", "2026-W35", "2026-W36")]
        sleep = [p[2]["sessions"] for p in pr]
        ship = [p[0]["minutes"] for p in pr]
        self.assertTrue(sleep[0] < sleep[1] < sleep[2], sleep)
        self.assertTrue(ship[0] < ship[1] < ship[2], ship)

    def test_no_made_up_day_has_a_long_silence(self):
        """Thinning a day for variety must not leave hours the model "said
        nothing about" — only the two days kept unjudged or failed on purpose."""
        import datetime as dt
        last = dt.date.fromisoformat(devseed.DAY)
        kept = {(last - dt.timedelta(days=n)).isoformat() for n in (devseed.UNJUDGED, devseed.FAILED)}
        for day in self.meta["order"]:
            if day in kept:
                continue
            with open(os.path.join(self.out, "api", "day", day + ".json"), encoding="utf-8") as f:
                silent = json.load(f)["minutes"]["silent"]
            with self.subTest(day):
                self.assertLessEqual(silent, 120)

    def test_the_reference_day_is_what_the_readme_shows(self):
        """DAY is the day the README's screenshots were taken of: adding or
        changing the other days must not move one record of it."""
        with open(os.path.join(self.out, "api", "day", devseed.DAY + ".json"), encoding="utf-8") as f:
            day = json.load(f)
        self.assertEqual(day["counts"], {"whoop": 2, "chrome": 32, "claude": 76, "zsh": 46, "git": 9})
        self.assertEqual(day["minutes"]["covered"], 1370)

    def test_a_late_night_is_one_night(self):
        """A day that starts up late follows an evening that ended up late, and
        an evening that ended asleep is followed by a morning asleep."""
        def ev(day):
            with open(os.path.join(self.out, "api", "day", day + ".json"), encoding="utf-8") as f:
                return (json.load(f).get("ev") or {}).get(devseed.MODEL) or []
        order = self.meta["order"]
        for day, nxt in zip(order, order[1:]):
            a, b = ev(day), ev(nxt)
            if a and b and nxt != devseed.DAY:
                with self.subTest(day):
                    self.assertEqual(a[-1]["title"] == "Up late, scrolling",
                                     b[0]["title"] == "Up late, scrolling")

    def test_a_weekend_is_not_a_workday(self):
        with open(os.path.join(self.out, "api", "day", "2026-08-30.json"), encoding="utf-8") as f:
            sunday = json.load(f)
        titles = " ".join(e["title"] for e in sunday["ev"][devseed.MODEL])
        self.assertNotIn("office", titles)


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
