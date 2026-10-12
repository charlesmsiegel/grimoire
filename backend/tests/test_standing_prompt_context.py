"""Standing fields are additive, shared writer guidance, frozen with each prompt."""

from copy import deepcopy

import pytest

from grimoire import model_guidance, store
from grimoire.store.context import assemble, layout, macros, pack

IDS = ("world_default_prompt", "campaign_default_prompt", "scene_focus")
TEXTS = ("The river freezes each winter.", "This winter the harbor stays open.", "Offer Mara a choice without promising agreement.")


@pytest.fixture
def scope(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    for name in ("Mara", "Seraphine"):
        store.characters.create_character(store.worlds.world_root(wid), name, "main", store.characters.blank_card(name))
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("mara", "seraphine"):
        store.appearances.appear(cid, sid, "characters", name, "main", "npc", narrate=False)
    return wid, cid, sid


def _save(scope, texts=TEXTS):
    wid, cid, sid = scope
    store.default_prompts.set_world(wid, texts[0])
    store.default_prompts.set_campaign(cid, texts[1])
    store.scenes.focus.set(cid, sid, texts[2])


def _compose(cid, sid, builder):
    if builder == "director":
        return store.context.compose_director_turn(cid, sid, "Wait.")
    if builder in ("opener", "adapt"):
        return store.context.compose_opener(cid, sid, "Mara waits.", adapt=builder == "adapt")
    kwargs = {"actor_ref": "characters:mara"} if builder == "npc" else {}
    if builder == "group":
        kwargs = {"actor_ref": "characters:mara", "eligible_speakers": [{"ref": "characters:seraphine", "name": "Seraphine"}]}
    if builder == "continue":
        kwargs = {"appended": (("Continue", "system", "Continue naturally."),)}
    return store.context.compose_turn(cid, sid, **kwargs)


@pytest.mark.parametrize("builder", ["narrator", "npc", "opener"])
@pytest.mark.parametrize("custom_layout", [False, True])
def test_unset_fields_preserve_play_prompt_bytes(scope, builder, custom_layout):
    _, cid, sid = scope
    if custom_layout:
        layout.write_layout([{"id": s.id} for s in assemble.SECTIONS if s.id not in IDS])
        store.config.write_config(prompt_layout_enabled="on")
    before = list(_compose(cid, sid, builder)[0])
    for blank in ("", " \n "):
        _save(scope, (blank, blank, blank))
        assert list(_compose(cid, sid, builder)[0]) == before


@pytest.mark.parametrize("enabled", [(True, False, False), (False, True, False), (False, False, True), (True, True, True)])
def test_each_enabled_field_renders_once_in_catalog_order(scope, enabled):
    _, cid, sid = scope
    _save(scope, tuple(text if on else "" for text, on in zip(TEXTS, enabled, strict=True)))
    messages, detail = store.context.compose_turn(cid, sid)
    rows = [r for r in detail["sections"] if r["id"] in IDS]
    assert [r["id"] for r in rows] == [rid for rid, on in zip(IDS, enabled, strict=True) if on]
    assert all(r["tier"] == pack.LOCK_IN and not r["dropped"] for r in rows)
    for text, on in zip(TEXTS, enabled, strict=True):
        assert str(list(messages)).count(text) == int(on)


@pytest.mark.parametrize("builder", ["narrator", "npc", "group", "director", "continue", "opener", "adapt", "offscreen"])
def test_all_play_builders_use_addressed_scene_focus(scope, builder):
    _, cid, sid = scope
    _save(scope)
    store.scenes.create_scene(cid, "Seraphine", focus="Another scene's unrelated direction.")
    if builder == "offscreen":
        store.scenes.set_pcless(cid, sid)
    messages, detail = _compose(cid, sid, builder)
    for text in TEXTS:
        assert str(list(messages)).count(text) == 1
    assert "Another scene's unrelated direction." not in str(list(messages))
    assert set(IDS) <= {r["id"] for r in detail["sections"]}


def test_identical_defaults_are_additive_not_deduplicated(scope):
    _, cid, sid = scope
    _save(scope, (TEXTS[0], TEXTS[0], ""))
    assert str(list(store.context.compose_turn(cid, sid)[0])).count(TEXTS[0]) == 2


def test_protected_fields_survive_tiny_positive_budget(scope):
    _, cid, sid = scope
    _save(scope)
    store.config.write_config(context_budget="1")
    messages, detail = store.context.compose_turn(cid, sid)
    assert detail["total_tokens"] > detail["budget_tokens"] == 1
    rows = [r for r in detail["sections"] if r["id"] in IDS]
    assert len(rows) == 3
    assert all(not r["dropped"] and r["text"] in messages[0]["content"] for r in rows)
    assert all(r["tokens"] == store.tokens.count_tokens(r["text"]) for r in rows)


def test_random_macro_expansion_is_captured_once(scope, monkeypatch):
    _, cid, sid = scope
    _save(scope, ("{{random:winter,summer}}", "{{random:open,closed}}", "{{random:stay,leave}}"))
    draws = []

    def choose(options):
        draws.append(tuple(options))
        return options[0]

    monkeypatch.setattr(macros.random, "choice", choose)
    prepared, detail = store.context.compose_turn(cid, sid, model="glm-5.3")
    assert len(draws) == 3
    snapshot = prepared.snapshot()
    prepared.for_model("unknown")
    prepared.for_model("glm-5.3")
    restored = model_guidance.PreparedMessages.from_snapshot(snapshot, "glm-5.3")
    assert list(restored) == list(prepared)
    assert len(draws) == 3
    assert detail["prompt_field_statuses"] == dict.fromkeys(IDS, "ok")


def test_scope_read_failure_warns_without_prompt_prose(scope, monkeypatch):
    wid, cid, sid = scope
    _save(scope)
    real_read = store.atomic.read_text
    path = store.worlds.world_root(wid) / "default_prompt.md"

    def refuse(selected):
        if selected == path:
            raise PermissionError("private exception prose")
        return real_read(selected)

    monkeypatch.setattr(store.atomic, "read_text", refuse)
    messages, detail = store.context.compose_turn(cid, sid)
    assert detail["prompt_field_statuses"][IDS[0]] == "unreadable"
    assert not any(r["id"] == IDS[0] for r in detail["sections"])
    assert TEXTS[0] not in str(list(messages)) and "private exception prose" not in str(detail)
    assert TEXTS[1] in str(list(messages)) and TEXTS[2] in str(list(messages))


def test_frozen_profiles_keep_old_fields_while_fresh_composition_reads_edits(scope):
    _, cid, sid = scope
    _save(scope)
    prepared, _ = store.context.compose_turn(cid, sid)
    snapshot = deepcopy(prepared.snapshot())
    _save(scope, ("", "", ""))
    historical = model_guidance.PreparedMessages.from_snapshot(snapshot, "unknown")
    assert list(historical) == list(prepared)
    assert all(text in str(list(historical)) for text in TEXTS)
    assert all(text not in str(list(store.context.compose_turn(cid, sid)[0])) for text in TEXTS)


def test_clearing_one_scope_leaves_the_others_and_layout_controls_each_id(scope):
    _, cid, sid = scope
    _save(scope)
    store.default_prompts.set_campaign(cid, "")
    layout.write_layout([{"id": "world_overview", "enabled": False},
                         {"id": "scene_focus", "enabled": False}])
    store.config.write_config(prompt_layout_enabled="on")
    messages, detail = store.context.compose_turn(cid, sid)
    assert TEXTS[0] in str(list(messages))
    assert all(text not in str(list(messages)) for text in TEXTS[1:])
    assert [r["id"] for r in detail["sections"] if r["id"] in IDS] == [IDS[0]]


def test_authorial_fields_do_not_activate_lore_or_execute_jinja(scope):
    wid, cid, sid = scope
    store.entities.create_entity(store.worlds.world_root(wid), "lore", "Seraphine", "Mara's unspoken history.", keys="river")
    _save(scope, ("The river freezes. {% set value = 42 %}", "", "Offer Mara a choice."))
    messages, _ = store.context.compose_turn(cid, sid)
    text = str(list(messages))
    assert "{% set value = 42 %}" in text
    assert "Mara's unspoken history." not in text


def test_fatal_scene_read_is_not_downgraded_to_a_focus_warning(scope, monkeypatch):
    _, cid, sid = scope

    def refuse(_cid, _sid):
        raise PermissionError("scene refused")

    monkeypatch.setattr(store.scenes.read, "read_scene", refuse)
    with pytest.raises(PermissionError, match="scene refused"):
        store.context.compose_turn(cid, sid)
