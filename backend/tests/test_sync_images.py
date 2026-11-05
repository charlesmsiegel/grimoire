"""C2: 018 image art syncs (image objects, placements, blobs) without ever creating a state the batch refuses.

Blobs are listed by name and size, hashed when copied or first seen, and loaded one at a time when a
commit needs them. Image objects and blobs are create-only; placements follow the three-way rules.
Each art file is its own unit: a denied or held one holds only itself and what depends on it.
"""

from __future__ import annotations

import hashlib

import pytest

from grimoire.core.identity import ImageRef, PlacementRef
from grimoire.images import Crop, Hidden, Placement, Shown
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore, RecordEnvelope, etag
from grimoire.store.layout import address_parts, blob_parts
from grimoire.sync import ResolveRefused, identify, resolve, sync, sync_until_quiet

from image_fixtures import HARBOR, PNG, PNG_2, SAILOR, WREN_A, WREN_B, handle, seed
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
