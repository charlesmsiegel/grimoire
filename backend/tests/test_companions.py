"""Accepted capture preserves individuals, authority and exactly-once state."""
from dataclasses import replace

import pytest

from grimoire.companions import CaptureRefused
from grimoire.core import DIRECTOR
from grimoire.core.identity import InstanceRef, HoldingsRef
from grimoire.core.authority import AuthorityError
from grimoire.holdings import HoldingsRefused
from grimoire.core.model import ControlAssignment
from grimoire.effectstore import InvalidEffect
from grimoire.party import PartyRefused, presence
from grimoire.store import LibraryStore, ProcessLocker, StaleWrite

from companion_fixtures import BALL, C, GRUB, MARA, SNIK, balls, capture, library, relations, request, seed
from effects_fixtures import ANA, BOB, Handles, contribution, moment
from graph_fixtures import hand_pair, relation_library
from party_fixtures import TOMA, party_library


@pytest.fixture
def world(tmp_path):
    with LibraryStore.create(tmp_path / 'lib', locker=ProcessLocker()) as store:
        handles = Handles()
        seed(store, handles)
        yield store, handles


def test_capture_reuses_named_individual_spends_once_and_reopens(world):
    store, handles = world
    before = {ref: store.read(ref).etag for ref in (MARA, SNIK, GRUB)}
    first = capture(store, handles)
    assert balls(store, handles) == 3
    result = first.commit()
    assert result.event is not None and balls(store, handles) == 2
    assert [(r.subject, r.object) for r in relations(store) if r.predicate == 'trained_by'] == [(SNIK, MARA)]
    assert party_library(store).view(C, as_of=7).state.party(MARA) == ((SNIK,), ())
    assert before == {ref: store.read(ref).etag for ref in before}
    retry = capture(store, handles)
    assert retry.preview() == {} and retry.commit().event == result.event
    assert balls(store, handles) == 2
    root = store.root
    store.close()
    with LibraryStore.open(root, locker=ProcessLocker()) as reopened:
        assert capture(reopened, handles).commit().event == result.event
        assert balls(reopened, handles) == 2


def test_second_same_species_creature_is_distinct_and_full_party_uses_reserve(world):
    store, handles = world
    capture(store, handles).commit()
    second = request(attempt_id='catch-2', creature=GRUB, at=moment(8))
    change = capture(store, handles, second)
    assert change.context(requested_by=DIRECTOR).placement == 'reserve'
    change.commit()
    assert party_library(store).view(C, as_of=8).state.party(MARA) == ((SNIK,), (GRUB,))
    assert len([r for r in relations(store) if r.predicate == 'trained_by']) == 2
    assert balls(store, handles) == 1
    with pytest.raises(PartyRefused):
        party_library(store).stage(C, 's1', (MARA, GRUB), as_of=8)


def test_capture_changes_membership_not_scene_presence(world):
    store, handles = world
    parties = party_library(store)
    cast = parties.stage(C, 's1', (MARA, SNIK), as_of=7)
    capture(store, handles).commit()
    state = parties.view(C, as_of=7).state
    with pytest.raises(PartyRefused, match='stage it again'):
        cast.may_speak(SNIK, state)
    assert parties.stage(C, 's1', (MARA, SNIK), as_of=7).may_speak(SNIK, state)
    without = parties.stage(C, 's1', (MARA,), as_of=7)
    assert presence(state, without, SNIK) == 'accompanying'


@pytest.mark.parametrize('outcome,device,remaining,links', [
    ('declined', BALL, 3, 0), ('failed', BALL, 2, 0), ('failed', None, 3, 0), ('success', None, 3, 2)])
def test_optional_device_failure_and_decline(world, outcome, device, remaining, links):
    store, handles = world
    req = request(outcome=outcome, device=device)
    change = capture(store, handles, req)
    result = change.commit()
    assert balls(store, handles) == remaining and len(relations(store)) == links
    assert capture(store, handles, req).preview() == {}
    assert (result.event is None) == (outcome == 'declined' or (outcome == 'failed' and device is None))


def test_explicit_full_active_refuses_without_spending_and_zero_capacity_reserves(world):
    store, handles = world
    with pytest.raises(CaptureRefused) as raised:
        capture(store, handles, request(capacity=0, placement='party'))
    assert raised.value.reason == 'party_full' and balls(store, handles) == 3
    capture(store, handles, request(capacity=0)).commit()
    assert party_library(store).view(C, as_of=7).state.party(MARA) == ((), (SNIK,))


def test_changed_attempt_and_revoked_retry_are_refused(world):
    store, handles = world
    lib = library(store, handles)
    result = capture(store, handles).commit()
    with pytest.raises(CaptureRefused) as raised:
        lib.preview(request(capacity=3))
    assert raised.value.reason == 'attempt_conflict'
    event, etag = lib.effects.read(result.event, requested_by=DIRECTOR)
    lib.effects.revoke(event.ref, reason='undo', expected=etag).commit()
    with pytest.raises(CaptureRefused) as raised:
        lib.preview(request())
    assert raised.value.reason == 'attempt_revoked' and balls(store, handles) == 3


def test_both_player_trainer_and_creature_must_endorse(world):
    store, handles = world
    lib = library(store, handles)
    controls = (ControlAssignment(C, MARA.instance_id, ANA), ControlAssignment(C, SNIK.instance_id, BOB))
    p = lib.preview(request(trainer_backing='trainer-action', creature_backing='creature-action'))
    cs = [contribution('trainer-action', actor_id=MARA.instance_id, by=ANA),
          contribution('creature-action', actor_id=SNIK.instance_id, by=BOB)]
    with pytest.raises(InvalidEffect):
        lib.accept(p, controls=controls, contributions=cs,
                   endorsements=[handles.endorse(p.proposal, 'trainer-action', ANA)])
    lib.accept(p, controls=controls, contributions=cs,
               endorsements=[handles.endorse(p.proposal, 'trainer-action', ANA),
                             handles.endorse(p.proposal, 'creature-action', BOB)]).commit()
    assert balls(store, handles) == 2


@pytest.mark.parametrize('when', ['preview', 'accepted'])
def test_concurrent_party_fill_aborts_without_charge(world, when):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request())
    change = lib.accept(preview, controls=()) if when == 'accepted' else None
    party_library(store).join(MARA, GRUB, at=moment(6), provenance='editor').commit()
    with pytest.raises(StaleWrite):
        change.commit() if change else lib.accept(preview, controls=())
    assert balls(store, handles) == 3
    assert not [r for r in relations(store) if r.predicate == 'trained_by']


@pytest.mark.parametrize('fault', ['missing', 'pair', 'trained', 'member', 'future', 'broken'])
def test_invalid_current_state_never_spends(world, fault):
    store, handles = world
    if fault == 'missing':
        req = request(creature=InstanceRef(C, 'creature', 'missing'))
    else:
        req = request()
    if fault == 'pair':
        hand_pair(store, SNIK, kind='character')
    if fault == 'trained':
        relation_library(store).add(C, 'trained_by', SNIK, TOMA, since=moment(1), provenance='editor').commit()
    if fault == 'member':
        party_library(store).join(TOMA, SNIK, at=moment(1), provenance='editor').commit()
    if fault == 'future':
        party_library(store).join(MARA, GRUB, at=moment(10), provenance='editor').commit()
    if fault == 'broken':
        folder = store.root / 'campaigns/c/relations'
        folder.mkdir(exist_ok=True)
        (folder / 'broken.json').write_text('{')
    with pytest.raises((CaptureRefused, InvalidEffect)):
        capture(store, handles, req)
    assert balls(store, handles) == 3


@pytest.mark.parametrize('changes', [{'capacity': True}, {'capacity': -1}, {'attempt_id': '../bad'},
    {'device': '../ball'}, {'creature': InstanceRef('other', 'creature', 'snik')},
    {'outcome': 'narrator-guessed'}, {'placement': 'discard'}, {'trainer': SNIK}])
def test_invalid_capture_request_is_rejected(changes):
    with pytest.raises(CaptureRefused):
        request(**changes)


def test_preview_endpoint_replacement_is_stale(world):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request())
    stored = store.read(SNIK)
    txn = store.transaction()
    txn.put(replace(stored.envelope, name='Edited label'), stored.etag)
    txn.commit(handles.app)
    with pytest.raises(StaleWrite):
        lib.accept(preview, controls=())
    assert balls(store, handles) == 3


def test_capture_does_not_publish_knowledge_control_or_transcript(world):
    store, handles = world
    paths = ('evidence', 'events', 'chats', 'turns')
    before = {str(p.relative_to(store.root)): p.read_bytes() for name in paths
              for p in (store.root / 'campaigns/c' / name).rglob('*.json')}
    change = capture(store, handles)
    with pytest.raises(AuthorityError, match='director perspective'):
        change.context(requested_by=ANA)
    change.commit()
    after = {str(p.relative_to(store.root)): p.read_bytes() for name in paths
             for p in (store.root / 'campaigns/c' / name).rglob('*.json')}
    assert before == after
    assert set(store.campaign_holding_names(C)[0]) == {HoldingsRef.of(MARA)}


def test_insufficient_device_is_never_free_capture(world):
    store, handles = world
    with pytest.raises((HoldingsRefused, InvalidEffect)):
        capture(store, handles, request(device='missing-ball'))
    assert relations(store) == [] and balls(store, handles) == 3


def test_explicit_reserve_even_with_space_and_reserved_trainer(world):
    store, handles = world
    parties = party_library(store)
    parties.move(MARA, TOMA, 'reserve', at=moment(1), provenance='editor').commit()
    with pytest.raises(CaptureRefused, match='trainer_in_reserve'):
        capture(store, handles)
    capture(store, handles, request(placement='reserve')).commit()
    assert parties.view(C, as_of=7).state.party(MARA) == ((), (SNIK,))


def test_no_backing_cannot_take_a_player_creature(world):
    store, handles = world
    lib = library(store, handles)
    with pytest.raises(InvalidEffect):
        lib.accept(lib.preview(request()), controls=(ControlAssignment(C, SNIK.instance_id, BOB),))
    assert relations(store) == [] and balls(store, handles) == 3


def test_generic_effect_duplicate_cannot_be_reported_as_a_capture(world):
    store, handles = world
    lib = library(store, handles)
    preview = lib.preview(request(outcome='failed'))
    lib.effects.accept(preview.proposal, controls=()).commit()
    with pytest.raises(CaptureRefused, match='invalid_capture_metadata'):
        lib.accept(preview, controls=())
    assert balls(store, handles) == 2


@pytest.mark.parametrize('damage', ['removed', 'null'])
def test_missing_capture_metadata_never_becomes_a_successful_retry(world, damage):
    store, handles = world
    lib = library(store, handles)
    req = request(outcome='failed')
    result = lib.accept(lib.preview(req), controls=()).commit()
    stored = store.read(result.event)
    payload = dict(stored.record.payload)
    if damage == 'removed':
        del payload['capture_v1']
    else:
        payload['capture_v1'] = None
    txn = store.transaction()
    txn.put(replace(stored.record, payload=payload), stored.etag)
    txn.commit(handles.app)
    with pytest.raises(CaptureRefused, match='invalid_capture_metadata'):
        lib.accept(lib.preview(req), controls=())
    assert balls(store, handles) == 2
