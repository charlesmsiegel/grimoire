"""The portable JSON Schema subset, its one prompt spelling, and a tolerant
reader of a reply (spec 01f, 01f-C3).

A schema here is what a call may hand a provider's structured mode -- OpenAI's
strict mode and Anthropic's `output_config.format` -- and so it has to be
inside what BOTH accept. `check` holds that rule, and it is the only copy of
it: a decision batch's schema (`decisions.schema`) passes it, and so must
every schema `inference.generate(schema=)` sends.

The subset is the decisions subset widened by exactly what a generation
needs (arrays and numbers):

- `type` is one of `TYPES`, never a type array: nullable is `anyOf` with a
  `{"type": "null"}` branch.
- An object has `properties`, a `required` naming EVERY property, and
  `additionalProperties: false` (strict mode requires both); an optional
  field is `anyOf: [T, {"type": "null"}]`.
- An array has `items`, one schema.
- Also allowed: `enum` (strings or integers, never empty), `anyOf`, and
  `description` (a string).

Everything else is refused, bounds included (`minimum`, `maxItems`,
`maxLength`, `pattern`, `format`, ...): Anthropic refuses numeric bounds, and
a bound the wire never enforced would be a promise the prompt makes alone. A
caller that needs one drops it from the schema it sends and enforces it in
its own parser. An empty `enum` is refused too: strict mode answers it with a
400 naming `response_format`, which would read as the mode refused.

`render` is the one spelling a prompt carries, registered as the prompts
filter `schema_json` (`prompts._env`), so a template and
`inference.generate`'s in-prompt check use the same function. `find_value`
reads a reply back and never raises: structured mode makes a conforming reply
likely, never certain, and an attempt sent without it guarantees nothing.

A gateway leaf, and **standard library only**: `decisions`, `inference`,
`prompts` and `store` code all import it, so it may import none of them.
`test_schemas.py` holds that by the AST, as `test_wire.py` holds `wire.py`.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The types a schema node may name.
TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})

#: Every key a schema node may carry. Anything else is refused.
KEYS = frozenset({"type", "properties", "required", "additionalProperties", "items",
                  "enum", "anyOf", "description"})

#: Keys that are a bound or a format the portable subset does not carry. Named
#: apart so the refusal can say what to do instead.
BOUNDS = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                    "multipleOf", "minItems", "maxItems", "minLength", "maxLength",
                    "pattern", "format", "minProperties", "maxProperties"})

#: Enum values one schema may hold, summed over every enum in it. OpenAI's
#: strict mode documents "up to 1000 enum values across all enum properties"
#: (Structured Outputs, "Supported schemas", checked 2026-10-08), and Anthropic
#: documents no lower one. `decisions` re-exports it: a batch is chunked under
#: it, and an item that alone exceeds it is refused.
MAX_ENUM_VALUES = 1000

#: Strict mode's string budgets, from the same page, checked 2026-10-08: "For a
#: single enum property with string values, the total string length of all
#: enum values cannot exceed 15,000 characters when there are more than 250
#: enum values", and "the total string length of all property names,
#: definition names, enum values, and const values cannot exceed 120,000
#: characters". The first is per enum; the second is per schema, counted as
#: property names and string enum values (`tally`) -- this subset has no
#: definitions and no consts.
MAX_ENUM_STRING_CHARS = 15_000
ENUM_STRING_CHARS_ABOVE = 250
MAX_SCHEMA_STRING_CHARS = 120_000

#: Strict mode's object-property limit (same page, checked 2026-10-08): "A
#: schema may have up to 5000 object properties total, with up to 10 levels of
#: nesting."
MAX_SCHEMA_PROPERTIES = 5000

#: The nesting limit from that same sentence: an object or array node counts a
#: level, an `anyOf` branch sits at its parent's level. A decision batch is
#: four deep (batch, item, answers, a question's value).
MAX_DEPTH = 10


class SchemaError(ValueError):
    """A schema outside the portable subset (`check`)."""


def check(schema: dict) -> None:
    """Raise `SchemaError` unless `schema` is inside the portable subset (the
    module docstring) and within strict mode's budgets."""
    _node(schema, "$", 1)
    properties, chars = tally(schema)
    if properties > MAX_SCHEMA_PROPERTIES:
        raise SchemaError(f"the schema has {properties} object properties; "
                          f"strict mode takes at most {MAX_SCHEMA_PROPERTIES}")
    if chars > MAX_SCHEMA_STRING_CHARS:
        raise SchemaError(f"the schema's property names and enum values total {chars} "
                          f"characters; strict mode takes at most {MAX_SCHEMA_STRING_CHARS}")
    values = _enum_values(schema)
    if values > MAX_ENUM_VALUES:
        raise SchemaError(f"the schema's enums hold {values} values; strict mode takes "
                          f"at most {MAX_ENUM_VALUES}")


def _node(node: object, where: str, depth: int) -> None:
    """Check one schema node at `where` (a JSON-path-ish label for the error),
    `depth` levels down."""
    if not isinstance(node, dict):
        raise SchemaError(f"{where}: a schema is an object, not {type(node).__name__}")
    if depth > MAX_DEPTH:
        raise SchemaError(f"{where}: nested past strict mode's {MAX_DEPTH} levels")
    _keys(node, where)
    if "anyOf" in node:
        _any_of(node, where, depth)
        return
    kind = _type(node, where)
    if kind == "object":
        _object(node, where, depth)
    elif kind == "array":
        if "items" not in node:
            raise SchemaError(f"{where}: an array names its 'items'")
        _node(node["items"], f"{where}.items", depth + 1)
    if "enum" in node:
        _enum(node["enum"], kind, where)


def _type(node: dict, where: str) -> str:
    """The node's one `type`, once its keys are the ones that type carries."""
    kind = node.get("type")
    if not isinstance(kind, str):
        raise SchemaError(f"{where}: needs a single 'type' (nullable is anyOf with a "
                          "null branch, never a type array)")
    if kind not in TYPES:
        raise SchemaError(f"{where}: unknown type {kind!r}")
    extra = set(node) - {"type", "description"} - _OWN_KEYS.get(kind, frozenset())
    if extra:
        raise SchemaError(f"{where}: a {kind} carries no {sorted(extra)}")
    return kind


def _keys(node: dict, where: str) -> None:
    """Refuse a key outside the subset -- a bound by name -- and a
    description that is not a string."""
    for key in node:
        if key in BOUNDS:
            raise SchemaError(f"{where}: {key!r} is a bound the portable subset does not "
                              "carry; enforce it in the reply's parser instead")
        if key not in KEYS:
            raise SchemaError(f"{where}: {key!r} is outside the portable subset")
    if "description" in node and not isinstance(node["description"], str):
        raise SchemaError(f"{where}: a description is a string")


def _any_of(node: dict, where: str, depth: int) -> None:
    """An `anyOf` node: its branches, each at this node's own level."""
    extra = set(node) - {"anyOf", "description"}
    if extra:
        raise SchemaError(f"{where}: an anyOf carries no {sorted(extra)}")
    branches = node["anyOf"]
    if not isinstance(branches, list) or not branches:
        raise SchemaError(f"{where}: anyOf is a non-empty list of schemas")
    for index, branch in enumerate(branches):
        _node(branch, f"{where}.anyOf[{index}]", depth)


#: The keys each type carries beside `type` and `description`.
_OWN_KEYS = {"object": frozenset({"properties", "required", "additionalProperties"}),
             "array": frozenset({"items"}),
             "string": frozenset({"enum"}),
             "integer": frozenset({"enum"})}


def _object(node: dict, where: str, depth: int) -> None:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        raise SchemaError(f"{where}: an object names its 'properties'")
    if node.get("additionalProperties") is not False:
        raise SchemaError(f"{where}: an object sets additionalProperties: false")
    required = node.get("required")
    if (not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
            or len(set(required)) != len(required)
            or set(required) != set(properties)):
        raise SchemaError(f"{where}: 'required' names every property, once (an optional "
                          "field is anyOf with a null branch)")
    for name, sub in properties.items():
        if not isinstance(name, str):
            raise SchemaError(f"{where}: a property name is a string")
        _node(sub, f"{where}.{name}", depth + 1)


def _enum(values: object, kind: str, where: str) -> None:
    if not isinstance(values, list) or not values:
        raise SchemaError(f"{where}: an enum is a non-empty list (a field with nothing "
                          "to offer is a plain string)")
    if kind == "string":
        if not all(isinstance(v, str) for v in values):
            raise SchemaError(f"{where}: a string enum holds strings")
        chars = sum(len(v) for v in values)
        if len(values) > ENUM_STRING_CHARS_ABOVE and chars > MAX_ENUM_STRING_CHARS:
            raise SchemaError(f"{where}: an enum of {len(values)} values totals {chars} "
                              f"characters; past {ENUM_STRING_CHARS_ABOVE} values strict "
                              f"mode takes at most {MAX_ENUM_STRING_CHARS}")
    elif not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        raise SchemaError(f"{where}: an integer enum holds integers")
    if len(set(values)) != len(values):
        raise SchemaError(f"{where}: an enum names each value once")


def _enum_values(node: object) -> int:
    """How many enum values `node` holds, summed over every enum in it. A key
    under `properties` is a name, so a property called `enum` is a schema."""
    if isinstance(node, list):
        return sum(_enum_values(sub) for sub in node)
    if not isinstance(node, dict):
        return 0
    total = 0
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            total += sum(_enum_values(sub) for sub in value.values())
        elif key == "enum" and isinstance(value, list):
            total += len(value)
        else:
            total += _enum_values(value)
    return total


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
    """The one spelling of `schema` a prompt carries. Sorted keys, and no HTML
    escaping -- the model should read "the character's", not "\\u0027" --
    which is why this is not Jinja's `tojson`. Registered as the prompts
    filter `schema_json`, so a template and the in-prompt check agree."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False)


# --- reading a reply ------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _first_wins(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    """An object that keeps a key's first occurrence, where JSON's default
    keeps the last: a reply that repeats a key is read by what it said first,
    as `decisions.find_object` always has."""
    out: dict[Any, Any] = {}
    for key, value in pairs:
        out.setdefault(key, value)
    return out


def _loads(text: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_first_wins)
    except (ValueError, RecursionError):
        return None


def find_value(text: str, *, kinds: tuple[type, ...] = (dict, list)) -> dict | list | None:
    """The reply's top-level JSON value -- an object or an array -- tolerant of
    a fence or prose around it: the whole text, else a leading fence's body,
    else the span from the first opening bracket to the last matching closing
    one (whichever of `{` and `[` opens first is tried first). The first
    candidate that decodes to one of `kinds` wins; None when none does, and
    never an exception. A repeated key, at any level, keeps its first value.

    `kinds` narrows what counts: `decisions.find_object` asks for objects
    alone, so a bare `[...]` still reads as no object there."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith("```") and (fence := _FENCE.match(stripped)):
        candidates.append(fence.group(1).strip())
    spans = []
    for opener, closer, kind in (("{", "}", dict), ("[", "]", list)):
        if kind not in kinds:
            continue
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start != -1 and end > start:
            spans.append((start, stripped[start:end + 1]))
    candidates.extend(span for _start, span in sorted(spans, key=lambda s: s[0]))
    for candidate in candidates:
        value = _loads(candidate)
        if isinstance(value, kinds):
            return value  # type: ignore[return-value]
    return None
