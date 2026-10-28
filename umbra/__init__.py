"""Umbra — your scattered digital footprints, on one timeline."""

import sys

# Before anything else is imported: the rest of the package uses 3.10+ syntax,
# and on an older Python it dies with a TypeError about `|` that says nothing
# about the version. pip refuses the install (requires-python), but a clone
# run as `python3 -m umbra` never asks pip. Keep this file 3.6-parseable.
if sys.version_info < (3, 11):
    sys.exit("Umbra needs Python 3.11 or newer; this is {} ({}).".format(
        sys.version.split()[0], sys.executable))

# The one source of the version: pyproject.toml reads it from here
# (dynamic = ["version"]).
__version__ = "0.1.0"
