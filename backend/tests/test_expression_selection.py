import asyncio

import pytest
from fastapi import HTTPException

from grimoire import store
from grimoire.routes.tracker import _expression_selection as selection
from tests import inference_fixtures as fx
from tests.llm_fakes import FakeLLM, decision_reply

pytestmark = pytest.mark.tracker


def test_public_context_filters_private_and_computed_values():
    actor = {'fields': {'clothing': {'value': 'coat', 'aware': 'present'},
                        'true_mood': {'value': 'SECRET', 'aware': 'self'},
                        'expression-mood': {'value': 'old', 'aware': 'present'}}}
    assert selection.public_values(actor) == {'clothing': 'coat'}


def test_clear_pair_preserves_narrative():
    snapshot = {'characters:mara': {'present': True, 'fields': {
        'expression-outfit': {'value': 'old', 'aware': 'present'},
        'expression-mood': {'value': 'old', 'aware': 'present'},
        'clothing': {'value': 'coat', 'aware': 'present'}}}}
    selection.clear_pair(snapshot, 'characters:mara')
    assert snapshot['characters:mara']['fields'] == {'clothing': {'value': 'coat', 'aware': 'present'}}


@pytest.fixture
def expression_scene(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    fx.primary(client)
    fx.put_settings(client, {"roles": {"decision": {"selection": {
        "provider": "openrouter", "model": "decision"}, "fallback": {"provider": ""}}}})
    wid = store.worlds.create_world("Realm")
    root = store.worlds.world_root(wid)
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Opening")
    refs = []
    for _ in range(2):
        actor, version = store.characters.create_character(root, "Mara")
        store.appearances.appear(cid, sid, "characters", actor, version, "npc", narrate=False)
        refs.append("characters:" + actor)
    index = store.scenes.append_message(cid, sid, "user", "They enter the hall.")
    ident = store.scenes.scene_identity(cid, sid)
    key = store.tracker.walk.key_at(cid, sid, index)
    prep = {"index": index, "present": set(refs), "post": index,
            "context_posts": [], "post_msg": {"speaker": "You", "content": "They enter the hall."}}
    snapshot = {ref: {"present": True, "name": "Mara", "fields": {
        "clothing": {"value": "coat", "aware": "present"},
        "true_mood": {"value": "SECRET", "aware": "self"},
        "expression-outfit": {"value": "stale", "aware": "present"},
        "expression-mood": {"value": "stale", "aware": "present"}}} for ref in refs}
    return cid, sid, ident, key, prep, snapshot, refs


def collection(outfits):
    return {"effective_revision": "revision-one", "outfits": {
        outfit: {"label": outfit, "clothing_aliases": [outfit], "expressions": {
            mood: {"available": True, "image_id": mood} for mood in moods}}
        for outfit, moods in outfits.items()}}


def test_ordered_batches_custom_slugs_and_duplicate_names(expression_scene, monkeypatch):
    cid, sid, ident, key, prep, snapshot, refs = expression_scene
    monkeypatch.setattr(selection.read, "effective", lambda target: collection(
        {"coat": ["quiet-smile", "watchful"], "formal": ["solemn", "bright"]}))
    fake = FakeLLM([[decision_reply({"choice": "coat"}, {"choice": "formal"})],
                    [decision_reply({"choice": "watchful"}, {"choice": "bright"})]])
    # Stable ordering is part of matching replies to actor references.
    prep["present"] = refs
    result, metadata, summary = asyncio.run(selection.choose(cid, sid, ident, key, prep, snapshot, fake))
    assert [(result[r]["fields"]["expression-outfit"]["value"],
             result[r]["fields"]["expression-mood"]["value"]) for r in refs] == [
                 ("coat", "watchful"), ("formal", "bright")]
    assert summary["selected"] == 2 and summary["failed"] == 0
    assert fake.calls == 2
    for req in fake.requests:
        prompt = str(req["messages"])
        assert "SECRET" not in prompt and "stale" not in prompt
        assert all(ref in prompt for ref in refs)
    assert set(metadata["versions"]) == set(refs)
    calls = list(store.usage.calls(days=1))
    assert {row["task"] for row in calls} == {"tracker-expression-outfit", "tracker-expression-mood"}


def test_singleton_and_empty_assets_make_no_calls(expression_scene, monkeypatch):
    cid, sid, ident, key, prep, snapshot, refs = expression_scene
    monkeypatch.setattr(selection.read, "effective", lambda t: collection(
        {"coat": ["custom-calm"]} if t.actor_id == refs[0].split(":")[1] else {}))
    fake = FakeLLM([["must not be used"]])
    result, _, summary = asyncio.run(selection.choose(cid, sid, ident, key, prep, snapshot, fake))
    assert result[refs[0]]["fields"]["expression-mood"]["value"] == "custom-calm"
    assert "expression-outfit" not in result[refs[1]]["fields"]
    assert summary == {"selected": 1, "failed": 0, "skipped": 1, "errors": {}}
    assert fake.calls == 0


def test_partial_mood_reply_clears_old_pair(expression_scene, monkeypatch):
    cid, sid, ident, key, prep, snapshot, refs = expression_scene
    prep["present"] = refs
    monkeypatch.setattr(selection.read, "effective", lambda t: collection({"coat": ["a", "b"]}))
    fake = FakeLLM([[decision_reply({"choice": "a"}, {"choice": "invalid"})]])
    result, _, summary = asyncio.run(selection.choose(cid, sid, ident, key, prep, snapshot, fake))
    assert result[refs[0]]["fields"]["expression-mood"]["value"] == "a"
    assert "expression-outfit" not in result[refs[1]]["fields"]
    assert summary["selected"] == 1 and summary["failed"] == 1


def test_disconnected_decision_role_preserves_narrative(expression_scene, monkeypatch, client):
    cid, sid, ident, key, prep, snapshot, refs = expression_scene
    def unavailable(*args, **kwargs):
        raise HTTPException(409, detail="Decision role disconnected")
    monkeypatch.setattr(selection, "require_inference", unavailable)
    monkeypatch.setattr(selection.read, "effective", lambda t: collection({"coat": ["a", "b"]}))
    fake = FakeLLM([["must not be used"]])
    result, _, summary = asyncio.run(selection.choose(cid, sid, ident, key, prep, snapshot, fake))
    assert summary["failed"] == 2 and fake.calls == 0
    for ref in refs:
        assert result[ref]["fields"]["clothing"] == snapshot[ref]["fields"]["clothing"]
        assert "expression-mood" not in result[ref]["fields"]


@pytest.mark.parametrize('switch', ['tracker', 'mood'])
def test_switch_off_between_stages_prevents_second_call(expression_scene, monkeypatch, switch):
    cid, sid, ident, key, prep, snapshot, refs = expression_scene
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection({'coat': ['a', 'b'], 'formal': ['c', 'd']}))
    original = selection.operations.decide
    async def after_outfit(task, *args, **kwargs):
        result = await original(task, *args, **kwargs)
        if switch == 'tracker':
            monkeypatch.setattr(store.tracker.settings, 'enabled', lambda cid: False)
        else:
            store.tracker.fields.write_scene_layer(cid, sid, {'off': ['expression-mood']})
        return result
    monkeypatch.setattr(selection.operations, 'decide', after_outfit)
    fake = FakeLLM([[decision_reply({'choice': 'coat'}, {'choice': 'formal'})]])
    result, _, summary = asyncio.run(selection.choose(cid, sid, ident, key, prep, snapshot, fake))
    assert fake.calls == 1
    assert summary['selected'] == 0
    assert all('expression-outfit' not in result[ref]['fields'] for ref in refs)


def test_oversize_actor_options_do_not_refuse_other_actor(expression_scene, monkeypatch):
    cid, sid, ident, key, prep, snapshot, refs = expression_scene
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection(
        {f'outfit{i}': ['calm'] for i in range(256)} if t.actor_id == refs[0].split(':')[1]
        else {'coat': ['a', 'b']}))
    fake = FakeLLM([[decision_reply({'choice': 'b'})]])
    result, _, summary = asyncio.run(selection.choose(cid, sid, ident, key, prep, snapshot, fake))
    assert summary['selected'] == 1 and summary['failed'] == 1
    assert 'expression-outfit' not in result[refs[0]]['fields']
    assert result[refs[1]]['fields']['expression-mood']['value'] == 'b'
