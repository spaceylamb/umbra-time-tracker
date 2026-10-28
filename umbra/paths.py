"""Where Umbra keeps things, per platform.

Three directories, three different jobs. Keeping them apart is not tidiness —
each one has a different backup, sharing and version-control story:

    data      events.db, generated pages, OAuth tokens
              Regenerable-ish, highly sensitive, must never reach a repo.
    config    config.toml, your own sources, places.json
              Written by you. People often want this in their dotfiles repo.
    logs      scheduled-job output
              Disposable, but "the log from two runs ago" is exactly what you
              need when chasing a silent failure — so not /tmp, which reboots
              clear.

## Why macOS does not follow XDG

On macOS the native location wins, even when XDG_DATA_HOME happens to be set.
Every existing install already has its database at
~/Library/Application Support/Umbra; honouring XDG there would silently move
one user's only copy of their data to a new path and leave them looking at an
empty timeline. A relocation must be an explicit act, which is what
UMBRA_DATA_DIR is for.

## Overrides

UMBRA_DATA_DIR / UMBRA_CONFIG_DIR / UMBRA_LOG_DIR beat everything, on every
platform. They exist for tests, for people who keep their home directory on a
synced volume, and for anyone who wants the database somewhere encrypted.
"""

import os
import sys


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return os.path.expanduser(v) if v else None


def _home(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


def _xdg(var: str, default: str, *parts: str) -> str:
    """An XDG base dir, falling back to the spec's own default.

    The spec says a relative value must be ignored rather than resolved
    against the home directory — a relative XDG_DATA_HOME would otherwise put
    the database wherever the process happened to be started from, which for a
    scheduled job is not a place anyone would think to look.
    """
    base = os.environ.get(var)
    if not base or not os.path.isabs(base):
        base = _home(default)
    return os.path.join(base, *parts)


def data_dir() -> str:
    """Database, generated pages, OAuth tokens."""
    return (_env("UMBRA_DATA_DIR")
            or (_home("Library", "Application Support", "Umbra")
                if sys.platform == "darwin" else
                os.path.join(os.environ.get("LOCALAPPDATA")
                             or _home("AppData", "Local"), "Umbra")
                if sys.platform == "win32" else
                _xdg("XDG_DATA_HOME", ".local/share", "umbra")))


def config_dir() -> str:
    """config.toml, your own sources, places.json.

    Unlike data, this is XDG on macOS too: ~/.config is where the existing
    installs already keep it, and it is what people expect to symlink out of a
    dotfiles repo.
    """
    return (_env("UMBRA_CONFIG_DIR")
            or (os.path.join(os.environ.get("APPDATA")
                             or _home("AppData", "Roaming"), "Umbra")
                if sys.platform == "win32" else
                _xdg("XDG_CONFIG_HOME", ".config", "umbra")))


def log_dir() -> str:
    """Scheduled-job stdout/stderr."""
    return (_env("UMBRA_LOG_DIR")
            or (_home("Library", "Logs", "Umbra")
                if sys.platform == "darwin" else
                os.path.join(data_dir(), "logs")
                if sys.platform == "win32" else
                _xdg("XDG_STATE_HOME", ".local/state", "umbra", "logs")))
