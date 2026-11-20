"""Accepted effects bind real native scene inputs, including orphan adoption."""
from dataclasses import replace

import pytest

from grimoire.continuitywire.projection import scene_revision
from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef, ChatRef
from grimoire.effectstore import InvalidEffect
from grimoire.effectstore.continuity import (capture_scene_sources, decode_scene_sources, scene_sources_payload,
                                            stale_scene_sources)
from grimoire.scene import Post, TurnRecord, TURN_SCHEMA, turn_to_payload
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts

from capture_fixtures import planned, publish, recovery, seed_capture, throw
from effects_fixtures import CHAT, CONTROLS, TAM, Handles, effects_library
from store_helpers import OWNER


@pytest.fixture
def world(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_capture(store, handles)
        yield store, handles


def put(store, handles, record, expected):
    transaction = store.transaction()
    transaction.put(record, expected)
    transaction.commit(handles.app)


def write_turn(store, handles, *, text='I hold the key.', number=1, chat=CHAT, extra=None):
    turn = TurnRecord(chat, number, 'post', Post(OWNER, TAM, 'action', text, (TAM,)), extras=extra or {})
    record = ScopeRecord(turn.ref, TURN_SCHEMA, turn_to_payload(turn), owner=OWNER)
    stored = store.campaign_turns(CHAT.campaign_id).records.get(turn.ref)
    put(store, handles, record, None if stored is None else stored.etag)
    return turn


def test_acceptance_records_exact_native_turn_and_empty_coverage(world):
    store, handles = world
    empty = capture_scene_sources(store, CHAT)
    assert scene_sources_payload(empty) == {'version': 1, 'coverage': 'empty_limited', 'pins': []}
    turn = write_turn(store, handles)
    other = write_turn(store, handles, chat=ChatRef('c', 'elsewhere'))
    pins = capture_scene_sources(store, CHAT)
    assert len(pins) == 1 and pins[0].path == '/'.join(address_parts(turn.ref))
    assert pins[0].etag == store.campaign_turns('c').records[turn.ref].etag
    planned(store, handles).commit()
    event, _ = effects_library(store, handles).read(AcceptedRef('c', 'e-1'), requested_by=DIRECTOR)
    assert decode_scene_sources(event.extras['continuity_sources'], CHAT) == pins
    assert event.fingerprint == throw().fingerprint


@pytest.mark.parametrize('change', ['edit', 'new_turn', 'bad_schema', 'bad_json'])
def test_acceptance_commit_rechecks_same_chat_scene_and_listing(world, change):
    store, handles = world
    turn = write_turn(store, handles)
    pending = planned(store, handles)
    if change == 'edit': write_turn(store, handles, text='I destroyed the key.')
    if change == 'new_turn': write_turn(store, handles, number=2)
    if change == 'bad_schema':
        stored = store.campaign_turns('c').records[turn.ref]
        put(store, handles, replace(stored.record, schema='future.scene/99'), stored.etag)
    if change == 'bad_json':
        (store.root / '/'.join(address_parts(turn.ref))).write_text('{broken')
    with pytest.raises(StaleWrite): pending.commit()
    assert not store.campaign_accepted('c').records


def test_bookkeeping_only_envelope_change_preserves_projection(world):
    store, handles = world
    turn = write_turn(store, handles)
    pins = capture_scene_sources(store, CHAT)
    pending = planned(store, handles)
    stored = store.campaign_turns('c').records[turn.ref]
    # Envelope metadata changes exact etag; authored fiction is unchanged.
    put(store, handles, replace(stored.record, owner='human:other'), stored.etag)
    assert capture_scene_sources(store, CHAT)[0].etag != pins[0].etag
    assert not stale_scene_sources(store, CHAT, pins)
    pending.commit()


def test_adoption_captures_and_rechecks_then_offline_edit_invalidates_provenance(world):
    store, handles = world
    write_turn(store, handles)
    original = planned(store, handles)
    publish(store, original, relations=True, holders=True)
    adopted = recovery(store, handles).adopt(throw(), controls=CONTROLS)
    write_turn(store, handles, text='Different offline history.')
    with pytest.raises(StaleWrite): adopted.commit()
    assert not store.campaign_accepted('c').records
    adopted = recovery(store, handles).adopt(throw(), controls=CONTROLS)
    adopted.commit()
    event, _ = effects_library(store, handles).read(AcceptedRef('c', 'e-1'), requested_by=DIRECTOR)
    pins = decode_scene_sources(event.extras['continuity_sources'], CHAT)
    assert not stale_scene_sources(store, CHAT, pins)
    write_turn(store, handles, text='Another offline history.')
    assert stale_scene_sources(store, CHAT, pins)


def test_partial_event_completion_preserves_original_source_pins(world):
    store, handles = world
    write_turn(store, handles)
    original = planned(store, handles)
    publish(store, original, event=True)
    lib = effects_library(store, handles)
    event, etag = lib.read(AcceptedRef('c', 'e-1'), requested_by=DIRECTOR)
    pins = event.extras['continuity_sources']
    write_turn(store, handles, text='Changed after original event landed.')
    recovery(store, handles).complete(event.ref, expected=etag).commit()
    completed, completed_etag = lib.read(event.ref, requested_by=DIRECTOR)
    assert completed_etag == etag and completed.extras['continuity_sources'] == pins


def test_capture_refuses_cap_without_truncating(world, monkeypatch):
    store, handles = world
    write_turn(store, handles)
    write_turn(store, handles, number=2)
    monkeypatch.setattr('grimoire.effectstore.continuity.MAX_SCENE_SOURCES', 1)
    with pytest.raises(InvalidEffect): planned(store, handles)
    assert not store.campaign_accepted('c').records


@pytest.mark.parametrize('value', [None, {}, {'version': 99, 'coverage': 'empty_limited', 'pins': []},
                                  {'version': 1, 'coverage': 'same_chat_turns', 'pins': []}])
def test_unknown_or_malformed_provenance_refuses(value):
    with pytest.raises(ValueError): decode_scene_sources(value, CHAT)


def test_source_revision_uses_exact_validated_native_projection(world):
    store, handles = world
    turn = write_turn(store, handles)
    stored = store.campaign_turns('c').records[turn.ref]
    payload = thaw(stored.record.payload)
    # Older valid envelopes may omit optional keys emitted by today's codec.
    for key in ('device', 'follows', 'set_aside'):
        payload.pop(key)
    put(store, handles, replace(stored.record, payload=payload), stored.etag)
    pin = capture_scene_sources(store, CHAT)[0]
    assert pin.revision == scene_revision(payload)
