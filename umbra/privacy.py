"""Privacy filtering, applied at import time rather than cleaned up afterwards.

From real data: a single 45-minute window of ordinary browsing already contained
a bank's account dashboard, a password manager, and the bank's OAuth sign-on
page. This is not a hypothetical risk.

Three tiers:
  DROP   discarded entirely; not one byte reaches the store
  REDACT keep the timestamp and the host, replace the content with a
         placeholder (which preserves the useful fact that this stretch of
         time was spent dealing with the bank)
  KEEP   as-is
"""

import re
from urllib.parse import urlparse

DROP, REDACT, KEEP = "drop", "redact", "keep"

# Never recorded — password managers and authentication flows.
_DROP_HOST = re.compile(
    r"(dashlane|1password|lastpass|bitwarden|keeper|accounts\.google\.com/signin"
    r"|login\.microsoftonline|okta\.com|duosecurity)",
    re.I,
)
# Patterns stay multilingual even though the prose around them is English.
# These match *the user's own data*, not this source code: someone browsing in
# Chinese sees 两步验证 on the page that must not be recorded. Dropping that
# pattern would be a privacy regression, not a translation — so every
# language's patterns are active at once, and none of them are switched by
# locale. Add your own below.
#
# ⚠️ Word boundaries are load-bearing, not tidiness. Without \b this pattern
# matched any title *containing* the letters:
#
#     "Design inspiration"   → de|sign in|spiration   → dropped
#     "Design in Figma"      → de|sign in| Figma      → dropped
#     "Cataloging books"     → cata|login|g books     → dropped
#
# Every one of those was deleted at import and never reached the store, with
# no error and nothing downstream to notice. Over-matching here is the quieter
# of the two failures and the worse one: a leak is at least discoverable, while
# silently erased history cannot be told apart from history that never
# happened. Widen this pattern only with a test alongside.
#
# The CJK alternatives sit outside the \b group on purpose: \w matches CJK
# characters, so a boundary between two of them does not exist and \b would
# stop the pattern matching mid-sentence.
_DROP_TITLE = re.compile(
    r"(?:\b(?:dashlane|1password|sign[- ]?in(?:to)?|log[- ]?in|oauth|password"
    r"|two[- ]factor|2fa)\b"
    r"|verify your identity"
    r"|两步验证|验证码)",
    re.I,
)

# Host kept, content dropped — banking, taxes, health.
_REDACT_HOST = re.compile(
    r"(citi(bank|cards)?\.com|chase\.com|bankofamerica|wellsfargo|schwab|fidelity"
    r"|vanguard|paypal|venmo|monarchmoney|mint\.com|turbotax|irs\.gov"
    r"|myhealth|kaiserpermanente|healthcare\.gov)",
    re.I,
)

# App-level blocklist, for knowledgeC and the live collector.
_DROP_BUNDLE = re.compile(r"(dashlane|1password|com\.agilebits|keeper)", re.I)


def classify_url(url: str, title: str = "") -> str:
    host = (urlparse(url).hostname or "")
    if _DROP_HOST.search(host) or _DROP_HOST.search(url) or _DROP_TITLE.search(title):
        return DROP
    if _REDACT_HOST.search(host):
        return REDACT
    return KEEP


def classify_bundle(bundle_id: str | None) -> str:
    return DROP if _DROP_BUNDLE.search(bundle_id or "") else KEEP


def apply_url(url: str, title: str) -> tuple[str, str] | None:
    """Returns (url, title), or None meaning discard."""
    verdict = classify_url(url, title)
    if verdict == DROP:
        return None
    if verdict == REDACT:
        host = urlparse(url).hostname or "?"
        return (f"https://{host}/[redacted]", f"[{host}]")
    return (url, title)
