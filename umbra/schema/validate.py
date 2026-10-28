"""Validate a value against a definition in api.schema.json. Stdlib only.

Covers the keywords the schema uses and nothing else. An unknown keyword is an
error, not a pass: a validator that skips what it does not understand turns
every new constraint into one that holds in TypeScript and is never checked in
Python — the drift this file exists to stop.
"""

import re

from . import load

# Annotations: they describe, they do not constrain.
_NOTES = {"$schema", "$id", "title", "description", "$defs"}
_KNOWN = _NOTES | {"type", "properties", "required", "additionalProperties",
                   "items", "prefixItems", "minItems", "maxItems",
                   "minLength", "maxLength", "pattern", "enum", "$ref"}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    # bool is an int in Python; JSON says it is not.
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class Invalid(ValueError):
    pass


def validate(value, defn: str, root: dict | None = None) -> None:
    """Raise Invalid with a JSON-pointer-ish path at the first violation."""
    root = root or load()
    _check(value, {"$ref": f"#/$defs/{defn}"}, root, defn)


def errors(value, defn: str, root: dict | None = None) -> list[str]:
    try:
        validate(value, defn, root)
    except Invalid as e:
        return [str(e)]
    return []


def bcat_errors(day: dict) -> list[str]:
    """The one Day constraint JSON Schema cannot say: bcat has a slot per five
    minutes of *this* day, rounded up — 288 only on an ordinary one."""
    want = -(-day["n_min"] // 5)
    got = len(day["bcat"])
    return [] if got == want else [f"Day.bcat: {got} slots for a {day['n_min']}-minute day, want {want}"]


def _resolve(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise Invalid(f"only local refs are supported: {ref}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(v, s: dict, root: dict, at: str) -> None:
    unknown = set(s) - _KNOWN
    if unknown:
        raise Invalid(f"{at}: schema uses unsupported keyword(s) {sorted(unknown)}")
    if "$ref" in s:
        _check(v, _resolve(s["$ref"], root), root, at)
    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_TYPES[t](v) for t in types):
            raise Invalid(f"{at}: expected {'|'.join(types)}, got {type(v).__name__}")
    if "enum" in s and v not in s["enum"]:
        raise Invalid(f"{at}: {v!r} not in {s['enum']}")
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            raise Invalid(f"{at}: shorter than {s['minLength']}")
        if "maxLength" in s and len(v) > s["maxLength"]:
            raise Invalid(f"{at}: longer than {s['maxLength']}")
        if "pattern" in s and not re.search(s["pattern"], v):
            raise Invalid(f"{at}: {v!r} does not match {s['pattern']}")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            raise Invalid(f"{at}: fewer than {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            raise Invalid(f"{at}: more than {s['maxItems']} items")
        pre = s.get("prefixItems") or []
        for i, item in enumerate(v):
            if i < len(pre):
                _check(item, pre[i], root, f"{at}[{i}]")
            elif "items" in s:
                _check(item, s["items"], root, f"{at}[{i}]")
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                raise Invalid(f"{at}: missing required {k!r}")
        props = s.get("properties", {})
        extra = s.get("additionalProperties", True)
        for k, item in v.items():
            if k in props:
                _check(item, props[k], root, f"{at}.{k}")
            elif extra is False:
                raise Invalid(f"{at}: unexpected property {k!r}")
            elif isinstance(extra, dict):
                _check(item, extra, root, f"{at}.{k}")
