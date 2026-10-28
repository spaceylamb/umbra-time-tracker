"""From evidence to candidates: what repeats on a rhythm, and what to do about it.

Three disciplines, all from early runs on a real history:

1. Every candidate carries evidence lines. One without is made up and dropped —
   a proposal with no quote cannot be checked.
2. "Not enough history" and "it does not happen" are told apart: a source with
   four weeks of data cannot show a monthly rhythm, and the report says so.
3. What is filtered out is counted by reason. Dropping 300 items silently means
   nobody later knows what the report is missing.
"""

import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from umbra import db
from umbra.plugins.review import detect as R

UNTIL = None


def _ms(day: str, hour: int = 12, minute: int = 0) -> int:
    return int(dt.datetime.fromisoformat(
        f"{day}T{hour:02d}:{minute:02d}").timestamp() * 1000)


END = _ms("2026-08-26")


class Base(unittest.TestCase):
    conf: dict = {}

    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))

    def ev(self, uid, ts, source, kind, payload):
        self.con.execute("INSERT OR IGNORE INTO events (uid, ts, source, kind, payload) "
                         "VALUES (?,?,?,?,?)", (uid, ts, source, kind, json.dumps(payload)))

    def visit(self, day, url, title="x", hour=12, minute=0):
        self.ev(f"chrome:{day}:{hour}:{minute}:{url}", _ms(day, hour, minute),
                "chrome", "visit", {"url": url, "title": title})

    def weekly(self, url, title="x", start="2026-06-01", n=6):
        for i in range(n):
            self.visit((dt.date.fromisoformat(start) + dt.timedelta(weeks=i)).isoformat(),
                       url, title)

    def cands(self, **kw):
        return R.candidates(self.con, weeks=16, until_ms=END, conf=self.conf, **kw)

    def keys(self):
        return {c["key"] for c in self.cands()}


class CandidatesTest(Base):
    def test_a_weekly_page_is_a_candidate(self):
        self.weekly("https://grocery.example.com/store/pickup", "Grocery pickup")
        hit = [c for c in self.cands() if "grocery" in c["key"]]
        self.assertEqual([c["name"] for c in hit], ["weekly"])

    def test_a_one_off_is_not(self):
        self.visit("2026-08-01", "https://example.com/one-off")
        self.assertEqual(self.cands(), [])

    def test_a_redirect_storm_on_one_day_is_one_point(self):
        for i in range(29):
            self.visit("2026-08-21", "https://tool.example.com/search", hour=9, minute=i // 10)
        self.assertEqual(self.cands(), [])

    def test_every_candidate_has_evidence_and_a_fold_ratio(self):
        for i in range(5):
            d = (dt.date(2026, 7, 1) + dt.timedelta(weeks=i)).isoformat()
            for m in (0, 1, 3):
                self.visit(d, "https://money.example.com/dashboard", "Dashboard", 9, m)
        c = self.cands()[0]
        self.assertTrue(c["evidence"])
        self.assertGreater(c["fold_ratio"], 1.0)

    def test_outside_the_window_does_not_count(self):
        self.weekly("https://old.example.com/x", start="2026-01-01")
        self.assertEqual(self.cands(), [])

    def test_the_key_is_deterministic(self):
        """The dedupe key across months. A model-written title changes wording
        every round; a key on it is no key."""
        self.weekly("https://grocery.example.com/store/pickup")
        self.assertEqual(self.cands()[0]["akey"], "page:grocery.example.com/store/pickup")


class NoiseTest(Base):
    def test_entry_pages_and_redirects_are_dropped(self):
        for u in ("https://www.google.com/search?q=x", "https://gmail.com/",
                  "https://www.youtube.com/watch?v=x", "https://news.ycombinator.com/item?id=1",
                  "https://www.google.com/url?q=x", "https://substack.com/redirect/abc"):
            self.weekly(u)
        self.assertEqual(self.cands(), [])

    def test_maps_and_flights_are_work_not_entry_pages(self):
        """The first cut swept these in and 66 flight searches vanished."""
        for i in range(6):
            self.visit((dt.date(2026, 6, 1) + dt.timedelta(weeks=i)).isoformat(),
                       "https://www.google.com/travel/flights/search?q=x", "Flights")
            self.visit((dt.date(2026, 6, 4) + dt.timedelta(weeks=i)).isoformat(),
                       "https://www.google.com/maps/dir/a/b", "Directions")
        self.assertTrue({"www.google.com/travel/flights", "www.google.com/maps/dir"}
                        <= self.keys())

    def test_shopping_is_work_but_search_results_and_ads_are_not(self):
        self.weekly("https://www.amazon.com/gp/css/order-history", "Orders")
        self.weekly("https://www.amazon.com/s?k=x", "Search", start="2026-06-03")
        self.weekly("https://www.amazon.com/sspa/click?ad=1", "Ad", start="2026-06-05")
        self.assertEqual(self.keys(), {"www.amazon.com/gp/css"})

    def test_a_returns_page_is_not_a_search_page(self):
        """`/s` is search results; `/spr/returns` only starts with the letter."""
        self.weekly("https://www.amazon.com/spr/returns/label", "Return")
        self.assertEqual(self.keys(), {"www.amazon.com/spr/returns"})

    def test_reading_is_dropped_but_tools_are_not(self):
        for u in ("https://open.substack.com/pub/somewriter",
                  "https://www.youtube.com/@somechannel",
                  "https://zhuanlan.zhihu.com/p/123456"):
            self.weekly(u)
        self.weekly("https://dash.example.com/reports/daily", start="2026-06-02")
        self.assertEqual({k.split("/")[0] for k in self.keys()}, {"dash.example.com"})

    def test_drops_are_counted_by_reason(self):
        self.weekly("https://www.google.com/search?q=x")
        self.weekly("https://open.substack.com/pub/someone", start="2026-06-03")
        _, dropped = self.cands(with_dropped=True)
        self.assertEqual(dropped, {"entry or redirect page": 1, "reading": 1})

    def test_a_sign_in_page_is_its_own_kind_not_reading(self):
        """Signing in again every few days is a real cost: make the session
        longer. It looks like a redirect, so it is recognised first."""
        self.weekly("https://accounts.youtube.com/accounts/SetSID")
        self.assertEqual([c["kind"] for c in self.cands()], ["auth"])
        self.assertEqual(self.cands()[0]["lever"], "session")


class ConfigFiltersTest(Base):
    """What one person reads is not a rule for everyone. The built-in lists are
    generic; the rest is config."""

    def test_your_own_reading_sites(self):
        self.conf = {"plugins": {"review": {"reading": ["myblog.example.com"]}}}
        self.weekly("https://myblog.example.com/posts/1")
        _, dropped = self.cands(with_dropped=True)
        self.assertEqual(dropped, {"reading": 1})

    def test_your_own_ignore_patterns(self):
        self.conf = {"plugins": {"review": {"ignore": [r"^intranet\.example\.com/"]}}}
        self.weekly("https://intranet.example.com/home/x")
        _, dropped = self.cands(with_dropped=True)
        self.assertEqual(dropped, {"ignored in config": 1})

    def test_a_bad_pattern_is_named_not_swallowed(self):
        self.conf = {"plugins": {"review": {"ignore": ["(unclosed"]}}}
        with self.assertRaisesRegex(ValueError, "unclosed"):
            self.cands()


class MailTest(Base):
    def mail(self, day, title):
        self.ev(f"chrome:mail:{day}:{title}", _ms(day), "chrome", "visit",
                {"url": "https://mail.google.com/mail/u/0/#inbox/x", "title": title})

    def test_numbers_in_a_subject_are_one_kind_of_mail(self):
        for i in range(5):
            self.mail((dt.date(2026, 7, 1) + dt.timedelta(weeks=i)).isoformat(),
                      f"Your grocery order receipt {1000 + i} - Gmail")
        got = [c for c in self.cands() if c["kind"] == "mail"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["lever"], "unsubscribe")

    def test_mailbox_views_are_not_subjects(self):
        for t in ("(no subject) - Gmail", "Spam (12) - Gmail", "Inbox (7) - Gmail"):
            self.assertIsNone(R.mail_key(t), t)
        self.assertEqual(R.mail_key("Your grocery order receipt 1234 - Gmail"),
                         "Your grocery order receipt #")


class InstructionsTest(Base):
    def say(self, day, text, source="claude", n=0, hour=12, minute=0, cwd="", **p):
        kind = "prompt" if source == "claude" else "task"
        self.ev(f"{source}:{day}:{n}:{hour}:{minute}", _ms(day, hour, minute), source,
                kind, {"text": text, "cwd": cwd, **p})

    def rows(self):
        return R.instructions(self.con, _ms("2026-05-06"), END, conf=self.conf)[0]

    def test_different_wordings_of_one_thing_are_one_family(self):
        for i, t in enumerate(["ok, commit, merge, and then deploy it",
                               "fine, go ahead: commit, merge and deploy",
                               "commit, merge, deploy please",
                               "just commit merge and deploy it now"]):
            self.say((dt.date(2026, 7, 1) + dt.timedelta(weeks=i)).isoformat(), t)
        got = [c for c in self.cands() if c["kind"] == "instruction"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["lever"], "schedule")

    def test_chinese_wordings_cluster_too(self):
        for i, t in enumerate(["好的，你直接 commit、merge，然后 deploy 吧",
                               "好了，你可以直接 commit、merge and deploy 了",
                               "好的，直接 commit、merge、deploy 吧",
                               "你直接 commit merge and deploy 了吧"]):
            self.say((dt.date(2026, 7, 1) + dt.timedelta(weeks=i)).isoformat(), t)
        self.assertEqual(len([c for c in self.cands() if c["kind"] == "instruction"]), 1)

    def test_a_chain_does_not_join_its_ends(self):
        """A like B, B like C does not make A like C. A seed that grew once
        showed one ordinary instruction as "x17 across 17 days"."""
        got = R.cluster([(1, "merge this branch into main and deploy it to production"),
                         (2, "merge this branch into main and deploy it, then research diving seasons"),
                         (3, "research diving seasons and the best island locations")])
        self.assertFalse(any(len(g) == 3 for g in got))

    def test_butler_and_local_copies_count_once(self):
        self.say("2026-07-01", "merge this branch into main and deploy", "claude")
        self.say("2026-07-01", "merge this branch into main and deploy", "butler")
        rows, ratio = R.instructions(self.con, _ms("2026-05-06"), END, conf={})
        self.assertEqual((len(rows), ratio), (1, 2.0))

    WT = "/home/me/code/app/.butler-worktrees/044186f7"
    AUDIT = "You are the weekly audit agent. Follow the playbook for this repository."

    def test_a_task_another_program_sent_is_not_yours(self):
        """It came in through Butler's dispatch API: provider is the agent that
        ran it, the sender is only in source_app."""
        for i in range(6):
            self.say((dt.date(2026, 6, 1) + dt.timedelta(weeks=i)).isoformat(),
                     "Digest this week's sources for the knowledge base, then report",
                     "butler", source_app="Atlas", provider="claude")
        self.assertEqual(self.rows(), [])
        auto = R.already_automated(self.con, _ms("2026-05-06"), END, conf={})
        self.assertEqual([a["name"] for a in auto], ["weekly"])

    def test_its_local_copy_is_not_yours_either_even_hours_later(self):
        text = "Digest one primary source the research bot sent over (target: someone)"
        self.say("2026-08-01", text, "butler", source_app="Research bot")
        self.say("2026-08-01", text, hour=14, minute=10, cwd=self.WT)
        self.say("2026-08-06", text, n=1, cwd=self.WT)
        self.assertEqual(self.rows(), [])

    def test_the_same_words_you_typed_another_day_are_yours(self):
        text = "ok, commit, merge, and then deploy it"
        self.say("2026-08-01", text, "butler", source_app="Umbra")
        self.say("2026-08-03", text)
        self.assertEqual(len(self.rows()), 1)

    def test_a_role_prompt_is_a_program_even_without_a_sender(self):
        self.say("2026-08-01", self.AUDIT, "butler", provider="claude")
        self.assertEqual(self.rows(), [])

    def test_your_own_machine_patterns_and_providers(self):
        self.conf = {"plugins": {"review": {
            "agent_prompts": [r"^Daily briefing for"],
            "machine_providers": ["cron-bot"]}}}
        self.say("2026-08-01", "Daily briefing for the team, as usual please", "butler")
        self.say("2026-08-02", "Collect the overnight alerts and summarise them", "butler",
                 provider="cron-bot")
        self.say("2026-08-03", "please rename the settings page to preferences")
        self.assertEqual([t for _, t in self.rows()],
                         ["please rename the settings page to preferences"])


class OtherKindsTest(Base):
    def test_a_burst_and_not_also_a_rhythm(self):
        for i in range(8):
            self.visit((dt.date(2026, 8, 4) + dt.timedelta(days=i)).isoformat(),
                       "https://visa.example.gov/apply", "Visa application")
        got = self.cands()
        self.assertEqual([c["kind"] for c in got], ["burst"])
        self.assertEqual(got[0]["lever"], "recall")
        for k in ("first", "last", "quiet_days", "state"):
            self.assertIn(k, got[0])

    def test_a_project_and_its_sites_only_once(self):
        for i in range(4):
            for d in range(4):
                self.visit((dt.date(2026, 7, 15) + dt.timedelta(days=d * 2)).isoformat(),
                           f"https://s{i}.example.com/x", f"site {i}")
        got = self.cands()
        self.assertEqual([c["kind"] for c in got], ["project"])
        self.assertIn("s0.example.com", " ".join(e["text"] for e in got[0]["evidence"]))

    def test_offline_rhythms_are_candidates(self):
        for i in range(6):
            d = (dt.date(2026, 6, 1) + dt.timedelta(weeks=i)).isoformat()
            self.ev(f"loc:{d}", _ms(d), "location", "stay", {"place": "Gym", "named": True})
        got = [c for c in self.cands() if c["kind"] == "offline"]
        self.assertEqual([(c["key"], c["lever"]) for c in got], [("at:Gym", "delegate")])
        self.assertTrue(got[0]["evidence"])


class FlowsTest(Base):
    """A checkout is six pages; merged, it is one thing. Twenty pages on one
    site is everything done there, and merging that hides it."""

    def site(self, n_paths):
        for i in range(5):
            d = (dt.date(2026, 6, 1) + dt.timedelta(weeks=i)).isoformat()
            for j in range(n_paths):
                self.visit(d, f"https://shop.example.com/p{j}", minute=j)

    def test_a_few_steps_merge(self):
        self.site(5)
        got = [c for c in self.cands() if "shop.example.com" in c["key"]]
        self.assertEqual([len(c["steps"]) for c in got], [5])

    def test_too_many_steps_do_not(self):
        self.site(20)
        got = [c for c in self.cands() if "shop.example.com" in c["key"]]
        self.assertGreater(len(got), 1)
        self.assertTrue(all(not c.get("steps") for c in got))


class GateTest(unittest.TestCase):
    def test_spanning_three_weeks_passes(self):
        self.assertTrue(R.worth_surfacing({"n": 3, "span_days": 25, "regular": True}))
        self.assertTrue(R.worth_surfacing({"n": 4, "span_days": 30, "regular": False}))

    def test_too_short_does_not(self):
        self.assertFalse(R.worth_surfacing({"n": 3, "span_days": 3, "regular": True}))

    def test_many_and_regular_passes_while_short(self):
        """Something that only started two weeks ago."""
        self.assertTrue(R.worth_surfacing({"n": 8, "span_days": 18, "regular": True}))
        self.assertFalse(R.worth_surfacing({"n": 8, "span_days": 18, "regular": False}))


class CoverageTest(Base):
    def test_a_short_history_is_said(self):
        self.ev("plaud:1", _ms("2026-08-20"), "plaud", "speech", {"text": "hi"})
        cov = R.coverage(self.con, weeks=16, until_ms=END)
        self.assertTrue(cov["plaud"]["short"])
        self.assertNotIn("chrome", cov)

    def test_history_weeks_is_the_longest_relevant_source(self):
        self.ev("c:1", _ms("2026-07-01"), "chrome", "visit", {"url": "https://a.b/"})
        self.ev("p:1", _ms("2026-03-01"), "plaud", "speech", {"text": "hi"})
        self.assertAlmostEqual(R.history_weeks(self.con, until_ms=END), 8.0, delta=0.2)


class ReportTest(Base):
    def test_the_report_groups_by_what_to_do(self):
        self.weekly("https://grocery.example.com/store/pickup", "Grocery pickup")
        self.weekly("https://claude.ai/oauth/authorize?x=1", "Sign in", start="2026-06-03")
        text = R.render(self.con, weeks=16, until_ms=END, conf={})
        self.assertIn("Make it come to you", text)
        self.assertIn("Make the sign-in last longer", text)
        self.assertIn("grocery.example.com", text)
        self.assertIn("Coverage", text)

    def test_every_lever_has_words(self):
        for lev in R.LEVERS:
            self.assertTrue(R.LEVER_WORDS.get(lev), lev)


class ButlerSenderTest(unittest.TestCase):
    """The importer keeps who sent a task, or a program's weekly job reads as
    something you type every week."""

    def test_the_importer_keeps_the_sender(self):
        from umbra.importers import butler
        page = {"tasks": [{"id": "t1", "createdAt": "2026-08-01T12:00:00Z",
                           "prompt": "Digest this source", "provider": "claude",
                           "sourceApp": "Research bot"}]}
        with mock.patch.dict(os.environ, {"UMBRA_BUTLER_URL": "https://b.example",
                                          "UMBRA_BUTLER_PAT": "x"}), \
             mock.patch.object(butler.net, "get_json", return_value=page):
            rows = butler.collect()
        task = next(r for r in rows if r[4] == "task")
        self.assertEqual(task[5]["source_app"], "Research bot")


if __name__ == "__main__":
    unittest.main()
