"""An expression revision is complete or unavailable, never half an outfit."""

import io
import json

import pytest
from PIL import Image

from grimoire.store import assets, atomic, characters, worlds
from grimoire.store.expression_library import contracts, paths, read, storage, write


def png(color=(20, 40, 60, 100)):
    stream = io.BytesIO()
    Image.new("RGBA", (8, 12), color).save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def target(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    aid, vid = characters.create_character(worlds.world_root(wid), "Mara")
    return contracts.ExpressionTarget("world", wid, "characters", aid, vid)


def apply(target, *mutations):
    return write.apply_effective(target, list(mutations),
        expected_effective_revision=read.effective(target)["effective_revision"])


def outfit(target, name="everyday"):
    return apply(target, {"op": "create-outfit", "outfit": name, "metadata": {"label": name}},
                 {"op": "put-expression", "outfit": name, "expression": "neutral", "data": png(), "ext": "png"})


def test_sparse_outfits_publish_separately_from_gallery(target):
    collection = outfit(target)
    assert set(collection["outfits"]["everyday"]["expressions"]) == {"neutral"}
    assert collection["outfits"]["everyday"]["expressions"]["neutral"]["available"]
    root = worlds.world_root(target.owner_id)
    assert assets.list_images(root, target.actor_id, target.version_id) == []
    assert assets.image_path(root, target.actor_id, target.version_id, "avatar") is None


def test_failed_revision_publication_preserves_old_collection(target, monkeypatch):
    before = outfit(target)
    original = atomic.write_bytes

    def fail_manifest(path, data, *args, **kwargs):
        if path == paths.directory(target) / "manifest.json":
            raise OSError("disk full")
        return original(path, data, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_bytes", fail_manifest)
    with pytest.raises(OSError):
        apply(target, {"op": "put-expression", "outfit": "everyday", "expression": "anger", "data": png((80, 20, 30, 100)), "ext": "png"})
    assert read.effective(target) == before


def test_newer_manifest_refuses_write(target):
    directory = paths.directory(target)
    directory.mkdir(parents=True)
    manifest = directory / "manifest.json"
    manifest.write_text('{"format": 999}', encoding="utf-8")
    with pytest.raises(contracts.ExpressionUnavailableError):
        outfit(target)
    assert json.loads(manifest.read_text())["format"] == 999


def test_stale_save_cannot_overwrite_newer_revision(target):
    old = outfit(target)
    apply(target, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"label": "Casual"}})
    with pytest.raises(contracts.ExpressionConflictError):
        write.apply_effective(target, [{"op": "remove-outfit", "outfit": "everyday"}],
                              expected_effective_revision=old["effective_revision"])
    assert read.effective(target)["outfits"]["everyday"]["label"] == "Casual"


def test_manifest_before_refs_is_unavailable_until_complete(target):
    old = outfit(target)
    placement = next(paths.directory(target).glob("revisions/*/*/image-refs/*.json"))
    data = placement.read_bytes()
    placement.unlink()
    with pytest.raises(contracts.ExpressionUnavailableError):
        read.effective(target)
    placement.write_bytes(data)
    assert read.effective(target) == old


@pytest.mark.parametrize("patch", [{"format": True}, {"hidden_outfits": "armor"},
    {"hidden_expressions": {"everyday": "neutral"}}, {"placement_count": True}])
def test_malformed_manifest_is_not_treated_as_an_editable_collection(target, patch):
    outfit(target)
    directory = paths.directory(target)
    manifest, _ = storage.layer(directory)
    manifest.update(patch)
    revision = directory / "revisions" / manifest["revision"]
    placements = {p.relative_to(revision).as_posix(): p.read_bytes()
                  for p in revision.glob("*/image-refs/*.json")}
    manifest["content_digest"] = storage.checksum(manifest, placements)
    (directory / "manifest.json").write_bytes(storage.encoded(manifest))
    with pytest.raises(contracts.ExpressionUnavailableError):
        read.effective(target)
