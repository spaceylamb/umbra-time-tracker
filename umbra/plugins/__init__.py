"""The plugins Umbra ships with.

They load through exactly the same path as a plugin someone drops into their own
config directory. That is the point of shipping them here rather than in an
examples/ directory at the repo root: if the loader cannot load its own, it
cannot load anyone's, and nothing else would reveal that.

A user file of the same name wins over one of these, matching how a user source
overrides a built-in. Whoever wrote the file in their own directory meant it.

Being bundled is not being enabled. Enablement is a config decision and each
plugin has its own default — goals on, the two that send off — and sending
anywhere is a second switch again. See umbra/sink.py.
"""

import os

DIR = os.path.dirname(os.path.abspath(__file__))
