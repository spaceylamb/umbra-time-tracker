"""The import-time privacy filter.

This module's whole job is that certain things never reach the store. Every
other layer treats the store as ground truth and copies from it freely — the
rendered page embeds raw payloads verbatim — so anything that gets past this
filter is, in practice, permanent and shipped.

Which makes the failure mode worth naming: a regex that quietly stops matching
breaks nothing, raises nothing, and shows up months later as a bank dashboard
title sitting in an HTML file. There is no downstream check. These tests are
the only thing standing there, and until now there were none.

Two rules the cases below encode:

  · KEEP is the default, and it must stay the default. A filter that
    over-matches destroys ordinary history — and unlike a leak, the user cannot
    tell it happened.
  · REDACT must preserve the timestamp and the host. "This stretch of time was
    spent on bank things" is information worth keeping; the account balance is
    not.
"""

import unittest

from umbra import privacy


class ClassifyUrlDropTest(unittest.TestCase):
    """Password managers and auth flows: not one byte."""

    def test_password_manager_hosts(self):
        for host in ("dashlane.com", "1password.com", "vault.bitwarden.com",
                     "lastpass.com", "keeper.io"):
            with self.subTest(host):
                self.assertEqual(privacy.classify_url(f"https://{host}/x"),
                                 privacy.DROP)

    def test_identity_provider_hosts(self):
        for url in ("https://accounts.google.com/signin/v2/identifier",
                    "https://login.microsoftonline.com/common/oauth2",
                    "https://acme.okta.com/app/x",
                    "https://api-1234.duosecurity.com/frame"):
            with self.subTest(url):
                self.assertEqual(privacy.classify_url(url), privacy.DROP)

    def test_title_alone_is_enough(self):
        """The host can be perfectly ordinary while the page is a login."""
        for title in ("Sign in", "Log in to your account", "LOGIN",
                      "oAuth Sign On - Citibank", "Reset your password",
                      "Verify your identity"):
            with self.subTest(title):
                self.assertEqual(
                    privacy.classify_url("https://example.com/x", title),
                    privacy.DROP)

    def test_non_english_titles_still_drop(self):
        """The user's browsing is multilingual regardless of UI language.

        These patterns are not i18n — they are never switched by locale,
        because someone reading an English interface still visits a Chinese
        bank. Every language's patterns stay active at once.
        """
        for title in ("两步验证", "请输入验证码"):
            with self.subTest(title):
                self.assertEqual(
                    privacy.classify_url("https://example.com/x", title),
                    privacy.DROP)

    def test_match_is_case_insensitive(self):
        self.assertEqual(privacy.classify_url("https://DASHLANE.com/"),
                         privacy.DROP)

    def test_drop_beats_redact(self):
        """A bank's login page is a login page first.

        Ordering inside classify_url decides this, and getting it backwards
        would keep the host of a page that must vanish entirely.
        """
        self.assertEqual(
            privacy.classify_url("https://chase.com/login", "Sign in"),
            privacy.DROP)


class ClassifyUrlRedactTest(unittest.TestCase):
    """Banking, taxes and health: keep the host, drop the content."""

    def test_financial_hosts(self):
        for host in ("chase.com", "www.bankofamerica.com", "citibank.com",
                     "wellsfargo.com", "schwab.com", "fidelity.com",
                     "vanguard.com", "paypal.com", "venmo.com",
                     "app.monarchmoney.com", "mint.com"):
            with self.subTest(host):
                self.assertEqual(
                    privacy.classify_url(f"https://{host}/accounts"),
                    privacy.REDACT)

    def test_tax_and_health_hosts(self):
        for host in ("turbotax.intuit.com", "irs.gov", "myhealth.example.org",
                     "kaiserpermanente.org", "healthcare.gov"):
            with self.subTest(host):
                self.assertEqual(privacy.classify_url(f"https://{host}/x"),
                                 privacy.REDACT)


class ClassifyUrlKeepTest(unittest.TestCase):
    """Over-matching is the quieter failure, and the harder one to notice."""

    def test_ordinary_browsing_is_kept(self):
        for url, title in (
                ("https://github.com/spaceylamb/umbra-time-tracker", "Umbra"),
                ("https://news.ycombinator.com/", "Hacker News"),
                ("https://en.wikipedia.org/wiki/Sleep", "Sleep - Wikipedia"),
                ("https://docs.python.org/3/library/sqlite3.html", "sqlite3")):
            with self.subTest(url):
                self.assertEqual(privacy.classify_url(url, title),
                                 privacy.KEEP)

    def test_a_word_inside_another_word_does_not_trigger(self):
        """Regression: these titles were all being silently deleted.

        Before word boundaries were added, _DROP_TITLE matched any title
        *containing* the letters:

            "Design inspiration"  → de|sign in|spiration
            "Design in Figma"     → de|sign in| Figma
            "Cataloging books"    → cata|login|g books

        Each was discarded at import time and never reached the store — no
        error, and nothing downstream that could notice. Erased history is
        indistinguishable from history that never happened, which makes this
        the worse of the two failure directions.
        """
        for title in ("Design inspiration", "Design in Figma",
                      "Cataloging books", "Blogging tips",
                      "Logins and auth", "Assignment in progress"):
            with self.subTest(title):
                self.assertEqual(
                    privacy.classify_url("https://example.com/", title),
                    privacy.KEEP)

    def test_the_real_phrases_still_drop_after_the_boundary_fix(self):
        """The other half of the same fix: narrowing must not blind it."""
        for title in ("Sign in", "Sign into the app", "LOGIN", "log in",
                      "Log-in required", "Designing a login flow",
                      "Reset your password", "Two-factor authentication"):
            with self.subTest(title):
                self.assertEqual(
                    privacy.classify_url("https://example.com/", title),
                    privacy.DROP)

    def test_empty_and_malformed_input(self):
        for url in ("", "not a url", "about:blank", "chrome://newtab"):
            with self.subTest(url):
                self.assertEqual(privacy.classify_url(url), privacy.KEEP)


class ApplyUrlTest(unittest.TestCase):
    def test_drop_returns_none(self):
        self.assertIsNone(privacy.apply_url("https://dashlane.com/", "x"))

    def test_keep_returns_input_unchanged(self):
        pair = ("https://example.com/a?b=c", "Example")
        self.assertEqual(privacy.apply_url(*pair), pair)

    def test_redact_keeps_the_host_and_drops_the_rest(self):
        got = privacy.apply_url(
            "https://chase.com/accounts/1234?balance=99999",
            "Account Dashboard — $99,999.00")
        assert got is not None, "a REDACT verdict must return a pair"
        url, title = got
        self.assertIn("chase.com", url)
        self.assertIn("chase.com", title)
        for secret in ("1234", "99999", "99,999", "balance"):
            with self.subTest(secret):
                self.assertNotIn(secret, url)
                self.assertNotIn(secret, title)

    def test_redact_says_it_redacted(self):
        """The placeholder has to be visible.

        Silently shortening a URL would make a redacted row and a genuinely
        short row look identical — the exact confusion this project refuses
        everywhere else.
        """
        got = privacy.apply_url("https://chase.com/x", "Balances")
        assert got is not None
        self.assertIn("redacted", got[0])

    def test_redact_survives_a_missing_host(self):
        """apply_url must not crash on input its own classifier let through."""
        self.assertIsNotNone(privacy.apply_url("chase.com/x", ""))


class ClassifyBundleTest(unittest.TestCase):
    def test_password_manager_bundles_drop(self):
        for b in ("com.dashlane.dashlanephonefinal", "com.agilebits.onepassword7",
                  "com.callpod.keeper"):
            with self.subTest(b):
                self.assertEqual(privacy.classify_bundle(b), privacy.DROP)

    def test_ordinary_bundles_are_kept(self):
        for b in ("com.google.Chrome", "com.apple.Notes", "us.zoom.xos"):
            with self.subTest(b):
                self.assertEqual(privacy.classify_bundle(b), privacy.KEEP)

    def test_empty_bundle_is_kept_not_crashed(self):
        for b in ("", None):
            with self.subTest(b):
                self.assertEqual(privacy.classify_bundle(b), privacy.KEEP)


class ContractTest(unittest.TestCase):
    def test_verdicts_are_the_three_documented_strings(self):
        """Callers compare against these constants; a typo in one would make a
        branch silently unreachable."""
        self.assertEqual((privacy.DROP, privacy.REDACT, privacy.KEEP),
                         ("drop", "redact", "keep"))

    def test_classify_url_only_ever_returns_a_known_verdict(self):
        known = {privacy.DROP, privacy.REDACT, privacy.KEEP}
        for url in ("https://chase.com/", "https://dashlane.com/",
                    "https://example.com/", "", "://"):
            with self.subTest(url):
                self.assertIn(privacy.classify_url(url), known)


if __name__ == "__main__":
    unittest.main()
