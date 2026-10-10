"""`grimoire.schemas`: the portable JSON Schema subset, its one prompt
spelling, and the tolerant reader of a reply (spec 01f, 01f-C3)."""

from __future__ import annotations

import ast
import inspect
import json
import sys

import pytest
from jinja2 import Environment

from grimoire import decisions, prompts, schemas
from grimoire.decisions import Choice, Item, Option, Predicate, Score


def _obj(**properties) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


#: A generation's schema: arrays, numbers, a nullable field, enums of both
#: kinds and descriptions -- everything the subset carries.
GOOD = _obj(
    title={"type": "string", "description": "A short label."},
    date={"type": "string"},
    location={"type": "string", "enum": ["harbour", "marsh-house", ""]},
    cast={"type": "array", "items": {"type": "string", "enum": ["characters:mara"]}},
    weight={"type": "number"},
    level={"type": "integer", "enum": [0, 1, 2]},
    note={"anyOf": [{"type": "string"}, {"type": "null"}]},
    done={"type": "boolean"},
    nested=_obj(rows={"type": "array", "items": _obj(id={"type": "string"})}))


def test_check_accepts_the_subset():
    schemas.check(GOOD)
    schemas.check(_obj(rows={"type": "array", "items": {"type": "number"}}))
    schemas.check(_obj())


@pytest.mark.parametrize("root", [
    {"type": "array", "items": {"type": "string"}}, {"type": "string"},
    {"anyOf": [_obj(), {"type": "null"}]}, [], "object"])
def test_the_root_is_an_object(root):
    """Strict mode answers any other root with a 400 naming `response_format`,
    which would read as the mode refused."""
    with pytest.raises(schemas.SchemaError, match="root"):
        schemas.check(root)  # type: ignore[arg-type]


@pytest.mark.parametrize("key", sorted(schemas.BOUNDS))
def test_check_refuses_every_bound(key):
    value = "x" if key in ("pattern", "format") else 1
    with pytest.raises(schemas.SchemaError, match="parser"):
        schemas.check(_obj(field={"type": "string", key: value}))


@pytest.mark.parametrize("key", ["$ref", "$defs", "oneOf", "allOf", "not", "const",
                                 "default", "title", "examples"])
def test_check_refuses_keywords_outside_the_subset(key):
    with pytest.raises(schemas.SchemaError, match=key.replace("$", r"\$")):
        schemas.check(_obj(field={"type": "string", key: "x"}))


@pytest.mark.parametrize("bad, why", [
    ({"type": ["string", "null"]}, "type array"),
    ({"type": "tuple"}, "unknown type"),
    ({"enum": ["a"]}, "single 'type'"),
    ({"type": "string", "enum": []}, "non-empty"),
    ({"type": "string", "enum": ["a", 1]}, "holds strings"),
    ({"type": "integer", "enum": [True, 1]}, "holds integers"),
    ({"type": "string", "enum": ["a", "a"]}, "once"),
    ({"type": "number", "enum": [1.5]}, "carries no"),
    ({"type": "string", "items": {"type": "string"}}, "carries no"),
    ({"type": "array"}, "items"),
    ({"type": "string", "description": 3}, "description"),
    ({"anyOf": []}, "non-empty"),
    ({"anyOf": [{"type": "string"}], "type": "string"}, "anyOf carries no"),
    ("string", "is an object"),
])
def test_check_refuses_a_malformed_node(bad, why):
    with pytest.raises(schemas.SchemaError, match=why):
        schemas.check(_obj(field=bad))


def test_check_refuses_an_open_or_partly_required_object():
    open_ = _obj(a={"type": "string"})
    del open_["additionalProperties"]
    with pytest.raises(schemas.SchemaError, match="additionalProperties"):
        schemas.check(open_)
    loose = {**_obj(a={"type": "string"}), "additionalProperties": True}
    with pytest.raises(schemas.SchemaError, match="additionalProperties"):
        schemas.check(loose)
    partial = {**_obj(a={"type": "string"}, b={"type": "string"}), "required": ["a"]}
    with pytest.raises(schemas.SchemaError, match="every property"):
        schemas.check(partial)
    with pytest.raises(schemas.SchemaError, match="properties"):
        schemas.check({"type": "object", "additionalProperties": False, "required": []})


def test_check_refuses_nesting_past_max_depth():
    node: dict = {"type": "string"}
    for _ in range(schemas.MAX_DEPTH - 1):
        node = _obj(a=node)
    schemas.check(node)                      # MAX_DEPTH levels: the limit itself
    with pytest.raises(schemas.SchemaError, match="nested"):
        schemas.check(_obj(a=node))
    # An anyOf branch sits at its parent's level, an array's items one below.
    schemas.check(_obj(a={"anyOf": [node["properties"]["a"], {"type": "null"}]}))
    with pytest.raises(schemas.SchemaError, match="nested"):
        schemas.check(_obj(a={"type": "array", "items": node["properties"]["a"]}))


def test_check_holds_each_budget():
    over = schemas.MAX_ENUM_VALUES + 1
    with pytest.raises(schemas.SchemaError, match="enums hold"):
        schemas.check(_obj(a={"type": "integer", "enum": list(range(over))}))
    halves = _obj(a={"type": "integer", "enum": list(range(over // 2 + 1))},
                  b={"type": "integer", "enum": list(range(over // 2 + 1))})
    with pytest.raises(schemas.SchemaError, match="enums hold"):
        schemas.check(halves)

    many = schemas.MAX_SCHEMA_PROPERTIES + 1
    with pytest.raises(schemas.SchemaError, match="object properties"):
        schemas.check(_obj(**{f"p{i}": {"type": "boolean"} for i in range(many)}))

    long = "x" * (schemas.MAX_SCHEMA_STRING_CHARS + 1)
    with pytest.raises(schemas.SchemaError, match="characters"):
        schemas.check(_obj(a={"type": "string", "enum": [long]}))

    above = schemas.ENUM_STRING_CHARS_ABOVE + 1
    width = schemas.MAX_ENUM_STRING_CHARS // above + 1
    wide = [f"{i:0{width}d}" for i in range(above)]
    with pytest.raises(schemas.SchemaError, match="past"):
        schemas.check(_obj(a={"type": "string", "enum": wide}))
    # The same strings in fewer values than the threshold are within strict mode.
    schemas.check(_obj(a={"type": "string", "enum": wide[:schemas.ENUM_STRING_CHARS_ABOVE]}))


def test_a_property_named_like_a_keyword_is_a_name():
    schemas.check(_obj(enum={"type": "string"}, pattern={"type": "string"},
                       properties={"type": "string"}))
    assert schemas.tally(_obj(enum={"type": "string"})) == (1, 4)


def test_the_strict_mode_budgets_have_one_copy():
    for name in ("MAX_ENUM_VALUES", "MAX_ENUM_STRING_CHARS", "ENUM_STRING_CHARS_ABOVE",
                 "MAX_SCHEMA_STRING_CHARS", "MAX_SCHEMA_PROPERTIES"):
        assert getattr(decisions, name) == getattr(schemas, name), name


def _items() -> list[Item]:
    """Every question kind, nullable and not, at the edges the request
    validation allows."""
    opts = tuple(Option(f"characters:c{i}", f"C{i}") for i in range(decisions.MAX_OPTIONS))
    return [
        Item("ctx", (Predicate("over", "is it over?"),)),
        Item("ctx", (Choice("who", "who?", opts[:3]),
                     Choice("maybe", "who, if anyone?", opts[:1], allow_none=True),
                     Score("tone", "how far?", tuple(f"l{i}" for i in range(decisions.MAX_LEVELS))))),
        Item("ctx", (Choice("all", "who?", opts),)),
    ]


def test_every_decision_schema_is_inside_the_subset():
    items = _items()
    for explain in (False, True):
        for size in range(1, len(items) + 1):
            schemas.check(decisions.schema(items[:size], explain=explain))
        for _offset, chunk in decisions.chunks(items * 4):
            schemas.check(decisions.schema(chunk, explain=explain))


def test_the_schema_json_filter_is_render():
    tricky = {"type": "object", "additionalProperties": False, "required": ["b", "a"],
              "properties": {"b": {"type": "string",
                                   "description": "the character's <name> & title"},
                             "a": {"type": "integer"}}}
    text = schemas.render(tricky)
    assert text == json.dumps(tricky, indent=2, sort_keys=True, ensure_ascii=False)
    assert "the character's <name> & title" in text
    assert text.index('"a"') < text.index('"b"')
    template = prompts._env().from_string("{{ schema | schema_json }}")
    assert template.render(schema=tricky) == text
    # Jinja's own `tojson` escapes those characters, which is why it is not this.
    assert Environment().from_string("{{ s | tojson(indent=2) }}").render(s=tricky) != text


def test_decide_system_still_renders_with_tojson():
    """`decide` does not go through the in-prompt check, and its golden bytes
    do not move: its template keeps Jinja's `tojson`."""
    schema = decisions.schema([Item("ctx", (Predicate("over", "is it over?"),))],
                              explain=True)
    rendered = prompts.render("decide/system.j2", schema=schema, explain=True)
    expected = Environment().from_string("{{ s | tojson(indent=2) }}").render(s=schema)
    assert expected in rendered
    source = (prompts.templates_dir() / "decide" / "system.j2").read_text(encoding="utf-8")
    assert "{{ schema | tojson(indent=2) }}" in source
    assert "schema_json" not in source


@pytest.mark.parametrize("text, value", [
    ('{"a": 1}', {"a": 1}),
    ('[1, 2]', [1, 2]),
    ('```json\n[{"a": 1}]\n```', [{"a": 1}]),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": [1, 2]} -- done.', {"a": [1, 2]}),
    ('Here you go: [{"a": 1}, {"b": 2}] -- done.', [{"a": 1}, {"b": 2}]),
    ('{"a": 1, "a": 2}', {"a": 1}),
    ('', None), ('no idea', None), ('null', None), ('"text"', None), ('3', None),
    ('{"a": ', None),
])
def test_find_value(text, value):
    assert schemas.find_value(text) == value


def test_find_value_never_raises():
    assert schemas.find_value("[" * 100_000 + "]" * 100_000) is None
    assert schemas.find_value(None) is None  # type: ignore[arg-type]


def test_find_object_keeps_its_dict_only_contract():
    assert decisions.find_object("[1, 2, 3]") is None
    assert decisions.find_object('[{"a": 1}]') == {"a": 1}
    assert decisions.find_object('see {"a": 1, "a": 2} ok') == {"a": 1}
    assert schemas.find_value('[{"a": 1}]') == [{"a": 1}]


def test_schemas_imports_only_the_standard_library():
    """Standard library only: `decisions`, `inference`, `prompts` and the
    store all import it, so it may import none of them."""
    tree = ast.parse(inspect.getsource(schemas))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import in schemas.py: {ast.dump(node)}"
            imported.add((node.module or "").split(".")[0])
    assert imported, "the walk found no imports at all"
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
