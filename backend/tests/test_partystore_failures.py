"""Sprint 033 refusals and stale commits: a move is all or nothing and never guesses at membership."""

from __future__ import annotations

import dataclasses

import pytest

from grimoire.core import InstanceRef, Moment
from grimoire.party import PartyRefused, presence
from grimoire.store import LibraryStore, StaleWrite, StoreError
from grimoire.testing.fileops import snapshot

from graph_fixtures import hand_pair, instance_record, relation_library
from party_fixtures import C, CREW, GRUB, INNKEEPER, MARA, SNIK, TOMA, party_library, seed
from store_helpers import owner_handle


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


def refused(call) -> PartyRefused:
    with pytest.raises(PartyRefused) as caught:
        call()
    return caught.value


def test_the_named_moves_check_where_the_member_stands(store):
    party = party_library(store)
    at = Moment(3)
    assert refused(lambda: party.store_in_reserve(SNIK, at=at, provenance="d")).reason == "not_in_party"
    assert refused(lambda: party.withdraw(SNIK, at=at, provenance="d")).reason == "not_in_reserve"
    assert refused(lambda: party.leave(SNIK, at=at, provenance="d")).reason == "not_in_party"
    assert refused(lambda: party.release(SNIK, at=at, provenance="d")).reason == "not_a_member"
    party.join(MARA, SNIK, at=Moment(1), provenance="d").commit()
    assert refused(lambda: party.join(TOMA, SNIK, at=at, provenance="d")).reason == "already_member"
    assert refused(lambda: party.withdraw(SNIK, at=at, provenance="d")).reason == "not_in_reserve"
    assert refused(lambda: party.move(SNIK, MARA, "party", at=at, provenance="d")).reason == "already_there"
    party.store_in_reserve(SNIK, at=Moment(2), provenance="d").commit()
    assert refused(lambda: party.leave(SNIK, at=at, provenance="d")).reason == "not_in_party"
    assert refused(lambda: party.store_in_reserve(SNIK, at=at, provenance="d")).reason == "not_in_party"


def test_history_is_append_only(store):
    party = party_library(store)
    party.join(MARA, SNIK, at=Moment(5), provenance="d").commit()
    assert refused(lambda: party.store_in_reserve(SNIK, at=Moment(5), provenance="d")).reason == "same_moment"
    assert refused(lambda: party.release(SNIK, at=Moment(4), provenance="d")).reason == "out_of_order"
    party.release(SNIK, at=Moment(7), provenance="d").commit()
    assert refused(lambda: party.join(TOMA, SNIK, at=Moment(6), provenance="d")).reason == "out_of_order"
    party.join(TOMA, SNIK, at=Moment(7), provenance="d").commit()  # a new stint may begin where the last ended
    assert party.view(C, as_of=7).state.membership(SNIK).party == TOMA


def test_shape_and_existence(store):
    party = party_library(store)
    at = Moment(1)
    ghost = InstanceRef(C, "creature", "ghost")
    assert refused(lambda: party.join(MARA, ghost, at=at, provenance="d")).reason == "missing_member"
    assert refused(lambda: party.join(ghost, SNIK, at=at, provenance="d")).reason == "missing_party"
    assert refused(lambda: party.join(MARA, MARA, at=at, provenance="d")).reason == "self_membership"
    assert refused(lambda: party.join(InstanceRef("other", "character", "mara"), SNIK, at=at,
                                      provenance="d")).reason == "cross_campaign"
    assert refused(lambda: party.join(MARA, SNIK, at=1, provenance="d")).reason == "invalid_field"  # type: ignore[arg-type]
    assert refused(lambda: party.join(MARA, str(SNIK), at=at, provenance="d")).reason == "invalid_field"  # type: ignore[arg-type]
    assert refused(lambda: party.join(MARA, SNIK, at=at, provenance=" ")).reason == "invalid_field"
    assert refused(lambda: party.move(SNIK, MARA, "box", at=at, provenance="d")).reason == "invalid_field"
    assert refused(lambda: party.view("Bad Campaign", as_of=0)).reason == "invalid_field"
    assert refused(lambda: party.join(InstanceRef("nowhere", "character", "a"), InstanceRef("nowhere", "creature", "b"),
                                      at=at, provenance="d")).reason == "missing_campaign"


def test_a_cross_kind_pair_is_no_one_individual(store):
    hand_pair(store, SNIK, kind="character")
    party = party_library(store)
    assert refused(lambda: party.join(MARA, SNIK, at=Moment(1), provenance="d")).reason == "instance_id_conflict"
    assert refused(lambda: party.join(SNIK, MARA, at=Moment(1), provenance="d")).reason == "instance_id_conflict"


def test_a_cycle_cannot_be_recorded_at_an_earlier_sequence(store):
    party = party_library(store)
    party.join(SNIK, MARA, at=Moment(7), provenance="d").commit()  # Mara travels with Snik from 7
    found = refused(lambda: party.join(MARA, SNIK, at=Moment(5), provenance="d"))
    assert found.reason == "out_of_order" and found.members == (MARA,)
    assert refused(lambda: party.join(MARA, SNIK, at=Moment(8), provenance="d")).reason == "party_cycle"


def test_a_stored_anchor_cannot_take_travellers(store):
    party = party_library(store)
    party.join(MARA, GRUB, at=Moment(1), provenance="d").commit()
    party.store_in_reserve(GRUB, at=Moment(2), provenance="d").commit()
    assert refused(lambda: party.join(GRUB, SNIK, at=Moment(3), provenance="d")).reason == "party_in_reserve"
    party.move(SNIK, GRUB, "reserve", at=Moment(3), provenance="d").commit()  # its reserve is fine
    assert refused(lambda: party.stage(C, "s", [SNIK], as_of=3)).reason == "in_reserve"


def test_a_member_boxed_during_the_scene_no_longer_speaks(store):
    party = party_library(store)
    party.join(MARA, GRUB, at=Moment(1), provenance="d").commit()
    cast = party.stage(C, "s", [MARA, GRUB], as_of=6)
    assert cast.may_speak(GRUB, party.view(C, as_of=6).state)
    party.store_in_reserve(GRUB, at=Moment(6), provenance="d").commit()  # boxed mid-scene, same sequence
    state = party.view(C, as_of=6).state
    found = refused(lambda: cast.may_speak(GRUB, state))
    assert found.reason == "stale_cast" and found.members == (GRUB,)
    assert refused(lambda: presence(state, cast, GRUB)).reason == "stale_cast"
    assert cast.may_speak(MARA, state)  # the others are unaffected
    assert refused(lambda: dataclasses.replace(cast, present=(MARA,))).reason == "invalid_field"


def test_a_party_cannot_contain_itself(store):
    party = party_library(store)
    party.join(CREW, MARA, at=Moment(1), provenance="d").commit()
    assert refused(lambda: party.join(MARA, CREW, at=Moment(2), provenance="d")).reason == "party_cycle"
    party.join(MARA, SNIK, at=Moment(1), provenance="d").commit()
    assert refused(lambda: party.move(MARA, SNIK, "party", at=Moment(2), provenance="d")).reason == "party_cycle"


def test_conflicting_membership_is_refused_never_guessed(store):
    relations = relation_library(store)  # 021 or a hand edit can record two memberships at once
    relations.add(C, "in_party", SNIK, MARA, since=Moment(1), provenance="d").commit()
    relations.add(C, "in_reserve", SNIK, TOMA, since=Moment(1), provenance="d").commit()
    party = party_library(store)
    state = party.view(C, as_of=2).state
    assert SNIK in state.conflicted and state.problems[0].code == "conflicting_membership"
    assert state.party(MARA) == ((), ()) and state.party(TOMA) == ((), ())
    assert refused(lambda: state.membership(SNIK)).reason == "conflicting_membership"
    assert refused(lambda: party.release(SNIK, at=Moment(3), provenance="d")).reason == "conflicting_membership"
    assert refused(lambda: party.stage(C, "s", [MARA, SNIK], as_of=2)).reason == "conflicting_membership"
    assert refused(lambda: party.join(SNIK, GRUB, at=Moment(3), provenance="d")).reason == "conflicting_membership"


def test_an_unreadable_relation_file_fails_closed(store):
    party = party_library(store)
    party.join(MARA, GRUB, at=Moment(1), provenance="d").commit()
    party.store_in_reserve(GRUB, at=Moment(2), provenance="d").commit()
    party_cast_from_before = party.stage(C, "s", [MARA], as_of=3)
    (store.root / "campaigns" / "c" / "relations" / "r-3.json").write_text("{not json", encoding="utf-8")
    view = party.view(C, as_of=3)
    assert not view.state.complete and view.state.problems[-1].code == "relations_unreadable"
    assert refused(lambda: party.stage(C, "s", [MARA], as_of=3)).reason == "relations_unreadable"
    assert refused(lambda: party.join(MARA, SNIK, at=Moment(3), provenance="d")).reason == "relations_unreadable"
    assert refused(lambda: party.history(GRUB)).reason == "relations_unreadable"
    assert refused(lambda: presence(view.state, party_cast_from_before, SNIK)).reason == "relations_unreadable"


def test_a_membership_change_since_the_preview_is_stale(store):
    party = party_library(store)
    first = party.join(MARA, SNIK, at=Moment(1), provenance="d")
    rival = party.join(TOMA, SNIK, at=Moment(1), provenance="d")
    first.commit()
    before = snapshot(store.root)
    assert rival.stale()
    with pytest.raises(StaleWrite):
        rival.commit()
    assert snapshot(store.root) == before  # nothing written
    unrelated = party.join(MARA, GRUB, at=Moment(2), provenance="d")
    party.join(TOMA, INNKEEPER, at=Moment(2), provenance="d").commit()  # another member's move pins too
    with pytest.raises(StaleWrite):
        unrelated.commit()


def test_a_taken_relation_id_is_reported_stale_before_commit(store):
    party = party_library(store)
    change = party.join(MARA, SNIK, at=Moment(1), provenance="d")  # allocates r-1
    relation_library(store).add(C, "owns", MARA, GRUB, since=Moment(1), provenance="d").commit()  # takes r-1
    assert ("campaigns/c/relations/r-1.json", "exists") in change.stale()
    with pytest.raises(StaleWrite):
        change.commit()


def test_a_replaced_endpoint_is_stale(store):
    party = party_library(store)
    change = party.join(MARA, SNIK, at=Moment(1), provenance="d")
    current = store.read(SNIK)
    txn = store.transaction()  # a different individual given the same id by hand
    txn.put(instance_record(SNIK), current.etag)
    txn.commit(owner_handle())
    with pytest.raises(StaleWrite):
        change.commit()


def test_a_move_ends_and_adds_in_one_transaction(store):
    party = party_library(store)
    party.join(MARA, SNIK, at=Moment(1), provenance="d").commit()
    boxed = party.store_in_reserve(SNIK, at=Moment(2), provenance="d")
    boxed.commit()
    with pytest.raises(StoreError):
        boxed.commit()  # committing twice raises
    assert [r.predicate for r in party.history(SNIK)] == ["in_party", "in_reserve"]


def test_a_retracted_membership_does_not_count(store):
    relations = relation_library(store)
    commit = relations.add(C, "in_reserve", SNIK, MARA, since=Moment(1), provenance="d").commit()
    _, etag = relations.read(commit.relation)
    relations.retract(commit.relation, provenance="d", expected=etag).commit()
    party = party_library(store)
    assert party.view(C, as_of=2).state.membership(SNIK) is None
    party.join(MARA, SNIK, at=Moment(1), provenance="d").commit()  # a retraction is not history in the fiction
    assert party.stage(C, "s", [MARA, SNIK], as_of=2).may_speak(SNIK, party.view(C, as_of=2).state)
