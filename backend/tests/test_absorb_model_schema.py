"""The generated wire contract preserves explicit clears and closes every object."""

import json

from grimoire import inference, model_schema, schemas
from grimoire.store import absorb, groupstate, playstate
from grimoire.store.absorb import models, parse, prompt
from tests.test_absorb_store import _campaign_with_group, _char


def test_wire_schema_closes_nested_objects_and_preserves_nullable_state():
    schema = model_schema.structured(models.Extraction)
    assert set(schema["properties"]) == set(parse.parse_output("{}"))
    state = schema["properties"]["character_state_edits"]["items"]
    assert state["properties"]["current_state"]["anyOf"][-1] == {"type": "null"}
    group = schema["properties"]["group_state_edits"]["items"]
    assert set(group["required"]) >= {"goals", "resources", "focus", "public_perception", "secrets"}
    relation = schema["properties"]["relationship_deltas"]["items"]
    assert "from" in relation["properties"] and "from_" not in relation["properties"]
    membership = schema["properties"]["membership_changes"]["items"]
    assert membership["properties"]["change"]["enum"] == ["join", "leave"]

    def visit(node):
        assert "$ref" not in node and "$defs" not in node and "definitions" not in node
        assert "title" not in node and "default" not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for value in node["properties"].values():
                visit(value)
        if "items" in node:
            visit(node["items"])
        for value in node.get("anyOf", []):
            visit(value)
    visit(schema)


def test_raw_null_omission_keep_and_empty_string_clears_all_state_fields():
    payload = {"character_state_edits": [{"id": "characters:mara", "current_state": None,
        "knows": "", "suspects": None}], "group_state_edits": [{"id": "groups:salt-circle",
        "goals": None, "resources": "", "focus": 4}], "membership_changes": [None,
        {"group": "groups:salt-circle", "actor": "characters:mara", "change": " JOIN "}]}
    parsed = parse.parse_output(json.dumps(payload))
    assert parsed["character_state_edits"] == [{"id": "characters:mara", "knows": ""}]
    assert parsed["group_state_edits"] == [{"id": "groups:salt-circle", "resources": ""}]
    assert parsed["membership_changes"] == [{"group": "groups:salt-circle",
        "actor": "characters:mara", "change": "join"}]


def test_system_prompt_embeds_the_same_model_generated_schema():
    schema = model_schema.structured(models.Extraction)
    messages = prompt.build_prompt("Mara joined.", {})
    assert schemas.render(schema) in messages[0]["content"]
    inference._check_structured(messages, schema)


def test_nullable_current_state_keeps_existing_state_when_knowledge_changes(monkeypatch, tmp_path):
    cid, sid, root = _campaign_with_group(monkeypatch, tmp_path)
    actor = _char(root, "Mara")
    playstate.write_state(root, actor, "## Current state\nWary.\n\n## Knows\nThe gate.")
    parsed = parse.parse_output(json.dumps({"character_state_edits": [
        {"id": actor, "current_state": None, "knows": ""}]}))
    edits = absorb.materialize(cid, sid, parsed)
    assert len(edits) == 1 and "Wary." in edits[0]["after"]
    applied, failures = absorb.apply_edits(cid, edits, sid=sid)
    assert applied and not failures
    state = playstate.read_state(root, actor)
    assert state["current_state"] == "Wary." and state["knows"] == ""


def test_explicit_empty_state_clears_character_and_group_including_last_field(monkeypatch, tmp_path):
    cid, sid, root = _campaign_with_group(monkeypatch, tmp_path)
    actor = _char(root, "Mara")
    playstate.write_state(root, actor, "Wary.")
    groupstate.write_state(root, "salt-circle", "## Goals\nThe gate.")
    parsed = parse.parse_output(json.dumps({"character_state_edits": [
        {"id": actor, "current_state": ""}], "group_state_edits": [
        {"id": "groups:salt-circle", "goals": ""}]}))
    edits = absorb.materialize(cid, sid, parsed)
    assert {edit["kind"] for edit in edits} == {"character_state", "group_state"}
    assert all(edit["after"] == "" for edit in edits)
    _applied, failures = absorb.apply_edits(cid, edits, sid=sid)
    assert not failures
    assert playstate.read_state(root, actor)["current_state"] == ""
    assert groupstate.read_state(root, "salt-circle")["goals"] == ""


def test_the_schema_is_the_same_on_both_pydantic_runtimes():
    """Spec 07 section 9.1.1's v1/v2 parity. One process holds one pydantic,
    so parity is pinned through a committed fixture both runs compare with:
    `make check-py` on v2 and `make check-pydantic1` on v1. A deliberate model
    change regenerates the fixture (`prompt.extraction_schema()`, dumped with
    indent=1 and sorted keys) in the same commit."""
    from pathlib import Path
    fixture = Path(__file__).parent / "fixtures" / "absorb_extraction_schema.json"
    assert prompt.extraction_schema() == json.loads(fixture.read_text(encoding="utf-8"))
