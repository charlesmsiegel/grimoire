"""The portable-schema leaf (01f-S1, 01f-C3): `schemas.check`, the subset a
schema must stay inside to be sent to both OpenAI strict mode and Anthropic's
`output_config.format`; `schemas.render`, the one prompt spelling of a schema
(also the Jinja filter `schema_json`); and `schemas.find_value`, the tolerant
reader of a reply's JSON value.

Every case is a pure function of its input: the leaf imports nothing from the
package.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
import sys

import jinja2
import pytest

from grimoire import decisions, prompts, schemas
from grimoire.decisions import Choice, Item, Option, Predicate, Score
from grimoire.schemas import SchemaError


def _obj(**properties) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


#: Every keyword the subset allows, at least once.
FULL = _obj(
    title={"type": "string", "description": "A short label, 2-5 words."},
    count={"type": "integer"},
    weight={"type": "number"},
    open={"type": "boolean"},
    nothing={"type": "null"},
    location={"type": "string", "enum": ["saltmarch", "the-realm"]},
    level={"type": "integer", "enum": [0, 1, 2]},
    cast={"type": "array", "items": {"type": "string", "enum": ["characters:mara"]}},
    maybe={"anyOf": [{"type": "string"}, {"type": "null"}]},
    nested=_obj(inner={"type": "array", "items": _obj(x={"type": "boolean"})}),
)


def test_the_subset_is_accepted():
    schemas.check(FULL)
    schemas.check(_obj())        # an object with no properties at all


@pytest.mark.parametrize("key, value", [
    ("minimum", 0), ("maximum", 9), ("exclusiveMinimum", 0), ("exclusiveMaximum", 9),
    ("multipleOf", 2), ("minLength", 1), ("maxLength", 9), ("pattern", "^a"),
    ("format", "date"), ("const", "a"), ("$ref", "#/$defs/x"), ("default", ""),
    ("title", "Name"), ("examples", ["a"]),
])
def test_a_keyword_outside_the_subset_is_refused(key, value):
    with pytest.raises(SchemaError, match=key.replace("$", r"\$")):
        schemas.check(_obj(x={"type": "string", key: value}))


@pytest.mark.parametrize("key, value", [("minItems", 1), ("maxItems", 3)])
def test_an_array_bound_is_refused(key, value):
    with pytest.raises(SchemaError, match=key):
        schemas.check(_obj(x={"type": "array", "items": {"type": "string"}, key: value}))


@pytest.mark.parametrize("key", ["oneOf", "allOf"])
def test_a_combinator_other_than_any_of_is_refused(key):
    with pytest.raises(SchemaError, match=key):
        schemas.check(_obj(x={key: [{"type": "string"}, {"type": "null"}]}))


def test_not_and_defs_are_refused():
    with pytest.raises(SchemaError, match="not"):
        schemas.check(_obj(x={"type": "string", "not": {"type": "null"}}))
    with pytest.raises(SchemaError, match=r"\$defs"):
        schemas.check({**_obj(), "$defs": {"a": {"type": "string"}}})


def test_a_type_array_is_refused():
    with pytest.raises(SchemaError, match="anyOf"):
        schemas.check(_obj(x={"type": ["string", "null"]}))


def test_an_unknown_type_is_refused():
    for kind in ("date", {"type": "string"}, None, 3):
        with pytest.raises(SchemaError, match="type"):
            schemas.check(_obj(x={"type": kind}))


def test_a_node_needs_a_type_or_an_any_of_and_not_both():
    with pytest.raises(SchemaError):
        schemas.check(_obj(x={"description": "anything"}))
    with pytest.raises(SchemaError):
        schemas.check(_obj(x={"type": "string", "anyOf": [{"type": "string"}]}))
    with pytest.raises(SchemaError):
        schemas.check(_obj(x={"anyOf": []}))


@pytest.mark.parametrize("extra", [{}, {"additionalProperties": True},
                                   {"additionalProperties": {"type": "string"}}])
def test_an_object_must_close_itself(extra):
    node = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}},
            **extra}
    with pytest.raises(SchemaError, match="additionalProperties"):
        schemas.check(_obj(x=node))


@pytest.mark.parametrize("required", [[], ["a"], ["a", "b", "c"], ["a", "a", "b"], None])
def test_required_names_every_property_exactly(required):
    node = _obj(a={"type": "string"}, b={"type": "string"})
    if required is None:
        del node["required"]
    else:
        node["required"] = required
    with pytest.raises(SchemaError, match="required"):
        schemas.check(node)


def test_an_object_needs_properties():
    with pytest.raises(SchemaError, match="properties"):
        schemas.check({"type": "object", "additionalProperties": False, "required": []})


def test_an_array_needs_one_items_schema():
    with pytest.raises(SchemaError, match="items"):
        schemas.check(_obj(x={"type": "array"}))
    with pytest.raises(SchemaError, match="items"):
        schemas.check(_obj(x={"type": "array", "items": [{"type": "string"}]}))


def test_object_and_array_keywords_stay_on_their_own_types():
    with pytest.raises(SchemaError, match="items"):
        schemas.check(_obj(x={"type": "string", "items": {"type": "string"}}))
    with pytest.raises(SchemaError, match="properties"):
        schemas.check(_obj(x={"type": "string", "properties": {}}))


def test_an_empty_enum_is_refused():
    """Strict mode answers `enum: []` with a 400 naming `response_format`,
    which would read as the mode refused (spec 3.1)."""
    with pytest.raises(SchemaError, match="empty"):
        schemas.check(_obj(x={"type": "string", "enum": []}))


@pytest.mark.parametrize("node", [
    {"type": "string", "enum": ["a", 1]},
    {"type": "string", "enum": [1, 2]},
    {"type": "integer", "enum": ["a"]},
    {"type": "integer", "enum": [True, False]},
    {"type": "number", "enum": [1.5]},
    {"type": "boolean", "enum": [True]},
    {"type": "string", "enum": "abc"},
])
def test_enum_values_are_strings_or_integers_of_their_type(node):
    with pytest.raises(SchemaError, match="enum"):
        schemas.check(_obj(x=node))


def test_the_root_is_an_object():
    for root in ({"type": "string"}, {"type": "array", "items": {"type": "string"}},
                 {"anyOf": [_obj(), {"type": "null"}]}):
        with pytest.raises(SchemaError, match="root"):
            schemas.check(root)
    with pytest.raises(SchemaError):
        schemas.check(["not", "a", "schema"])  # type: ignore[arg-type]


def test_a_property_name_must_be_a_string():
    node = _obj(a={"type": "string"})
    node["properties"] = {1: {"type": "string"}}
    node["required"] = [1]
    with pytest.raises(SchemaError):
        schemas.check(node)


def _deep(levels: int) -> dict:
    """An object schema `levels` nodes deep along one path (a leaf string at
    the bottom counts as a level, as a decision's question value does)."""
    node: dict = {"type": "string"}
    for _ in range(levels - 1):
        node = _obj(x=node)
    return node


def test_nesting_is_capped_at_max_depth():
    assert schemas.MAX_DEPTH == 10
    schemas.check(_deep(schemas.MAX_DEPTH))
    with pytest.raises(SchemaError, match="nest"):
        schemas.check(_deep(schemas.MAX_DEPTH + 1))
    # An anyOf branch is an alternative, not a level of its own.
    inner = _deep(schemas.MAX_DEPTH - 1)
    schemas.check(_obj(x={"anyOf": [inner, {"type": "null"}]}))
    # An array's items are a level, as a property's value is.
    with pytest.raises(SchemaError, match="nest"):
        schemas.check(_obj(x={"type": "array", "items": _deep(schemas.MAX_DEPTH - 1)}))


def test_the_enum_value_budget():
    half = schemas.MAX_ENUM_VALUES // 2
    ok = _obj(a={"type": "integer", "enum": list(range(half))},
              b={"type": "integer", "enum": list(range(half))})
    schemas.check(ok)
    over = copy.deepcopy(ok)
    over["properties"]["b"]["enum"].append(half)
    with pytest.raises(SchemaError, match="enum values"):
        schemas.check(over)


def test_the_per_enum_string_budget():
    many = schemas.ENUM_STRING_CHARS_ABOVE + 1
    width = schemas.MAX_ENUM_STRING_CHARS // many + 1
    values = [f"{n:0{width}d}" for n in range(many)]
    with pytest.raises(SchemaError, match="characters"):
        schemas.check(_obj(x={"type": "string", "enum": values}))
    # The same total over fewer values is inside it.
    few = [f"{n:0{schemas.MAX_ENUM_STRING_CHARS // 200}d}" for n in range(200)]
    schemas.check(_obj(x={"type": "string", "enum": few}))


def test_the_schema_string_budget():
    width = 1000
    count = schemas.MAX_SCHEMA_STRING_CHARS // width + 1
    props = {f"{n:0{width}d}": {"type": "boolean"} for n in range(count)}
    assert count <= schemas.MAX_SCHEMA_PROPERTIES
    with pytest.raises(SchemaError, match="characters"):
        schemas.check(_obj(**props))


def test_the_property_budget():
    props = {f"p{n}": {"type": "boolean"} for n in range(schemas.MAX_SCHEMA_PROPERTIES + 1)}
    with pytest.raises(SchemaError, match="properties"):
        schemas.check(_obj(**props))
    del props[f"p{schemas.MAX_SCHEMA_PROPERTIES}"]
    schemas.check(_obj(**props))


def test_the_budgets_are_the_ones_decisions_uses():
    """One copy: `decisions` re-exports the leaf's constants."""
    for name in ("MAX_ENUM_VALUES", "MAX_ENUM_STRING_CHARS", "ENUM_STRING_CHARS_ABOVE",
                 "MAX_SCHEMA_STRING_CHARS", "MAX_SCHEMA_PROPERTIES"):
        assert getattr(decisions, name) == getattr(schemas, name), name
    assert (schemas.MAX_ENUM_VALUES, schemas.MAX_SCHEMA_STRING_CHARS,
            schemas.MAX_SCHEMA_PROPERTIES) == (1000, 120_000, 5000)


MARA = Option("mara", "Mara")
SERAPHINE = Option("seraphine", "Seraphine")


@pytest.mark.parametrize("items", [
    [Item("Mara closes the door.", (Predicate("over", "Is the scene over?"),))],
    [Item("ctx", (Choice("next", "Who speaks?", (MARA, SERAPHINE)),))],
    [Item("ctx", (Choice("next", "Who speaks?", (MARA,), allow_none=True),))],
    [Item("ctx", (Score("drift", "How far?", ("none", "some", "far")),))],
    [Item("ctx", (Predicate("a", "?"), Choice("b", "?", (MARA, SERAPHINE), allow_none=True),
                  Score("c", "?", ("x", "y")))) for _ in range(decisions.MAX_ITEMS_PER_CALL)],
    # 01e's kinds: a ranking's array of tiers (five deep), a selection's array,
    # and a joint's flattened choice.
    [Item("ctx", (decisions.Rank("r", "Order them", (MARA, SERAPHINE)),
                  decisions.Rank("t", "Top one", (MARA, SERAPHINE), top=1, allow_none=True)))],
    [Item("ctx", (decisions.MultiSelect("s", "Who?", (MARA, SERAPHINE), min=1, max=2),
                  decisions.MultiSelect("n", "Who?", (MARA,), allow_none=True)))],
    [Item("ctx", (decisions.Joint("j", "Act", (Option("greet", "Greet"),
                                               Option("wait", "Wait")),
                                  (("greet", (MARA, SERAPHINE)),), allow_none=True),))],
])
@pytest.mark.parametrize("explain", [True, False])
def test_every_decision_schema_passes(items, explain):
    schemas.check(decisions.schema(items, explain=explain))


def test_a_decision_batch_nests_four_deep():
    """Spec 3.1: batch, item, answers, a question's value."""
    batch = decisions.schema([Item("ctx", (Predicate("a", "?"),))], explain=True)
    assert schemas.depth(batch) == 4


# ---- render, and the filter that is the same function ----
UNORDERED = {"type": "object", "required": ["b", "a"], "additionalProperties": False,
             "properties": {"b": {"type": "string",
                                  "description": "the character's <mark> & more"},
                            "a": {"type": "string", "description": "Seraphine — café"}}}


def test_render_is_sorted_indented_and_unescaped():
    text = schemas.render(UNORDERED)
    assert text == json.dumps(UNORDERED, indent=2, sort_keys=True, ensure_ascii=False)
    assert "the character's <mark> & more" in text
    assert "café" in text and "\\u" not in text
    assert text.index('"a"') < text.index('"b"')


def test_the_schema_json_filter_is_render():
    env = prompts._env()
    assert env.filters["schema_json"] is schemas.render
    rendered = env.from_string("{{ schema | schema_json }}").render(schema=UNORDERED)
    assert rendered == schemas.render(UNORDERED)
    # What `tojson` would have done instead, which is why it is not used.
    assert env.from_string("{{ schema | tojson(indent=2) }}").render(schema=UNORDERED) \
        != rendered


def test_decide_system_template_is_unchanged():
    """`decide/system.j2` keeps `tojson`: its bytes are the same whatever
    filters the environment gains."""
    source = (prompts.templates_dir() / "decide" / "system.j2").read_text(encoding="utf-8")
    assert "{{ schema | tojson(indent=2) }}" in source and "schema_json" not in source
    bare = jinja2.Environment(loader=jinja2.FileSystemLoader(str(prompts.templates_dir())),
                              undefined=jinja2.StrictUndefined)
    items = [Item("Mara's <door> & bell", (Predicate("a", "?"),))]
    schema = decisions.schema(items, explain=True)
    kinds = decisions.kinds(items)
    for explain in (True, False):
        assert prompts.render("decide/system.j2", schema=schema, explain=explain,
                              kinds=kinds) == \
            bare.get_template("decide/system.j2").render(schema=schema, explain=explain,
                                                         kinds=kinds)


# ---- find_value ----
OBJ = {"title": "Saltmarch at dawn", "cast": ["characters:mara"]}
ARR = [{"id": "a"}, {"id": "b"}]


@pytest.mark.parametrize("text, expected", [
    (json.dumps(OBJ), OBJ),
    (json.dumps(ARR), ARR),
    (f"```json\n{json.dumps(OBJ)}\n```", OBJ),
    (f"```\n{json.dumps(ARR)}\n```", ARR),
    (f"Here you go: {json.dumps(OBJ)} Thanks.", OBJ),
    (f"Here you go: {json.dumps(ARR)} Thanks.", ARR),
    (f"[{json.dumps(OBJ)}]", [OBJ]),
    ('{"a": 1, "a": 2}', {"a": 1}),
    ('see [{"a": 1, "a": 2}] ok', [{"a": 1}]),
    ('note {"a": [1, 2]} end', {"a": [1, 2]}),
    ("", None), ("no idea", None), ("null", None), ('"just a string"', None),
    (json.dumps(OBJ)[:-3], None),
])
def test_find_value_reads_an_object_or_an_array(text, expected):
    assert schemas.find_value(text) == expected


def test_find_value_never_raises():
    # Valid JSON nested past any recursion limit: whether the decoder reads it
    # depends on the interpreter (3.13 raises RecursionError, so None; 3.14's
    # decoder returns the list). Either way nothing raises, and an objects-only
    # read -- the one production uses -- never answers with an array.
    deep = "[" * 100_000 + "]" * 100_000
    value = schemas.find_value(deep)
    assert value is None or isinstance(value, list)
    assert schemas.find_value(deep, arrays=False) is None
    assert schemas.find_value(None) is None  # type: ignore[arg-type]
    assert schemas.find_value(b"{}") is None  # type: ignore[arg-type]


def test_objects_only_is_find_object():
    """`decisions.find_object` keeps its dict-only contract, including the
    one case a list-to-None mapping would lose: `[{...}]` reads the object
    inside it (`parse` reads a list holding one object)."""
    for text in (json.dumps(OBJ), f"```json\n{json.dumps(OBJ)}\n```",
                 f"Here: {json.dumps(OBJ)} ok", f"[{json.dumps(OBJ)}]",
                 "", "no idea", "[1, 2, 3]", "null", '{"a": 1, "a": 2}'):
        assert decisions.find_object(text) == schemas.find_value(text, arrays=False), text
        assert not isinstance(decisions.find_object(text), list)
    assert decisions.find_object(f"[{json.dumps(OBJ)}]") == OBJ
    assert decisions.find_object("[1, 2, 3]") is None


# ---- the leaf ----
def test_the_leaf_imports_only_the_standard_library():
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


def test_decisions_binds_the_module_not_its_names():
    tree = ast.parse(inspect.getsource(decisions))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            assert node.module is None, ast.dump(node)
            assert [a.name for a in node.names] == ["schemas"], ast.dump(node)
