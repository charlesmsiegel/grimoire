"""New unique items carry 028's per-device tag, so two offline devices never mint the same item id.

All fixtures are synthetic (see ``possession_fixtures``); no model is called.
"""

from __future__ import annotations

from hashlib import sha256

import pytest

from grimoire.core.entity import EntityDefinition
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload
from grimoire.core.facets import FacetSet, Physical, Portable
from grimoire.core.identity import SEGMENT_MAX, SourceRef, validate_segment
from grimoire.possessions import PossessionRefused, Step
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, StaleWrite
from grimoire.sync import identify, sync

from possession_fixtures import CHEST, LONGSWORD, MARA, STARFALL, TOMA, make, op, possession_library, seed
from store_helpers import OWNER, owner_handle

SCOPES = ("sources", "campaigns", "kept")
RELICS = "catalog:relics"


def tag(device: str) -> str:
    """028's documented tag: the first eight hex digits of the SHA-256 of the device ID."""
    return sha256(device.encode()).hexdigest()[:8]


def created(library, creation, **kwargs):
    change = library.create(creation, **kwargs)
    assert change.outcome == "applied"
    change.commit()
    return change.item


def instance_files(store: LibraryStore) -> set[str]:
    return {path for path in store.scan().files if "/instances/" in path}


def paired_pair(tmp_path):
    """A seeded desktop and a phone, each identified, synced once."""
    desktop = LibraryStore.create(tmp_path / "desktop")
    seed(desktop)
    phone = LibraryStore.create(tmp_path / "phone")
    identify(desktop, "desktop")
    identify(phone, "phone")
    handles = {"desktop": owner_handle(*SCOPES), "phone": owner_handle(*SCOPES)}
    assert not sync(desktop, phone, handles).conflicts
    return desktop, phone, handles


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


def test_an_identified_library_tags_new_items_with_its_device(store):
    identify(store, "desktop")
    library = possession_library(store)
    first = created(library, make("make-1", name="Longsword", source=LONGSWORD))
    second = created(library, make("make-2", name="Longsword", source=LONGSWORD))
    original = created(library, make("make-3", name="Lantern"))
    expected = tag("desktop")
    assert (first.instance_id, second.instance_id, original.instance_id) == (
        f"longsword-{expected}-1", f"longsword-{expected}-2", f"item-{expected}-1")
    assert library.read(first).name == library.read(second).name == "Longsword"


def test_two_offline_devices_making_the_same_sword_keep_two_swords_after_sync(tmp_path):
    desktop, phone, handles = paired_pair(tmp_path)
    # offline: each device gives Mara a new longsword from the same template
    on_desktop = created(possession_library(desktop), make("make-desktop", name="Longsword", source=LONGSWORD))
    on_phone = created(possession_library(phone), make("make-phone", holder=TOMA, owner=TOMA, name="Longsword",
                                                       source=LONGSWORD))
    assert on_desktop != on_phone  # without the tag both would be longsword-1: one file, two swords
    report = sync(desktop, phone, handles)
    assert not report.conflicts, report.conflicts
    for device in (desktop, phone):
        library = possession_library(device)
        assert library.read(on_desktop).possession.state.holder == MARA
        assert library.read(on_phone).possession.state.holder == TOMA


def test_items_made_before_pairing_keep_their_ids_and_new_ones_are_tagged(store):
    library = possession_library(store)
    old = created(library, make("make-1", name="Longsword", source=LONGSWORD))
    assert old.instance_id == "longsword-1"
    identify(store, "desktop")
    library = possession_library(store)
    moved = library.apply(old, op("m1", Step.move(CHEST)), expected_rev=library.read(old).rev)
    moved.commit()
    assert library.read(old).possession.state.holder == CHEST
    new = created(library, make("make-2", name="Longsword", source=LONGSWORD))
    assert new.instance_id == f"longsword-{tag('desktop')}-1"
    # a retry of the creation that landed before pairing is still that item, not a new tagged one
    again = library.create(make("make-1", name="Longsword", source=LONGSWORD))
    assert (again.outcome, again.item) == ("already_applied", old)


@pytest.mark.parametrize("source", [LONGSWORD, STARFALL], ids=["template", "individual"])
def test_a_creation_planned_before_pairing_is_stale_once_the_library_is_paired(store, source):
    library = possession_library(store)
    change = library.create(make("make-1", name="Blade", source=source))
    assert change.item.instance_id == f"{source.entity_id}-1"
    identify(store, "desktop")
    before = instance_files(store)
    with pytest.raises(StaleWrite):
        change.commit()
    assert instance_files(store) == before


@pytest.mark.parametrize("source", [LONGSWORD, STARFALL], ids=["template", "individual"])
@pytest.mark.parametrize("damage", [b"{not json", b'{"format": "grimoire.sync.device/1"}'])
def test_a_damaged_device_identity_refuses_new_items_rather_than_plain_numbers(store, damage, source):
    identify(store, "desktop")
    (store.root / ".sync" / "device.json").write_bytes(damage)
    before = instance_files(store)
    with pytest.raises(PossessionRefused) as refused:
        possession_library(store).create(make("make-1", name="Blade", source=source))
    assert refused.value.reason == "device_identity"
    assert instance_files(store) == before


def test_a_long_base_is_cut_before_the_tag_so_the_id_stays_one_segment(store):
    identify(store, "desktop")
    kind = "l" * SEGMENT_MAX  # the longest kind: the id still fits, with the tag and number intact
    # planned, not committed: a 128-character folder plus this file name is past Windows' path limit
    item = possession_library(store).create(make("make-1", name="Lantern"), kind=kind).item
    assert item.instance_id == f"{kind[:100]}-{tag('desktop')}-1"
    validate_segment(item.instance_id, "instance id")


def artifact_tag(source) -> str:
    return sha256(str(source.unpinned()).encode()).hexdigest()[:8]


def add_relic_starfall(store: LibraryStore):
    """A second catalog with its own one-of-a-kind ``starfall``: same entity id, a different artifact."""
    relic = SourceRef(RELICS, "item", "starfall", "r1")
    txn = store.transaction()
    txn.put(SourceManifest(RELICS, OWNER, "Relics"), None)
    definition = EntityDefinition(relic, "Starfall", "individual", FacetSet((Physical("glass"), Portable())))
    txn.put(RecordEnvelope(relic, definition_to_payload(definition), name="Starfall",
                           extra={"schema": ENTITY_SCHEMA}), None)
    txn.commit(owner_handle())
    return relic


def test_one_particular_artifact_made_on_two_devices_meets_on_one_id(tmp_path):
    """An ``individual`` definition is one thing per campaign: the clash is real and must stay visible."""
    desktop, phone, handles = paired_pair(tmp_path)
    on_desktop = created(possession_library(desktop), make("make-desktop", name="Starfall", source=STARFALL))
    on_phone = created(possession_library(phone), make("make-phone", holder=TOMA, owner=TOMA, name="Starfall",
                                                       source=STARFALL))
    assert on_desktop == on_phone and on_desktop.instance_id == f"starfall-{artifact_tag(STARFALL)}-1"
    path = f"campaigns/{on_desktop.campaign_id}/instances/item/{on_desktop.instance_id}.json"
    assert sync(desktop, phone, handles).conflict(path) is not None  # keep-both, never two Starfalls


def test_two_different_artifacts_with_one_name_never_meet(tmp_path):
    """Two catalogs each with their own ``starfall``: whatever either device made first, no false clash."""
    desktop, phone, handles = paired_pair(tmp_path)
    relic = add_relic_starfall(desktop)
    assert not sync(desktop, phone, handles).conflicts
    first = created(possession_library(desktop), make("make-relic", name="Starfall", source=relic))
    on_desktop = created(possession_library(desktop), make("make-arms", name="Starfall", source=STARFALL))
    on_phone = created(possession_library(phone), make("make-phone", holder=TOMA, owner=TOMA, name="Starfall",
                                                       source=STARFALL))
    assert first != on_desktop and on_desktop == on_phone  # the same blade drew one id on both devices
    report = sync(desktop, phone, handles)
    path = f"campaigns/{first.campaign_id}/instances/item/{first.instance_id}.json"
    assert report.conflict(path) is None
    assert possession_library(phone).read(first).possession.origin.source == relic


def test_an_identity_damaged_after_the_plan_writes_nothing(store):
    identify(store, "desktop")
    change = possession_library(store).create(make("make-1", name="Longsword", source=LONGSWORD))
    (store.root / ".sync" / "device.json").write_bytes(b"{not json")
    before = instance_files(store)
    with pytest.raises(StaleWrite):
        change.commit()
    assert instance_files(store) == before


@pytest.mark.parametrize("shape", ["link", "folder"])
def test_a_device_identity_that_is_not_a_file_refuses_new_items(store, tmp_path, shape):
    identify(store, "desktop")
    identity = store.root / ".sync" / "device.json"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(identity.read_bytes())
    identity.unlink()
    if shape == "link":
        try:
            identity.symlink_to(elsewhere)
        except OSError:
            pytest.skip("this filesystem cannot hold a link")
    else:
        identity.mkdir()
    with pytest.raises(PossessionRefused) as refused:
        possession_library(store).create(make("make-1", name="Longsword", source=LONGSWORD))
    assert refused.value.reason == "device_identity"


def test_an_artifact_made_before_and_after_pairing_is_reported_twice_never_hidden(tmp_path):
    """One particular artifact made on a library with no device identity yet (``starfall-1``) and on an
    identified one (``starfall-<digest>-1``) are two files, so sync copies both without a conflict.
    Each copy is then reported ``duplicate_individual``, naming both (Wave 4 audit); nothing is repaired."""
    desktop = LibraryStore.create(tmp_path / "desktop")
    seed(desktop)
    phone = LibraryStore.create(tmp_path / "phone")
    seed(phone)  # the same campaign set up on both devices before they ever met
    identify(phone, "phone")
    old = created(possession_library(desktop), make("make-desk", name=None, source=STARFALL))
    new = created(possession_library(phone), make("make-phone", holder=TOMA, owner=TOMA, name=None, source=STARFALL))
    assert old.instance_id == "starfall-1" and old != new
    identify(desktop, "desktop")
    handles = {"desktop": owner_handle(*SCOPES), "phone": owner_handle(*SCOPES)}
    assert not sync(desktop, phone, handles).conflicts
    for store in (desktop, phone):
        found = possession_library(store).campaign_items("c1")
        assert set(found.views) >= {old, new} and found.complete
        twins = [problem for problem in found.problems if problem.code == "duplicate_individual"]
        assert sorted(str(problem.item) for problem in twins) == sorted((str(old), str(new)))
        assert {problem.reason for problem in twins} == {", ".join(sorted((str(old), str(new))))}
    plain = possession_library(desktop)  # two copies of an ordinary sword add no problem: only the artifact's two
    created(plain, make("make-a", name=None, source=LONGSWORD))
    created(plain, make("make-b", name=None, source=LONGSWORD))
    assert [p.code for p in plain.campaign_items("c1").problems] == ["duplicate_individual"] * 2
    desktop.close()
    phone.close()
