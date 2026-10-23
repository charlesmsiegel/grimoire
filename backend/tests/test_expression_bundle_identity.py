"""Bundle object names are untrusted aliases, mapped once on import."""

import json
import zipfile
from pathlib import PurePosixPath

from grimoire.store import world_bundle
from grimoire.store.expression_library import contracts, paths, read, storage
from tests.test_expressions_store import apply, outfit, png
from tests.test_expressions_store import target as target


def test_world_bundle_expression_object_alias_chain_maps_once(target, tmp_path, monkeypatch):
    outfit(target)
    before = apply(target, {"op": "put-expression", "outfit": "everyday",
                           "expression": "joy", "data": png((80, 10, 30, 255)), "ext": "png"})
    expressions = before["outfits"]["everyday"]["expressions"]
    neutral_id, joy_id = expressions["neutral"]["image_id"], expressions["joy"]["image_id"]
    alias = "px1-" + "a" * 64
    assert alias not in {neutral_id, joy_id}
    claimed = {neutral_id: alias, joy_id: neutral_id}
    destination = tmp_path / "original.zip"
    world_bundle.write_bundle(target.owner_id, destination)
    with zipfile.ZipFile(destination) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    remapped = {}
    manifest_name = next(name for name in entries if name.endswith("/expressions/manifest.json"))
    manifest = json.loads(entries[manifest_name])
    revision_prefix = manifest_name.removesuffix("manifest.json") + "revisions/" + manifest["revision"] + "/"
    placements = {}
    for original_name, original_data in entries.items():
        name, data = original_name, original_data
        member = PurePosixPath(name)
        if name.startswith("image-store/objects/") and member.stem in claimed:
            image = claimed[member.stem]
            name = "image-store/objects/" + image[4:6] + "/" + image + ".json"
        if name.startswith(revision_prefix) and "/image-refs/" in name:
            placement = json.loads(data)
            placement["image"] = claimed[placement["image"]]
            data = storage.encoded(placement)
            placements[name.removeprefix(revision_prefix)] = data
        remapped[name] = data
    manifest["content_digest"] = storage.checksum(manifest, placements)
    remapped[manifest_name] = storage.encoded(manifest)
    aliased = tmp_path / "aliased.zip"
    with zipfile.ZipFile(aliased, "w") as archive:
        for name, data in remapped.items():
            archive.writestr(name, data)
    imported_home = tmp_path / "imported"
    imported_home.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(imported_home))
    wid = world_bundle.import_bundle(aliased)
    imported = contracts.ExpressionTarget("world", wid, target.kind, target.actor_id, target.version_id)
    # Reading the complete layer also verifies its digest survived containment.
    storage.layer(paths.directory(imported))
    after = read.effective(imported)["outfits"]["everyday"]["expressions"]
    assert after["neutral"]["image_id"] == neutral_id
    assert after["joy"]["image_id"] == joy_id
    assert after["neutral"]["available"] and after["joy"]["available"]
