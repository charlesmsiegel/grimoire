"""Actual accepted relationship and scene changes cannot silently combine offline."""
import pytest

from grimoire.sync import plan_sync, sync, sync_until_quiet, resolve, resolve_ledger, ResolveRefused, MERGE
from grimoire.store import etag
from effects_fixtures import CHAT, give
from ledger_fixtures import Device, build_base
from sync_helpers import DESKTOP, PHONE, handles
from test_effect_continuity import write_turn
from test_sync_ledger import ledger_files


@pytest.fixture
def pair(tmp_path):
    base = build_base(tmp_path)
    made = {n: Device(base, tmp_path, n) for n in (DESKTOP, PHONE)}
    write_turn(made[DESKTOP].store, made[DESKTOP].handles)
    sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles())
    yield made
    for d in made.values(): d.close()


@pytest.mark.parametrize('first', [DESKTOP, PHONE])
def test_relationship_after_scene_vs_offline_edit_stays_reviewed(pair, first):
    desk, phone = pair[DESKTOP], pair[PHONE]
    desk.accept(give())
    write_turn(phone.store, phone.handles, text='The key was destroyed before the transfer.')
    before = {n: ledger_files(d.store) for n, d in pair.items()}
    path = 'campaigns/c/turns/t-1.000001.json'
    tags = {n: etag(d.store.read_exact(path)) for n, d in pair.items()}
    other = PHONE if first == DESKTOP else DESKTOP
    for attempt in range(2):
        report = sync(pair[first].store, pair[other].store, handles())
        assert report.conflict(path).code == 'semantic_review'
        assert {n: ledger_files(d.store) for n, d in pair.items()} == before
        assert all(etag(d.store.read_exact(path)) == tags[n] for n, d in pair.items())
        if attempt == 0:
            from grimoire.store import LibraryStore
            for d in pair.values():
                root = d.store.root
                d.store.close()
                d.store = LibraryStore.open(root)
    with pytest.raises(ResolveRefused, match='semantic'):
        resolve(desk.store, phone.store, path, tags[PHONE], tags, handles())
    plan = plan_sync(desk.store, phone.store)
    from grimoire.sync.units import token
    with pytest.raises(ResolveRefused, match='semantic'):
        resolve_ledger(desk.store, phone.store, 'c', MERGE, token(plan, 'c'), handles())


@pytest.mark.parametrize('first', [DESKTOP, PHONE])
def test_unrelated_new_turn_converges_with_accepted_effect(pair, first):
    from ledger_fixtures import OTHER
    desk, phone = pair[DESKTOP], pair[PHONE]
    desk.accept(give())
    write_turn(phone.store, phone.handles, chat=OTHER)
    other = PHONE if first == DESKTOP else DESKTOP
    assert not sync_until_quiet(pair[first].store, pair[other].store, handles()).conflicts
    assert ledger_files(desk.store) == ledger_files(phone.store)


def test_direct_relation_and_existing_scene_edit_require_potential_review(pair):
    from grimoire.graphstore import RelationLibrary
    from grimoire.core.knowledge import Moment
    from effects_fixtures import TAM, INES
    from store_helpers import OWNER
    from chat_fixtures import Clock, Ids
    desk, phone = pair[DESKTOP], pair[PHONE]
    change = RelationLibrary(desk.store, desk.handles.app, clock=Clock(), new_id=Ids(), owner=OWNER)
    ref = change.add('c', 'has_met', TAM, INES, since=Moment(5), provenance='director').commit().relation
    write_turn(phone.store, phone.handles, text='Offline scene revision.')
    path = f'campaigns/c/relations/{ref.relation_id}.json'
    report = sync(desk.store, phone.store, handles())
    assert report.conflict(path).code == 'semantic_review'
    expected = {n: None if d.store.read_exact(path) is None else etag(d.store.read_exact(path)) for n, d in pair.items()}
    with pytest.raises(ResolveRefused, match='semantic'):
        resolve(desk.store, phone.store, path, expected[DESKTOP], expected, handles())


@pytest.mark.parametrize('legacy', [False, True])
def test_missing_or_malformed_provenance_cannot_silently_merge_edit(pair, legacy):
    from dataclasses import replace
    from grimoire.store.jsonfmt import thaw
    desk, phone = pair[DESKTOP], pair[PHONE]
    ref = desk.accept(give())
    stored = desk.store.campaign_accepted('c').records[ref]
    payload = thaw(stored.record.payload)
    if legacy: payload.pop('continuity_sources')
    else: payload['continuity_sources'] = {'version': 999}
    txn = desk.store.transaction()
    txn.put(replace(stored.record, payload=payload), stored.etag)
    txn.commit(desk.handles.app)
    write_turn(phone.store, phone.handles, text='An incompatible scene.')
    report = sync(desk.store, phone.store, handles())
    assert report.conflict('campaigns/c/turns/t-1.000001.json').code == 'semantic_review'
    assert not phone.store.campaign_accepted('c').records


def test_scene_changed_after_clean_plan_stops_event_apply(pair):
    from grimoire.sync import apply_sync, SyncInterrupted
    desk, phone = pair[DESKTOP], pair[PHONE]
    desk.accept(give())
    plan = plan_sync(desk.store, phone.store, handles())
    write_turn(phone.store, phone.handles, text='Changed after planning.')
    before = ledger_files(phone.store)
    with pytest.raises(SyncInterrupted, match='semantic'):
        apply_sync(plan, handles())
    assert ledger_files(phone.store) == before


def test_legacy_event_vs_existing_scene_deletion_is_reviewed(pair):
    from dataclasses import replace
    from grimoire.store.jsonfmt import thaw
    desk, phone = pair[DESKTOP], pair[PHONE]
    ref = desk.accept(give())
    stored = desk.store.campaign_accepted('c').records[ref]
    payload = thaw(stored.record.payload)
    payload.pop('continuity_sources')
    txn = desk.store.transaction()
    txn.put(replace(stored.record, payload=payload), stored.etag)
    txn.commit(desk.handles.app)
    path = 'campaigns/c/turns/t-1.000001.json'
    (phone.store.root / path).unlink()  # owner removes canonical file while offline
    report = sync(desk.store, phone.store, handles())
    assert report.conflict(path).code == 'semantic_review'
    assert not phone.store.campaign_accepted('c').records


def test_identical_malformed_declarations_report_review_without_differing_scenes(pair):
    from grimoire.core.identity import ContinuityRef
    from grimoire.store import ScopeRecord
    from store_helpers import OWNER
    for d in pair.values():
        txn = d.store.transaction()
        txn.put(ScopeRecord(ContinuityRef('c', 'bad'), 'future.continuity/99', {}, owner=OWNER), None)
        txn.commit(d.handles.app)
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert any(c.code == 'semantic_review' for c in report.conflicts)


def test_generic_turn_resolve_rechecks_new_event_after_initial_review(pair, monkeypatch):
    from grimoire.sync import semantic
    desk, phone = pair[DESKTOP], pair[PHONE]
    write_turn(phone.store, phone.handles, text='Edited before an event arrives mid-resolution.')
    path = 'campaigns/c/turns/t-1.000001.json'
    expected = {n: etag(d.store.read_exact(path)) for n, d in pair.items()}
    original = semantic.reviews
    changed = []
    def during(plan):
        result = original(plan)
        changed.append(True)
        if len(changed) == 2:
            desk.accept(give())
        return result
    monkeypatch.setattr(semantic, 'reviews', during)
    with pytest.raises(ResolveRefused, match='semantic'):
        resolve(desk.store, phone.store, path, expected[PHONE], expected, handles())
    assert etag(desk.store.read_exact(path)) == expected[DESKTOP]


def test_native_fixture_imports_in_fresh_interpreter():
    import os
    import subprocess
    import sys
    env = dict(os.environ, PYTHONPATH='backend/src:backend/tests')
    result = subprocess.run([sys.executable, '-c', 'from ledger_fixtures import Device'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('changed_device', [DESKTOP, PHONE])
@pytest.mark.parametrize('mutation', ['edit', 'remove'])
def test_declared_holder_endpoint_changes_token_and_stops_scene_apply(pair, changed_device, mutation):
    from grimoire.core.identity import ContinuityRef
    from grimoire.continuity import scene_revision
    from grimoire.store import ScopeRecord
    from grimoire.sync import apply_sync, semantic, SyncInterrupted
    from grimoire.store.envelope import parse_scope_record
    from grimoire.store.jsonfmt import parse_strict
    from effects_fixtures import TAM
    from store_helpers import OWNER
    desk, phone = pair[DESKTOP], pair[PHONE]
    source = 'campaigns/c/holdings/character/tam.json'
    dependent = 'campaigns/c/turns/t-1.000001.json'
    turn = parse_scope_record(parse_strict(desk.store.read_exact(dependent)))[0]
    payload = {'source': {'path': source, 'revision': etag(desk.store.read_exact(source))},
               'dependent': {'path': dependent, 'revision': scene_revision(turn.payload)},
               'reason': 'causal_action', 'certainty': 'known'}
    for d in pair.values():
        txn = d.store.transaction()
        txn.put(ScopeRecord(ContinuityRef('c', 'holder-use'), 'grimoire.continuity-dependency/1', payload, owner=OWNER), None)
        txn.commit(d.handles.app)
    write_turn(phone.store, phone.handles, number=2)
    plan = plan_sync(desk.store, phone.store, handles())
    before = semantic.token(plan, 'c')
    if mutation == 'edit':
        pair[changed_device].hand_edit(TAM, 'coin', 9)
    else:
        (pair[changed_device].store.root / source).unlink()
    after = semantic.token(plan_sync(desk.store, phone.store, handles()), 'c')
    assert before != after
    with pytest.raises(SyncInterrupted, match='semantic'):
        apply_sync(plan, handles())
    assert desk.store.read_exact('campaigns/c/turns/t-1.000002.json') is None
