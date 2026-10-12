"""Focus rides the existing metadata call; standing inputs are a captured snapshot."""

import json

import pytest

from grimoire import routes, store
from grimoire.store.context import layout, macros
from tests import draft_runs
from tests.llm_fakes import FakeOpenRouterComplete


@pytest.fixture
def scope(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    store.overlay.create_character(cid, "Mara", "main")
    store.overlay.create_entity(cid, "locations", "Saltmarch")
    return wid, cid


@pytest.mark.parametrize("kind", ["suggestions", "offscreen", "intent"])
def test_creation_instructions_explicitly_name_focus_prompt_readers(client, scope, kind):
    _, cid = scope
    if kind == "intent":
        messages, _ = store.suggest.build_intent_request(cid, "Mara waits.")
    else:
        snapshot = store.suggest.build_snapshot(cid, offscreen=kind == "offscreen")
        messages = store.suggest.build_prompt(snapshot, offscreen=kind == "offscreen")
    instruction = messages[0]["content"]
    assert "Focus is shared with narrator and NPC prompt readers" in instruction


def _generate(client, cid, kind, value, offscreen=False):
    reply = {"title": "Mara", "premise": "Mara waits.", "date": "", "location": "saltmarch",
             "cast": ["characters:mara"], **value}
    payload = {"suggestions": [reply]} if kind == "suggestions" else reply
    fake = FakeOpenRouterComplete(json.dumps(payload))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    suffix = "scene-suggestions" if kind == "suggestions" else "scene-intent"
    body = {"offscreen": offscreen}
    body.update({"rank": False} if kind == "suggestions" else {"text": "Mara waits at Saltmarch to consider her options."})
    answer = draft_runs.post(client, f"/api/campaigns/{cid}/{suffix}", json=body)
    assert answer.status_code == 200, answer.json()
    result = answer.json()
    card = result["suggestions"][0] if kind == "suggestions" else result
    return card, result, fake


@pytest.mark.parametrize("kind", ["suggestions", "intent"])
@pytest.mark.parametrize("offscreen", [False, True])
def test_suggestion_and_intent_generate_focus_in_same_call(client, scope, kind, offscreen):
    wid, cid = scope
    store.default_prompts.set_world(wid, "The river freezes.")
    store.default_prompts.set_campaign(cid, "The harbor stays open.")
    card, result, fake = _generate(client, cid, kind, {"focus": "  Let Mara choose.\nGently. "}, offscreen)
    assert fake.calls == 1
    assert card["focus"] == "Let Mara choose. Gently."
    assert card["cast"][0]["id"] == "mara" and card["location"]["id"] == "saltmarch"
    assert result["prompt_field_statuses"] == {"world_default_prompt": "ok", "campaign_default_prompt": "ok"}
    assert "The river freezes." in fake.messages[0]["content"]
    assert "The harbor stays open." in fake.messages[0]["content"]
    assert '"focus"' in fake.messages[0]["content"]
    assert store.scenes.list_scenes(cid) == []
    if kind == "intent":
        assert fake.schemas[0]["properties"]["focus"] == {"type": "string"}
        assert "focus" in fake.schemas[0]["required"]


@pytest.mark.parametrize("value", [{}, {"focus": None}, {"focus": 42}, {"focus": []}, {"focus": {}}])
@pytest.mark.parametrize("kind", ["suggestions", "intent"])
def test_missing_or_nonstring_focus_keeps_valid_draft(client, scope, kind, value):
    _, cid = scope
    card, _, fake = _generate(client, cid, kind, value)
    assert fake.calls == 1
    assert card["focus"] == "" and card["title"] == "Mara"
    assert card["cast"][0]["id"] == "mara" and card["location"]["id"] == "saltmarch"


def test_creation_defaults_follow_layout_without_scene_binding(client, scope, monkeypatch):
    wid, cid = scope
    store.scenes.create_scene(cid, "Mara", focus="Another scene's unrelated direction.")
    store.default_prompts.set_world(wid, "The {{random:river,harbor}} freezes. {{user}} {{char}} {{date}}")
    store.default_prompts.set_campaign(cid, "The harbor stays open.")
    original = macros.expand_macros
    calls = []

    def observe(text, subs, selected, sid, *, datetime_subs=None):
        calls.append((subs, selected, sid, datetime_subs))
        return original(text, subs, selected, sid, datetime_subs=datetime_subs)

    monkeypatch.setattr(macros, "expand_macros", observe)
    captured = store.suggest.creation_defaults(cid)
    assert len(captured["sections"]) == 2
    assert calls == [({}, cid, "", {}), ({}, cid, "", {})]
    text = "\n".join(captured["sections"])
    assert "{{user}} {{char}}" in text and "{{date}}" not in text
    assert "Another scene's unrelated direction." not in text
    assert text.index("# World default prompt") < text.index("# Campaign default prompt")
    layout.write_layout([{"id": "world_default_prompt", "enabled": False},
                         {"id": "world_default_prompt", "enabled": True}])
    store.config.write_config(prompt_layout_enabled="on")
    assert len(store.suggest.creation_defaults(cid)["sections"]) == 1
    store.default_prompts.set_campaign(cid, "")
    assert store.suggest.creation_defaults(cid)["sections"] == []


def test_supplied_creation_capture_is_reused_without_resolving_again(client, scope, monkeypatch):
    wid, cid = scope
    store.default_prompts.set_world(wid, "The river freezes.")
    capture = store.suggest.creation_defaults(cid)
    snapshot = store.suggest.build_snapshot(cid)

    def refuse(_cid):
        raise AssertionError("captured fields were read again")

    monkeypatch.setattr(store.suggest, "creation_defaults", refuse)
    assert "The river freezes." in store.suggest.build_prompt(snapshot, defaults=capture)[0]["content"]
    assert "The river freezes." in store.suggest.build_intent_request(cid, "Mara waits.", defaults=capture)[0][0]["content"]


def test_creation_field_read_failure_returns_captured_warning(client, scope, monkeypatch):
    wid, cid = scope
    store.default_prompts.set_campaign(cid, "The harbor stays open.")
    real_read = store.atomic.read_text
    world_path = store.worlds.world_root(wid) / "default_prompt.md"

    def refuse(path):
        if path == world_path:
            raise PermissionError("private failure prose")
        return real_read(path)

    monkeypatch.setattr(store.atomic, "read_text", refuse)
    _, result, fake = _generate(client, cid, "suggestions", {})
    assert result["prompt_field_statuses"]["world_default_prompt"] == "unreadable"
    assert "private failure prose" not in str(result) + str(fake.messages)
    assert "The harbor stays open." in fake.messages[0]["content"]


def test_focus_survives_save_list_pick_create(client, scope):
    _, cid = scope
    saved = client.post(f"/api/campaigns/{cid}/scene-ideas", json={
        "title": "Mara", "premise": "Mara waits.", "focus": " Let Mara choose.\nGently. "})
    assert saved.status_code == 200
    idea = client.get(f"/api/campaigns/{cid}/scene-ideas?greetings=false").json()[0]
    assert idea["focus"] == "Let Mara choose. Gently."
    response = client.post(f"/api/campaigns/{cid}/scenes", json={"title": idea["title"], "focus": idea["focus"]})
    assert response.status_code == 200
    assert store.scenes.focus.read(cid, response.json()["id"])["text"] == idea["focus"]


@pytest.mark.parametrize("value", [None, 42, [], {}])
def test_saved_idea_focus_requires_a_string(client, scope, value):
    _, cid = scope
    response = client.post(f"/api/campaigns/{cid}/scene-ideas", json={"title": "Mara", "focus": value})
    assert response.status_code == 422
