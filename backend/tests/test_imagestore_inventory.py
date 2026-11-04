"""A5: the 011 inventory's picture rows map onto 018 calls (Sprint 018 handoff to 026).

The read-only V0.2.1 scanner inventories the synthetic fixture; a *test-local* adapter (the
real importer is 026) turns its image objects, placements and campaign layering into one
image batch. Shared objects stay one image, crops and focus survive, a hidden inherited picture
stays hidden, and a dangling object is refused. The V1 tree is never modified.
"""

from __future__ import annotations

import pytest

from grimoire.core.identity import ImageRef, InstanceRef, LegacyId, SourceRef
from grimoire.images import Expression, Focus, Hidden, Placement, Shown
from grimoire.imagestore import ImageLibrary, InvalidPlacement
from grimoire.migrate_v1 import scan
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from image_fixtures import handle
from store_helpers import OWNER
from v1_fixture import MISSING_ID, RED_ID, build_library, tree_digest

SALTMARCH, REALM = "world:saltmarch", "world:realm"
DEFAULT = SourceRef(SALTMARCH, "character", "mara-default")
OLDER = SourceRef(SALTMARCH, "character", "mara-older")
REALM_MARA = SourceRef(REALM, "character", "mara-default")
CAMPAIGN_MARA = InstanceRef("drowned", "character", "mara")


def _image_id(px1: str) -> str:
    return "px-" + px1[4:16]


def _entity(owner: dict) -> SourceRef:
    source = {"saltmarch": SALTMARCH, "realm": REALM}[owner["scope_id"]]
    return SourceRef(source, "character", f"{owner['record']}-{owner['version']}")


def _focus(v1_focus: int | None) -> Focus | None:
    return None if v1_focus is None else Focus(5000, v1_focus * 100)


@pytest.fixture
def inventory(tmp_path):
    v1 = build_library(tmp_path / "v1")
    home = tmp_path / "h"
    home.mkdir()
    before = tree_digest(v1)
    manifest = scan(v1, archive_id="fixture", destination=tmp_path / "v2-dest", home=home, environ={}).manifest
    return v1, manifest, before


def test_a5_inventory_rows_map_onto_images_placements_and_layering(inventory, tmp_path):
    v1, manifest, before = inventory
    rows = manifest["images"]
    with LibraryStore.create(tmp_path / "lib") as store:
        images = ImageLibrary(store, handle(), owner=OWNER)
        batch = images.change()
        for source in (SALTMARCH, REALM):
            batch.put(SourceManifest(source, OWNER), None)
        for entity in (DEFAULT, OLDER, REALM_MARA):
            batch.put(RecordEnvelope(entity.at("r1"), {}), None)
        batch.put(RecordEnvelope(CAMPAIGN_MARA, {}, derived_from=str(DEFAULT)), None)
        by_px1 = {}
        for obj in rows["objects"]:  # only objects with intact bytes can become image objects
            if obj["valid"] and obj["blob"] == "present":
                data = (v1 / f"assets/image-store/blobs/{obj['blob_sha256'][:2]}/"
                             f"{obj['blob_sha256']}.{obj['ext']}").read_bytes()
                ref = batch.add_image(SALTMARCH, _image_id(obj["id"]), data,
                                      {"legacy_id": str(LegacyId("fixture", obj["path"]))})
                by_px1[obj["id"]] = ref
        world_rows = {}
        for row in rows["placements"]:
            if row["owner"]["scope"] != "world" or row["owner"]["scope_id"] != "saltmarch":
                continue
            assert row["status"] == "resolved"
            entity, image = _entity(row["owner"]), by_px1[row["image"]]
            provenance = {"legacy_id": row["legacy_id"]}
            if row["area"] == "expression":
                assert row["live"]
                slot = f"expr.{row['outfit']}.{row['name']}"
                placement = Placement("set", "expression", image, expression=Expression(row["outfit"], row["name"]),
                                      provenance=provenance)
            else:
                slot = row["name"]
                placement = Placement("set", "avatar" if slot == "avatar" else "gallery", image,
                                      focus=_focus(row["focus"]), provenance=provenance)
            batch.place(entity, slot, placement, None)
            world_rows[row["path"]] = (slot, image)
        campaign_rows = {row["path"]: row for row in rows["placements"] if row["owner"]["scope"] == "campaign"}
        # the fixture holds exactly these layering states; any other would need its own mapping
        assert {layer["state"] for layer in rows["campaign_layering"]} == {"hidden", "focus_override"}
        for layer in rows["campaign_layering"]:
            slot, image = world_rows[layer["world_placement"]]
            if layer["state"] == "hidden":
                batch.place(CAMPAIGN_MARA, slot, Placement("hide"), None)
            elif layer["state"] == "focus_override":
                focus = campaign_rows[layer["campaign_placement"]]["focus"]
                batch.place(CAMPAIGN_MARA, slot, Placement("adjust", image=image, focus=_focus(focus)), None)
        batch.commit()

        dangling = next(row for row in rows["placements"] if row["status"] == "dangling_object")
        assert dangling["image"] == MISSING_ID
        lonely = images.change()  # a refused op leaves a batch unchanged; this one is tried on its own
        with pytest.raises(InvalidPlacement) as caught:
            lonely.place(_entity(dangling["owner"]), "avatar",
                         Placement("set", "avatar", ImageRef(REALM, _image_id(MISSING_ID))), None)
        assert caught.value.reason == "missing_image"

        default = images.effective_placements(DEFAULT)
        older = images.effective_placements(OLDER)
        avatar, smile = default.slot("avatar"), older.slot("expr.casual.neutral")
        assert avatar.image == smile.image == by_px1[RED_ID]  # one V1 object, still one image
        assert avatar.focus == Focus(5000, 4000)
        assert isinstance(default.slot("gallery_1"), Shown)
        campaign = images.effective_placements(CAMPAIGN_MARA)
        adjusted = campaign.slot("avatar")
        assert (adjusted.layer, adjusted.image, adjusted.focus) == ("campaign_adjusted", by_px1[RED_ID],
                                                                    Focus(5000, 7000))
        assert campaign.slot("gallery_1") == Hidden("gallery_1")
        assert images.read_image(by_px1[RED_ID])[0].provenance["legacy_id"].startswith("v1:fixture:")
    assert tree_digest(v1) == before  # the V1 source was never modified
