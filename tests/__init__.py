"""Every test run gets its own empty config, data and log directories.

Without this the suite read the real ~/.config/umbra/config.toml: someone with
`lang = "zh"` saw six prompt tests fail, and a test drifting with the machine's
config is irreproducible. Set here, before any test module imports umbra —
paths are resolved once, at import.

This file only runs when tests is imported as a package, which is what
`-t .` does: run the suite as `python -m unittest discover -s tests -t .`.
"""

import atexit
import os
import shutil
import tempfile

_root = tempfile.mkdtemp(prefix="umbra-test-")
atexit.register(shutil.rmtree, _root, ignore_errors=True)
for _var in ("UMBRA_CONFIG_DIR", "UMBRA_DATA_DIR", "UMBRA_LOG_DIR"):
    os.environ[_var] = os.path.join(_root, _var.split("_")[1].lower())
