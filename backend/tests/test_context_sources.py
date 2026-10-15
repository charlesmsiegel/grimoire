"""#131: source text comes from the shared render, never a second render."""

import json
import random
import shutil

import pytest

from grimoire import prompts
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    config,
    context,
    dossiers,
    entities,
    relationships,
    scenes,
    taglines,
    worlds,
)
from grimoire.store.context import assemble, layout, macros, semantic, source_text


@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    for name in ("Mara", "Seraphine", "Winifred", "Realm"):
        card = characters.blank_card(name)
        card["data"]["description"] = f"{name} keeps the map."
        characters.create_character(root, name, "main", card)
    taglines.write(root, "realm", "Carries the key.")
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Saltmarch")
    for name in ("mara", "seraphine"):
        appearances.appear(cid, sid, "characters", name, "main", "npc")
    other = scenes.create_scene(cid, "Realm")
    appearances.appear(cid, other, "characters", "winifred", "main", "npc")
    root = campaigns.campaign_root(cid)
    dossiers.write(root, "winifred", "Watches the gate.")
    entities.create_entity(root, "lore", "Realm", "The tide rises at dusk.")
    entities.create_entity(root, "locations", "Saltmarch", "The gate opens at dawn.",
                           secrecy="secret", keys="wait")
    relationships.set_feeling(cid, "characters:mara", "characters:seraphine", 4, 3, 1, "warm")
    relationships.set_feeling(cid, "characters:seraphine", "characters:mara", 2, 1, 3, "wary")
    scenes.append_message(cid, sid, "user", "We wait.")
    return cid, sid, root


def _rows(cid, sid):
    return {row["id"]: row for row in context.context_sections(cid, sid)}


@pytest.mark.parametrize("section, expected", [
    ("character_descriptions", [("Mara", "## Mara\nMara keeps the map."),
                               ("Seraphine", "## Seraphine\nSeraphine keeps the map.")]),
    ("world_info", [("Realm", "The tide rises at dusk."),
                    ("Saltmarch", "The gate opens at dawn.")]),
    ("relationships", [("Mara → Seraphine: trust 4, affection 3, tension 1 (warm)", "Mara → Seraphine: trust 4, affection 3, tension 1 (warm)"),
                       ("Seraphine → Mara: trust 2, affection 1, tension 3 (wary)", "Seraphine → Mara: trust 2, affection 1, tension 3 (wary)")]),
    ("off_scene_cast_active", [("Winifred", "Winifred: Watches the gate.")]),
    ("off_scene_cast_known", [("Realm", "Realm: Carries the key. (available as: main)")]),
])
def test_each_source_exposes_only_its_rendered_contribution(scene, section, expected):
    cid, sid, _ = scene
    row = _rows(cid, sid)[section]
    assert [(s["label"], s["text"]) for s in row["sources"]] == expected
    assert all(s["text"] in row["text"] for s in row["sources"])
    json.dumps(row)


def test_sources_reuse_macro_draws_and_do_not_change_messages(scene, monkeypatch):
    cid, sid, root = scene
    entities.create_entity(root, "lore", "Mara", "{{random:map,key}}.")
    locked = appearances.locked_actor_root(cid)
    card = characters.read_card(locked, "mara", "main")
    card["data"]["description"] = "{{random:map,key}}."
    characters.update_version(locked, "mara", "main", card)
    draws = []
    original = macros.random.choice

    def choose(options):
        got = original(options)
        draws.append(got)
        return got

    monkeypatch.setattr(macros.random, "choice", choose)
    state = random.getstate()
    try:
        random.seed(131)
        messages, detail = context.compose_turn(cid, sid)
        rendered_draws = list(draws)
        assert len(rendered_draws) == 2
        rows = {r["id"]: r for r in detail["sections"]}
        assert rows["character_descriptions"]["sources"][0]["text"] == f"## Mara\n{draws[0]}."
        lore = next(s for s in rows["world_info"]["sources"] if s["label"] == "Mara")
        assert lore["text"] == f"{draws[1]}."
        assert all("\x00" not in m["content"] for m in messages)
        draws.clear()
        random.seed(131)
        plain, _ = context.compose_turn(cid, sid, describe=False)
        assert plain == messages
        assert draws == rendered_draws
    finally:
        random.setstate(state)


def test_sources_distinguish_shed_entries_from_contributions(scene):
    cid, sid, root = scene
    entities.create_entity(root, "lore", "Seraphine", "The map stays.", fields={"keep": "true"})
    config.write_config(context_budget="1")
    rows = _rows(cid, sid)
    row = rows["world_info"]
    assert row["text"] == "The map stays."
    assert {s["label"]: s["shed"] for s in row["sources"]} == {
        "Realm": True, "Saltmarch": True, "Seraphine": False,
    }
    assert rows["off_scene_cast_active"]["dropped"] is True
    assert rows["off_scene_cast_active"]["sources"][0]["text"] == "Winifred: Watches the gate."


def test_templates_render_without_source_capture():
    text = prompts.render("scene/sections/character_descriptions.j2", cast_blocks=[
        {"name": "Mara", "description": "Keeps the map."},
    ])
    assert text.strip() == "## Mara\nKeeps the map."


@pytest.fixture
def templates(monkeypatch, tmp_path):
    root = tmp_path / "templates"
    shutil.copytree(prompts.DEFAULT_TEMPLATES_DIR, root)
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(root))
    prompts._env.cache_clear()
    yield root
    prompts._env.cache_clear()


def test_custom_recall_templates_still_receive_the_original_bodies(scene, templates, monkeypatch):
    """A user's old template may slice a body; inspector metadata must not
    replace that body with a marker or change what its slice means."""
    cid, sid, root = scene
    entities.create_entity(root, "lore", "Seraphine", "Recalled map.", keys="unsaid")
    monkeypatch.setattr(semantic, "recall_scored", lambda candidates, _text, **_: [
        (entry, 0.8) for entry in candidates])
    (templates / "scene/sections/recalled_lore.j2").write_text(
        "{{ recalled_lore_bodies[0][:8] }}", encoding="utf-8")
    described, detail = context.compose_turn(cid, sid)
    plain, _ = context.compose_turn(cid, sid, describe=False)
    assert described == plain
    row = next(r for r in detail["sections"] if r["id"] == "recalled_lore")
    assert row["text"] == "Recalled"
    assert "sources" not in row  # an old custom template has no source annotations


def test_recalled_lore_exposes_its_own_expanded_source(scene, monkeypatch):
    cid, sid, root = scene
    entities.create_entity(root, "lore", "Seraphine", "Recalled {{unknown}}map.", keys="unsaid")
    monkeypatch.setattr(semantic, "recall_scored", lambda candidates, _text, **_: [
        (entry, 0.8) for entry in candidates])
    row = _rows(cid, sid)["recalled_lore"]
    assert [(s["label"], s["text"]) for s in row["sources"]] == [("Seraphine", "Recalled map.")]


def test_repeated_source_text_is_attributed_by_occurrence(scene, templates):
    cid, sid, _ = scene
    (templates / "scene/sections/character_descriptions.j2").write_text(
        '{%- from "scene/_sources.j2" import source_text with context -%}'
        '{%- set block = source_text("Mara", "  map  ") -%}'
        'map [{{ block }}] / [{{ block }}] map',
        encoding="utf-8")
    row = _rows(cid, sid)["character_descriptions"]
    assert row["text"] == "map [  map  ] / [  map  ] map"
    assert [(s["label"], s["text"]) for s in row["sources"]] == [
        ("Mara", "  map  "), ("Mara", "  map  ")]


@pytest.mark.parametrize("seed", [0, 1])
def test_macros_crossing_source_boundaries_keep_the_same_prompt(scene, seed):
    """Even a malformed pair of cards must keep macro expansion's old result;
    boundary bookkeeping cannot change random options or consume another draw."""
    cid, sid, _ = scene
    root = appearances.locked_actor_root(cid)
    for char_id, description in (("mara", "{{random:map,"), ("seraphine", "key}}")):
        card = characters.read_card(root, char_id, "main")
        card["data"]["description"] = description
        characters.update_version(root, char_id, "main", card)
    state = random.getstate()
    try:
        random.seed(seed)
        described, _ = context.compose_turn(cid, sid)
        random.seed(seed)
        plain, _ = context.compose_turn(cid, sid, describe=False)
        assert described == plain
    finally:
        random.setstate(state)



def test_surviving_source_slices_follow_the_shedding_render(scene):
    cid, sid, root = scene
    entities.create_entity(root, "lore", "Seraphine", "{{unknown}} map stays.",
                           fields={"keep": "true"})
    config.write_config(context_budget="1")
    row = _rows(cid, sid)["world_info"]
    assert row["text"] == "map stays."
    kept = next(s for s in row["sources"] if not s["shed"])
    assert kept["label"] == "Seraphine"
    assert kept["text"] == "map stays."


def test_an_empty_annotated_body_does_not_draw_a_shared_heading(scene, templates, monkeypatch):
    cid, sid, _ = scene
    root = appearances.locked_actor_root(cid)
    card = characters.read_card(root, "mara", "main")
    card["data"]["description"] = "{{random:map,key}}"
    characters.update_version(root, "mara", "main", card)
    (templates / "scene/sections/off_scene_cast_active.j2").write_text(
        '{%- from "scene/_sources.j2" import source_text with context -%}'
        '{{ source_text("Winifred", "   ") }}', encoding="utf-8")
    (templates / "scene/off_scene_cast_heading.j2").write_text(
        "{% raw %}{{random:map,key}}{% endraw %}", encoding="utf-8")
    chosen = [next(s for s in assemble.SECTIONS if s.id == name)
              for name in ("off_scene_cast_active", "character_descriptions")]
    monkeypatch.setattr(layout, "apply", lambda _sections: chosen)
    assembled = assemble._assemble(cid, sid)
    state = random.getstate()
    try:
        random.seed(4)
        described = assemble._render_sections(assembled, cid, sid, describe_sources=True)
        random.seed(4)
        plain = assemble._render_sections(assembled, cid, sid)
        assert [(s["id"], s["text"]) for s in described] == [
            (s["id"], s["text"]) for s in plain]
    finally:
        random.setstate(state)


@pytest.mark.parametrize("body", [
    '{{ source_text("Mara", "keeps the map") | upper }}',
    '{{ source_text("Mara", "keeps the map")[6:] }}',
])
def test_a_template_that_transforms_a_source_renders_the_plain_prompt(scene, templates, body):
    """A template may format a contribution AFTER the macro (against the
    documented order). Its marks then stop parsing or vanish; the section must
    fall back to the plain render rather than leak NUL debris into the prompt
    or drift from what `describe=False` sends."""
    cid, sid, _ = scene
    (templates / "scene/sections/character_descriptions.j2").write_text(
        '{%- from "scene/_sources.j2" import source_text with context -%}' + body,
        encoding="utf-8")
    described, detail = context.compose_turn(cid, sid)
    plain, _ = context.compose_turn(cid, sid, describe=False)
    assert described == plain
    assert all("\x00" not in m["content"] for m in described)
    row = next(r for r in detail["sections"] if r["id"] == "character_descriptions")
    assert "sources" not in row


@pytest.mark.parametrize("seed", range(6))
def test_a_template_drawing_from_random_renders_the_plain_prompt(scene, templates, seed):
    """Jinja's `random` filter draws from the global `random` module. A second
    render for comparison would consume different draws than describe=False, so
    the later macro draws would change. Both paths must leave `random` identical."""
    cid, sid, _ = scene
    (templates / "scene/sections/character_descriptions.j2").write_text(
        '{%- from "scene/_sources.j2" import source_text with context -%}'
        '{{ source_text("Mara", "keeps " ~ ["the", "a", "some"] | random ~ " map") }}'
        ' {{ ["x", "y", "z"] | random }}',
        encoding="utf-8")
    state = random.getstate()
    try:
        random.seed(seed)
        described, _ = context.compose_turn(cid, sid)
        after_described = random.getstate()
        random.seed(seed)
        plain, _ = context.compose_turn(cid, sid, describe=False)
        after_plain = random.getstate()
    finally:
        random.setstate(state)
    assert described == plain
    assert after_described == after_plain


def test_a_custom_template_serialising_cast_rows_sees_no_source_refs(scene, templates):
    """#131: a template that dumps whole cast rows must render what it did before."""
    cid, sid, _ = scene
    (templates / "scene/sections/character_descriptions.j2").write_text(
        "{% for b in cast_blocks %}{{ b }}{% endfor %}", encoding="utf-8")
    described, _ = context.compose_turn(cid, sid)
    plain, _ = context.compose_turn(cid, sid, describe=False)
    assert described == plain
    assert "characters:" not in next(m["content"] for m in described if "Mara" in m["content"])


def test_relationship_labels_keep_names_that_contain_the_separator():
    """A name with ': ' must not truncate the label: the line is its own label."""
    capture = source_text.Capture()
    text = prompts.render("scene/sections/relationships.j2", relationship_lines=[
        "Captain: Mara → Seraphine: trust 4"], capture_source=capture.mark)
    _, sources = capture.finish(capture.unmark(text))
    assert [s["label"] for s in sources] == ["Captain: Mara → Seraphine: trust 4"]
