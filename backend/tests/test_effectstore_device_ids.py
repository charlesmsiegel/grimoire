"""New 021 accepted-event ids, and the 017 relation ids an acceptance begins, carry 028's device tag.

Two paired devices that each accept an effect offline must never write one event id (or one relation id)
with two contents: 028's ledger merge tells events apart by id. All fixtures are synthetic.
"""

from __future__ import annotations

from hashlib import sha256

import pytest

from grimoire.core.identity import SEGMENT_MAX, AcceptedRef, RelationRef, validate_segment
from grimoire.effectstore import InvalidEffect
from grimoire.store import LibraryStore, StaleWrite
from grimoire.sync import identify

from capture_fixtures import allies, planned, problems, publish, recovery, seed_capture, throw
from effects_fixtures import C, CONTROLS, KEY2, Handles, contribution, effects_library, give, seed


def tag(device: str) -> str:
    """028's documented tag: the first eight hex digits of the SHA-256 of the device ID."""
    return sha256(device.encode()).hexdigest()[:8]


@pytest.fixture
def world(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        handles = Handles()
        seed(store, handles)
        yield store, handles


def accept(library, handles, proposal):
    """Wren's backed give (``give()``) needs the player's keys; a director-only effect needs none."""
    backed = any(item.backed_by for item in proposal.operations)
    return library.accept(proposal, controls=CONTROLS, contributions=[contribution()] if backed else [],
                          endorsements=[handles.endorse(proposal)] if backed else [])


def later():
    """A director-only second effect at sequence 8: the tide carries the brass key to Tam."""
    return give(KEY2, sequence=8, agent=None, backed=None, summary="The tide carries the brass key to Tam.")


def undo(library, handles, ref, proposal) -> None:
    _, etag = library.read(ref, requested_by="director")
    backed = any(item.backed_by for item in proposal.operations)
    library.revoke(ref, reason="revised", expected=etag,
                   endorsements=[handles.undo(ref, proposal)] if backed else []).commit()


def files(store, folder: str) -> set[str]:
    return {path for path in store.scan().files if f"/{folder}/" in path}


def begun(event) -> RelationRef:
    return next(receipt.relation for item, receipt in zip(event.operations, event.receipts) if item.op.kind == "begin")


def test_an_unidentified_library_keeps_plain_ids(world):
    store, handles = world
    library = effects_library(store, handles)
    done = accept(library, handles, give()).commit()
    event, _ = library.read(done.ref, requested_by="director")
    assert done.ref.event_id == "e-1" and begun(event).relation_id == "r-1"


def test_an_identified_library_tags_new_event_and_relation_ids(world):
    store, handles = world
    identify(store, "desktop")
    library = effects_library(store, handles)
    done = accept(library, handles, give()).commit()
    event, _ = library.read(done.ref, requested_by="director")
    suffix = "-" + tag("desktop")
    assert done.ref.event_id == "e-1" + suffix
    assert begun(event).relation_id == "r-1" + suffix
    validate_segment(done.ref.event_id, "event id")
    # the tagged event is ordinary in every other way: continuity sees it, revoke undoes it
    found = library.continuity(C, as_of=7, requested_by="director")
    assert [e.event_id for e in found.events] == [done.ref.event_id] and not found.problems
    undo(library, handles, done.ref, give())
    assert not library.continuity(C, as_of=7, requested_by="director").problems


def test_two_devices_drawing_the_same_number_write_two_events(tmp_path):
    """The same generator on two devices (the first free number on each) gives two ids, never one."""
    ids = []
    for device in ("desktop", "phone"):
        with LibraryStore.create(tmp_path / device) as store:
            handles = Handles()
            seed(store, handles)
            identify(store, device)
            done = accept(effects_library(store, handles), handles, give()).commit()
            ids.append(done.ref.event_id)
    assert ids[0] != ids[1] and all(item.startswith("e-1-") for item in ids)


def test_events_accepted_before_identify_keep_their_ids_and_play_goes_on_tagged(world):
    store, handles = world
    library = effects_library(store, handles)
    first = accept(library, handles, give()).commit()
    identify(store, "desktop")
    library = effects_library(store, handles)
    second = accept(library, handles, later()).commit()
    assert first.ref.event_id == "e-1"
    assert second.ref.event_id == "e-1-" + tag("desktop")  # the generator restarted; the tag keeps it apart
    found = library.continuity(C, as_of=8, requested_by="director")
    assert [e.event_id for e in found.events] == ["e-1", second.ref.event_id] and not found.problems
    assert [e.ordinal for e in found.events] == [1, 2]
    # undo is still latest first across the two kinds of id
    undo(library, handles, second.ref, later())
    undo(library, handles, first.ref, give())
    assert not library.continuity(C, as_of=8, requested_by="director").problems


def test_an_acceptance_planned_before_identify_is_stale_at_commit(world):
    store, handles = world
    change = accept(effects_library(store, handles), handles, give())
    assert change.ref.event_id == "e-1"
    identify(store, "desktop")
    before = files(store, "accepted") | files(store, "relations")
    with pytest.raises(StaleWrite):
        change.commit()
    assert files(store, "accepted") | files(store, "relations") == before


@pytest.mark.parametrize("damage", [b"{not json", b'{"format": "grimoire.sync.device/1"}'])
def test_a_damaged_identity_refuses_a_new_event_rather_than_plain_ids(world, damage):
    store, handles = world
    identify(store, "desktop")
    library = effects_library(store, handles)
    done = accept(library, handles, give()).commit()
    (store.root / ".sync" / "device.json").write_bytes(damage)
    before = files(store, "accepted") | files(store, "relations")
    with pytest.raises(InvalidEffect) as refused:
        accept(library, handles, later())
    assert refused.value.reason == "device_identity"
    assert files(store, "accepted") | files(store, "relations") == before
    # a retry of the accepted effect is still recognised: a no-op mints no id
    again = accept(library, handles, give())
    assert again.duplicate_of == done.ref and again.preview() == {}


def test_an_identity_damaged_after_planning_is_stale_at_commit(world):
    store, handles = world
    identify(store, "desktop")
    change = accept(effects_library(store, handles), handles, give())
    (store.root / ".sync" / "device.json").write_bytes(b"{not json")
    with pytest.raises(StaleWrite):
        change.commit()
    assert not files(store, "accepted")


def test_a_tagged_id_still_skips_every_taken_id(world):
    """The tag is part of the id the allocator checks: a 020 event or a 021 token naming it keeps it taken."""
    store, handles = world
    identify(store, "desktop")
    suffix = "-" + tag("desktop")
    library = effects_library(store, handles)
    first = accept(library, handles, give()).commit()
    # a later library whose generator repeats e-1 draws past it: the id with its tag is taken
    again = effects_library(store, handles)
    second = accept(again, handles, later()).commit()
    assert first.ref.event_id == "e-1" + suffix and second.ref.event_id == "e-2" + suffix
    assert AcceptedRef(C, second.ref.event_id) == second.ref


def test_an_id_too_long_for_its_tag_is_refused_not_cut(world):
    store, handles = world
    identify(store, "desktop")
    library = effects_library(store, handles, new_id=lambda prefix: "e" * SEGMENT_MAX)
    with pytest.raises(ValueError):
        accept(library, handles, give())
    assert not files(store, "accepted")


# -- 030's adopt begins what is still missing under the same tagged ids --------------------------

def capture_world(tmp_path):
    store = LibraryStore.create(tmp_path / "capture")
    handles = Handles()
    seed_capture(store, handles)
    identify(store, "desktop")
    return store, handles


def test_adopt_on_an_identified_library_begins_tagged_relations(tmp_path):
    store, handles = capture_world(tmp_path)
    try:
        publish(store, planned(store, handles), holders=True)  # only the holder file landed: the event is orphaned
        change = recovery(store, handles).adopt(throw(), controls=CONTROLS)
        assert change.ref == AcceptedRef(C, f"e-1-{tag('desktop')}")
        assert [r.relation for r in change.relation_changes] == [RelationRef(C, f"r-1-{tag('desktop')}")]
        change.commit()
        assert [r.ref for r in allies(store)] == [RelationRef(C, f"r-1-{tag('desktop')}")]
        assert problems(store, handles) == []
    finally:
        store.close()


def test_an_adoption_whose_identity_changes_before_commit_is_stale(tmp_path):
    store, handles = capture_world(tmp_path)
    try:
        publish(store, planned(store, handles), holders=True)
        change = recovery(store, handles).adopt(throw(), controls=CONTROLS)
        (store.root / ".sync" / "device.json").write_bytes(b"{not json")
        with pytest.raises(StaleWrite):
            change.commit()
    finally:
        store.close()
