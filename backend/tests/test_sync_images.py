"""C2: 018 image art syncs (image objects, placements, blobs) without ever creating a state the batch refuses.

Blobs are listed by name and size, hashed when copied or first seen, and loaded one at a time when a
commit needs them. Image objects and blobs are create-only; placements follow the three-way rules.
Each art file is its own unit: a denied or held one holds only itself and what depends on it.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading

import pytest

from grimoire.core.identity import IdentityConflict, ImageRef, InstanceRef, PlacementRef, SourceRef
from grimoire.core.index import DefinitionIndex
from grimoire.images import Crop, Hidden, Placement, Shown
from grimoire.imagestore import ImageLibrary
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, WriteDenied, etag, loader
from grimoire.store.layout import address_parts, blob_parts
from grimoire.sync import ResolveRefused, SyncInterrupted, apply_sync, identify, plan_sync, resolve, sync, sync_until_quiet

from image_fixtures import HARBOR, MIRA, PNG, PNG_2, SAILOR, WREN_A, WREN_B, handle, seed
from ledger_helpers import open_authority
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE

FULL = ("sources", "campaigns", "assets", "kept")
H = {DESKTOP: handle(*FULL), PHONE: handle(*FULL)}
NO_ASSETS = {DESKTOP: handle(*FULL), PHONE: handle("sources", "campaigns", "kept")}


def _path(ref) -> str:  # type: ignore[no-untyped-def]
    return "/".join(address_parts(ref))


def _blob(data: bytes) -> str:
    return "/".join(blob_parts(hashlib.sha256(data).hexdigest(), "png"))


def _images(store: LibraryStore) -> ImageLibrary:
    return ImageLibrary(store, H[DESKTOP], owner=OWNER)


@pytest.fixture
def pair(tmp_path):
    """A seeded desktop (harbor's portrayals, campaign c1's instances) synced once to an empty phone."""
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    yield desktop, phone
    desktop.close()
    phone.close()


def _campaign_art(store: LibraryStore, data: bytes = PNG, image_id: str = "wren", subject=WREN_A):  # type: ignore[no-untyped-def]
    batch = _images(store).change()
    ref = batch.add_image("campaign:c1", image_id, data, {"origin": "test"})
    batch.place(subject, "avatar", Placement("set", "avatar", ref, Crop(0, 0, 5000, 5000)), None)
    batch.commit()
    return ref


def _slot(store: LibraryStore, subject=WREN_A, slot="avatar"):  # type: ignore[no-untyped-def]
    return _images(store).effective_placements(subject).slot(slot)


def test_a_hide_placement_reaches_the_other_device(tmp_path):
    """probe_hide inverted: both devices hold the same art; a later hide on one is hidden on both."""
    stores = {}
    for name in (DESKTOP, PHONE):  # both seeded with identical bytes, the art included
        store = LibraryStore.create(tmp_path / name)
        seed(store)
        batch = _images(store).change()
        ref = batch.add_image(HARBOR, "wren", PNG, {"origin": "test"})
        batch.place(SAILOR, "avatar", Placement("set", "avatar", ref), None)
        batch.commit()
        identify(store, name)
        stores[name] = store
    desktop, phone = stores[DESKTOP], stores[PHONE]
    try:
        assert sync_until_quiet(desktop, phone, H).conflicts == ()
        assert isinstance(_slot(phone), Shown)  # inherited from the sailor portrayal
        _images(desktop).place(WREN_A, "avatar", Placement("hide", "avatar"), None).commit()
        report = sync_until_quiet(desktop, phone, H)
        assert report.conflicts == ()
        hide = _path(PlacementRef(WREN_A, "avatar"))
        assert (hide, PHONE) in report.copied and hide not in report.skipped[DESKTOP]
        assert isinstance(_slot(desktop), Hidden) and isinstance(_slot(phone), Hidden)
    finally:
        desktop.close()
        phone.close()


def test_a_new_image_arrives_with_its_blob_and_resolves_identically(pair):
    desktop, phone = pair
    ref = _campaign_art(desktop)
    report = sync_until_quiet(desktop, phone, H)
    assert report.conflicts == ()
    assert {(_path(ref), PHONE), (_blob(PNG), PHONE), (_path(PlacementRef(WREN_A, "avatar")), PHONE)} <= set(report.copied)
    assert (phone.root / _blob(PNG)).read_bytes() == PNG
    shown = _slot(phone)
    assert isinstance(shown, Shown) and shown == _slot(desktop)
    assert ImageLibrary(phone, H[PHONE]).read_image_bytes(ref) == PNG
    # a later crop change syncs as an ordinary three-way edit
    _, found = _images(desktop).read_placement(PlacementRef(WREN_A, "avatar"))
    _images(desktop).place(WREN_A, "avatar", Placement("set", "avatar", ref, Crop(1000, 1000, 5000, 5000)), found).commit()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    assert _slot(phone) == _slot(desktop) and _slot(phone).crop == Crop(1000, 1000, 5000, 5000)


def test_one_image_in_two_placements_with_distinct_crops(pair):
    desktop, phone = pair
    ref = _campaign_art(desktop)
    _images(desktop).place(WREN_B, "avatar", Placement("set", "avatar", ref, Crop(5000, 5000, 5000, 5000)), None).commit()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    for subject in (WREN_A, WREN_B):
        assert _slot(phone, subject) == _slot(desktop, subject)
    assert _slot(phone, WREN_A).crop != _slot(phone, WREN_B).crop


def test_a_placement_whose_image_differs_on_the_destination_is_held(pair):
    """Both devices create image ``wren`` offline with different pictures: the image objects conflict
    (create-only), and each device's placement naming its own image is held, never copied."""
    desktop, phone = pair
    _campaign_art(desktop, PNG)
    _images(phone).add_image("campaign:c1", "wren", PNG_2).commit()
    report = sync(desktop, phone, H)
    image, placement = _path(ImageRef("campaign:c1", "wren")), _path(PlacementRef(WREN_A, "avatar"))
    assert report.conflict(image).code == "missing_ancestor"
    assert report.conflict(placement).code == "head_dependency"
    assert image in report.conflict(placement).detail
    assert not (phone.root / placement).exists()
    assert _slot(phone) is None or not isinstance(_slot(phone), Shown)


def test_a_placement_whose_image_is_unreadable_on_the_origin_is_held(pair):
    desktop, phone = pair
    ref = _campaign_art(desktop)
    (desktop.root / _path(ref)).write_bytes(b"{not json")
    report = sync(desktop, phone, H)
    assert report.conflict(_path(ref)).code == "blocked"
    assert report.conflict(_path(PlacementRef(WREN_A, "avatar"))).code == "head_dependency"
    assert (_blob(PNG), PHONE) in report.copied  # the blob needs nothing: it still lands
    assert not (phone.root / _path(PlacementRef(WREN_A, "avatar"))).exists()


@pytest.mark.parametrize("same_size", [True, False])
def test_a_corrupt_blob_is_blocked_and_never_propagated(pair, same_size):
    desktop, phone = pair
    ref = _campaign_art(desktop)
    corrupt = PNG[:-1] + b"X" if same_size else PNG + b"tail"
    (desktop.root / _blob(PNG)).write_bytes(corrupt)
    report = sync(desktop, phone, H)
    conflict = report.conflict(_blob(PNG))
    assert conflict.code == "blocked" and "corrupt" in conflict.detail and conflict.kept == {}
    assert not (phone.root / _blob(PNG)).exists() and desktop.conflicts() == ()
    assert all(".json~" in path for path in phone.conflicts())  # a blob is never kept; held records are, as usual
    assert not any(corrupt in (phone.root / path).read_bytes() for path in phone.conflicts())
    for held in (_path(ref), _path(PlacementRef(WREN_A, "avatar"))):  # the fixpoint holds its dependents
        assert report.conflict(held).code == "head_dependency"
        assert not (phone.root / held).exists()


def test_a_handle_without_assets_syncs_everything_else(pair):
    """A blob write is denied ``not_granted``; only the art is denied, every earlier kind still syncs."""
    desktop, phone = pair
    ref = _campaign_art(desktop)
    txn = desktop.transaction()  # an ordinary instance edit and a new campaign instance in the same sync
    txn.put(RecordEnvelope(WREN_B, {"hp": 4}, derived_from=str(SAILOR), base=str(SAILOR.at("r1"))),
            desktop.read(WREN_B).etag)
    txn.commit(H[DESKTOP])
    report = sync(desktop, phone, NO_ASSETS)
    art = {_blob(PNG), _path(ref), _path(PlacementRef(WREN_A, "avatar"))}
    assert {conflict.path for conflict in report.conflicts} == art
    assert {report.conflict(path).code for path in art} == {"denied"}
    assert (_path(WREN_B), PHONE) in report.copied
    assert (phone.root / _path(WREN_B)).read_bytes() == (desktop.root / _path(WREN_B)).read_bytes()
    assert not any((phone.root / path).exists() for path in art)


def test_a_denied_blob_drops_only_its_image_and_placement(pair):
    """Two pictures, one blob already on the phone: only the art of the missing blob is denied."""
    desktop, phone = pair
    first = _campaign_art(desktop, PNG, "wren")
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    second = _campaign_art(desktop, PNG_2, "wren-b", subject=WREN_B)
    reuse = _images(desktop).change()
    reused = reuse.add_image("campaign:c1", "wren-again", PNG)  # the phone already holds this blob
    reuse.commit()
    report = sync(desktop, phone, NO_ASSETS)
    denied = {_blob(PNG_2), _path(second), _path(PlacementRef(WREN_B, "avatar"))}
    assert {conflict.path for conflict in report.conflicts} == denied
    assert (_path(reused), PHONE) in report.copied and first != reused


def test_resolve_of_a_placement_is_refused_until_its_image_is_on_the_receiver(pair):
    desktop, phone = pair
    _campaign_art(desktop)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    placement = PlacementRef(WREN_A, "avatar")
    new = _images(desktop).change()  # the desktop shows a new picture; its blob is then damaged
    second = new.add_image("campaign:c1", "wren-new", PNG_2)
    new.place(WREN_A, "avatar", Placement("set", "avatar", second), _images(desktop).read_placement(placement)[1])
    new.commit()
    (desktop.root / _blob(PNG_2)).write_bytes(PNG_2 + b"tail")
    _images(phone).place(WREN_A, "avatar", Placement("hide", "avatar"), _images(phone).read_placement(placement)[1]
                         ).commit()
    report = sync(desktop, phone, H)
    assert report.conflict(_path(placement)).code == "concurrent"
    mine, theirs = (desktop.root / _path(placement)).read_bytes(), (phone.root / _path(placement)).read_bytes()
    with pytest.raises(ResolveRefused, match="wren-new"):
        resolve(desktop, phone, _path(placement), etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, H)
    assert (phone.root / _path(placement)).read_bytes() == theirs
    (desktop.root / _blob(PNG_2)).write_bytes(PNG_2)  # repaired: the image and blob now sync
    sync(desktop, phone, H)
    assert (phone.root / _path(second)).exists()
    resolve(desktop, phone, _path(placement), etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, H)
    assert _slot(phone) == _slot(desktop) and isinstance(_slot(phone), Shown)


def test_resolve_never_replaces_an_image_object_or_a_blob(pair):
    desktop, phone = pair
    _campaign_art(desktop, PNG)
    _images(phone).add_image("campaign:c1", "wren", PNG_2).commit()
    image = _path(ImageRef("campaign:c1", "wren"))
    sync(desktop, phone, H)
    mine, theirs = (desktop.root / image).read_bytes(), (phone.root / image).read_bytes()
    with pytest.raises(ResolveRefused, match="create-only"):
        resolve(desktop, phone, image, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, H)
    assert (phone.root / image).read_bytes() == theirs


def test_inherited_source_art_waits_for_an_identical_instance(pair):
    """W7: a campaign placement of a source picture means the same only where the instance derives the
    same way: while the instance itself conflicts, the placement is held."""
    desktop, phone = pair
    batch = _images(desktop).change()
    ref = batch.add_image(HARBOR, "wren-sailor", PNG)
    batch.place(WREN_A, "avatar", Placement("set", "avatar", ref, Crop(0, 0, 5000, 5000)), None)
    batch.commit()
    for store, hp in ((desktop, 1), (phone, 2)):  # the instance diverges on both devices
        txn = store.transaction()
        txn.put(RecordEnvelope(WREN_A, {"hp": hp}, derived_from=str(SAILOR), base=str(SAILOR.at("r1"))),
                store.read(WREN_A).etag)
        txn.commit(H[DESKTOP])
    report = sync(desktop, phone, H)
    placement = _path(PlacementRef(WREN_A, "avatar"))
    assert report.conflict(_path(WREN_A)).code == "concurrent"
    assert report.conflict(placement).code == "head_dependency" and _path(WREN_A) in report.conflict(placement).detail
    assert (_path(ref), PHONE) in report.copied  # the source image itself needs only its source and blob
    mine = (desktop.root / _path(WREN_A)).read_bytes()
    theirs = (phone.root / _path(WREN_A)).read_bytes()
    resolve(desktop, phone, _path(WREN_A), etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, H)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    assert _slot(phone) == _slot(desktop) and _slot(phone).layer == "campaign"


def test_an_agreed_blob_is_not_read_again(pair, monkeypatch):
    """Hash at copy or first sight; after one verified agreement, name + size + the archive are trusted."""
    desktop, phone = pair
    _campaign_art(desktop)
    calls: list[str] = []
    real = LibraryStore.verify_blob

    def spy(self, path):  # type: ignore[no-untyped-def]
        calls.append(self.root.name)
        return real(self, path)
    monkeypatch.setattr(LibraryStore, "verify_blob", spy)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    assert calls[0] == DESKTOP  # the copy's origin was hashed before planning the copy
    calls.clear()
    report = sync(desktop, phone, H)
    assert not report.wrote and report.conflicts == () and calls == []


def test_a_blob_both_devices_hold_is_hashed_once_on_each_at_first_sight(tmp_path, monkeypatch):
    stores = {}
    for name in (DESKTOP, PHONE):
        store = LibraryStore.create(tmp_path / name)
        seed(store)
        _images(store).add_image("campaign:c1", "wren", PNG).commit()
        identify(store, name)
        stores[name] = store
    calls: list[str] = []
    real = LibraryStore.verify_blob
    monkeypatch.setattr(LibraryStore, "verify_blob", lambda self, path: calls.append(self.root.name) or real(self, path))
    try:
        assert sync_until_quiet(stores[DESKTOP], stores[PHONE], H).conflicts == ()
        assert sorted(calls) == [DESKTOP, PHONE]
    finally:
        for store in stores.values():
            store.close()


@pytest.fixture
def unpaired(tmp_path):
    """A seeded desktop and an empty phone, both identified, never synced (a fresh pairing)."""
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    yield desktop, phone
    desktop.close()
    phone.close()


def test_a_stale_adjust_is_never_copied(unpaired):
    """W9 ``stale_adjust``: an adjust names the inherited picture it re-crops. Once the source placement
    shows another picture, the origin's own batch refuses the adjust, so sync never copies it."""
    desktop, phone = unpaired
    batch = _images(desktop).change()
    first, second = batch.add_image(HARBOR, "first", PNG), batch.add_image(HARBOR, "second", PNG_2)
    batch.place(SAILOR, "avatar", Placement("set", "avatar", first), None)
    batch.place(WREN_A, "avatar", Placement("adjust", "avatar", first, Crop(0, 0, 5000, 5000)), None)
    batch.commit()
    source = PlacementRef(SAILOR, "avatar")
    _images(desktop).place(SAILOR, "avatar", Placement("set", "avatar", second),
                           _images(desktop).read_placement(source)[1]).commit()
    report = sync_until_quiet(desktop, phone, H)
    adjust = _path(PlacementRef(WREN_A, "avatar"))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {adjust: "head_dependency"}
    assert "second" in report.conflict(adjust).detail and "first" in report.conflict(adjust).detail
    assert not (phone.root / adjust).exists()
    assert (_path(source), PHONE) in report.copied
    assert _slot(phone, SAILOR) == _slot(desktop, SAILOR) and _slot(phone).image == second  # inherited, unadjusted


def _derive_from_unstorable(store: LibraryStore, subject=WREN_A) -> None:  # type: ignore[no-untyped-def]
    """An ordinary 010 instance write whose ``derived_from`` names an entity no library can store."""
    current = store.read(subject).etag if (store.root / _path(subject)).exists() else None
    txn = store.transaction()
    txn.put(RecordEnvelope(subject, {}, derived_from="world:harbor/character/con"), current)
    txn.commit(H[DESKTOP])


def _harbor_art(store: LibraryStore) -> ImageRef:
    batch = _images(store).change()
    ref = batch.add_image(HARBOR, "harbor-pic", PNG)
    batch.place(WREN_A, "avatar", Placement("set", "avatar", ref), None)  # legal: wren-a derives from harbor
    batch.commit()
    return ref


def test_an_unstorable_derivation_entity_holds_only_its_placement(unpaired):
    """A legal placement of a harbor picture, then an ordinary instance edit deriving from
    ``world:harbor/character/con``: a fresh pairing still syncs; only that placement is held."""
    desktop, phone = unpaired
    ref = _harbor_art(desktop)
    _derive_from_unstorable(desktop)
    report = sync_until_quiet(desktop, phone, H)
    placement = _path(PlacementRef(WREN_A, "avatar"))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {placement: "head_dependency"}
    assert "storable" in report.conflict(placement).detail
    assert {(_path(WREN_A), PHONE), (_path(ref), PHONE), (_blob(PNG), PHONE)} <= set(report.copied)
    assert not (phone.root / placement).exists()
    mine = (desktop.root / placement).read_bytes()  # resolve applies the same rule: refused, never a crash
    with pytest.raises(ResolveRefused, match="storable"):
        resolve(desktop, phone, placement, etag(mine), {DESKTOP: etag(mine), PHONE: None}, H)
    assert not (phone.root / placement).exists()


def test_a_hand_made_placement_on_an_unstorable_derivation_is_held(unpaired):
    desktop, phone = unpaired
    odd = InstanceRef("c1", "character", "odd")
    _derive_from_unstorable(desktop, odd)
    _harbor_art(desktop)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    text = (desktop.root / _path(PlacementRef(WREN_A, "avatar"))).read_text().replace("wren-a", "odd")
    made = desktop.root / _path(PlacementRef(odd, "avatar"))  # another tool's placement on ``odd``
    made.parent.mkdir(parents=True)
    made.write_text(text)
    report = sync(desktop, phone, H)
    held = _path(PlacementRef(odd, "avatar"))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {held: "head_dependency"}
    assert not (phone.root / held).exists()


def test_a_hide_placement_reaches_a_device_without_assets(pair):
    """A hide needs no blob: a device without the ``assets`` grant still receives it."""
    desktop, phone = pair
    _images(desktop).place(WREN_A, "avatar", Placement("hide", "avatar"), None).commit()
    report = sync_until_quiet(desktop, phone, NO_ASSETS)
    assert report.conflicts == () and (_path(PlacementRef(WREN_A, "avatar")), PHONE) in report.copied
    assert isinstance(_slot(phone), Hidden)


def _verify_spy(monkeypatch) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str]] = []
    real = LibraryStore.verify_blob

    def spy(self, path):  # type: ignore[no-untyped-def]
        calls.append((self.root.name, path))
        return real(self, path)
    monkeypatch.setattr(LibraryStore, "verify_blob", spy)
    return calls


def test_a_blob_the_receiver_may_not_write_is_never_read(unpaired, monkeypatch):
    """C2: a phone whose durable authority grants no ``assets`` never receives the blobs, and no sync
    reads or hashes them. Once the grant is added, each is hashed on the origin before it is copied."""
    desktop, phone = unpaired
    authority = open_authority(phone)
    try:
        for number, scope in enumerate(("sources", "campaigns", "kept")):
            authority.add_grant(Grant(f"g{number}", OWNER, frozenset({Permission.APPLY}),
                                      ResourcePath.parse(scope), OWNER))
        handles = {DESKTOP: H[DESKTOP], PHONE: authority.handle_for(OWNER, trusted=True)}
        assert sync_until_quiet(desktop, phone, handles).conflicts == ()
        batch = _images(desktop).change()
        pictures = [PNG + bytes([n]) * 100 for n in range(3)]
        for n, data in enumerate(pictures):
            ref = batch.add_image("campaign:c1", f"pic-{n}", data)
        batch.place(WREN_A, "avatar", Placement("set", "avatar", ref, Crop(0, 0, 5000, 5000)), None)
        batch.commit()
        calls = _verify_spy(monkeypatch)
        blobs = {_blob(data) for data in pictures}
        for _ in range(3):
            report = sync_until_quiet(desktop, phone, handles)
            assert calls == []
            codes = {conflict.path: conflict.code for conflict in report.conflicts}
            assert set(codes.values()) == {"denied"} and blobs | {_path(PlacementRef(WREN_A, "avatar"))} <= set(codes)
            assert not any((phone.root / path).exists() for path in codes)
        authority.add_grant(Grant("g-assets", OWNER, frozenset({Permission.APPLY}), ResourcePath.parse("assets"),
                                  OWNER))
        report = sync_until_quiet(desktop, phone, handles)
        assert report.conflicts == () and blobs <= {path for path, _ in report.copied}
        assert sorted(calls) == sorted((DESKTOP, path) for path in blobs)  # once, on the origin, before the copy
        assert _slot(phone) == _slot(desktop)
    finally:
        authority.close()


def test_a_blob_in_a_conflict_that_never_copies_is_not_read(pair, monkeypatch):
    """A blob deleted by hand on one device after agreement is a ``deleted`` conflict on every sync; a
    conflict that copies nothing never reads the blob. Resolving it hashes it first."""
    desktop, phone = pair
    _campaign_art(desktop)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    (phone.root / _blob(PNG)).unlink()
    calls = _verify_spy(monkeypatch)
    for _ in range(2):
        report = sync(desktop, phone, H)
        assert report.conflict(_blob(PNG)).code == "deleted" and calls == []
    name = "sha256:" + hashlib.sha256(PNG).hexdigest()
    resolve(desktop, phone, _blob(PNG), name, {DESKTOP: name, PHONE: None}, H)
    assert calls == [(DESKTOP, _blob(PNG))] and (phone.root / _blob(PNG)).read_bytes() == PNG


@pytest.mark.parametrize("state", ["poisoned", "closed"])
def test_an_unavailable_authority_propagates_instead_of_denying_the_art(unpaired, state):
    """SYNC.md "Denied writes": a denial for an unavailable authority propagates. The plan must not
    pre-deny a blob (holding its image and placement as ``denied``) from grants that an authority which
    failed to persist, or was closed, still lists in memory: the commit decides, and it raises."""
    desktop, phone = unpaired
    authority = open_authority(phone)
    try:
        for number, scope in enumerate(("sources", "campaigns", "kept")):  # no assets grant
            authority.add_grant(Grant(f"g{number}", OWNER, frozenset({Permission.APPLY}),
                                      ResourcePath.parse(scope), OWNER))
        handles = {DESKTOP: H[DESKTOP], PHONE: authority.handle_for(OWNER, trusted=True)}
        assert sync_until_quiet(desktop, phone, handles).conflicts == ()
        _campaign_art(desktop)
        if state == "poisoned":
            authority._poison(OSError(28, "No space left on device"))  # the ledger failed to persist
        else:
            authority.close()
        assert not authority.usable
        with pytest.raises(WriteDenied, match="unavailable"):
            sync(desktop, phone, handles)
        assert not (phone.root / _blob(PNG)).exists()
    finally:
        authority.close()


def test_the_same_picture_dropped_by_hand_on_both_devices_can_be_imported_again(pair, monkeypatch):
    """A blob's name is its content hash, so the same bytes returning after a tombstone are never an
    outdated version: they are copied like a new file (create-only), and the new image and placement
    that use them arrive with them (a record's tombstone still holds its exact bytes: test_sync_scenarios S4)."""
    desktop, phone = pair
    old = _campaign_art(desktop, image_id="old")
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    for store in (desktop, phone):  # IMAGES.md: an unwanted (say, oversize) blob is deleted by hand
        for path in (_blob(PNG), _path(old), _path(PlacementRef(WREN_A, "avatar"))):
            (store.root / path).unlink()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()  # both absent: tombstones archived
    new = _campaign_art(desktop, image_id="new", subject=WREN_B)
    calls = _verify_spy(monkeypatch)
    report = sync_until_quiet(desktop, phone, H)
    assert report.conflicts == ()
    assert {_blob(PNG), _path(new), _path(PlacementRef(WREN_B, "avatar"))} <= {path for path, _ in report.copied}
    assert calls == [(DESKTOP, _blob(PNG))]  # hashed on the origin before the copy, as any new blob
    assert (phone.root / _blob(PNG)).read_bytes() == PNG and _slot(phone, WREN_B) == _slot(desktop, WREN_B)
    assert sync(desktop, phone, H).conflicts == ()


@pytest.mark.parametrize("mode", ["inherit", "adjust"])
def test_a_hand_edited_source_inherit_or_adjust_is_never_copied(unpaired, mode):
    """W8 ``mode_not_allowed``: a standalone source placement inherits nothing, so it may ``set`` or ``hide``
    but never reset or re-crop. One edited by hand to ``inherit`` or ``adjust`` is a state the origin's own
    batch refuses, so sync holds it, never copies it."""
    desktop, phone = unpaired
    batch = _images(desktop).change()
    ref = batch.add_image(HARBOR, "wren", PNG, {"origin": "test"})
    batch.place(SAILOR, "avatar", Placement("set", "avatar", ref), None)
    batch.commit()
    source = desktop.root / _path(PlacementRef(SAILOR, "avatar"))
    text = source.read_text()
    edited = (text.replace('"image": "world:harbor#image/wren",\n    "mode": "set",', '"mode": "inherit",')
              .replace(',\n    "role": "avatar"', "")
              if mode == "inherit" else text.replace('"mode": "set"', '"mode": "adjust"'))
    assert edited != text
    source.write_text(edited)
    report = sync_until_quiet(desktop, phone, H)
    held = _path(PlacementRef(SAILOR, "avatar"))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {held: "head_dependency"}
    assert "mode_not_allowed" in report.conflict(held).detail
    assert not (phone.root / held).exists()
    assert {(_path(ref), PHONE), (_blob(PNG), PHONE)} <= set(report.copied)  # the rest still syncs
    mine = source.read_bytes()  # resolve applies the same rule: refused
    with pytest.raises(ResolveRefused, match="mode_not_allowed"):
        resolve(desktop, phone, held, etag(mine), {DESKTOP: etag(mine), PHONE: None}, H)
    assert not (phone.root / held).exists()


@pytest.mark.parametrize("how", ["sync", "resolve"])
def test_a_blob_copy_never_deadlocks_with_a_planner(pair, monkeypatch, how):
    """The commit that copies desktop's blob onto the phone loads it lazily from the desktop. A planner
    of the same pair locks the libraries in root order (desktop, then phone). If the commit held only the
    phone's lock while it loaded from the desktop, each would hold one lock and wait for the other.

    The commit pauses at its first load until the planner holds the desktop's lock (or 2 seconds pass):
    with one lock order the planner cannot get it, and both finish; with opposite orders, a hang.
    """
    desktop, phone = pair
    assert os.fspath(desktop.root) < os.fspath(phone.root)  # the planner's order: desktop first
    _campaign_art(desktop)
    if how == "sync":
        plan = plan_sync(desktop, phone, H)
        assert any(copy.path == _blob(PNG) for copy in plan.copies[PHONE])

        def commit() -> None:
            apply_sync(plan, H)
    else:
        assert sync_until_quiet(desktop, phone, H).conflicts == ()
        (phone.root / _blob(PNG)).unlink()  # a ``deleted`` conflict that resolve delivers
        name = "sha256:" + hashlib.sha256(PNG).hexdigest()

        def commit() -> None:
            resolve(desktop, phone, _blob(PNG), name, {DESKTOP: name, PHONE: None}, H)
    loading, planning = threading.Event(), threading.Event()
    read, cached_reads = desktop.read_blob_exact, LibraryStore.cached_reads

    def paused_read(*args):  # type: ignore[no-untyped-def]
        if not loading.is_set():
            loading.set()  # the commit on the phone holds the phone's lock here
            planning.wait(timeout=2)
        return read(*args)

    @contextlib.contextmanager
    def announced(self):  # type: ignore[no-untyped-def]
        with cached_reads(self):
            if self is desktop and threading.current_thread().name == "planner":
                planning.set()
            yield
    monkeypatch.setattr(desktop, "read_blob_exact", paused_read)
    monkeypatch.setattr(LibraryStore, "cached_reads", announced)
    errors: list[BaseException] = []

    def run(work) -> None:  # type: ignore[no-untyped-def]
        try:
            work()
        except Exception as error:  # noqa: BLE001 - only a hang (or an unexpected error) fails
            errors.append(error)

    def planner() -> None:
        assert loading.wait(timeout=10)
        plan_sync(desktop, phone, H)
    threads = [threading.Thread(target=run, args=(work,), name=name, daemon=True)  # a deadlock fails, never hangs
               for work, name in ((commit, "committer"), (planner, "planner"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), "a blob copy and a planner deadlocked"
    assert all(isinstance(error, SyncInterrupted) for error in errors), errors
    assert loading.is_set() and (phone.root / _blob(PNG)).read_bytes() == PNG


def _new_avatar_over_the_synced_one(desktop: LibraryStore, phone: LibraryStore) -> tuple[ImageRef, str]:
    """Wren's avatar shows ``wren`` (PNG) on both devices; the desktop then places a new picture
    ``wren-new`` (PNG_2) over it, through its own batch. Returns the new image and the placement path."""
    first = _campaign_art(desktop)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    assert isinstance(_slot(phone), Shown) and _slot(phone).image == first
    placement = PlacementRef(WREN_A, "avatar")
    batch = _images(desktop).change()
    second = batch.add_image("campaign:c1", "wren-new", PNG_2)
    batch.place(WREN_A, "avatar", Placement("set", "avatar", second), _images(desktop).read_placement(placement)[1])
    batch.commit()
    return second, _path(placement)


# a hand edit of the new image object, before its first sync, that the 018 batch and resolver refuse (W6)
IMAGE_EDITS = {
    "blob_size_mismatch": (f'"size": {len(PNG_2)}', f'"size": {len(PNG_2) + 1}'),
    "unreadable_payload": (f'"size": {len(PNG_2)}', f'"size": "{len(PNG_2)}"'),
    "another_schema": ('"schema": "grimoire.image/0"', '"schema": "grimoire.image/9"'),
}


@pytest.mark.parametrize("edit", sorted(IMAGE_EDITS))
def test_an_image_the_batch_cannot_show_never_replaces_a_working_avatar(pair, edit):
    """W6: a placement shows an image only if that image object reads (schema, 018 codec) and its blob
    is present at the size it declares (``missing_blob`` in the batch; ``invalid_image`` or
    ``blob_size_mismatch`` in the resolver). Such an image object, edited by hand on the origin, is
    held with every placement that shows it; the receiver keeps showing its working avatar."""
    desktop, phone = pair
    second, placement = _new_avatar_over_the_synced_one(desktop, phone)
    image = desktop.root / _path(second)
    old, new = IMAGE_EDITS[edit]
    text = image.read_text()
    assert old in text
    image.write_text(text.replace(old, new))
    with pytest.raises(Exception, match="missing|wrong size|invalid|schema"):  # the origin's own batch refuses it
        _images(desktop).place(WREN_B, "avatar", Placement("set", "avatar", second), None).commit()
    theirs = (phone.root / placement).read_bytes()
    report = sync_until_quiet(desktop, phone, H)
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {
        _path(second): "head_dependency", placement: "head_dependency"}
    assert "refuse" in report.conflict(placement).detail and "refuse" in report.conflict(_path(second)).detail
    assert (_blob(PNG_2), PHONE) in report.copied  # the blob needs nothing: it still lands
    assert not (phone.root / _path(second)).exists() and (phone.root / placement).read_bytes() == theirs
    shown = _slot(phone)
    assert isinstance(shown, Shown) and shown.image == ImageRef("campaign:c1", "wren")
    mine = (desktop.root / placement).read_bytes()  # resolve applies the same rule: refused
    with pytest.raises(ResolveRefused, match="refuse"):
        resolve(desktop, phone, placement, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, H)
    edited = image.read_bytes()
    with pytest.raises(ResolveRefused, match="refuse"):
        resolve(desktop, phone, _path(second), etag(edited), {DESKTOP: etag(edited), PHONE: None}, H)
    assert (phone.root / placement).read_bytes() == theirs and not (phone.root / _path(second)).exists()
    image.write_text(text)  # repaired on the origin: never copied, so the image is still new and syncs
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    assert _slot(phone) == _slot(desktop) and _slot(phone).image == second


def test_a_placement_with_another_schema_never_replaces_a_working_avatar(pair):
    """The resolver reads a placement only under 018's placement schema (otherwise ``invalid_placement``),
    so a placement edited by hand to another schema is held, never copied over a working one."""
    desktop, phone = pair
    second, placement = _new_avatar_over_the_synced_one(desktop, phone)
    edited = desktop.root / placement
    text = edited.read_text()
    assert '"schema": "grimoire.placement/0"' in text
    edited.write_text(text.replace('"schema": "grimoire.placement/0"', '"schema": "grimoire.placement/9"'))
    theirs = (phone.root / placement).read_bytes()
    report = sync_until_quiet(desktop, phone, H)
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {placement: "head_dependency"}
    assert "schema" in report.conflict(placement).detail
    assert {(_path(second), PHONE), (_blob(PNG_2), PHONE)} <= set(report.copied)  # the image still lands
    assert (phone.root / placement).read_bytes() == theirs
    assert isinstance(_slot(phone), Shown) and _slot(phone).image == ImageRef("campaign:c1", "wren")
    mine = edited.read_bytes()
    with pytest.raises(ResolveRefused, match="schema"):
        resolve(desktop, phone, placement, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, H)
    assert (phone.root / placement).read_bytes() == theirs


def test_an_adjust_of_an_inherited_placement_018_cannot_read_never_replaces_a_working_picture(unpaired):
    """W9 reads the inherited placement an adjust re-crops through 018's own schema and codec. When that
    source placement is unreadable on both devices (here: another schema, edited the same way on each),
    the batch refuses the adjust (``nothing_to_adjust``) and the resolver reads it as ``invalid_placement``,
    so sync holds the adjust instead of copying it over the receiver's working campaign picture."""
    desktop, phone = unpaired
    batch = _images(desktop).change()
    first = batch.add_image(HARBOR, "first", PNG)
    batch.place(SAILOR, "avatar", Placement("set", "avatar", first), None)
    batch.commit()
    wren = _campaign_art(desktop)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    adjust = PlacementRef(WREN_A, "avatar")
    _images(desktop).place(WREN_A, "avatar", Placement("adjust", "avatar", first, Crop(0, 0, 5000, 5000)),
                           _images(desktop).read_placement(adjust)[1]).commit()
    source = _path(PlacementRef(SAILOR, "avatar"))
    for store in (desktop, phone):
        text = (store.root / source).read_text()
        assert '"schema": "grimoire.placement/0"' in text
        (store.root / source).write_text(text.replace('"schema": "grimoire.placement/0"', '"schema": "grimoire.placement/9"'))
    theirs = (phone.root / _path(adjust)).read_bytes()
    report = sync_until_quiet(desktop, phone, H)
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {_path(adjust): "head_dependency"}
    assert "schema" in report.conflict(_path(adjust)).detail
    assert (phone.root / _path(adjust)).read_bytes() == theirs
    assert isinstance(_slot(phone), Shown) and _slot(phone).image == wren
    mine = (desktop.root / _path(adjust)).read_bytes()
    with pytest.raises(ResolveRefused, match="schema"):
        resolve(desktop, phone, _path(adjust), etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, H)
    assert (phone.root / _path(adjust)).read_bytes() == theirs


# -- W5: art of a source load() excludes ------------------------------------------------------------
#: Where a hand copy of card:mira's revision lands inside harbor's records (see ``_exclude_harbor``).
STRAY = SourceRef(HARBOR, "character", "mira").at("r1")
RIVAL = "catalog:rival"


def _exclude_harbor(store: LibraryStore):  # type: ignore[no-untyped-def]
    """A hand copy of card:mira's revision dropped into harbor's records. The file claims another
    source, so ``load()`` excludes all of harbor (``ownership``) though its manifest still reads, and
    sync blocks that one file (``address_mismatch``). Returns the stray file, to repair by deleting it."""
    stray = store.root / _path(STRAY)
    stray.parent.mkdir(parents=True)
    stray.write_bytes((store.root / _path(MIRA.at("r1"))).read_bytes())
    assert HARBOR in store.excluded_sources()
    return stray


def _harbor_avatar(store: LibraryStore, subjects=(SAILOR, WREN_B)) -> ImageRef:  # type: ignore[no-untyped-def]
    """A harbor picture, set through the 018 batch (while harbor is admitted) as the avatar of the
    sailor portrayal and of campaign instances that inherit from it."""
    batch = _images(store).change()
    ref = batch.add_image(HARBOR, "harbor-avatar", PNG_2)
    for subject in subjects:
        batch.place(subject, "avatar", Placement("set", "avatar", ref), None)
    batch.commit()
    return ref


def _harbor_art_paths(ref: ImageRef) -> set[str]:
    """The art that belongs to harbor or shows its picture: the image, the source placement and the
    campaign placement that inherits the picture through wren-b's derivation."""
    return {_path(ref), _path(PlacementRef(SAILOR, "avatar")), _path(PlacementRef(WREN_B, "avatar"))}


def test_art_of_a_source_the_origin_excludes_is_held_until_the_origin_repairs_it(pair):
    """W5 ``excluded_source``: while the origin's ``load()`` excludes harbor, the origin resolves its art
    to nothing, so a receiver that admits harbor would show pictures the origin does not; the image
    object is create-only, so that copy could never be taken back. Every harbor art file (the image,
    the source placement, the campaign placement inheriting the picture) is held, naming the
    exclusion; campaign art that reads no source, and the blobs, still land. Once the origin is
    repaired, the next sync copies the art and both devices show the same pictures."""
    desktop, phone = pair
    ref = _harbor_avatar(desktop)
    wren = _campaign_art(desktop, subject=WREN_A)
    stray = _exclude_harbor(desktop)
    report = sync_until_quiet(desktop, phone, H)
    held = _harbor_art_paths(ref)
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {
        **dict.fromkeys(held, "head_dependency"), _path(STRAY): "blocked"}
    assert all("excluded on the origin" in report.conflict(path).detail for path in held)
    assert {(_path(wren), PHONE), (_blob(PNG), PHONE), (_blob(PNG_2), PHONE)} <= set(report.copied)
    assert not any((phone.root / path).exists() for path in held)
    assert _slot(phone, SAILOR) is None and isinstance(_slot(phone, WREN_A), Shown)
    mine = (desktop.root / _path(ref)).read_bytes()  # resolve applies the same rule: refused
    with pytest.raises(ResolveRefused, match="excluded on the origin"):
        resolve(desktop, phone, _path(ref), etag(mine), {DESKTOP: etag(mine), PHONE: None}, H)
    assert not (phone.root / _path(ref)).exists()
    stray.unlink()  # repaired on the origin: harbor is admitted again, and the held art is still new
    stray.parent.rmdir()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    for subject in (SAILOR, WREN_B):
        assert isinstance(_slot(phone, subject), Shown) and _slot(phone, subject) == _slot(desktop, subject)
        assert _slot(phone, subject).image == ref


class _RivalIndex(DefinitionIndex):
    """Simulates a whole-library ``identity_conflict`` (today's loader drops the links that could cause
    one before the index sees them; see test_store_exclusions.py): while a source ``catalog:rival``
    exists, harbor is excluded, though neither source's own files are damaged."""

    def __init__(self, sources, definitions, heads=None, links=()):  # type: ignore[no-untyped-def]
        sources = list(sources)
        if {HARBOR, RIVAL} <= {source.source_id for source in sources}:
            raise IdentityConflict(f"{HARBOR} conflicts with {RIVAL}", (HARBOR,))
        super().__init__(sources, definitions, heads, links)


@pytest.mark.parametrize("excluding", [DESKTOP, PHONE])
def test_art_of_a_source_excluded_only_across_sources_is_held(pair, monkeypatch, excluding):
    """A cross-source exclusion is invisible to one source's own files: neither the origin's manifest
    nor the receiver store's art-only check (which reads one source's scan) sees it. Sync reads both
    devices' ``excluded_sources`` (``load()``'s own verdict), so harbor's art is held whichever device
    excludes harbor: on the origin it shows nothing; on the receiver its own batch would refuse it."""
    desktop, phone = pair
    monkeypatch.setattr(loader, "DefinitionIndex", _RivalIndex)
    for store in pair:
        store._scans, store._exclusions = {}, None  # the algorithm changed under the cache (tests only)
    ref = _harbor_avatar(desktop)
    rival = {DESKTOP: desktop, PHONE: phone}[excluding]
    txn = rival.transaction()
    txn.put(SourceManifest(RIVAL, OWNER), None)
    txn.commit(H[excluding])
    assert {store.root.name for store in pair if HARBOR in store.excluded_sources()} == {excluding}
    report = sync(desktop, phone, H)
    held = _harbor_art_paths(ref)
    assert {conflict.path: conflict.code for conflict in report.conflicts} == dict.fromkeys(held, "head_dependency")
    why = "excluded on the origin" if excluding == DESKTOP else f"excluded on {PHONE}"
    assert all(why in report.conflict(path).detail for path in held)
    assert not any((phone.root / path).exists() for path in held)
    assert (_blob(PNG_2), PHONE) in report.copied


def test_inherited_art_is_held_while_the_receiver_excludes_its_source(pair):
    """The receiver's store refuses source art written into a source its own files exclude, but it
    checks a campaign placement against its campaign only. So a new campaign placement that inherits
    a harbor picture both devices already hold would land on a phone that excludes harbor, in a state
    the phone's own batch refuses (``excluded_source``). It is held, naming the phone's exclusion,
    and lands once the phone is repaired."""
    desktop, phone = pair
    ref = _harbor_avatar(desktop, subjects=(SAILOR,))
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    stray = _exclude_harbor(phone)
    _images(desktop).place(WREN_B, "avatar", Placement("set", "avatar", ref, Crop(0, 0, 5000, 5000)), None).commit()
    report = sync(desktop, phone, H)
    placement = _path(PlacementRef(WREN_B, "avatar"))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {
        placement: "head_dependency", _path(STRAY): "blocked"}
    assert f"excluded on {PHONE}" in report.conflict(placement).detail
    assert not (phone.root / placement).exists()
    mine = (desktop.root / placement).read_bytes()  # resolve applies the same rule: refused
    with pytest.raises(ResolveRefused, match=f"excluded on {PHONE}"):
        resolve(desktop, phone, placement, etag(mine), {DESKTOP: etag(mine), PHONE: None}, H)
    assert not (phone.root / placement).exists()
    stray.unlink()  # repaired on the phone: the held placement lands and resolves as on the desktop
    stray.parent.rmdir()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    assert isinstance(_slot(phone, WREN_B), Shown) and _slot(phone, WREN_B) == _slot(desktop, WREN_B)
