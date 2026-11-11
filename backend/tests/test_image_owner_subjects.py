"""A source or a play scope as a whole as the subject of its own pictures (``OwnerRef``: a cover, a chat's
backdrop), and a standalone subject's ``hide``: the 018 rules they keep, their files, and their sync.

Synthetic sources and byte strings only.
"""

from __future__ import annotations

import hashlib

import pytest
from image_fixtures import CARD, HARBOR, PNG, PNG_2, SAILOR, WREN_A, handle, seed
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE

from grimoire.core.identity import ImageRef, OwnerRef, PlacementRef, ScopeRef
from grimoire.images import Crop, Hidden, Placement, Shown, Unavailable, validate_placement
from grimoire.imagestore import ImageLibrary, InvalidPlacement
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite, etag
from grimoire.store.layout import address_parts, blob_parts, classify
from grimoire.sync import ResolveRefused, identify, resolve, sync_until_quiet
from grimoire.sync.art import art_needs

FULL = ("sources", "campaigns", "assets", "kept")
H = {DESKTOP: handle(*FULL), PHONE: handle(*FULL)}
WORLD, CAMPAIGN = OwnerRef(HARBOR), OwnerRef("campaign:c1")


def _path(ref) -> str:  # type: ignore[no-untyped-def]
    return "/".join(address_parts(ref))


def _blob(data: bytes) -> str:
    return "/".join(blob_parts(hashlib.sha256(data).hexdigest(), "png"))


def _images(store: LibraryStore) -> ImageLibrary:
    return ImageLibrary(store, H[DESKTOP], owner=OWNER)


def _cover(store: LibraryStore, owner: OwnerRef, data: bytes = PNG, image_id: str = "cover-1", slot: str = "cover",
           expected: str | None = None) -> ImageRef:
    batch = _images(store).change()
    ref = batch.add_image(owner.scope, image_id, data, {"origin": "test"})
    batch.place(owner, slot, Placement("set", "gallery", ref), expected)
    batch.commit()
    return ref


def _slot(store: LibraryStore, subject, slot: str = "cover"):  # type: ignore[no-untyped-def]
    return _images(store).effective_placements(subject).slot(slot)


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "library") as opened:
        seed(opened)
        yield opened


@pytest.fixture
def pair(tmp_path):
    """A seeded desktop synced once to an empty phone."""
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    yield desktop, phone
    desktop.close()
    phone.close()


# -- identity and files --------------------------------------------------------------------------------------

@pytest.mark.parametrize("ref, path", [
    (PlacementRef(WORLD, "cover"), "sources/world/harbor/_source/scope-art/cover.json"),
    (PlacementRef(CAMPAIGN, "backdrop.t-1"), "campaigns/c1/scope-art/backdrop.t-1.json"),
    (PlacementRef(OwnerRef("catalog"), "cover"), "sources/catalog/_source/scope-art/cover.json"),
])
def test_an_owners_placement_has_its_own_file_and_the_path_names_exactly_it(ref, path):
    assert _path(ref) == path and classify(tuple(path.split("/"))) == ("placement", ref)
    assert PlacementRef.parse(str(ref)) == ref


@pytest.mark.parametrize("path", [
    "campaigns/c1/scope-art/Cover.json", "campaigns/c1/scope-art/cover.txt", "campaigns/c1/scope-art/a/cover.json",
    "sources/world/harbor/_source/scope-art/character/wren/cover.json", "scope-art/cover.json",
])
def test_no_other_path_under_scope_art_is_a_placement(path):
    assert classify(tuple(path.split("/"))) is None


@pytest.mark.parametrize("scope", ["campaign:", "campaign:c1/x", "v1:a", "World", "world:harbor/character/wren", "", 3])
def test_an_owner_is_a_source_id_or_one_play_scope(scope):
    with pytest.raises(ValueError):
        OwnerRef(scope)


def test_an_owner_reads_as_its_scope():
    assert (str(WORLD), WORLD.source, WORLD.campaign_id) == (HARBOR, HARBOR, None)
    assert (str(CAMPAIGN), CAMPAIGN.source, CAMPAIGN.campaign_id) == ("campaign:c1", None, "c1")
    assert OwnerRef.parse("campaign:c1") == CAMPAIGN


# -- the 018 rules -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("owner", [WORLD, CAMPAIGN])
def test_an_owner_sets_changes_and_hides_its_own_picture_by_compare_and_swap(store, owner):
    images = _images(store)
    assert images.effective_placements(owner).slots == ()
    first = _cover(store, owner)
    shown = _slot(store, owner)
    assert isinstance(shown, Shown) and (shown.image, shown.role, shown.layer) == \
        (first, "gallery", "source" if owner is WORLD else "campaign")
    resolution = images.effective_placements(owner)
    assert resolution.lineage == (owner,) and resolution.origin("cover").origin == "local"
    _placement, tag = images.read_placement(PlacementRef(owner, "cover"))
    with pytest.raises(StaleWrite):  # a write that did not read the slot never lands on it
        images.place(owner, "cover", Placement("hide"), None).commit()
    second = _cover(store, owner, PNG_2, "cover-2", expected=tag)
    assert _slot(store, owner).image == second and images.read_image_bytes(first) == PNG  # never replaced in place
    _placement, tag = images.read_placement(PlacementRef(owner, "cover"))
    images.place(owner, "cover", Placement("hide"), tag).commit()
    assert isinstance(_slot(store, owner), Hidden)
    assert store.load().problems == ()


def test_an_owner_uses_only_its_own_pictures_and_inherits_nothing(store):
    images = _images(store)
    world = _cover(store, WORLD)
    campaign = _cover(store, CAMPAIGN, PNG_2)
    card = images.change()
    other = card.add_image(CARD, "mira", PNG, None)
    card.commit()
    for owner, image in ((WORLD, campaign), (WORLD, other), (CAMPAIGN, world), (CAMPAIGN, other)):
        with pytest.raises(InvalidPlacement) as caught:
            images.place(owner, "banner", Placement("set", "gallery", image), None)
        assert caught.value.reason == "wrong_scope"
    # an instance may use its derivation source's picture (W7); the campaign itself derives from nothing
    images.place(WREN_A, "gallery_1", Placement("set", "gallery", world), None).commit()
    for mode in (Placement("inherit"), Placement("adjust", image=world, crop=Crop(0, 0, 5000, 5000))):
        for owner in (WORLD, CAMPAIGN):
            with pytest.raises(InvalidPlacement) as caught:
                images.place(owner, "cover", mode, None)
            assert caught.value.reason == "mode_not_allowed"
    assert validate_placement(WORLD, "avatar", Placement("set", "gallery", world)) == \
        ("role_slot_mismatch", "slot 'avatar' holds a avatar, not a gallery")
    assert validate_placement(CAMPAIGN, "expr.a", Placement("hide"))[0] == "role_slot_mismatch"


def test_an_owner_must_exist_and_a_batch_may_make_its_campaign(store):
    images = _images(store)
    for owner in (OwnerRef("campaign:nowhere"), OwnerRef("world:nowhere")):
        with pytest.raises(InvalidPlacement) as caught:
            images.place(owner, "cover", Placement("hide"), None)
        assert caught.value.reason == "missing_subject"
        (problem,) = images.effective_placements(owner).problems
        assert problem.code in ("missing_subject", "missing_source") and images.effective_placements(owner).slots == ()
    batch = images.change()  # an importer's campaign and its cover in one transaction
    batch.put(ScopeRecord(ScopeRef("fresh"), "grimoire.play-scope/1", {"created_at": "2026-01-01T00:00:00Z"},
                          owner=OWNER), None)
    ref = batch.add_image("campaign:fresh", "cover", PNG, None)
    batch.place(OwnerRef("campaign:fresh"), "cover", Placement("set", "gallery", ref), None)
    batch.commit()
    assert _slot(store, OwnerRef("campaign:fresh")).image == ref


def test_a_damaged_or_stray_file_among_an_owners_pictures_shows_nothing_and_is_reported(store):
    _cover(store, CAMPAIGN)
    folder = store.root / "campaigns" / "c1" / "scope-art"
    (folder / "cover.json").write_text("{ not json")
    (folder / "backdrop.t-1.json").mkdir()
    (folder / "Cover copy.json").write_text("{}")
    resolution = _images(store).effective_placements(CAMPAIGN)
    assert {(slot.slot, type(slot), slot.reason) for slot in resolution.slots} == {
        ("cover", Unavailable, "invalid_placement"), ("backdrop.t-1", Unavailable, "invalid_placement")}
    assert {problem.path.rsplit("/", 1)[-1] for problem in resolution.problems} == {"backdrop.t-1.json", "Cover copy.json"}
    with pytest.raises(InvalidPlacement) as caught:  # and it can be replaced, as read
        _images(store).read_placement(PlacementRef(CAMPAIGN, "cover"))
    assert caught.value.reason == "invalid_placement" and caught.value.etag == etag((folder / "cover.json").read_bytes())
    # an actor's art is unaffected: the owner's folder is no part of any instance's layer
    assert _images(store).effective_placements(WREN_A).problems == ()


def test_a_standalone_definitions_hide_takes_its_picture_off_for_everything_that_inherits_it(store):
    images = _images(store)
    batch = images.change()
    ref = batch.add_image(HARBOR, "wren", PNG, None)
    batch.place(SAILOR, "cover", Placement("set", "gallery", ref), None)
    batch.commit()
    assert _slot(store, WREN_A).image == ref
    _placement, tag = images.read_placement(PlacementRef(SAILOR, "cover"))
    images.place(SAILOR, "cover", Placement("hide"), tag).commit()
    assert isinstance(_slot(store, SAILOR), Hidden) and isinstance(_slot(store, WREN_A), Hidden)
    assert images.effective_placements(WREN_A).origin("cover").placed_by == SAILOR
    assert images.read_image_bytes(ref) == PNG  # the picture itself is still in its gallery


# -- sync ----------------------------------------------------------------------------------------------------

def test_an_owners_cover_arrives_with_its_picture_and_its_removal_follows(pair):
    desktop, phone = pair
    world, campaign = _cover(desktop, WORLD), _cover(desktop, CAMPAIGN, PNG_2)
    report = sync_until_quiet(desktop, phone, H)
    assert report.conflicts == ()
    copied = {path for path, device in report.copied if device == PHONE}
    assert {_path(PlacementRef(WORLD, "cover")), _path(PlacementRef(CAMPAIGN, "cover")), _path(world), _path(campaign),
            _blob(PNG), _blob(PNG_2)} <= copied
    assert _slot(phone, WORLD).image == world and _slot(phone, CAMPAIGN).image == campaign
    assert (phone.root / _blob(PNG_2)).read_bytes() == PNG_2
    for owner in (WORLD, CAMPAIGN):  # taken off on the phone: the hide travels back like any record
        _placement, tag = _images(phone).read_placement(PlacementRef(owner, "cover"))
        _images(phone).place(owner, "cover", Placement("hide"), tag).commit()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    assert isinstance(_slot(desktop, WORLD), Hidden) and isinstance(_slot(desktop, CAMPAIGN), Hidden)
    assert desktop.load().problems == () and phone.load().problems == ()


@pytest.mark.parametrize("owner", [WORLD, CAMPAIGN])
def test_two_devices_covers_are_kept_both_and_resolved_by_choice(pair, owner):
    desktop, phone = pair
    mine = _cover(desktop, owner, PNG, "desk")
    theirs = _cover(phone, owner, PNG_2, "phone")
    path = _path(PlacementRef(owner, "cover"))
    report = sync_until_quiet(desktop, phone, H)
    assert [(conflict.path, conflict.code) for conflict in report.conflicts] == [(path, "missing_ancestor")], report.conflicts
    # each still shows its own cover, and each now holds the other's picture too: nothing was overwritten or lost
    assert _slot(desktop, owner).image == mine and _slot(phone, owner).image == theirs
    assert (desktop.root / _path(theirs)).is_file() and (phone.root / _path(mine)).is_file()
    assert sync_until_quiet(desktop, phone, H).conflicts != ()  # a second sync settles nothing by itself
    kept = (desktop.root / path).read_bytes()
    resolve(desktop, phone, path, etag(kept), {DESKTOP: etag(kept), PHONE: etag((phone.root / path).read_bytes())}, H)
    assert _slot(phone, owner).image == mine and sync_until_quiet(desktop, phone, H).conflicts == ()


def test_a_cover_naming_another_scopes_picture_by_hand_is_held_never_copied(pair):
    desktop, phone = pair
    _cover(desktop, CAMPAIGN)
    batch = _images(desktop).change()
    world = batch.add_image(HARBOR, "wren", PNG_2, None)
    batch.commit()
    target = desktop.root / _path(PlacementRef(CAMPAIGN, "cover"))
    edited = target.read_text().replace("campaign:c1#image/cover-1", str(world))
    assert edited != target.read_text()
    target.write_text(edited)
    assert _slot(desktop, CAMPAIGN).reason == "wrong_scope"  # the origin itself shows nothing there
    report = sync_until_quiet(desktop, phone, H)
    held = _path(PlacementRef(CAMPAIGN, "cover"))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {held: "head_dependency"}
    assert "not in scope" in report.conflict(held).detail and not (phone.root / held).exists()
    mine = target.read_bytes()
    with pytest.raises(ResolveRefused, match="not in scope"):
        resolve(desktop, phone, held, etag(mine), {DESKTOP: etag(mine), PHONE: None}, H)


def test_what_an_owners_placement_needs_on_the_other_device(pair):
    desktop, _phone = pair
    world, campaign = _cover(desktop, WORLD), _cover(desktop, CAMPAIGN, PNG_2)
    files = {}
    for ref in (PlacementRef(WORLD, "cover"), PlacementRef(CAMPAIGN, "cover"), world, campaign):
        files[_path(ref)] = (desktop.root / _path(ref)).read_bytes()
    manifest = "sources/world/harbor/_source/manifest.json"
    files[manifest] = (desktop.root / manifest).read_bytes()
    blobs = {_blob(PNG): len(PNG), _blob(PNG_2): len(PNG_2)}
    needs = art_needs(_path(PlacementRef(WORLD, "cover")), files[_path(PlacementRef(WORLD, "cover"))], files, blobs)
    assert (needs.identical, needs.present, needs.campaign, needs.hold, needs.admitted) == \
        (frozenset({_path(world), _blob(PNG)}), frozenset({manifest}), None, None, frozenset({HARBOR}))
    needs = art_needs(_path(PlacementRef(CAMPAIGN, "cover")), files[_path(PlacementRef(CAMPAIGN, "cover"))], files, blobs)
    assert (needs.identical, needs.present, needs.campaign, needs.hold, needs.admitted) == \
        (frozenset({_path(campaign), _blob(PNG_2)}), frozenset(), "c1", None, frozenset())
    # a source the origin's load() excludes: its cover is held on the origin's word
    held = art_needs(_path(PlacementRef(WORLD, "cover")), files[_path(PlacementRef(WORLD, "cover"))], files, blobs,
                     frozenset({HARBOR}))
    assert held.hold is not None and "excluded" in held.hold
