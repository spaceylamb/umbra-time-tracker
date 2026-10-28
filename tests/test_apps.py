"""bundle id -> a human name.

Where this came from: on 2026-08-17 the user looked at the page and asked
"phone foreground: xin — what is that?". It was WeChat.

The problem was never that one app. It was **how names were derived**: the
iphone and app importers built the `app` field as "bundle id split on dots,
last segment", so

    com.tencent.xin        -> xin              WeChat
    com.xingin.discover    -> discover         Xiaohongshu
    com.google.chrome.ios  -> ios              Chrome
    com.typeless.mobile    -> mobile           Typeless (dictation)
    com.spotify.client     -> client           Spotify

Not merely ugly: those strings are **fed verbatim to the model** when it judges
what was going on. "phone foreground: ios" supports no conclusion; "WeChat"
says a conversation was happening. Same shape as the fabricated "WHOOP workout"
found earlier that day — information destroyed at the input stage.

The table holds only ids that are actually recognised. Anything else falls back
to itself, and that is deliberate: an invented plausible name is worse than an
ugly raw one, because you never look at the first again while the second is
visibly unclaimed.
"""

import unittest

from umbra import apps


class CanonicalNameTest(unittest.TestCase):
    """name() is the model-facing string, and it is always English."""

    def test_wechat(self):
        self.assertEqual(apps.name("com.tencent.xin"), "WeChat")

    def test_the_mac_build_gets_the_same_name(self):
        """com.tencent.xinWeChat is the Mac build. One app answering to two
        names across two devices makes a cross-device timeline fail to line
        up."""
        self.assertEqual(apps.name("com.tencent.xinWeChat"), "WeChat")

    def test_xiaohongshu(self):
        self.assertEqual(apps.name("com.xingin.discover"), "Xiaohongshu")

    def test_chrome_is_chrome_on_every_platform(self):
        self.assertEqual(apps.name("com.google.Chrome"), "Chrome")
        self.assertEqual(apps.name("com.google.chrome.ios"), "Chrome")

    def test_the_dictation_app(self):
        """Typeless is a dictation tool. Recognising it matters more than
        most: the stretches where it appears are exactly the stretches where
        idle misreports "nobody here", because speaking produces no keyboard
        or mouse events."""
        self.assertIn("Typeless", apps.name("com.typeless.mobile"))

    def test_canonical_names_are_ascii(self):
        """The whole point of the name()/label() split.

        name() feeds the digest, and the digest is compared byte-for-byte as
        the inference cache key. If any canonical name were localised, then
        switching the interface language would rewrite every historical digest
        and re-run inference over all of it at real API cost. Canonical is a
        wire format, not a preference.
        """
        bad = [(b, n) for b, n in apps.NAMES.items() if not n.isascii()]
        self.assertEqual(bad, [], f"non-ASCII canonical names: {bad}")


class LocalizedLabelTest(unittest.TestCase):
    """label() is the person-facing string and may be localised freely."""

    def test_chinese_labels(self):
        self.assertEqual(apps.label("com.tencent.xin", "zh"), "微信")
        self.assertEqual(apps.label("com.xingin.discover", "zh"), "小红书")

    def test_english_is_the_canonical_name(self):
        self.assertEqual(apps.label("com.tencent.xin", "en"), "WeChat")

    def test_unknown_language_falls_back_to_canonical(self):
        self.assertEqual(apps.label("com.tencent.xin", "de"), "WeChat")

    def test_a_locale_need_only_list_what_differs(self):
        """Zoom is Zoom in every language; absent entries fall through."""
        self.assertEqual(apps.label("us.zoom.xos", "zh"), "Zoom")

    def test_labels_covers_every_known_bundle(self):
        for lang in ("en", "zh"):
            with self.subTest(lang):
                self.assertEqual(set(apps.labels(lang)), set(apps.NAMES))

    def test_localizing_does_not_change_what_the_model_sees(self):
        """The invariant the split exists to protect."""
        before = {b: apps.name(b) for b in apps.NAMES}
        apps.labels("zh")
        self.assertEqual({b: apps.name(b) for b in apps.NAMES}, before)

    def test_every_locale_key_is_a_known_bundle(self):
        """A typo'd bundle id in LOCALE would silently never apply."""
        for lang, table in apps.LOCALE.items():
            for bundle in table:
                with self.subTest(lang=lang, bundle=bundle):
                    self.assertIn(bundle, apps.NAMES)


class UnknownTest(unittest.TestCase):
    def test_unrecognised_falls_back_to_itself(self):
        """No guessing. An invented pretty name is worse than an ugly raw one:
        you will never look at the first again, while the second is visibly
        unclaimed."""
        self.assertEqual(apps.name("com.nobody.whatever"), "whatever")
        self.assertEqual(apps.label("com.nobody.whatever", "zh"), "whatever")

    def test_empty_in_empty_out(self):
        self.assertEqual(apps.name(""), "")
        self.assertEqual(apps.name(None), "")
        self.assertEqual(apps.label(None, "zh"), "")

    def test_an_id_with_no_dots_is_returned_as_is(self):
        self.assertEqual(apps.name("Finder"), "Finder")

    def test_never_returns_empty_unless_the_input_was_empty(self):
        for b in ("com.a.b", "x", "com."):
            if b:
                self.assertTrue(apps.name(b), f"{b} returned empty")


class TableTest(unittest.TestCase):
    def test_no_duplicate_bundle_ids(self):
        self.assertEqual(len(apps.NAMES), len(set(apps.NAMES)))

    def test_no_name_is_blank(self):
        self.assertTrue(all(v.strip() for v in apps.NAMES.values()))

    def test_nothing_unrecognised_is_given_an_invented_name(self):
        """Reverse anchor: no placeholder like "Unknown app" belongs in the
        table — that disguises unclaimed as claimed."""
        placeholders = ("unknown", "other", "未知", "其他")   # zh placeholders too
        bad = [v for v in apps.NAMES.values()
               if any(p in v.lower() for p in placeholders)]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
