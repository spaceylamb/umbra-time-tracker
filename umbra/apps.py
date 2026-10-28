"""bundle id -> a human name.

The `app` field an importer hands over is "the bundle id split on dots, last
segment", which for most apps is a meaningless word:

    com.tencent.xin        -> xin              WeChat
    com.xingin.discover    -> discover         Xiaohongshu
    com.google.chrome.ios  -> ios              Chrome
    com.typeless.mobile    -> mobile           Typeless (dictation)
    com.spotify.client     -> client           Spotify

This is not only ugly. These strings are **fed verbatim to the model** when it
judges what was going on — "phone foreground: ios" supports no conclusion at
all, "Chrome" does. Same shape as the fabricated "WHOOP workout": information
destroyed at the input stage, without an error.

WARNING: an unrecognised id **falls back to itself**; nothing is guessed. A
   plausible invented name is worse than an ugly raw one — you will never look
   at the first again, while the second is visibly unclaimed. No placeholder
   like "Unknown app" belongs in the table either; that disguises unclaimed as
   claimed.

## Two names, on purpose: name() and label()

    name(bundle)          canonical, English, fed to the model
    label(bundle, lang)   localised, shown to a person

They are split because the model-facing string is part of the **digest cache
key**. The digest is the text sent to the model, and inference is incremental
against a byte-for-byte comparison of it — so anything that changes that text
invalidates every historical day and re-runs inference over all of it at real
API cost.

Fused into one locale-dependent function, the first user who switched the
interface to another language would silently trigger a full re-inference of
their entire history. Split, the display language is free to change as often
as anyone likes and not one digest moves.

Which is also why the canonical column stays English even where the localised
name is the one the owner reads: canonical is a wire format, not a preference.
"""

# Only ids that are actually recognised. Matched exactly, never by prefix:
# a prefix match would conflate com.apple.mobilephone (Phone) with
# com.apple.mobileslideshow (Photos).
NAMES = {
    # Chat / social
    "com.tencent.xin": "WeChat",
    "com.tencent.xinWeChat": "WeChat",        # Mac build, same app, same name
    "com.apple.MobileSMS": "Messages",
    "com.larksuite.larkApp": "Lark",
    "com.larksuite.lark": "Lark",
    "com.larksuite.larkApp.iron": "Lark",
    "com.tdesktop.Telegram": "Telegram",
    "com.facebook.Messenger": "Messenger",
    "com.facebook.Facebook": "Facebook",
    "com.burbn.instagram": "Instagram",
    "com.xingin.discover": "Xiaohongshu",
    "com.douban.frodo": "Douban",
    "com.apple.InCallService": "Call",
    "com.apple.mobilephone": "Phone",
    "us.zoom.xos": "Zoom",

    # Browsers / mail
    "com.google.Chrome": "Chrome",
    "com.google.chrome.ios": "Chrome",        # phone build; must not read as "ios"
    "com.google.Gmail": "Gmail",
    "com.apple.webapp": "Web App",

    # Development / tools
    "com.googlecode.iterm2": "iTerm",
    "com.openai.codex": "Codex",
    "com.openai.chat": "ChatGPT",
    "com.anthropic.claudefordesktop": "Claude",
    "abnerworks.Typora": "Typora",
    "com.apple.finder": "Finder",
    "com.apple.Preview": "Preview",
    "com.apple.Notes": "Notes",
    "com.apple.Preferences": "Settings",
    "com.apple.systempreferences": "System Settings",
    "com.apple.control-center": "Control Center",
    "com.apple.springboard.today-view": "Widgets",

    # Typeless is a **dictation tool**. This entry matters more than most: the
    # stretches where it is active are exactly the stretches where idle
    # misreports "nobody here" — speaking produces no keyboard or mouse events.
    "com.typeless.mobile": "Typeless (dictation)",

    # Content
    "com.tencent.weread": "WeRead",
    "com.spotify.client": "Spotify",
    "com.apple.camera": "Camera",
    "com.apple.mobileslideshow": "Photos",
    "com.google.Maps": "Google Maps",
    "com.bloombuilt.dayone-mac": "Day One",
    "com.bloombuilt.dayone-ios": "Day One",

    # Other
    "com.teslamotors.TeslaApp": "Tesla",
    "com.whoop.iphone": "WHOOP",
    "com.taobao.taobao4iphone": "Taobao",
    "com.august.iossapp": "August Lock",
    "com.sp0n.vigilante": "Citizen",
    "com.capp.poplive": "Poplive",
}

# Display overrides, per language. Only the entries that differ need to appear;
# anything absent falls back to the canonical name above, which is why this
# table is short and stays short.
#
# Adding a language means adding a key here — no code changes. Note this is
# genuinely i18n (one name per locale, pick one), unlike the patterns in
# privacy.py and prompts.py, which match the user's own multilingual data and
# must all stay active at once regardless of locale.
LOCALE = {
    "zh": {
        "com.tencent.xin": "微信",
        "com.tencent.xinWeChat": "微信",
        "com.apple.MobileSMS": "信息",
        "com.larksuite.larkApp": "飞书",
        "com.larksuite.lark": "飞书",
        "com.larksuite.larkApp.iron": "飞书",
        "com.xingin.discover": "小红书",
        "com.douban.frodo": "豆瓣",
        "com.apple.InCallService": "通话",
        "com.apple.mobilephone": "电话",
        "com.apple.webapp": "网页 App",
        "com.apple.finder": "访达",
        "com.apple.Preview": "预览",
        "com.apple.Notes": "备忘录",
        "com.apple.Preferences": "设置",
        "com.apple.systempreferences": "系统设置",
        "com.apple.control-center": "控制中心",
        "com.apple.springboard.today-view": "小组件",
        "com.typeless.mobile": "Typeless（听写）",
        "com.tencent.weread": "微信读书",
        "com.apple.camera": "相机",
        "com.apple.mobileslideshow": "照片",
        "com.google.Maps": "Google 地图",
        "com.bloombuilt.dayone-mac": "Day One 日记",
        "com.bloombuilt.dayone-ios": "Day One 日记",
        "com.taobao.taobao4iphone": "淘宝",
        "com.august.iossapp": "August 门锁",
    },
}


def _fallback(bundle: str) -> str:
    return bundle.rsplit(".", 1)[-1] or bundle


def name(bundle: str | None) -> str:
    """The canonical name — **this is what the model sees**.

    Never locale-dependent. See the module docstring: this string is part of
    the digest cache key, and making it follow the interface language would
    turn a language toggle into a full re-inference of all history.
    """
    if not bundle:
        return ""
    return NAMES.get(bundle) or _fallback(bundle)


def label(bundle: str | None, lang: str = "en") -> str:
    """The name shown to a person. Falls back to canonical, then to the id."""
    if not bundle:
        return ""
    return (LOCALE.get(lang, {}).get(bundle)
            or NAMES.get(bundle)
            or _fallback(bundle))


def labels(lang: str = "en") -> dict:
    """bundle id -> display name, injected into the front end."""
    return {b: label(b, lang) for b in NAMES}
