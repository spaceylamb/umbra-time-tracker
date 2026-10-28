"""Where configuration lands.

~/.config/umbra/ rather than the data directory, and the split is practical
rather than tidy: everything in the data directory is regenerable, sensitive,
and must never reach version control; everything here is the opposite — people
generally want it in their dotfiles repo. Mixed together, neither can be
managed as a whole.

See paths.py for what each directory resolves to per platform.
"""

import os

from . import paths

CONFIG_DIR = paths.config_dir()
SOURCES_DIR = os.path.join(CONFIG_DIR, "sources")
# Analyzers and Sinks. Separate from sources because they answer different
# questions: a source says where data comes from, a plugin says what is made of
# it and where the result goes. Someone adding one should not have to work out
# which of the two their file is.
PLUGINS_DIR = os.path.join(CONFIG_DIR, "plugins")


def load() -> dict:
    """~/.config/umbra/config.toml; an absent file is an empty config.

    Malformed TOML **must raise** rather than return empty. Returning empty
    means an edit silently fails to take effect, and there is no path from the
    symptom ("why is it still calling Gemini?") back to the cause (a missing
    quote on line 3).
    """
    import tomllib

    path = os.path.join(CONFIG_DIR, "config.toml")
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise RuntimeError(f"config.toml has a syntax error: {e} ({path})") from e
