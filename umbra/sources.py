"""The source registry: built-ins plus your own, and whether each can run now.

A source is a module, and the whole protocol is expressed as module attributes
(see the module docstring of tests/test_sources.py). This file does three
things:

    discover()      find every source (built-in + ~/.config/umbra/sources/*.py)
    skip_reason()   say, in plain words, why one cannot run on this platform
    status()        answer each REQUIRES item — shared by setup and doctor

## Requirement answers are three-valued

probe() returns (ok, hint) where ok is True / False / **None**.
None means "cannot say" — Full Disk Access has no reliable probe. With only two
values, either "not granted" or "ready" has to be impersonated, and the setup
wizard prints a ✓ or ✗ based on it. Showing the wrong one is worse than showing
nothing.
"""

import importlib.util
import os
import shutil
import sys

from . import config
from .importers import ALL


def probe(req: str) -> tuple[bool | None, str]:
    """Is this requirement satisfied right now? (ok, what is missing)."""
    kind, _, arg = req.partition(":")
    if kind == "env":
        if os.environ.get(arg):
            return True, ""
        return False, f"env var {arg} is not set (usually lives in your env file)"
    if kind == "file":
        path = os.path.expanduser(arg)
        if os.path.exists(path):
            return True, ""
        return False, f"missing file {arg}"
    if kind == "cli":
        if shutil.which(arg):
            return True, ""
        return False, f"command {arg} not found on PATH"
    if kind == "fda":
        # Full Disk Access is only knowable by actually reading a protected
        # file, which is exactly what collect() does. No answer is invented here.
        return None, "needs Full Disk Access (System Settings → Privacy & Security)"
    return None, f"unknown requirement {req!r}"


def conf(name: str) -> dict:
    """[sources.<name>] from config.toml; absent is empty."""
    return ((config.load().get("sources") or {}).get(name)) or {}


def enabled(name: str) -> bool:
    """`[sources.<name>] enabled = false` turns one off. On unless said so —
    a source that is not set up already stands aside on its own (missing()),
    so this switch is for one you *could* run and do not want."""
    return conf(name).get("enabled", True) is not False


def missing(mod) -> list[str]:
    """What this source is definitely missing, in words; empty when it can
    run. An unknown answer (Full Disk Access) is not missing: the import is
    the only probe, so it gets to try."""
    return [hint or req for req, ok, hint in status(mod) if ok is False]


def aside() -> tuple[set[str], set[str]]:
    """(off, not set up): the sources standing aside, by the name they write
    into events (knowledgec writes "app"). Off covers turned off and not for
    this platform.

    Their old import failures are history, not news. Without this, a source
    someone never meant to use (Butler, here) sat in "Needs you" with a red
    dot on the tab, for a problem nobody has. The caller decides what not set
    up means for a source that *used to* work — that one lost something
    (a token file, a key) and is worth saying so.
    """
    found, _ = discover()
    off, unset = set(), set()
    for name, mod in found.items():
        src = getattr(mod, "SOURCE", None) or name
        if skip_reason(mod) or not enabled(name):
            off.add(src)
        elif missing(mod):
            unset.add(src)
    return off, unset


def skip_reason(mod) -> str | None:
    """Why this source cannot run on the current platform; None if it can."""
    plat = getattr(mod, "PLATFORM", None)
    if plat is None or sys.platform.startswith(plat):
        return None
    human = {"darwin": "macOS", "linux": "Linux", "win32": "Windows"}.get(plat, plat)
    return f"this data only exists on {human}"


def status(mod) -> list[tuple[str, bool | None, str]]:
    """Answer every requirement. A module's own check() wins if it has one."""
    check = getattr(mod, "check", None)
    if callable(check):
        return list(check())
    return [(r, *probe(r)) for r in getattr(mod, "REQUIRES", [])]


def load_file(path: str, name: str | None = None):
    """Load one user-supplied .py as a module, namespaced so it cannot collide
    with a real package name.

    Public because the plugin registry needs the same service. A loader is not
    an internal of source discovery; it is the one place that decides how a file
    a user wrote becomes something importable, and there should be one of those.

    `name` is explicit for a package-shaped plugin, whose file is __init__.py —
    deriving the name from the filename would call every one of them "__init__".
    """
    name = name or os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(f"umbra_user_source.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def discover(user_dir: str | None = None):
    """Every source: (name -> module, name -> why it failed to load).

    A user file **overrides a built-in of the same name**, and that is a
    feature: unhappy with the built-in chrome filtering, drop in your own
    chrome.py and it is replaced, with no fork of the repo.

    A broken file is skipped and recorded rather than taking the others down
    with it — but the error must stay visible, because a silent skip leaves
    "why didn't my source run?" with no answer. A file with no collect() counts
    as an error too: that is almost certainly a mistake, not someone
    deliberately parking a non-source in the sources directory.
    """
    found = dict(ALL)
    errors: dict[str, str] = {}
    d = user_dir if user_dir is not None else config.SOURCES_DIR
    if not os.path.isdir(d):
        return found, errors
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        name = fn[:-3]
        try:
            mod = load_file(os.path.join(d, fn))
        except Exception as e:                          # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {e}"
            continue
        if not callable(getattr(mod, "collect", None)):
            errors[name] = "has no collect(since_ms) function — not a source"
            continue
        found[name] = mod
    return found, errors


# The old private name, kept so nothing that reached for it breaks while it
# stops being the thing to reach for.
_load_user = load_file
