"""Store-backed unique possessions (Sprint 034): the proving scenario, acceptance cases and failures.

Every write is previewed and committed through the guarded store transaction. All fixtures are
synthetic (see ``possession_fixtures``) and no model is ever called.
"""

from __future__ import annotations

import json

import pytest

from grimoire.chatstore import campaign_taken_ids
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.possessions import (
    POSSESSION_KEY, PossessionRefused, Step, possession_from_payload, possession_to_payload,
)
from grimoire.possessionstore import PartyReplaced
from grimoire.store import LibraryStore, StaleWrite, TakenIdsUnlisted

from possession_fixtures import (
    ARMS, CHEST, HALL, LONGSWORD, MARA, STARFALL, TOMA, make, op, possession_library, seed,
)


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


@pytest.fixture
def library(store):
    return possession_library(store)


def instance_file(store, ref: InstanceRef):
    return store.root.joinpath("campaigns", ref.campaign_id, "instances", ref.kind, f"{ref.instance_id}.json")


def edit(store, ref: InstanceRef, change) -> None:
    path = instance_file(store, ref)
    document = json.loads(path.read_bytes())
    change(document)
    path.write_bytes(json.dumps(document).encode())


def created(library, creation, **kwargs) -> InstanceRef:
    change = library.create(creation, **kwargs)
    assert change.outcome == "applied"
    change.commit()
    return change.item


def done(library, item, operation):
    change = library.apply(item, operation, expected_rev=library.read(item).rev)
    assert change.outcome == "applied"
    change.commit()
    return library.read(item)


def refused(call, *args, **kwargs) -> str:
    with pytest.raises(PossessionRefused) as error:
        call(*args, **kwargs)
    return error.value.reason


def test_proving_scenario_two_same_named_swords_survive_reopen(store, tmp_path):
    library = possession_library(store)
    first = created(library, make("make-1", name="Longsword", source=LONGSWORD))
    second = created(library, make("make-2", name="Longsword", source=LONGSWORD))
    assert first != second and (first.instance_id, second.instance_id) == ("longsword-1", "longsword-2")
    assert library.read(first).name == library.read(second).name == "Longsword"
    # Toma cannot wear a sword Mara owns, whoever holds it
    assert refused(library.apply, first, op("t1", Step.equip(TOMA, "main-hand")), expected_rev=1) == "not_owner"
    worn = done(library, first, op("e1", Step.equip(MARA, "main-hand")))
    assert (worn.possession.equipped, worn.rev, library.read(second).rev) == ("main-hand", 2, 1)
    assert refused(library.apply, second, op("e2", Step.equip(MARA, "main-hand")), expected_rev=1) == "slot_taken"
    done(library, second, op("g1", Step.move(TOMA), Step.own(TOMA), note="a gift"))
    done(library, second, op("e2", Step.equip(TOMA, "main-hand")))
    store.close()
    with LibraryStore.open(tmp_path / "lib") as reopened:
        library = possession_library(reopened)
        found = library.campaign_items("c1")
        assert found.problems == () and set(found.views) == {first, second}
        assert {slot: [v.item for v in views] for slot, views in found.equipped_by(MARA).items()} == {
            "main-hand": [first]}
        assert [view.item for view in found.equipped_by(TOMA)["main-hand"]] == [second]
        kept = library.read(second).possession
        assert (kept.owner, kept.holder, kept.rev, [e.op for e in kept.log]) == (TOMA, TOMA, 3, ["g1", "e2"])
        # a copy of the campaign under another id (a fork) keeps the item's identity and history
        payload = json.loads(instance_file(reopened, second).read_bytes())["payload"][POSSESSION_KEY]
        forked = possession_from_payload(InstanceRef("c9", "item", second.instance_id), payload)
        assert (forked.owner, forked.rev, forked.origin.holder) == (InstanceRef("c9", "character", "toma-1"), 3,
                                                                   InstanceRef("c9", "character", "mara-1"))
        assert (forked.origin.op, forked.origin.source, [e.op for e in forked.log]) == ("make-2", LONGSWORD,
                                                                                      ["g1", "e2"])
        assert possession_to_payload(forked) == payload


def test_preview_is_the_committed_bytes(store, library):
    item = created(library, make())
    change = library.apply(item, op("e1", Step.equip(MARA, "off-hand")), expected_rev=1)
    preview = change.preview()
    (path,) = preview
    assert path == "campaigns/c1/instances/item/item-1.json"
    assert change.before.equipped is None and change.after.equipped == "off-hand"
    change.commit()
    assert instance_file(store, item).read_bytes() == preview[path]


def test_an_entity_payload_and_envelope_survive_a_change(store, library):
    item = created(library, make(name="Notched Blade"))
    edit(store, item, lambda d: (d["payload"].update({"x-tale": {"told": 3}}), d.update({"x-note": "kept"})))
    done(library, item, op("m1", Step.move(CHEST)))
    document = json.loads(instance_file(store, item).read_bytes())
    assert document["payload"]["x-tale"] == {"told": 3} and document["x-note"] == "kept"
    assert document["payload"]["local_name"] == "Notched Blade"
    assert document["payload"][POSSESSION_KEY]["holder"] == "item/chest-1"


def test_create_replay_and_reuse(store, library):
    creation = make("make-1", name="Longsword", source=LONGSWORD)
    item = created(library, creation)
    again = library.create(creation)
    assert (again.outcome, again.item, again.preview(), again.commit()) == ("already_applied", item, {}, {})
    assert refused(library.create, make("make-1", name="Shortsword")) == "op_id_reused"
    assert refused(library.create, creation, kind="relic") == "op_id_reused"


def test_an_individual_artifact_is_unique_per_campaign(store, library):
    blade = created(library, make("make-1", name=None, source=STARFALL))
    assert blade.instance_id == "starfall-1" and library.read(blade).possession.origin.source == STARFALL
    document = json.loads(instance_file(store, blade).read_bytes())
    assert document["derived_from"] == str(STARFALL)
    assert refused(library.create, make("make-2", name=None, source=STARFALL)) == "duplicate_individual"
    assert refused(library.create, make("make-3", name=None, source=STARFALL), kind="relic") == "kind_mismatch"
    missing = SourceRef(ARMS, "item", "nothing", "r1")
    assert refused(library.create, make("make-4", source=missing)) == "missing_definition"


def test_a_slot_taken_since_the_plan_is_stale_at_commit(store, library):
    first, second = created(library, make("make-1")), created(library, make("make-2"))
    planned = library.apply(first, op("e1", Step.equip(MARA, "main-hand")), expected_rev=1)
    done(library, second, op("e2", Step.equip(MARA, "main-hand")))
    with pytest.raises(StaleWrite) as caught:
        planned.commit()
    assert not isinstance(caught.value, PartyReplaced)
    assert library.read(first).possession.equipped is None


def test_a_concurrent_change_to_the_item_is_a_stale_revision(store, library):
    item = created(library, make())
    planned = library.apply(item, op("m1", Step.move(TOMA)), expected_rev=1)
    done(library, item, op("m2", Step.move(HALL)))
    with pytest.raises(StaleWrite):
        planned.commit()
    assert refused(library.apply, item, op("m1", Step.move(TOMA)), expected_rev=1) == "stale_revision"


def test_a_replaced_party_is_never_written_through(store, library):
    item = created(library, make())
    operation = op("m1", Step.move(TOMA))
    change = library.apply(item, operation, expected_rev=1)
    edit(store, TOMA, lambda d: d.update(payload={"note": "a different individual"}))
    with pytest.raises(PartyReplaced):
        change.commit()
    assert refused(library.apply, item, operation, expected_rev=1, parties=change.party_etags) == "party_replaced"
    assert library.apply(item, operation, expected_rev=1).outcome == "applied"  # without the pin: a fresh plan
    assert library.read(item).possession.holder == MARA


def test_a_landed_retry_is_already_applied_whatever_its_party_became(store, library):
    item = created(library, make())
    operation = op("m1", Step.move(TOMA))
    change = library.apply(item, operation, expected_rev=1)
    change.commit()
    edit(store, TOMA, lambda d: d.update(payload={"note": "edited after the move landed"}))
    again = library.apply(item, operation, expected_rev=1, parties=change.party_etags)
    assert (again.outcome, again.after.holder) == ("already_applied", TOMA)


def test_a_retry_pin_must_name_the_operations_parties(library):
    item = created(library, make())
    assert refused(library.apply, item, op("m1", Step.move(TOMA)), expected_rev=1,
                   parties={HALL: "etag"}) == "invalid_field"


def test_an_unreadable_instance_file_blocks_custody_checks(store, library):
    item = created(library, make())
    instance_file(store, HALL).write_bytes(b"{broken")
    found = library.campaign_items("c1")
    assert not found.complete
    assert [(p.path, p.code, p.reason) for p in found.problems] == [
        ("campaigns/c1/instances/location/hall-1.json", "unreadable", "malformed")]
    assert refused(library.apply, item, op("m1", Step.move(TOMA)), expected_rev=1) == "unreadable_items"
    assert refused(library.apply, item, op("e1", Step.equip(MARA, "main-hand")), expected_rev=1) == "unreadable_items"
    assert refused(library.create, make("make-2")) == "unreadable_items"
    assert library.apply(item, op("o1", Step.own(TOMA)), expected_rev=1).outcome == "applied"  # needs no other item


def test_hand_edits_are_reported_never_repaired(store, library):
    first, second = created(library, make("make-1")), created(library, make("make-2"))
    for item in (first, second):
        edit(store, item, lambda d: d["payload"][POSSESSION_KEY].update(equipped="main-hand"))
    found = library.campaign_items("c1")
    assert sorted((p.item.instance_id, p.code) for p in found.problems) == [
        ("item-1", "slot_conflict"), ("item-2", "slot_conflict")]
    instance_file(store, MARA).unlink()
    assert {p.code for p in library.campaign_items("c1").problems} == {"slot_conflict", "missing_party"}
    edit(store, first, lambda d: d["payload"][POSSESSION_KEY].update(schema="grimoire.possession/9"))
    problems = library.campaign_items("c1").problems
    assert ("undecodable", "unsupported_schema") in {(p.code, p.reason) for p in problems}


def test_reading_what_is_not_an_item(store, library):
    assert refused(library.read, MARA) == "not_an_item"
    assert refused(library.read, InstanceRef("c1", "item", "nothing-1")) == "missing_item"


@pytest.mark.parametrize("named, expected", [(False, "item-2"), (True, "item-3")])
def test_an_id_named_only_by_a_possession_record_is_never_reallocated(store, library, named, expected):
    """A hand-deleted party's id must not go to a new item (control: unnamed, the id is free)."""
    first = created(library, make("make-1"))
    if named:  # the record names a holder whose instance file was deleted by hand
        edit(store, first, lambda d: d["payload"][POSSESSION_KEY].update(holder="character/item-2"))
    assert ("item-2" in campaign_taken_ids(store, "c1")) is named
    assert created(library, make("make-2")).instance_id == expected


def test_an_unreadable_instance_file_fails_allocation_closed(store, library):
    instance_file(store, TOMA).write_bytes(b"{broken")
    with pytest.raises(TakenIdsUnlisted):
        campaign_taken_ids(store, "c1")
