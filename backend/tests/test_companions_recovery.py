"""Capture recovery uses the real journal and rechecks capture-specific postconditions."""
import json
from dataclasses import replace

import pytest

from grimoire.companions import CapturePreview, CaptureRefused
from grimoire.core import DIRECTOR
from grimoire.partystore import PartyLibrary
from chat_fixtures import Clock
from grimoire.store import LibraryStore, ProcessLocker, StaleWrite
from grimoire.testing import Crash, RootedFileOps, SimulatedCrash, Target

from companion_fixtures import C, GRUB, MARA, SNIK, balls, capture, library, relations, request, seed
from effects_fixtures import Handles, moment
from graph_fixtures import relation_library
from party_fixtures import TOMA, party_library
from test_companions import world


def partial(store, change, *, event=False, relations=False, holders=False):
    effect = change.effect
    txn = store.transaction()
    puts = list(effect.puts) if event else []
    if relations:
        puts += [put for part in effect.relation_changes for put in part.puts]
    if holders:
        puts += [put for part in effect.holdings_changes for put in part.puts]
    for put in puts:
        txn.put(*put)
    txn.commit(effect.library.handle)


@pytest.mark.parametrize('shape', ['event', 'relations', 'holders'])
def test_partial_capture_completes_or_adopts_exactly_once(world, shape):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request())
    change = lib.accept(preview, controls=())
    partial(store, change, **{shape: True})
    if shape == 'event':
        finished = lib.complete(change.effect.ref, expected=store.read(change.effect.ref).etag)
    else:
        finished = lib.adopt(preview, controls=())
    assert finished.commit().event == change.effect.ref
    assert balls(store, handles) == 2 and len(relations(store)) == 2
    assert lib.accept(lib.preview(request()), controls=()).preview() == {}
    assert not lib.effects.continuity(C, as_of=7, requested_by=DIRECTOR).problems


def test_device_free_adoption_retains_attempt_metadata_across_reopen_and_revoke(world):
    store, handles = world
    lib = library(store, handles)
    req = request(device=None)
    preview = lib.preview(req)
    change = lib.accept(preview, controls=())
    partial(store, change, relations=True)
    lib.adopt(preview, controls=()).commit()
    root = store.root
    store.close()
    with LibraryStore.open(root, locker=ProcessLocker()) as reopened:
        lib = library(reopened, handles)
        assert lib.accept(lib.preview(req), controls=()).preview() == {}
        with pytest.raises(CaptureRefused, match='attempt_conflict'):
            lib.preview(replace(req, capacity=2))
        ref = change.effect.ref
        lib.effects.revoke(ref, reason='undo', expected=reopened.read(ref).etag).commit()
        with pytest.raises(CaptureRefused, match='attempt_revoked'):
            lib.preview(req)
        assert balls(reopened, handles) == 3


@pytest.mark.parametrize('field,value', [('attempt_id', 'changed'), ('capacity', 99)])
def test_orphan_provenance_binds_fields_omitted_by_effect_fingerprint(world, field, value):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request(device=None))
    change = lib.accept(preview, controls=())
    partial(store, change, relations=True)
    forged = replace(preview, request=replace(preview.request, **{field: value}))
    assert forged.proposal == preview.proposal
    with pytest.raises(CaptureRefused, match='capture_identity_mismatch'):
        lib.adopt(forged, controls=())
    assert not store.campaign_accepted(C).records


def test_failed_capture_orphan_binds_creature_even_without_relation(world):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request(outcome='failed'))
    change = lib.accept(preview, controls=())
    partial(store, change, holders=True)
    payload = preview.to_payload()
    payload['request']['creature'] = str(GRUB)
    forged = CapturePreview.from_payload(payload)
    assert forged.proposal.fingerprint == preview.proposal.fingerprint
    with pytest.raises(CaptureRefused, match='capture_identity_mismatch'):
        lib.adopt(forged, controls=())
    assert not store.campaign_accepted(C).records and balls(store, handles) == 2


@pytest.mark.parametrize('mode', ['complete', 'adopt'])
@pytest.mark.parametrize('conflict', ['full', 'trained'])
def test_recovery_refuses_post_state_conflicts(world, mode, conflict):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request())
    change = lib.accept(preview, controls=())
    partial(store, change, event=mode == 'complete', holders=mode == 'adopt')
    if conflict == 'full':
        PartyLibrary(store, handles.app, clock=Clock(), new_id=lambda p: p + '-editor').join(MARA, GRUB, at=moment(6), provenance='editor').commit()
    else:
        relation_library(store).add(C, 'trained_by', SNIK, TOMA, since=moment(6), provenance='editor', relation_id='r-editor').commit()
    before = {ref: v.etag for ref, v in store.campaign_relations(C).records.items()}
    with pytest.raises(CaptureRefused):
        (lib.complete(change.effect.ref, expected=store.read(change.effect.ref).etag)
         if mode == 'complete' else lib.adopt(preview, controls=()))
    assert before == {ref: v.etag for ref, v in store.campaign_relations(C).records.items()}
    assert balls(store, handles) == (3 if mode == 'complete' else 2)


def test_recovery_commit_rechecks_capacity(world):
    store, handles = world
    lib = library(store, handles)
    change = capture(store, handles)
    partial(store, change, event=True)
    recovering = lib.complete(change.effect.ref, expected=store.read(change.effect.ref).etag)
    PartyLibrary(store, handles.app, clock=Clock(), new_id=lambda p: p + '-editor').join(MARA, GRUB, at=moment(6), provenance='editor').commit()
    with pytest.raises(StaleWrite):
        recovering.commit()
    assert balls(store, handles) == 3


@pytest.mark.parametrize('target', ['journal', 'event', 'holdings'])
def test_native_journal_crash_reopen_and_retry_preserve_one_ball(tmp_path, target):
    root = tmp_path / 'lib'
    locker = ProcessLocker()
    handles = Handles()
    with LibraryStore.create(root, locker=locker) as store:
        seed(store, handles)
        preview = library(store, handles).preview(request())
        ref = capture(store, handles).effect.ref
    paths = {'journal': 'lib/.txn/journal.json', 'event': f'lib/campaigns/c/accepted/{ref.event_id}.json',
             'holdings': 'lib/campaigns/c/holdings/character/mara.json'}
    ops = RootedFileOps(tmp_path, plan=[Crash(Target('replace', target=paths[target]), 'after')], record=False)
    with pytest.raises(SimulatedCrash):
        with LibraryStore.open(root, locker=locker, ops=ops) as store:
            lib = library(store, handles)
            lib.accept(lib.preview(request()), controls=()).commit()
    with LibraryStore.open(root, locker=locker) as store:
        capture(store, handles).commit()
        assert balls(store, handles) == 2 and len(relations(store)) == 2
        assert not library(store, handles).effects.continuity(C, as_of=7, requested_by=DIRECTOR).problems


def test_adoption_preview_survives_json_roundtrip_and_process_restart(world):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request(device=None))
    saved = json.dumps(preview.to_payload())
    partial(store, lib.accept(preview, controls=()), relations=True)
    root = store.root
    store.close()
    with LibraryStore.open(root, locker=ProcessLocker()) as reopened:
        lib = library(reopened, handles)
        lib.adopt(CapturePreview.from_payload(json.loads(saved)), controls=()).commit()
        assert lib.accept(lib.preview(request(device=None)), controls=()).preview() == {}
