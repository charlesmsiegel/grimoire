"""Tests for the extended character-card import pipeline.

Spec: docs/superpowers/specs/2026-05-19-card-imports-design.md §2, §5, §7.
Verifies that ``import_character_card`` materializes greetings and lore
as first-class library entities, applies the macro pass, suffixes on
collision, and writes a markdown report.
"""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.characters import CharactersService
from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.types.characters import IngestOptions


async def _seed_world(store: StateStore, world_id: str) -> None:
    await store.write_library_file(
        library_id=f"worlds/{world_id}/world/{world_id}",
        frontmatter={"id": world_id, "name": world_id, "version": 1},
        body="",
        source="test:seed",
    )


def _card(
    *,
    name: str = "Beatrice",
    first_mes: str | None = "Default hello from {{char}}.",
    alts: list[str] | None = None,
    character_book: dict | None = None,
    description: str = "A wandering witch.",
) -> dict:
    data: dict = {
        "name": name,
        "description": description,
        "first_mes": first_mes or "",
        "alternate_greetings": alts or [],
        "tags": ["witch"],
    }
    if character_book is not None:
        data["character_book"] = character_book
    return {"spec": "chara_card_v2", "data": data}


# ---------------------------------------------------------------------------
# Greetings
# ---------------------------------------------------------------------------


async def test_first_mes_becomes_default_greeting(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    raw = json.dumps(_card(first_mes="Hi {{user}} from {{char}}.")).encode("utf-8")
    result, _ = await characters.import_character_card(raw, "w1")
    assert any(c == "greeting:beatrice--default" for c in result.created)
    ent = await library.get_entity("w1", "greeting", "beatrice--default")
    # macro pass expanded {{char}} but kept {{user}} literal
    assert "Beatrice" in ent.body
    assert "{{user}}" in ent.body
    assert ent.frontmatter["import_source"]["kind"] == "sillytavern_first_mes"


async def test_alternate_greetings_become_greeting_files(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    raw = json.dumps(_card(alts=["First alt", "Second alt"])).encode("utf-8")
    result, _ = await characters.import_character_card(raw, "w1")
    assert any(c == "greeting:beatrice--alt-01" for c in result.created)
    assert any(c == "greeting:beatrice--alt-02" for c in result.created)
    ent1 = await library.get_entity("w1", "greeting", "beatrice--alt-01")
    assert ent1.frontmatter["import_source"]["source_index"] == 1
    assert "alternate-greeting" in ent1.frontmatter["tags"]


async def test_primary_greeting_toggle_off(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    raw = json.dumps(_card(first_mes="Hello.", alts=["Hi"])).encode("utf-8")
    result, _ = await characters.import_character_card(
        raw, "w1", options=IngestOptions(import_primary_greeting=False)
    )
    assert not any(c == "greeting:beatrice--default" for c in result.created)
    assert any(c == "greeting:beatrice--alt-01" for c in result.created)


# ---------------------------------------------------------------------------
# Character book → lore
# ---------------------------------------------------------------------------


async def test_character_book_entries_become_lore_files(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    book = {
        "entries": [
            {"keys": ["Tremere"], "content": "Vampires of secrecy."},
            {"keys": ["Camarilla"], "content": "Ruling sect."},
        ]
    }
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    result, _ = await characters.import_character_card(raw, "w1")
    lore_refs = [c for c in result.created if c.startswith("lore:")]
    assert any("tremere" in r for r in lore_refs)
    assert any("camarilla" in r for r in lore_refs)
    tremere = await library.get_entity("w1", "lore", "beatrice--tremere")
    assert "Vampires of secrecy" in tremere.body
    assert tremere.frontmatter["keywords"] == ["Tremere"]
    assert tremere.frontmatter["import_source"]["kind"] == "sillytavern_character_book"


async def test_macro_pass_applied_to_lore_body(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    book = {
        "entries": [
            {"keys": ["Tremere"], "content": "Whisper to {{char}} of secrets."},
        ]
    }
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    await characters.import_character_card(raw, "w1")
    entity = await library.get_entity("w1", "lore", "beatrice--tremere")
    assert "Beatrice" in entity.body
    assert "{{char}}" not in entity.body


async def test_character_book_toggle_off(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    book = {"entries": [{"keys": ["Tremere"], "content": "Vampires."}]}
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    result, _ = await characters.import_character_card(
        raw, "w1", options=IngestOptions(import_character_book=False)
    )
    assert not any(c.startswith("lore:") for c in result.created)


# ---------------------------------------------------------------------------
# Macro pass on character body fields
# ---------------------------------------------------------------------------


async def test_macros_expanded_in_description(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    raw = json.dumps(_card(description="{{char}} is loved by {{user}}.")).encode("utf-8")
    await characters.import_character_card(raw, "w1")
    char = await characters.get("w1", "beatrice")
    # {{char}} expanded; {{user}} preserved for runtime
    assert "Beatrice is loved by" in char.body
    assert "{{user}}" in char.body
    assert "{{char}}" not in char.body


# ---------------------------------------------------------------------------
# Collision suffixing
# ---------------------------------------------------------------------------


async def test_greeting_collision_gets_suffix(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    # Pre-seed a colliding greeting.
    await library.create_entity(
        "w1",
        "greeting",
        "beatrice--default",
        {
            "id": "beatrice--default",
            "name": "Pre-existing",
            "present_characters": ["beatrice"],
            "tags": [],
        },
        body="pre-existing body",
        source="test",
    )
    raw = json.dumps(_card(first_mes="New greeting.")).encode("utf-8")
    result, _ = await characters.import_character_card(raw, "w1")
    # Original survived; new one got a suffix.
    pre = await library.get_entity("w1", "greeting", "beatrice--default")
    assert "pre-existing" in pre.body
    new = await library.get_entity("w1", "greeting", "beatrice--default-2")
    assert "New greeting" in new.body
    assert any(c == "greeting:beatrice--default-2" for c in result.created)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


async def test_import_writes_markdown_report(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    raw = json.dumps(
        _card(first_mes="Hi.", character_book={"entries": [{"keys": ["k"], "content": "v"}]})
    ).encode("utf-8")
    result, _ = await characters.import_character_card(raw, "w1")
    report_refs = [c for c in result.created if c.startswith("report:")]
    assert report_refs, "expected an import report to be written"
    report_path = Path(store.data_root) / report_refs[0].split(":", 1)[1]
    assert report_path.exists()
    body = report_path.read_text(encoding="utf-8")
    assert "Beatrice" in body
    assert "## Created" in body


async def test_report_lists_discarded_system_prompt(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    card_dict = _card()
    card_dict["data"]["system_prompt"] = "Stay in voice."
    raw = json.dumps(card_dict).encode("utf-8")
    result, _ = await characters.import_character_card(raw, "w1")
    report_refs = [c for c in result.created if c.startswith("report:")]
    body = (Path(store.data_root) / report_refs[0].split(":", 1)[1]).read_text(encoding="utf-8")
    assert "system_prompt" in body
