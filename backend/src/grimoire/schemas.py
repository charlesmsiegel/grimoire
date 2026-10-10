"""The portable JSON Schema subset (spec 01f, 01f-C3): what a schema may
contain if it is to be sent both to OpenAI's strict mode and to Anthropic's
`output_config.format`, how a prompt spells one, and how a reply's JSON value
is read back.

A gateway leaf, and **standard library only**, so `decisions`, `inference`,
`prompts` and `store` code can all import it. `test_schemas.py` holds that by
the AST, as `test_wire.py` holds `wire.py`.

Three things live here, each once:

- **`check`**: the subset. Every node has a `type` (one of `TYPES`, never a
  type array -- a nullable field is `anyOf` with a `{"type": "null"}` branch)
  or an `anyOf`, not both. An object has `properties`, a `required` naming
  every one of them, and `additionalProperties: false`: strict mode requires
  both, so an optional field is `anyOf: [T, {"type": "null"}]`. An array has
  one `items` schema. Also allowed: `enum` (a non-empty list of strings on a
  string, or of integers on an integer), and `description` (a string). The
  root is an object. Everything else is refused -- numeric bounds (which
  Anthropic refuses), `minItems`/`maxItems`, `minLength`/`maxLength`,
  `pattern`, `format`, `$ref`/`$defs`, `oneOf`, `allOf`, `not`, `const`, and
  any key not named here. **The subset stays strict on purpose**: a bound a
  caller needs is dropped from the schema it sends and enforced by its own
  parser after the reply arrives. There is no bound-stripping helper, which
  would let a prompt promise a bound the wire never enforced. An empty `enum`
  is refused because strict mode answers it with a 400 naming
  `response_format`, which reads as the mode refused.
- **`render`**: the one spelling a prompt carries,
  `json.dumps(indent=2, sort_keys=True, ensure_ascii=False)` -- no HTML
  escaping, so the model reads "the character's", not "\\u0027". `prompts`
  registers it as the Jinja filter `schema_json`, so a template and
  `inference.generate`'s in-prompt check use the same function.
  (`decide/system.j2` keeps Jinja's `tojson`, whose golden bytes do not move.)
- **`find_value`**: the reply's top-level JSON value, tolerant of a fence or
  prose around it. It never raises.

The strict-mode budgets are here, and `decisions` re-exports them.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The types a node may name.
TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})

#: Every key the subset allows, on the node types it belongs to.
_ANY = frozenset({"type", "anyOf", "enum", "description"})
_OBJECT = frozenset({"properties", "required", "additionalProperties"})
_ARRAY = frozenset({"items"})
ALLOWED = _ANY | _OBJECT | _ARRAY

#: The keys the subset refuses because they bound a value: the caller's parser
#: enforces a bound instead (Anthropic refuses numeric bounds outright).
BOUNDS = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                    "multipleOf", "minItems", "maxItems", "minLength", "maxLength",
                    "pattern", "format"})

#: Enum values one schema may hold, summed over every enum in it. OpenAI's
#: strict mode documents "up to 1000 enum values across all enum properties"
#: (Structured Outputs, "Supported schemas", checked 2026-10-08), and
#: Anthropic documents no lower one.
MAX_ENUM_VALUES = 1000

#: Strict mode's string budgets, from the same page, checked 2026-10-08: "For a
#: single enum property with string values, the total string length of all
#: enum values cannot exceed 15,000 characters when there are more than 250
#: enum values", and "the total string length of all property names,
#: definition names, enum values, and const values cannot exceed 120,000
#: characters". The subset has no definitions and no consts, so the second
#: counts property names and enum strings (`tally`).
MAX_ENUM_STRING_CHARS = 15_000
ENUM_STRING_CHARS_ABOVE = 250
MAX_SCHEMA_STRING_CHARS = 120_000

#: Strict mode's other schema limits (same page, checked 2026-10-08): "A
#: schema may have up to 5000 object properties total, with up to 10 levels
#: of nesting." Depth (`depth`) counts the schema nodes on a path from the
#: root through `properties` values and `items`; an `anyOf` branch is an
#: alternative at its parent's depth. A decision batch is four deep (batch,
#: item, answers, a question's value).
MAX_SCHEMA_PROPERTIES = 5000
MAX_DEPTH = 10


class SchemaError(ValueError):
    """A schema outside the portable subset (`check`)."""


def check(schema: dict) -> None:
    """Raise `SchemaError` unless `schema` is inside the portable subset (the
    module docstring), its root an object, its nesting within `MAX_DEPTH`
    and every strict-mode budget kept."""
    if not isinstance(schema, dict):
        raise SchemaError("a schema is a JSON object")
    if schema.get("type") != "object" or "anyOf" in schema:
        raise SchemaError("the root of a schema must be an object (type: object)")
    enums = _node(schema, "$", 1)
    if enums > MAX_ENUM_VALUES:
        raise SchemaError(f"the schema holds {enums} enum values; strict mode "
                          f"carries at most {MAX_ENUM_VALUES}")
    properties, chars = tally(schema)
    if properties > MAX_SCHEMA_PROPERTIES:
        raise SchemaError(f"the schema holds {properties} object properties; strict "
                          f"mode carries at most {MAX_SCHEMA_PROPERTIES}")
    if chars > MAX_SCHEMA_STRING_CHARS:
        raise SchemaError(f"the schema's property names and enum values total {chars} "
                          f"characters; strict mode carries at most "
                          f"{MAX_SCHEMA_STRING_CHARS}")


def _node(node: object, path: str, level: int) -> int:
    """Check one node at nesting `level`, and return how many enum values it
    and everything under it hold."""
    if not isinstance(node, dict):
        raise SchemaError(f"{path}: a schema is a JSON object")
    if level > MAX_DEPTH:
        raise SchemaError(f"{path}: the schema nests past {MAX_DEPTH} levels, "
                          "strict mode's limit")
    _keys(node, path)
    if "anyOf" in node:
        return _any_of(node, path, level)
    kind = _type(node, path)
    enums = _enum(node, path, kind) if "enum" in node else 0
    if kind == "object":
        return enums + _object(node, path, level)
    if kind == "array":
        return enums + _node_items(node, path, level)
    return enums


def _keys(node: dict, path: str) -> None:
    """Refuse a key outside the subset, a non-string description, and a node
    naming neither or both of `type` and `anyOf`."""
    for key in node:
        if key in BOUNDS:
            raise SchemaError(f"{path}: {key!r} is a bound, which the portable subset "
                              "refuses; enforce it in the reply's parser instead")
        if key not in ALLOWED:
            raise SchemaError(f"{path}: {key!r} is not in the portable schema subset")
    if "description" in node and not isinstance(node["description"], str):
        raise SchemaError(f"{path}: 'description' must be a string")
    if ("type" in node) == ("anyOf" in node):
        raise SchemaError(f"{path}: a schema names a 'type' or an 'anyOf', and not both")


def _type(node: dict, path: str) -> str:
    """The node's one type, with the keywords of other types refused on it."""
    kind = node["type"]
    if isinstance(kind, list):
        raise SchemaError(f"{path}: a type array is refused; a nullable field is "
                          "anyOf with a {\"type\": \"null\"} branch")
    if not isinstance(kind, str) or kind not in TYPES:
        raise SchemaError(f"{path}: unknown type {kind!r}")
    for key in _OBJECT if kind != "object" else ():
        if key in node:
            raise SchemaError(f"{path}: {key!r} belongs on an object, not a {kind}")
    if kind != "array" and "items" in node:
        raise SchemaError(f"{path}: 'items' belongs on an array, not a {kind}")
    return kind


def _any_of(node: dict, path: str, level: int) -> int:
    branches = node["anyOf"]
    for key in ("enum", *sorted(_OBJECT | _ARRAY)):
        if key in node:
            raise SchemaError(f"{path}: {key!r} belongs on a typed schema, not an anyOf")
    if not isinstance(branches, list) or not branches:
        raise SchemaError(f"{path}: 'anyOf' is a non-empty list of schemas")
    # An alternative at its parent's depth, not a level of its own.
    return sum(_node(branch, f"{path}.anyOf[{i}]", level)
               for i, branch in enumerate(branches))


def _enum(node: dict, path: str, kind: str) -> int:
    values = node["enum"]
    if not isinstance(values, list):
        raise SchemaError(f"{path}: 'enum' must be a list")
    if not values:
        raise SchemaError(f"{path}: an empty 'enum' is refused (strict mode answers it "
                          "with a 400); send the field as a plain type instead")
    if kind == "string":
        if not all(isinstance(v, str) for v in values):
            raise SchemaError(f"{path}: a string 'enum' holds strings only")
        chars = sum(len(v) for v in values)
        if len(values) > ENUM_STRING_CHARS_ABOVE and chars > MAX_ENUM_STRING_CHARS:
            raise SchemaError(f"{path}: an enum of {len(values)} values totals {chars} "
                              f"characters; past {ENUM_STRING_CHARS_ABOVE} values "
                              f"strict mode carries at most {MAX_ENUM_STRING_CHARS}")
    elif kind == "integer":
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            raise SchemaError(f"{path}: an integer 'enum' holds integers only")
    else:
        raise SchemaError(f"{path}: 'enum' is for strings or integers, not a {kind}")
    if len(set(values)) != len(values):
        raise SchemaError(f"{path}: an 'enum' lists each value once")
    return len(values)


def _object(node: dict, path: str, level: int) -> int:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        raise SchemaError(f"{path}: an object has 'properties'")
    if node.get("additionalProperties") is not False:
        raise SchemaError(f"{path}: an object has 'additionalProperties': false "
                          "(strict mode requires it)")
    required = node.get("required")
    if (not isinstance(required, list) or not all(isinstance(r, str) for r in required)
            or len(set(required)) != len(required) or set(required) != set(properties)):
        raise SchemaError(f"{path}: 'required' names every property exactly once, "
                          "as strings (strict mode requires it; an optional field is "
                          "anyOf with null)")
    total = 0
    for name, sub in properties.items():
        if not isinstance(name, str):
            raise SchemaError(f"{path}: a property name is a string, not {name!r}")
        total += _node(sub, f"{path}.{name}", level + 1)
    return total


def _node_items(node: dict, path: str, level: int) -> int:
    items = node.get("items")
    if not isinstance(items, dict):
        raise SchemaError(f"{path}: an array has one 'items' schema")
    return _node(items, f"{path}[]", level + 1)


def depth(schema: object) -> int:
    """How many levels `schema` nests (`MAX_DEPTH`'s count): 1 for a leaf, one
    more for each `properties` value or `items` below it, an `anyOf` branch
    at its parent's level."""
    if not isinstance(schema, dict):
        return 0
    if isinstance(schema.get("anyOf"), list):
        return max((depth(b) for b in schema["anyOf"]), default=0)
    below = []
    if isinstance(schema.get("properties"), dict):
        below.extend(schema["properties"].values())
    if isinstance(schema.get("items"), dict):
        below.append(schema["items"])
    return 1 + max((depth(sub) for sub in below), default=0)


def tally(node: object) -> tuple[int, int]:
    """(properties, characters) of a schema as strict mode counts them: every
    entry of every `properties` mapping, and the characters of its key and of
    every string `enum` value. A key under `properties` is a name, never a
    keyword, so a property called `enum` is counted as a property."""
    properties = chars = 0
    below: list[object] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                properties += len(value)
                chars += sum(len(name) for name in value)
                below.extend(value.values())
            elif key == "enum" and isinstance(value, list):
                chars += sum(len(v) for v in value if isinstance(v, str))
            else:
                below.append(value)
    elif isinstance(node, list):
        below.extend(node)
    for sub in below:
        p, c = tally(sub)
        properties, chars = properties + p, chars + c
    return properties, chars


def render(schema: dict) -> str:
    """The one spelling of `schema` a prompt carries: sorted keys, indented
    two, and no escaping (the model should read "the character's", not
    "\\u0027"). The prompts filter `schema_json` is this function."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False)


# --- reading a reply ----------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _first_wins(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    """An object that keeps a key's first occurrence, where JSON's default
    keeps the last. Both continuity parsers always did, and a twin of the same
    shape must not read differently."""
    out: dict[Any, Any] = {}
    for key, value in pairs:
        out.setdefault(key, value)
    return out


def _loads(text: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_first_wins)
    except (ValueError, RecursionError):
        return None


def _span(text: str, lo: str, hi: str) -> tuple[int, str] | None:
    start, end = text.find(lo), text.rfind(hi)
    return (start, text[start:end + 1]) if start != -1 and end > start else None


def find_value(text: str, *, arrays: bool = True) -> dict | list | None:
    """The reply's top-level JSON value: the whole text, else a leading
    fence's body, else the span from the first opening bracket to the last
    matching closer -- the object's (`{`...`}`) and the array's (`[`...`]`),
    the one that opens first tried first. The first candidate that decodes to
    an object (or, with `arrays`, an array) wins; None when none does. A
    repeated key, at any level, keeps its first value. Never raises.

    `arrays=False` is `decisions.find_object`'s reading, byte for byte: only
    an object counts, so `[{...}]` reads the object inside it through the
    brace span (`decisions.parse` reads a list holding one object)."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith("```") and (fence := _FENCE.match(stripped)):
        candidates.append(fence.group(1).strip())
    spans = [s for s in (_span(stripped, "{", "}"),
                         _span(stripped, "[", "]") if arrays else None) if s is not None]
    candidates.extend(body for _start, body in sorted(spans, key=lambda s: s[0]))
    for candidate in candidates:
        value = _loads(candidate)
        if isinstance(value, dict) or (arrays and isinstance(value, list)):
            return value
    return None
