"""The API contract (api.schema.json) and a stdlib validator for it.

ADR 0001: one JSON Schema at the boundary generates the TypeScript types and
validates at the Python edge, so the two languages cannot drift quietly.
`dependencies = []` rules out the `jsonschema` package, so validate.py covers
exactly the keywords api.schema.json uses — and refuses any it does not know,
so the schema cannot grow a keyword that is silently ignored here.
"""

import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.schema.json")


def load() -> dict:
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)
