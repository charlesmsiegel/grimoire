from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import populate_world_content as pwc
from grimoire.store import characters, entities, greetings, tags, worlds


def _world(monkeypatch, tmp_path) -> tuple[str, Path]:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Ashgrove")
    return wid, worlds.world_root(wid)


def test_build_index_lists_existing_entities_tags_and_greetings(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")
    entities.create_entity(root, "lore", "Gangs")
    tags.add_tag(root, "Farmer")
    greetings.create_greeting(root, "Hariel", "hariel", "default", "hi")

    idx = pwc.build_index(root)

    assert {"kind": "locations", "id": "blind-lion", "name": "Blind Lion"} in idx["entities"]
    assert {"kind": "lore", "id": "gangs", "name": "Gangs"} in idx["entities"]
    assert {"id": "farmer", "display_name": "Farmer"} in idx["tags"]
    g = idx["greetings"][0]
    assert g["name"] == "Hariel" and g["character"] == "hariel" and g["version"] == "default"


def test_index_cli_prints_json(monkeypatch, tmp_path, capsys):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")

    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "index", "--world", wid])
    assert pwc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["entities"][0]["name"] == "Blind Lion"


def test_apply_tags_reuses_existing_id_and_tracks_touched_file(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    existing_id = tags.add_tag(root, "Farmer")
    world_rel = pwc._world_rel(root)
    results = pwc.new_results()

    specs = [{"display_name": "farmer"}, {"display_name": "Merchant"}]
    result = pwc.apply_tags(root, specs, results)

    assert result["farmer"] == existing_id
    assert len(tags.read_tags(root)) == 2  # no duplicate "farmer"/"farmer-2" entry
    assert result["Merchant"] in tags.read_tags(root)
    assert f"{world_rel}/tags.md" in results["touched_files"]


def test_apply_entities_skips_existing_name_case_insensitively(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")
    results = pwc.new_results()

    pwc.apply_entities(root, [
        {"kind": "locations", "name": "blind lion", "body": "dup"},
        {"kind": "locations", "name": "Guild Hall", "body": "new"},
    ], results, wid)

    names = {e["name"] for e in entities.list_entities(root, "locations")}
    assert names == {"Blind Lion", "Guild Hall"}
    assert len(results["skipped"]) == 1 and len(results["created"]) == 1
    assert any(p.endswith("locations/guild-hall.md") for p in results["touched_files"])


def test_apply_entities_rejects_creatures_outside_fantasy_worlds(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)  # "Ashgrove" is not in CREATURE_ALLOWED_WORLDS
    results = pwc.new_results()

    pwc.apply_entities(root, [{"kind": "creatures", "name": "Dragon", "body": "x"}], results, wid)

    assert entities.list_entities(root, "creatures") == []
    assert results["errors"][0]["reason"] == "creatures not allowed outside fantasy worlds"


def test_apply_reclassifications_is_idempotent_across_reruns(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    lore_id = entities.create_entity(
        root, "lore", "Gangs",
        "Gangs in Gilderock: the Erune family, the Avengers.")
    spec = {
        "lore_id": lore_id, "new_kind": "groups",
        "name": "Erune family", "body": "Mafia family."}

    r1 = pwc.new_results()
    pwc.apply_reclassifications(root, [spec], r1, wid)
    groups = [g["name"] for g in entities.list_entities(root, "groups")]
    assert groups == ["Erune family"]
    assert entities.list_entities(root, "lore") == []
    assert r1["created"][0]["kind"] == "groups"

    # Same spec applied again — lore_id already gone, target already exists
    r2 = pwc.new_results()
    pwc.apply_reclassifications(root, [spec], r2, wid)
    groups = [g["name"] for g in entities.list_entities(root, "groups")]
    assert groups == ["Erune family"]  # not duplicated
    # Deleting an already-gone lore entry is not an error
    assert r2["errors"] == []
    assert r2["skipped"][0]["reason"] == "target already exists"


def _card(first_mes="", alts=None):
    return {"data": {"name": "Adriana", "first_mes": first_mes,
                      "alternate_greetings": alts or [], "description": "", "personality": "",
                      "scenario": "", "mes_example": "", "tags": [], "extensions": {}},
            "spec": "chara_card_v3", "spec_version": "3.0"}


def test_apply_greeting_imports_titles_in_order(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Alt one.", "Alt two."]))
    results = pwc.new_results()

    ref_map = pwc.apply_greeting_imports(root, [
        {"character": "adriana", "version": "default", "titles": ["Guild induction", "Lost in the city"]},
    ], results)

    by_id = {g["id"]: g["name"] for g in greetings.list_greetings(root)}
    assert by_id[ref_map["new:adriana:default:0"]] == "Guild induction"
    assert by_id[ref_map["new:adriana:default:1"]] == "Lost in the city"
    assert "Adriana (alt 2)" in by_id.values()  # no title supplied for idx 2 -> kept raw import name


def test_apply_greeting_imports_resolves_new_refs_idempotently_on_rerun(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Alt one."]))
    spec = [{"character": "adriana", "version": "default", "titles": ["Guild induction", "Lost in the city"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 2

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)  # same spec again

    assert len(greetings.list_greetings(root)) == 2  # not duplicated
    assert r2["skipped"][0]["reason"] == "already imported"
    assert ref_map_2 == ref_map_1  # refs resolve to the SAME greetings both times
    assert ref_map_2["new:adriana:default:0"] == ref_map_1["new:adriana:default:0"]


def test_apply_greeting_imports_idempotent_with_partial_titles_on_rerun(monkeypatch, tmp_path):
    """Critical test: partial-title scenario that breaks mtime-based ordering.
    3 greetings created (main + 2 alts), but only 2 titles supplied (indices 0-1).
    Index 2 keeps raw name 'Adriana (alt 2)'. On first run, refs map correctly.
    On second run with mtime-based ordering, the modified greetings (0 & 1) have
    new mtimes while the unmodified one (2) keeps original (earlier) mtime,
    completely reordering the sort. ID-based ordering fixes this."""
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Alt one.", "Alt two."]))
    spec = [{"character": "adriana", "version": "default", "titles": ["Guild induction", "Lost in the city"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 3

    by_id_1 = {g["id"]: g["name"] for g in greetings.list_greetings(root)}
    # Verify first run mapping: titled greetings renamed, untitled keeps raw name
    assert by_id_1[ref_map_1["new:adriana:default:0"]] == "Guild induction"
    assert by_id_1[ref_map_1["new:adriana:default:1"]] == "Lost in the city"
    assert by_id_1[ref_map_1["new:adriana:default:2"]] == "Adriana (alt 2)"

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)  # same spec again

    assert len(greetings.list_greetings(root)) == 3  # not duplicated
    assert r2["skipped"][0]["reason"] == "already imported"
    # The critical assertion: ref_map_2 must equal ref_map_1, which means
    # the ordering didn't change despite partial renames having different mtimes
    assert ref_map_2 == ref_map_1
    assert ref_map_2["new:adriana:default:0"] == ref_map_1["new:adriana:default:0"]
    assert ref_map_2["new:adriana:default:1"] == ref_map_1["new:adriana:default:1"]
    assert ref_map_2["new:adriana:default:2"] == ref_map_1["new:adriana:default:2"]
