"""Refresh changes only presentation; source changes make its result obsolete."""
import asyncio
import copy

import pytest

from grimoire import routes, store
from grimoire.routes.tracker import _expression_refresh as refresh
from grimoire.routes.tracker import _expression_selection as selection
from grimoire.routes.tracker import _update_run
from grimoire.store.tracker import expression_fields
from tests.llm_fakes import FakeLLM, decision_reply

from .test_expression_selection import collection
from .test_expression_selection import expression_scene as expression_scene

pytestmark = pytest.mark.tracker


@pytest.fixture
def recorded(expression_scene):
    cid, sid, ident, key, prep, snapshot, refs = expression_scene
    store.tracker.records.save(cid, ident, key, snapshot, changed=[],
                              fields_digest=store.tracker.fields.digest(store.tracker.fields.effective(cid, sid)),
                              model='narrative-model')
    store.tracker.records.set_flags(cid, ident, [key], 'upstream_changed')
    return cid, sid, ident, key, prep, snapshot, refs


def test_refresh_route_preserves_narrative_and_exposes_terminal_run(recorded, client, monkeypatch):
    cid, sid, ident, key, prep, _, _refs = recorded
    before = store.tracker.records.read_snapshot(cid, ident, key)
    entry = store.tracker.records.read_index(cid, ident)[key]
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection(
        {'coat': ['calm', 'bright'], 'formal': ['solemn', 'smile']}))
    fake = FakeLLM([[decision_reply({'choice': 'coat'}, {'choice': 'formal'})],
                    [decision_reply({'choice': 'bright'}, {'choice': 'solemn'})]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f'/api/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/expressions')
    assert response.status_code == 202, response.text
    run_id = response.json()['run']['id']
    run = client.app.state.runs.get(run_id, ('scene', cid, ident), identity=ident)
    assert run.terminal.wait(10)
    assert run.state == 'landed', run.error
    assert run.result['applied'] is True and run.result['selected'] == 2
    after = store.tracker.records.read_snapshot(cid, ident, key)
    assert expression_fields.narrative_body(after) == expression_fields.narrative_body(before)
    assert store.tracker.records.read_index(cid, ident)[key] == entry
    assert after['expressions']['post_id'] == store.scenes.read_scene(cid, sid)['messages'][prep['index']]['post_id']
    assert fake.calls == 2
    listed = client.get(f'/api/campaigns/{cid}/scenes/{sid}/tracker/runs').json()['runs']
    assert any(r['id'] == run_id and r['state'] == 'landed' for r in listed)


def test_refresh_missing_snapshot_requires_generation(expression_scene, client):
    cid, sid, _, key, prep, _, _ = expression_scene
    base = f'/api/campaigns/{cid}/scenes/{sid}'
    post = store.scenes.read_scene(cid, sid)['messages'][prep['index']]['post_id']
    for url in (f'{base}/tracker/records/{key}/expressions', f'{base}/vn/posts/{post}/expressions'):
        response = client.post(url)
        assert response.status_code == 409
        assert 'Generate tracker' in response.json()['detail']


@pytest.mark.parametrize('change', ['edit', 'delete', 'new-generation', 'flags', 'body'])
def test_obsolete_refresh_never_publishes(recorded, monkeypatch, change):
    cid, sid, ident, key, _, _, refs = recorded
    prep, body, entry = refresh._capture(cid, sid, ident, key)
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection({'coat': ['calm']}))
    snapshot, metadata, _ = asyncio.run(selection.choose(cid, sid, ident, key, prep, body['snapshot'],
                                                        FakeLLM([['unused']])))
    if change == 'edit':
        store.scenes.edit_message(cid, sid, prep['index'], 'Changed after the decision began.')
    elif change == 'delete':
        store.tracker.records.discard(cid, ident, [key])
    elif change == 'new-generation':
        store.tracker.records.mark_pending(cid, ident, key)
    elif change == 'flags':
        store.tracker.records.set_flags(cid, ident, [key], 'upstream_changed')
    else:
        amended = copy.deepcopy(body['snapshot'])
        amended[refs[0]]['fields']['clothing']['value'] = 'A new coat'
        store.tracker.records.save(cid, ident, key, amended, changed=[], fields_digest='new', model='new-model')
    newest = store.tracker.records.read_snapshot(cid, ident, key)
    assert refresh._commit(cid, sid, ident, key, prep, body, entry, snapshot, metadata) is False
    assert store.tracker.records.read_snapshot(cid, ident, key) == newest


def test_changed_collection_clears_pair_at_publication(recorded, monkeypatch):
    cid, sid, ident, key, _, _, refs = recorded
    prep, body, entry = refresh._capture(cid, sid, ident, key)
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection({'coat': ['calm']}))
    snapshot, metadata, _ = asyncio.run(selection.choose(cid, sid, ident, key, prep, body['snapshot'],
                                                        FakeLLM([['unused']])))
    monkeypatch.setattr(selection.read, 'effective', lambda t: {**collection({'formal': ['solemn']}),
                                                              'effective_revision': 'revision-two'})
    assert refresh._commit(cid, sid, ident, key, prep, body, entry, snapshot, metadata)
    after = store.tracker.records.read_snapshot(cid, ident, key)
    for ref in refs:
        assert not expression_fields.KEYS.intersection(after['snapshot'][ref]['fields'])
    assert len(after['expressions']['errors']) == 2


def test_full_regeneration_recomputes_expressions(recorded, monkeypatch):
    cid, sid, ident, key, _, _, refs = recorded
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection({'coat': ['custom-calm']}))
    fake = FakeLLM([['{"changes":{}}']])
    result = asyncio.run(_update_run._update_locked(cid, sid, key, None, fake, ident))
    assert result['state'] == 'landed', result
    assert result['result']['expressions']['selected'] == 2
    after = store.tracker.records.read_snapshot(cid, ident, key)
    for ref in refs:
        assert after['snapshot'][ref]['fields']['expression-mood']['value'] == 'custom-calm'
    assert fake.calls == 1  # Narrative only; both sprite stages are singleton choices.


def test_swipe_prevents_expression_refresh(recorded, monkeypatch):
    cid, sid, ident, _, _, snapshot, _ = recorded
    store.scenes.append_message(cid, sid, 'assistant', 'First reply.', speaker='Mara')
    record, = store.responses.migrate(cid, sid)
    key = store.tracker.walk.ordered_keys(cid, sid)[-1][1]
    store.tracker.records.save(cid, ident, key, snapshot, changed=[], fields_digest='test', model='test')
    prep, body, entry = refresh._capture(cid, sid, ident, key)
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection({'coat': ['calm']}))
    selected, metadata, _ = asyncio.run(selection.choose(cid, sid, ident, key, prep, body['snapshot'],
                                                        FakeLLM([['unused']])))
    store.responses.save_variant(cid, sid, record['id'], 'Replacement reply.', 'complete')
    assert refresh._commit(cid, sid, ident, key, prep, body, entry, selected, metadata) is False
    assert store.tracker.records.read_snapshot(cid, ident, key) == body


def test_missing_image_clears_pair_even_with_unchanged_manifest(recorded, monkeypatch):
    cid, sid, ident, key, _, _, refs = recorded
    prep, body, entry = refresh._capture(cid, sid, ident, key)
    offered = collection({'coat': ['calm']})
    monkeypatch.setattr(selection.read, 'effective', lambda t: offered)
    snapshot, metadata, _ = asyncio.run(selection.choose(cid, sid, ident, key, prep, body['snapshot'],
                                                        FakeLLM([['unused']])))
    offered['outfits']['coat']['expressions']['calm']['available'] = False
    assert refresh._commit(cid, sid, ident, key, prep, body, entry, snapshot, metadata)
    after = store.tracker.records.read_snapshot(cid, ident, key)
    assert all('expression-outfit' not in after['snapshot'][ref]['fields'] for ref in refs)
    assert len(after['expressions']['errors']) == 2


def test_deleted_historical_version_preserves_other_actor_and_narrative(recorded, monkeypatch):
    cid, sid, ident, key, _, _, refs = recorded
    prep, body, entry = refresh._capture(cid, sid, ident, key)
    monkeypatch.setattr(selection.read, 'effective', lambda t: collection({'coat': ['calm']}))
    snapshot, metadata, _ = asyncio.run(selection.choose(cid, sid, ident, key, prep, body['snapshot'],
                                                        FakeLLM([['unused']])))
    actor = refs[0].split(':')[1]
    version = metadata['versions'][refs[0]]
    root = store.overlay.char_root(cid, actor)
    store.characters.create_version(root, actor, 'later', store.characters.read_card(root, actor, version))
    store.characters.delete_version(root, actor, version)
    assert refresh._commit(cid, sid, ident, key, prep, body, entry, snapshot, metadata)
    after = store.tracker.records.read_snapshot(cid, ident, key)
    assert 'expression-outfit' not in after['snapshot'][refs[0]]['fields']
    assert after['snapshot'][refs[1]]['fields']['expression-mood']['value'] == 'calm'
    assert expression_fields.narrative_body(after) == expression_fields.narrative_body(body)
