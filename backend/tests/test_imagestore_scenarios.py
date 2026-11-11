"""Sprint 018 acceptance and failure scenarios: one picture, many placements, nothing falls back.

A1-A4 are the proving scenarios; F1-F18 are the failures of the spec. Every picture is a tiny
synthetic byte string and every source is invented.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef
from grimoire.images import Crop, Expression, Focus, Hidden, Placement, Shown, Unavailable
from grimoire.imagestore import ImageLibrary, InvalidBatch, InvalidImage, InvalidPlacement
from grimoire.library import LibraryPathError
from grimoire.store import (
    BlobCorrupt, LibraryStore, LinkSpec, NameCollision, RecordEnvelope, SourceManifest, StaleWrite, StoreError,
    WriteDenied,
)
from grimoire.store.layout import address_parts, blob_parts
from grimoire.testing.fileops import snapshot

from image_fixtures import (
    CAPTAIN, CARD, GIF, HARBOR, JPEG, MIRA, OTHER, PNG, PNG_2, SAILOR, STRANGER, WEBP, WREN_A, WREN_B, WREN_C,
    WREN_D, WREN_E, handle, instance_puts, seed, source_puts,
)
from store_helpers import OWNER, make_dir_link

SRC = Path(__file__).resolve().parents[1] / "src"
WREN_IMG = ImageRef(HARBOR, "wren")
C1, C2 = Crop(0, 500, 10000, 6000), Crop(2000, 0, 6000, 6000)
P1, P2 = {"note": "sailor avatar", "legacy_id": "v1:arch:a/avatar.json"}, {"note": "captain portrait"}


def setp(role, image=WREN_IMG, **kwargs) -> Placement:
    return Placement("set", role, image, **kwargs)


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


@pytest.fixture
def images(store):
    return ImageLibrary(store, handle(), owner=OWNER)


@pytest.fixture
def pictured(images):
    """The proving picture: one image, sailor avatar (C1), captain portrait (C2) and smile."""
    batch = images.change()
    batch.add_image(HARBOR, "wren", PNG, {"origin": "test"})
    batch.place(SAILOR, "avatar", setp("avatar", crop=C1, provenance=P1), None)
    batch.place(CAPTAIN, "portrait", setp("portrait", crop=C2, provenance=P2), None)
    batch.place(CAPTAIN, "expr.default.smile", setp("expression", expression=Expression("default", "smile")), None)
    batch.commit()
    return images


def _blob_files(root: Path) -> list[Path]:
    return [path for path in (root / "assets").rglob("*") if path.is_file()] if (root / "assets").exists() else []


# -- acceptance ----------------------------------------------------------------------------------

def test_a1_one_picture_two_portrayals_distinct_roles_crops_and_provenance(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        batch = ImageLibrary(store, handle(), owner=OWNER).change()
        for envelope, expected in source_puts():  # definitions, manifest and art in ONE transaction
            batch.put(envelope, expected)
        batch.add_image(HARBOR, "wren", PNG)
        batch.place(SAILOR, "avatar", setp("avatar", crop=C1, provenance=P1), None)
        batch.place(CAPTAIN, "portrait", setp("portrait", crop=C2, provenance=P2), None)
        batch.place(CAPTAIN, "expr.default.smile", setp("expression", expression=Expression("default", "smile")),
                    None)
        batch.place(SAILOR, "expr.default.smile", setp("expression", crop=C2,
                                                       expression=Expression("default", "smile")), None)
        assert len(batch.commit().etags) == len(source_puts()) + 6  # blob, image, four placements
    with LibraryStore.open(tmp_path / "lib") as store:
        images = ImageLibrary(store, handle())
        sailor = images.effective_placements(SAILOR).slot("avatar")
        captain = images.effective_placements(CAPTAIN)
        portrait, smile = captain.slot("portrait"), captain.slot("expr.default.smile")
        assert sailor.image == portrait.image == smile.image == WREN_IMG
        assert sailor.blob == portrait.blob and sailor.blob.sha256 == images.read_image(WREN_IMG)[0].blob.sha256
        assert (sailor.role, sailor.crop, sailor.provenance) == ("avatar", C1, P1)
        assert (portrait.role, portrait.crop, portrait.provenance) == ("portrait", C2, P2)
        assert smile.role == "expression" and smile.crop is None
        sailor_smile = images.effective_placements(SAILOR).slot("expr.default.smile")
        assert sailor_smile.image == WREN_IMG and sailor_smile.crop == C2  # same picture, its own crop
        assert len(_blob_files(tmp_path / "lib")) == 1
        assert images.read_image_bytes(WREN_IMG) == PNG
        assert store.load().problems == ()


def test_a2_a_standalone_card_has_an_avatar_with_no_world(images, store):
    batch = images.change()
    batch.add_image(CARD, "mira", GIF)
    batch.place(MIRA, "avatar", setp("avatar", ImageRef(CARD, "mira")), None)
    batch.commit()
    avatar = images.effective_placements(MIRA).slot("avatar")
    assert isinstance(avatar, Shown) and avatar.blob.media_type == "image/gif"
    assert (store.root / "sources/card/mira/_source/images/mira.json").is_file()  # owned by the card
    assert (store.root.joinpath(*blob_parts(avatar.blob.sha256, "gif"))).is_file()  # bytes in assets/


def test_a3_campaign_layering_inherit_hide_adjust_set(pictured, store):
    images = pictured
    scarred = ImageRef("campaign:c1", "avatar")  # named like the slot: must never be a fallback
    batch = images.change()
    batch.add_image("campaign:c1", "avatar", JPEG)
    batch.add_image("campaign:c1", "wren-scarred", WEBP)
    batch.place(WREN_B, "avatar", Placement("hide"), None)
    batch.place(WREN_C, "avatar", Placement("adjust", image=WREN_IMG, crop=Crop(100, 100, 5000, 5000)), None)
    batch.place(WREN_D, "avatar", setp("avatar", ImageRef("campaign:c1", "wren-scarred")), None)
    batch.place(WREN_E, "portrait", setp("portrait", WREN_IMG), None)  # the derivation source's image
    batch.commit()
    a = images.effective_placements(WREN_A).slot("avatar")
    assert isinstance(a, Shown) and a.layer == "source" and a.crop == C1
    assert images.effective_placements(WREN_B).slot("avatar") == Hidden("avatar")
    assert all(not isinstance(s, Shown) or s.image != scarred for s in images.effective_placements(WREN_B).slots)
    c = images.effective_placements(WREN_C).slot("avatar")
    assert (c.layer, c.image, c.crop) == ("campaign_adjusted", WREN_IMG, Crop(100, 100, 5000, 5000))
    d = images.effective_placements(WREN_D).slot("avatar")
    assert (d.layer, d.image) == ("campaign", ImageRef("campaign:c1", "wren-scarred"))
    e = images.effective_placements(WREN_E).slot("portrait")
    assert (e.layer, e.image) == ("campaign", WREN_IMG)


def test_a4_identical_bytes_in_two_sources_share_one_blob(pictured, store):
    images = pictured
    batch = images.change()
    batch.add_image(CARD, "copy", PNG, {"note": "card copy"})
    batch.add_image(CARD, "copy-2", PNG)  # twice in one batch: still one blob put
    preview = batch.preview()
    assert not any(path.startswith("assets/") for path in preview)  # already stored: deduplicated
    batch.commit()
    assert images.read_image(ImageRef(CARD, "copy"))[0].provenance == {"note": "card copy"}
    assert images.read_image(WREN_IMG)[0].provenance == {"origin": "test"}
    assert len(_blob_files(store.root)) == 1
    fresh = images.change()
    fresh.add_image(CARD, "new-1", PNG_2)
    fresh.add_image(CARD, "new-2", PNG_2)
    assert sum(path.startswith("assets/") for path in fresh.preview()) == 1


# -- failures ------------------------------------------------------------------------------------

def _refused(error, reason, call, *args):
    with pytest.raises(error) as caught:
        call(*args)
    assert caught.value.reason == reason


def test_f1_placing_a_nonexistent_image_writes_nothing(images, store):
    before = snapshot(store.root)
    _refused(InvalidPlacement, "missing_image", images.place, SAILOR, "avatar", setp("avatar"))
    assert snapshot(store.root) == before


def test_f2_wrong_scope_images_are_refused(pictured, store):
    images = pictured
    batch = images.change()
    batch.add_image(OTHER, "x", GIF)
    batch.add_image("campaign:c1", "local", GIF)
    batch.put(SourceManifest("world:harbor:dlc", OWNER), None)
    batch.put(RecordEnvelope(SourceRef("world:harbor:dlc", "item", "oar", "r1"), {}), None)
    batch.commit()
    oar = SourceRef("world:harbor:dlc", "item", "oar")
    for subject, slot, image in (
            (SAILOR, "gallery_1", ImageRef(OTHER, "x")),            # another source
            (SAILOR, "gallery_1", ImageRef("campaign:c1", "local")),  # a campaign
            (oar, "gallery_1", WREN_IMG),                             # its parent source
            (WREN_A, "gallery_1", ImageRef(OTHER, "x")),            # unrelated to the derivation
            (WREN_A, "gallery_1", ImageRef("campaign:c2", "x"))):   # another campaign
        _refused(InvalidPlacement, "wrong_scope", images.place, subject, slot, setp("gallery", image))


@pytest.mark.parametrize("data, reason", [(b"", "empty"), (b"<svg/>", "unsupported_media"),
                                          (b"\x89PNG\r\n\x1a\n" + b"\x00" * (32 * 1024 * 1024), "too_large")])
def test_f3_unusable_bytes_are_refused(images, store, data, reason):
    before = snapshot(store.root)
    _refused(InvalidImage, reason, images.add_image, HARBOR, "x", data)
    assert snapshot(store.root) == before


def test_f4_corrupt_blobs_are_reported_refused_and_repaired_explicitly(pictured, store):
    images = pictured
    path = store.root.joinpath(*blob_parts(images.read_image(WREN_IMG)[0].blob.sha256, "png"))
    path.write_bytes(PNG[:-1] + b"X")  # same size, different bytes
    with pytest.raises(BlobCorrupt):
        images.read_image_bytes(WREN_IMG)
    assert isinstance(images.effective_placements(SAILOR).slot("avatar"), Shown)  # digest not checked here
    _refused(InvalidImage, "blob_corrupt", images.add_image, CARD, "copy", PNG)
    _refused(InvalidImage, "nothing_to_repair", images.repair_blob, PNG_2)  # absent
    images.repair_blob(PNG).commit()
    assert images.read_image_bytes(WREN_IMG) == PNG
    _refused(InvalidImage, "nothing_to_repair", images.repair_blob, PNG)  # intact now
    path.write_bytes(PNG + b"tail")
    assert images.effective_placements(SAILOR).slot("avatar").reason == "blob_size_mismatch"
    batch = images.change()  # repair then add the same bytes in one batch: the repair survives
    batch.repair_blob(PNG)
    batch.add_image(CARD, "copy", PNG)
    batch.commit()
    assert images.read_image_bytes(ImageRef(CARD, "copy")) == PNG


def test_f4_an_oversize_blob_cannot_be_repaired(pictured, store):
    images = pictured
    path = store.root.joinpath(*blob_parts(images.read_image(WREN_IMG)[0].blob.sha256, "png"))
    with path.open("r+b") as handle_:
        handle_.truncate(32 * 1024 * 1024 + 1)
    _refused(InvalidImage, "oversize_blob", images.repair_blob, PNG)
    _refused(InvalidImage, "blob_corrupt", images.add_image, CARD, "copy", PNG)


def test_f4_a_look_alike_blob_name_is_a_collision(images, store):
    from grimoire.store import ImageBlob
    blob = ImageBlob(PNG)
    shard = store.root / "assets" / "blobs" / blob.sha256[:2]
    shard.mkdir(parents=True)
    (shard / f"{blob.sha256.upper()}.png").write_bytes(PNG)
    if (shard / f"{blob.sha256}.png").exists():
        pytest.skip("case-insensitive filesystem: the look-alike is the same file")
    with pytest.raises(NameCollision):
        images.add_image(HARBOR, "x", PNG)
    with pytest.raises(NameCollision):
        images.repair_blob(PNG)


def test_f5_hand_edited_placements_show_nothing_else(pictured, store):
    images = pictured
    path = store.root.joinpath(*address_parts(PlacementRef(SAILOR, "avatar")))
    document = json.loads(path.read_bytes())
    for payload, reason in (({"mode": "set", "role": "avatar", "image": f"{OTHER}#image/x"}, "wrong_scope"),
                            ({"mode": "set", "role": "avatar", "image": f"{HARBOR}#image/gone"}, "missing_image"),
                            ({"mode": "teleport"}, "invalid_placement"),
                            ({"mode": "set", "role": "avatar", "image": 7}, "invalid_placement")):
        path.write_text(json.dumps({**document, "payload": payload}))
        found = images.effective_placements(SAILOR).slot("avatar")
        assert isinstance(found, Unavailable) and found.reason == reason
    image_path = store.root.joinpath(*address_parts(WREN_IMG))
    image_path.write_text("{broken")
    path.write_text(json.dumps(document))
    assert images.effective_placements(SAILOR).slot("avatar").reason == "invalid_image"


def test_f7_unknown_subjects_and_campaigns_are_refused(pictured):
    images = pictured
    _refused(InvalidPlacement, "missing_subject", images.place, SourceRef(HARBOR, "character", "ghost"), "avatar",
             setp("avatar"))
    _refused(InvalidPlacement, "missing_subject", images.place, InstanceRef("c1", "character", "ghost"), "avatar",
             Placement("hide"))
    _refused(InvalidImage, "missing_scope", images.add_image, "campaign:c9", "x", PNG)
    _refused(InvalidImage, "missing_scope", images.add_image, "catalog:absent", "x", PNG)


@pytest.mark.parametrize("subject, slot, placement, reason", [
    (SAILOR, "avatar", Placement("inherit"), "mode_not_allowed"),
    (SAILOR, "avatar", Placement("adjust", image=WREN_IMG), "mode_not_allowed"),
    (WREN_A, "gallery_7", Placement("adjust", image=WREN_IMG), "nothing_to_adjust"),
    (WREN_A, "avatar", Placement("adjust", image=ImageRef("campaign:c1", "nope")), "missing_image"),
    (SAILOR, "gallery_1", setp("avatar"), "role_slot_mismatch"),
    (CAPTAIN, "expr.default.frown", setp("expression", expression=Expression("default", "smile")),
     "role_slot_mismatch"),
])
def test_f8_mode_role_and_slot_rules(pictured, subject, slot, placement, reason):
    _refused(InvalidPlacement, reason, pictured.place, subject, slot, placement)


def test_f8_adjust_must_name_the_inherited_picture(pictured):
    images = pictured
    batch = images.change()
    batch.add_image("campaign:c1", "other", GIF)
    batch.commit()
    _refused(InvalidPlacement, "wrong_scope", images.place, WREN_A, "avatar",
             Placement("adjust", image=ImageRef(OTHER, "x")))
    _refused(InvalidPlacement, "stale_adjust", images.place, WREN_A, "avatar",
             Placement("adjust", image=ImageRef("campaign:c1", "other")))


def test_f9_stale_expectations_and_reused_ids_write_nothing(pictured, store):
    images = pictured
    before = snapshot(store.root)
    with pytest.raises(StaleWrite):
        images.place(SAILOR, "avatar", setp("avatar"), None).commit()  # exists: needs its etag
    with pytest.raises(StaleWrite):
        images.place(SAILOR, "avatar", setp("avatar"), "sha256:" + "0" * 64).commit()
    _refused(InvalidImage, "image_exists", images.add_image, HARBOR, "wren", PNG_2)
    assert snapshot(store.root) == before
    _, etag = images.read_placement(PlacementRef(SAILOR, "avatar"))
    images.place(SAILOR, "avatar", setp("avatar", crop=C2), etag).commit()
    assert images.effective_placements(SAILOR).slot("avatar").crop == C2


def test_f10_denied_blob_authorization_writes_nothing(store):
    images = ImageLibrary(store, handle("sources", "campaigns"))
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        images.add_image(HARBOR, "x", PNG).commit()
    assert snapshot(store.root) == before


def test_f11_a_linked_assets_folder_is_never_written_or_read(pictured, store, tmp_path):
    images = pictured
    target = tmp_path / "elsewhere"
    (store.root / "assets").rename(target)
    make_dir_link(store.root / "assets", target)
    before = snapshot(target)
    with pytest.raises(LibraryPathError):
        images.add_image(HARBOR, "x", PNG_2).commit()
    _refused(InvalidImage, "missing_blob", images.read_image_bytes, WREN_IMG)  # never read through the link
    found = images.effective_placements(SAILOR).slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "missing_blob"
    assert snapshot(target) == before


def test_f13_unknown_fields_survive_a_rewrite_and_newer_formats_are_kept(pictured, store):
    images = pictured
    ref = PlacementRef(SAILOR, "avatar")
    path = store.root.joinpath(*address_parts(ref))
    document = json.loads(path.read_bytes())
    document["x-envelope"] = {"kept": True}
    document["payload"]["x-payload"] = [1]
    path.write_text(json.dumps(document))
    placement, etag = images.read_placement(ref)
    from dataclasses import replace
    images.place(SAILOR, "avatar", replace(placement, crop=C2), etag).commit()
    rewritten = json.loads(path.read_bytes())
    assert rewritten["x-envelope"] == {"kept": True} and rewritten["payload"]["x-payload"] == [1]
    assert rewritten["payload"]["crop"] == {"x": 2000, "y": 0, "w": 6000, "h": 6000}
    rewritten["format"] = "grimoire.asset-record/9"
    path.write_text(json.dumps(rewritten))
    from grimoire.store import etag as etag_of
    with pytest.raises(StaleWrite, match="unsupported_format"):
        images.place(SAILOR, "avatar", setp("avatar"), etag_of(path.read_bytes())).commit()
    assert images.effective_placements(SAILOR).slot("avatar").reason == "unsupported_format"


def test_f13_a_malformed_placement_can_be_replaced_fresh(pictured, store):
    images = pictured
    path = store.root.joinpath(*address_parts(PlacementRef(SAILOR, "avatar")))
    path.write_text("{not json")
    from grimoire.store import etag as etag_of
    images.place(SAILOR, "avatar", setp("avatar"), etag_of(path.read_bytes())).commit()
    assert isinstance(images.effective_placements(SAILOR).slot("avatar"), Shown)


def test_f16_stray_names_are_reported_as_problems(pictured, store):
    images = pictured
    folder = store.root / "sources/world/harbor/_source/placements/character/wren-sailor"
    (folder / "Portrait.json").write_bytes(b"{}")  # no portrait.json exists: a look-alike everywhere
    (folder / "notes.txt").write_bytes(b"x")
    result = images.effective_placements(SAILOR)
    assert {(p.code) for p in result.problems} >= {"name_mismatch", "unrecognized"}
    assert [s.slot for s in result.slots] == ["avatar"]


def test_f17_excluded_sources_and_dropped_entities_show_nothing(pictured, store):
    images = pictured
    manifest = store.root / "sources/world/harbor/_source/manifest.json"
    manifest.write_text("{broken")
    result = images.effective_placements(SAILOR)
    assert result.slots == () and [p.code for p in result.problems] == ["excluded_source"]
    inherited = images.effective_placements(WREN_A)
    assert inherited.slot("avatar") is None and "excluded_source" in {p.code for p in inherited.problems}


def test_f17_orphan_placements_of_a_missing_entity_are_never_shown(pictured, store):
    images = pictured
    folder = store.root / "sources/world/harbor/_source/placements/character/ghost"
    folder.mkdir(parents=True)
    source = store.root / "sources/world/harbor/_source/placements/character/wren-sailor/avatar.json"
    document = json.loads(source.read_bytes())
    document["address"] = f"{HARBOR}/character/ghost#placement/avatar"
    (folder / "avatar.json").write_text(json.dumps(document))
    result = images.effective_placements(SourceRef(HARBOR, "character", "ghost"))
    assert result.slots == () and [p.code for p in result.problems] == ["missing_entity"]


def test_f17_a_dropped_entity_shows_nothing(pictured, store):
    images = pictured
    records = store.root / "sources/world/harbor/_source/records/character/wren-sailor"
    (records / "r2.json").write_bytes((records / "r1.json").read_bytes().replace(b"@r1", b"@r2"))
    result = images.effective_placements(SAILOR)  # two revisions, no head: dropped
    assert result.slots == () and [p.code for p in result.problems] == ["entity_dropped"]


def test_f17_an_entity_in_a_batch_of_an_excluded_source_is_refused(pictured, store):
    images = pictured
    (store.root / "sources/catalog/other/_source/manifest.json").write_text("{broken")
    batch = images.change()
    batch.put(RecordEnvelope(SourceRef(OTHER, "character", "new", "r1"), {}), None)
    with pytest.raises(InvalidImage) as caught:
        batch.add_image(OTHER, "x", PNG)
    assert caught.value.reason == "missing_scope"
    with pytest.raises(InvalidPlacement) as refused:
        batch.place(SourceRef(OTHER, "character", "new"), "avatar", setp("avatar"), None)
    assert refused.value.reason == "missing_subject"


def test_f18_an_adjust_whose_picture_changed_is_stale(pictured, store):
    images = pictured
    batch = images.change()
    batch.place(WREN_C, "avatar", Placement("adjust", image=WREN_IMG, focus=Focus(5000, 7000)), None)
    batch.add_image(HARBOR, "wren-older", PNG_2)
    batch.commit()
    _, etag = images.read_placement(PlacementRef(SAILOR, "avatar"))
    images.place(SAILOR, "avatar", setp("avatar", ImageRef(HARBOR, "wren-older")), etag).commit()
    found = images.effective_placements(WREN_C).slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "stale_adjust"


@pytest.mark.parametrize("derived_from, base, code", [
    (str(SAILOR), str(CAPTAIN.at("r1")), "ambiguous_derivation"),
    ("not a reference", None, "invalid_derivation"),
])
def test_unknown_derivations_inherit_nothing_and_allow_only_campaign_images(pictured, store, derived_from, base,
                                                                             code):
    images = pictured
    odd = InstanceRef("c1", "character", "odd")
    txn = store.transaction()
    if code == "invalid_derivation":  # the store refuses to write an unparsable reference; hand-edit it
        txn.put(RecordEnvelope(odd, {}), None)
        txn.commit(handle())
        path = store.root.joinpath(*address_parts(odd))
        document = json.loads(path.read_bytes())
        document["derived_from"] = derived_from
        path.write_text(json.dumps(document))
    else:
        txn.put(RecordEnvelope(odd, {}, derived_from=derived_from, base=base), None)
        txn.commit(handle())
    result = images.effective_placements(odd)
    assert result.slots == () and code in {p.code for p in result.problems}
    _refused(InvalidPlacement, "wrong_scope", images.place, odd, "gallery_1", setp("gallery"))


def test_preview_binds_the_commit(pictured, store):
    images = pictured
    batch = images.change()
    batch.add_image(CARD, "x", GIF)
    batch.preview()
    other = images.change()  # someone else creates the same image id meanwhile
    other.add_image(CARD, "x", JPEG)
    other.commit()
    with pytest.raises(InvalidImage):
        batch.commit()
    batch = images.change()
    batch.add_image(CARD, "y", WEBP)
    seen = batch.preview()
    images.add_image(CARD, "z", WEBP).commit()  # the same bytes arrive first: only the blob drops
    batch.commit()
    assert any(path.startswith("assets/") for path in seen)


def test_preview_binding_refuses_other_changes(pictured, store):
    images = pictured
    batch = images.change()
    batch.place(WREN_A, "avatar", Placement("hide"), None)
    batch.preview()
    images.owner = "human:someone-else"  # the bytes to write change after the human saw them
    with pytest.raises(StaleWrite):
        batch.commit()


def test_derived_variants_in_another_source_do_not_inherit_art(pictured, store):
    images = pictured
    variant = SourceRef("world:elderfen", "character", "wren")
    txn = store.transaction()
    txn.put(SourceManifest("world:elderfen", OWNER, links=(LinkSpec(str(variant.at("r1")), "derived_variant",
                                                                    str(SAILOR.at("r1"))),)), None)
    txn.put(RecordEnvelope(variant.at("r1"), {}), None)
    txn.commit(handle())
    assert images.effective_placements(variant).slots == ()


def test_reads_never_write_and_pinned_subjects_are_refused(pictured, store):
    before = snapshot(store.root)
    pictured.effective_placements(SAILOR)
    pictured.effective_placements(WREN_C)
    assert snapshot(store.root) == before
    with pytest.raises(ValueError):
        pictured.effective_placements(SAILOR.at("r1"))


def test_the_image_service_loads_no_provider():
    code = ("import json, sys; import grimoire.imagestore; "
            "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'grimoire')))")
    loaded = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                                       env={**os.environ, "PYTHONPATH": str(SRC)}).stdout)
    assert "grimoire.store" in loaded and "grimoire.images" in loaded  # the check is not vacuous
    assert not [m for m in loaded if m.startswith(("grimoire.providers", "grimoire.capture", "grimoire.evals"))]
    network = ("socket", "ssl", "select", "selectors", "http.client", "urllib.request", "subprocess", "asyncio")
    code = "import sys, grimoire.imagestore; print('\\n'.join(sorted(sys.modules)))"
    every = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONPATH": str(SRC)}).stdout.split()
    assert not [m for m in every if m in network or m.split(".")[0] in network]


def test_instance_fixture_is_derived_from_the_sailor():
    assert all(envelope.derived_from == str(SAILOR) for envelope, _ in instance_puts())
    assert STRANGER.source == OTHER


# -- implementation-gate regressions: fail closed on unreadable campaign layers and blobs ---------

@pytest.fixture
def hidden(pictured):
    pictured.place(WREN_B, "avatar", Placement("hide"), None).commit()
    pictured.place(WREN_D, "avatar", setp("avatar", WREN_IMG, crop=C2), None).commit()
    assert pictured.effective_placements(WREN_B).slot("avatar") == Hidden("avatar")
    return pictured


def _link_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("links are not available here")


@pytest.mark.parametrize("damage", ["file-link", "folder-link", "lookalike-folder", "file-is-folder"])
def test_a_damaged_hide_never_falls_back_to_the_source_picture(hidden, store, tmp_path, damage):
    images = hidden
    target = store.root.joinpath(*address_parts(PlacementRef(WREN_B, "avatar")))
    folder = target.parent
    if damage == "file-link":
        outside = tmp_path / "outside.json"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        _link_or_skip(target, outside)
    elif damage == "folder-link":
        moved = tmp_path / "moved"
        folder.rename(moved)
        _link_or_skip(folder, moved)
    elif damage == "lookalike-folder":
        folder.rename(folder.with_name("Wren-b"))
        if folder.exists():
            pytest.skip("case-insensitive filesystem: the look-alike is the same folder")
    else:
        target.unlink()
        (target / "inside.json").parent.mkdir()
        (target / "inside.json").write_bytes(b"{}")
    result = images.effective_placements(WREN_B)
    found = result.slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "invalid_placement"
    assert result.problems  # never silent
    assert not any(isinstance(slot, Shown) for slot in result.slots)


def test_a_damaged_campaign_set_never_shows_the_source_picture(hidden, store, tmp_path):
    target = store.root.joinpath(*address_parts(PlacementRef(WREN_D, "avatar")))
    outside = tmp_path / "outside.json"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    _link_or_skip(target, outside)
    found = hidden.effective_placements(WREN_D).slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "invalid_placement"


def test_a_stray_unrelated_file_does_not_hide_inherited_art(hidden, store):
    folder = store.root.joinpath(*address_parts(PlacementRef(WREN_A, "avatar"))).parent
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_bytes(b"x")
    result = hidden.effective_placements(WREN_A)
    assert isinstance(result.slot("avatar"), Shown) and [p.code for p in result.problems] == ["unrecognized"]


@pytest.mark.parametrize("damage", ["link", "directory"])
def test_a_bad_blob_entry_is_an_unavailable_slot_not_a_crash(pictured, store, tmp_path, damage):
    path = store.root.joinpath(*blob_parts(pictured.read_image(WREN_IMG)[0].blob.sha256, "png"))
    path.unlink()
    if damage == "link":
        outside = tmp_path / "blob.png"
        outside.write_bytes(PNG)
        _link_or_skip(path, outside)
    else:
        path.mkdir()
    found = pictured.effective_placements(SAILOR).slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "missing_blob"


def test_resolution_never_reads_blob_bytes(pictured, store, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("resolution must not read or hash blob bytes")
    monkeypatch.setattr(type(store), "blob_etag", refuse)
    monkeypatch.setattr(type(store), "read_blob", refuse)
    assert isinstance(pictured.effective_placements(SAILOR).slot("avatar"), Shown)
    assert isinstance(pictured.effective_placements(WREN_A).slot("avatar"), Shown)


def test_a_missing_derivation_entity_allows_only_campaign_images_on_write(pictured, store):
    ghostly = InstanceRef("c1", "character", "ghostly")
    txn = store.transaction()
    txn.put(RecordEnvelope(ghostly, {}, derived_from=f"{HARBOR}/character/ghost"), None)
    txn.commit(handle())
    _refused(InvalidPlacement, "wrong_scope", pictured.place, ghostly, "avatar", setp("avatar"))
    result = pictured.effective_placements(ghostly)
    assert result.slots == () and "missing_entity" in {p.code for p in result.problems}


def test_an_unstorable_instance_id_is_a_problem_not_a_crash(pictured):
    result = pictured.effective_placements(InstanceRef("c1", "character", "con"))
    assert result.slots == ()


def test_reading_an_image_of_an_excluded_source_is_refused(pictured, store):
    (store.root / "sources/world/harbor/_source/manifest.json").write_text("{broken")
    _refused(InvalidImage, "excluded_source", pictured.read_image, WREN_IMG)
    _refused(InvalidImage, "excluded_source", pictured.read_image_bytes, WREN_IMG)


def test_f17_ownership_excluded_and_manifest_less_subjects_show_nothing(pictured, store):
    images = pictured
    # a record smuggled into harbor that claims another source excludes harbor (ownership)
    stray = store.root / "sources/world/harbor/_source/records/character/wren-sailor/r9.json"
    document = json.loads((stray.parent / "r1.json").read_bytes())
    document["address"] = f"{OTHER}/character/wren-sailor@r9"
    stray.write_text(json.dumps(document))
    result = images.effective_placements(SAILOR)
    assert result.slots == () and [p.code for p in result.problems] == ["excluded_source"]
    stray.unlink()
    (store.root / "sources/card/mira/_source/manifest.json").unlink()  # revisions without a manifest
    result = images.effective_placements(MIRA)
    assert result.slots == () and [p.code for p in result.problems] == ["excluded_source"]


@pytest.mark.parametrize("which", ["subject-folder", "kind-folder", "placements"])
def test_a_file_where_a_campaign_folder_belongs_never_reveals_the_source_picture(hidden, store, which):
    folder = store.root.joinpath(*address_parts(PlacementRef(WREN_B, "avatar"))).parent
    victim = {"subject-folder": folder, "kind-folder": folder.parent, "placements": folder.parent.parent}[which]
    import shutil
    shutil.rmtree(victim)
    victim.write_bytes(b"a sync conflict left a file here")
    result = hidden.effective_placements(WREN_B)
    found = result.slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "invalid_placement" and result.problems


def test_a_device_name_image_id_is_unstorable_not_a_duplicate(images, store):
    from grimoire.store import UnstorableIdentifier
    before = snapshot(store.root)
    with pytest.raises(UnstorableIdentifier):
        images.add_image(HARBOR, "con", PNG)
    assert snapshot(store.root) == before


def test_w6_write_time_image_and_blob_refusals(pictured, store):
    images = pictured
    batch = images.change()
    batch.add_image(HARBOR, "broken", GIF)
    batch.add_image(HARBOR, "gone", JPEG)
    batch.add_image(HARBOR, "short", WEBP)
    batch.commit()
    store.root.joinpath(*address_parts(ImageRef(HARBOR, "broken"))).write_text("{not json")
    gone = images.read_image(ImageRef(HARBOR, "gone"))[0].blob
    store.root.joinpath(*blob_parts(gone.sha256, gone.ext)).unlink()
    short = images.read_image(ImageRef(HARBOR, "short"))[0].blob
    store.root.joinpath(*blob_parts(short.sha256, short.ext)).write_bytes(WEBP + b"longer")
    for image, reason in (("broken", "invalid_image"), ("gone", "missing_blob"), ("short", "missing_blob")):
        _refused(InvalidPlacement, reason, images.place, SAILOR, "gallery_1", setp("gallery", ImageRef(HARBOR, image)))


def test_two_campaigns_share_bytes_but_not_state(pictured, store):
    images = pictured
    other = InstanceRef("c2", "character", "wren")
    txn = store.transaction()
    txn.put(RecordEnvelope(other, {}, derived_from=str(SAILOR)), None)
    txn.commit(handle())
    images.place(other, "avatar", Placement("hide"), None).commit()
    assert images.effective_placements(other).slot("avatar") == Hidden("avatar")
    shown = images.effective_placements(WREN_A).slot("avatar")
    assert isinstance(shown, Shown) and shown.image == WREN_IMG
    assert len(_blob_files(store.root)) == 1


# -- round-1 brutal-review regressions -----------------------------------------------------------

def test_a_device_name_slot_is_refused_and_leaves_the_batch_usable(images, store):
    from grimoire.store import UnstorableIdentifier
    batch = images.change()
    image = batch.add_image(HARBOR, "wren", PNG)
    with pytest.raises(UnstorableIdentifier):
        batch.place(SAILOR, "con", setp("gallery", image), None)
    with pytest.raises(UnstorableIdentifier):
        batch.put(RecordEnvelope(SourceRef(HARBOR, "character", "aux", "r1"), {}), None)
    batch.place(SAILOR, "avatar", setp("avatar", image), None)
    batch.commit()  # the refused ops left nothing behind
    assert isinstance(images.effective_placements(SAILOR).slot("avatar"), Shown)


def test_a_damaged_campaign_layer_blocks_an_adjust_too(pictured, store):
    images = pictured
    images.place(WREN_C, "avatar", Placement("adjust", image=WREN_IMG, crop=Crop(1, 1, 10, 10)), None).commit()
    folder = store.root.joinpath(*address_parts(PlacementRef(WREN_C, "avatar"))).parent
    (folder / "Portrait.json").write_bytes(b"{}")  # a look-alike: the layer might hide something
    found = images.effective_placements(WREN_C).slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "invalid_placement"


def test_a_batch_sees_a_commit_made_after_it_refused_an_op(pictured, store):
    images = pictured
    batch = images.change()
    with pytest.raises(InvalidPlacement) as refused:
        batch.place(MIRA, "avatar", setp("avatar", ImageRef(CARD, "mira")), None)
    assert refused.value.reason == "missing_image"
    images.add_image(CARD, "mira", GIF).commit()  # another batch commits the picture
    batch.place(MIRA, "avatar", setp("avatar", ImageRef(CARD, "mira")), None)  # now accepted
    batch.commit()


def test_validation_cost_does_not_grow_with_subjects_times_source_size(images, store, monkeypatch):
    from grimoire.store import loader
    txn = store.transaction()
    txn.put(SourceManifest("catalog:big", OWNER), None)
    for n in range(40):
        txn.put(RecordEnvelope(SourceRef("catalog:big", "character", f"c{n}", "r1"), {}, name=f"C{n}"), None)
    txn.commit(handle())
    scans = []
    real = loader.contained_scan
    monkeypatch.setattr(loader, "contained_scan",
                        lambda access, source_id, **kwargs: scans.append(source_id) or real(access, source_id, **kwargs))
    batch = images.change()
    image = batch.add_image("catalog:big", "shared", PNG)
    for n in range(40):
        batch.place(SourceRef("catalog:big", "character", f"c{n}"), "avatar", setp("avatar", image), None)
    batch.preview()
    batch.commit()
    # one scan for the new source, a second after the commit changes nothing under records/: not 40 x 3
    assert scans.count("catalog:big") <= 2
    scans.clear()
    for n in range(40):
        assert isinstance(images.effective_placements(SourceRef("catalog:big", "character", f"c{n}")).slot("avatar"),
                          Shown)
    assert scans.count("catalog:big") == 0  # warm reads re-parse nothing


# -- round-2 brutal-review regressions -----------------------------------------------------------

def test_a_later_op_cannot_leave_an_earlier_placement_invalid(pictured, store):
    images = pictured
    batch = images.change()  # the adjust names today's picture; a later op changes the inherited one
    batch.place(WREN_A, "avatar", Placement("adjust", image=WREN_IMG, crop=Crop(1, 1, 10, 10)), None)
    image = batch.add_image(HARBOR, "wren2", PNG_2)
    _, etag = images.read_placement(PlacementRef(SAILOR, "avatar"))
    batch.place(SAILOR, "avatar", setp("avatar", image), etag)
    with pytest.raises(InvalidPlacement) as refused:
        batch.commit()
    assert refused.value.reason == "stale_adjust"
    other = images.change()  # a later put moves the instance's derivation away from the image's source
    other.place(WREN_B, "portrait", setp("portrait", WREN_IMG), None)
    instance = store.read(WREN_B)
    other.put(RecordEnvelope(WREN_B, {}, derived_from=str(MIRA)), instance.etag)
    with pytest.raises(InvalidPlacement) as refused:
        other.preview()
    assert refused.value.reason == "wrong_scope"


def test_a_linked_images_folder_is_not_reported_as_a_duplicate(pictured, store, tmp_path):
    folder = store.root / "sources/world/harbor/_source/images"
    moved = tmp_path / "images-elsewhere"
    folder.rename(moved)
    try:
        os.symlink(moved, folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("links are not available here")
    _refused(InvalidImage, "unreadable_scope", pictured.add_image, HARBOR, "brand-new", PNG_2)


def test_a_hand_broken_placement_can_be_replaced_through_the_service(pictured, store):
    images = pictured
    ref = PlacementRef(SAILOR, "avatar")
    path = store.root.joinpath(*address_parts(ref))
    document = json.loads(path.read_bytes())
    document["payload"]["crop"] = {"x": 0, "y": 0, "w": 20000, "h": 1}
    path.write_text(json.dumps(document))
    with pytest.raises(InvalidPlacement) as refused:
        images.read_placement(ref)
    images.place(SAILOR, "avatar", setp("avatar"), refused.value.etag).commit()
    path.write_text("{not json")
    with pytest.raises(InvalidPlacement) as refused:
        images.read_placement(ref)
    images.place(SAILOR, "avatar", setp("avatar"), refused.value.etag).commit()
    assert isinstance(images.effective_placements(SAILOR).slot("avatar"), Shown)


def test_a_sync_conflict_text_file_does_not_hide_inherited_art(hidden, store):
    folder = store.root.joinpath(*address_parts(PlacementRef(WREN_A, "avatar"))).parent
    folder.mkdir(parents=True)
    (folder / "Notes (conflicted copy).TXT").write_bytes(b"x")
    assert isinstance(hidden.effective_placements(WREN_A).slot("avatar"), Shown)
    (folder / "Avatar.sync-conflict-20261104-ABCDEFG.json").write_bytes(b"{}")  # could be a placement
    found = hidden.effective_placements(WREN_A).slot("avatar")
    assert isinstance(found, Unavailable) and found.text == "campaign layer damaged"



# -- round-3 brutal-review regressions -----------------------------------------------------------

def test_unstorable_source_references_on_disk_never_crash(pictured, store):
    images = pictured
    path = store.root.joinpath(*address_parts(PlacementRef(SAILOR, "avatar")))
    document = json.loads(path.read_bytes())
    gallery = path.with_name("gallery_9.json")
    bad = dict(document, address=f"{SAILOR}#placement/gallery_9",
               payload={"mode": "set", "role": "gallery", "image": "world:con#image/x"})
    gallery.write_text(json.dumps(bad))  # a hand edit naming a source no portable path can hold
    result = images.effective_placements(SAILOR)
    assert result.slot("gallery_9").reason == "wrong_scope" and isinstance(result.slot("avatar"), Shown)
    assert isinstance(images.effective_placements(WREN_C).slot("avatar"), Shown)  # derived instances too
    z = InstanceRef("c1", "character", "z")
    txn = store.transaction()
    txn.put(RecordEnvelope(z, {}, derived_from="world:con/character/x"), None)
    txn.commit(handle())
    assert images.effective_placements(z).slots == ()
    batch = images.change()
    local = batch.add_image("campaign:c1", "z-local", JPEG)
    batch.place(z, "avatar", setp("avatar", local), None)  # campaign images stay allowed
    batch.commit()
    assert images.effective_placements(z).slot("avatar").image == local
    with pytest.raises(InvalidImage):
        images.read_image(ImageRef("world:con", "x"))


def test_a_repeated_put_is_refused_and_leaves_the_batch_usable(images, store):
    z = InstanceRef("c1", "character", "z")
    batch = images.change()
    batch.put(RecordEnvelope(z, {}), None)
    with pytest.raises(InvalidBatch) as refused:
        batch.put(RecordEnvelope(z, {"again": True}), None)
    assert refused.value.reason == "duplicate_put"
    batch.commit()
    assert store.read(z).envelope.payload == {}


@pytest.mark.parametrize("content, reason", [
    (lambda doc: json.dumps(dict(doc, format="grimoire.asset-record/99")), "unsupported_format"),
    (lambda doc: "{not json", "invalid_image"),
])
def test_read_image_refuses_unreadable_records_with_a_reason(pictured, store, content, reason):
    path = store.root.joinpath(*address_parts(WREN_IMG))
    path.write_text(content(json.loads(path.read_bytes())))
    for read in (pictured.read_image, pictured.read_image_bytes):
        _refused(InvalidImage, reason, read, WREN_IMG)


def test_an_empty_folder_at_a_campaign_placement_path_shows_nothing_else(hidden, store):
    target = store.root.joinpath(*address_parts(PlacementRef(WREN_B, "avatar")))
    target.unlink()
    target.mkdir()  # empty: no hide can be stored here, and nothing may fall back
    found = hidden.effective_placements(WREN_B).slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "invalid_placement"



# -- round-4 brutal-review regressions -----------------------------------------------------------

@pytest.mark.parametrize("damage", ["lookalike-folder-beside-canonical", "lowercase-duplicate"])
def test_a_hide_in_a_look_alike_place_never_reveals_the_source_picture(hidden, store, damage):
    images = hidden
    target = store.root.joinpath(*address_parts(PlacementRef(WREN_B, "avatar")))
    folder = target.parent
    images.place(WREN_B, "gallery_1", Placement("hide"), None).commit()  # keeps the canonical folder alive
    if damage == "lookalike-folder-beside-canonical":
        sibling = folder.with_name("Wren-b")
        if sibling.exists():  # checked before creating it: there the look-alike is the same folder
            pytest.skip("case-insensitive filesystem: the look-alike is the same folder")
        sibling.mkdir()
        target.rename(sibling / "avatar.json")
    else:
        target.rename(folder / "avatar copy.json")
    result = images.effective_placements(WREN_B)
    found = result.slot("avatar")
    assert isinstance(found, Unavailable) and found.reason == "invalid_placement" and result.problems


def test_an_adjust_over_a_broken_inherited_picture_is_unavailable_not_hidden(pictured, store):
    images = pictured
    images.place(WREN_C, "avatar", Placement("adjust", image=WREN_IMG, crop=Crop(1, 1, 10, 10)), None).commit()
    blob = images.read_image(WREN_IMG)[0].blob
    store.root.joinpath(*blob_parts(blob.sha256, blob.ext)).unlink()
    found = images.effective_placements(WREN_C).slot("avatar")
    assert found == Unavailable("avatar", "missing_blob", str(WREN_IMG))


def test_read_errors_are_invalid_image_reasons(pictured, store, tmp_path):
    images = pictured
    _refused(InvalidImage, "missing_image", images.read_image, ImageRef(HARBOR, "absent"))
    path = store.root.joinpath(*address_parts(WREN_IMG))
    outside = tmp_path / "wren.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    _link_or_skip(path, outside)
    _refused(InvalidImage, "invalid_image", images.read_image, WREN_IMG)


def test_a_newer_placement_is_reported_as_such_and_never_replaceable(pictured, store):
    ref = PlacementRef(SAILOR, "avatar")
    path = store.root.joinpath(*address_parts(ref))
    path.write_text(json.dumps(dict(json.loads(path.read_bytes()), format="grimoire.asset-record/9")))
    with pytest.raises(InvalidPlacement) as refused:
        pictured.read_placement(ref)
    assert refused.value.reason == "unsupported_format" and refused.value.etag is None


def test_check_runs_the_commits_checks_read_only_and_not_after_commit(images, store):
    batch = images.change()
    batch.add_image(CARD, "mira", GIF)
    batch.place(MIRA, "avatar", setp("avatar", ImageRef(CARD, "mira")), None)
    before = snapshot(store.root)
    batch.check()
    assert snapshot(store.root) == before  # nothing written, no approval used
    batch.commit()
    with pytest.raises(StoreError, match="already committed"):
        batch.check()
