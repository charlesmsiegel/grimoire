"""Expression metadata and objects survive the same moves as ordinary art."""

import os
import time
import zipfile

from grimoire.store import assets, backups, image_gc, world_bundle, worlds
from grimoire.store import paths as store_paths
from grimoire.store.expression_library import contracts, lifecycle, paths, read
from tests.test_expressions_inheritance import campaign_target
from tests.test_expressions_store import apply, outfit
from tests.test_expressions_store import target as target


def test_expression_only_blob_is_reachable(target):
    collection = outfit(target)
    image = collection["outfits"]["everyday"]["expressions"]["neutral"]["image_id"]
    report = image_gc.scan(store_paths.home(), cancel=lambda: False)
    assert report["counts"]["placements"] >= 1
    assert image not in {row["id"] for row in report["collectable"]}
    assert read.effective(target)["outfits"]["everyday"]["expressions"]["neutral"]["available"]


def test_image_backup_includes_expression_manifest(target):
    outfit(target)
    with zipfile.ZipFile(backups.create_image_backup()) as archive:
        name = (paths.directory(target) / "manifest.json").relative_to(store_paths.home()).as_posix()
        assert name in archive.namelist()


def test_materialize_keeps_patch_and_hidden_expression(target):
    outfit(target)
    local = campaign_target(target)
    apply(local, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"label": "Travel"}},
          {"op": "hide-expression", "outfit": "everyday", "expression": "neutral"})
    lifecycle.materialize_effective(local)
    manifest, refs = read.layer(paths.directory(local))
    assert manifest["outfits"]["everyday"]["label"] == "Travel"
    assert ("everyday", "neutral") not in refs
    assert "neutral" in manifest["hidden_expressions"]["everyday"]


def test_world_bundle_round_trip_preserves_expression_objects(target, tmp_path, monkeypatch):
    before = outfit(target)
    destination = tmp_path / "world.zip"
    world_bundle.write_bundle(target.owner_id, destination)
    new_home = tmp_path / "imported"
    new_home.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(new_home))
    wid = world_bundle.import_bundle(destination)
    other = contracts.ExpressionTarget("world", wid, target.kind, target.actor_id, target.version_id)
    after = read.effective(other)
    assert after["outfits"]["everyday"]["expressions"]["neutral"]["image_id"] == before["outfits"]["everyday"]["expressions"]["neutral"]["image_id"]
    assert after["outfits"]["everyday"]["expressions"]["neutral"]["available"]
    assert assets.list_images(worlds.world_root(wid), target.actor_id, target.version_id) == []


def test_cleanup_keeps_current_previous_and_pinned_revisions(target):
    first = outfit(target)["revision"]
    with read.pinned(target):
        apply(target, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"label": "Travel"}})
        apply(target, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"label": "Home"}})
        root = paths.directory(target) / "revisions"
        for revision in root.iterdir():
            os.utime(revision, (time.time() - 7200,) * 2)
        assert lifecycle.cleanup_revisions(target) == 0
        assert (root / first).exists()
    assert lifecycle.cleanup_revisions(target) == 1
    assert not (root / first).exists()
    assert read.effective(target)["outfits"]["everyday"]["expressions"]["neutral"]["available"]


def test_world_export_excludes_retired_sprites_unless_gallery_uses_them(target, tmp_path):
    image_id = outfit(target)["outfits"]["everyday"]["expressions"]["neutral"]["image_id"]
    apply(target, {"op": "remove-expression", "outfit": "everyday", "expression": "neutral"})
    archive = tmp_path / "world.zip"
    world_bundle.write_bundle(target.owner_id, archive)
    with zipfile.ZipFile(archive) as packed:
        assert not any(image_id in name for name in packed.namelist())
    assets.link_in(assets.version_dir(worlds.world_root(target.owner_id), target.actor_id, target.version_id), "gallery_1", image_id)
    world_bundle.write_bundle(target.owner_id, archive)
    with zipfile.ZipFile(archive) as packed:
        assert any(image_id in name for name in packed.namelist())
