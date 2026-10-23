"""Native actor packs preserve versions and deliberate inheritance choices."""

import io
import zipfile
from dataclasses import replace

import pytest
from PIL import Image

from grimoire.store import assets, campaigns, characters, overlay, pcs, worlds
from grimoire.store.expression_library import character_pack, contracts, paths, read
from tests.test_expressions_inheritance import campaign_target
from tests.test_expressions_store import apply, outfit, png
from tests.test_expressions_store import target as target


@pytest.mark.parametrize("kind", ["characters", "pcs"])
def test_native_character_pack_retains_every_version_expression(target, kind, tmp_path, monkeypatch):
    root = worlds.world_root(target.owner_id)
    if kind == "pcs":
        aid, vid = pcs.create_pc(root, "Winifred", [])
        target = replace(target, kind=kind, actor_id=aid, version_id=vid)
    outfit(target)
    directory = root / kind / target.actor_id
    extension = ".json" if kind == "characters" else ".md"
    (directory / ("formal" + extension)).write_bytes((directory / (target.version_id + extension)).read_bytes())
    outfit(replace(target, version_id="formal"), "formal")
    archive = tmp_path / "actor.zip"
    actor = contracts.ExpressionActorTarget(target.scope, target.owner_id, kind, target.actor_id)
    character_pack.export(actor, archive)
    new_home = tmp_path / "imported"
    monkeypatch.setenv("GRIMOIRE_HOME", str(new_home))
    wid = worlds.create_world("Realm")
    dest = replace(actor, owner_id=wid)
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, dest)
    character_pack.import_commit(preview["token"], dest, "detached")
    for vid, oid in ((target.version_id, "everyday"), ("formal", "formal")):
        collection = read.effective(contracts.ExpressionTarget("world", wid, kind, target.actor_id, vid))
        assert collection["outfits"][oid]["expressions"]["neutral"]["available"]


def test_native_pack_preserves_lineage_and_tombstones(target, tmp_path):
    outfit(target)
    apply(target, {"op": "put-expression", "outfit": "everyday", "expression": "anger", "data": png()})
    local = campaign_target(target)
    apply(local, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"label": "Travel"}},
          {"op": "hide-expression", "outfit": "everyday", "expression": "anger"})
    actor = contracts.ExpressionActorTarget("campaign", local.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    cid = campaigns.create_campaign("Saltmarch copy", target.owner_id)
    dest = replace(actor, owner_id=cid)
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, dest)
    assert preview["can_bind"]
    character_pack.import_commit(preview["token"], dest, "bind")
    other = replace(local, owner_id=cid)
    manifest, refs = read.layer(paths.directory(other))
    assert manifest["outfits"]["everyday"] == {"label": "Travel"}
    assert refs == {}
    assert manifest["hidden_expressions"]["everyday"] == ["anger"]
    assert "neutral" in read.effective(other)["outfits"]["everyday"]["expressions"]


def test_native_dependency_mismatch_requires_explicit_detached_import(target, tmp_path, monkeypatch):
    outfit(target)
    local = campaign_target(target)
    actor = contracts.ExpressionActorTarget("campaign", local.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "imported"))
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara")
    cid = campaigns.create_campaign("Saltmarch", wid)
    dest = replace(actor, owner_id=cid)
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, dest)
    assert not preview["can_bind"]
    with pytest.raises(contracts.ExpressionConflictError):
        character_pack.import_commit(preview["token"], dest, "bind")
    character_pack.import_commit(preview["token"], dest, "detached")
    assert read.effective(replace(local, owner_id=cid))["outfits"]["everyday"]["expressions"]["neutral"]["available"]


def test_native_corrupt_object_does_not_publish_actor(target, tmp_path):
    outfit(target)
    actor = contracts.ExpressionActorTarget("world", target.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    bad = io.BytesIO()
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(bad, "w") as dest:
        for name in source.namelist():
            dest.writestr(name, b"broken" if name.startswith("objects/") else source.read(name))
    destination = replace(actor, actor_id="winifred")
    bad.seek(0)
    with pytest.raises(contracts.ExpressionInvalidError):
        character_pack.import_preview(bad, destination)
    assert not (worlds.world_root(target.owner_id) / target.kind / "winifred").exists()


def test_native_pack_retains_animated_gallery_without_making_it_a_sprite(target, tmp_path):
    root = worlds.world_root(target.owner_id)
    gif = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(gif, "GIF", save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
    assets.put_in(assets.version_dir(root, target.actor_id, target.version_id), "gallery_1", gif.getvalue(), "gif")
    actor = contracts.ExpressionActorTarget("world", target.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    destination = replace(actor, actor_id="winifred")
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, destination)
    character_pack.import_commit(preview["token"], destination, "detached")
    path = assets.image_path(root, "winifred", target.version_id, "gallery_1")
    with Image.open(path) as image:
        assert image.is_animated
    assert read.effective(replace(target, actor_id="winifred"))["outfits"] == {}


def test_native_pack_refuses_binding_to_hidden_world_actor(target, tmp_path):
    outfit(target)
    local = campaign_target(target)
    actor = contracts.ExpressionActorTarget("campaign", local.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    cid = campaigns.create_campaign("Saltmarch copy", target.owner_id)
    overlay.add_deleted(cid, f"{target.kind}/{target.actor_id}")
    destination = replace(actor, owner_id=cid)
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, destination)
    assert not preview["can_bind"]
    with pytest.raises(contracts.ExpressionConflictError):
        character_pack.import_commit(preview["token"], destination, "bind")


def test_native_pack_refuses_linked_card(target, tmp_path):
    card = worlds.world_root(target.owner_id) / target.kind / target.actor_id / f"{target.version_id}.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(card.read_bytes())
    card.unlink()
    try:
        card.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")
    actor = contracts.ExpressionActorTarget("world", target.owner_id, target.kind, target.actor_id)
    with pytest.raises(contracts.ExpressionUnavailableError):
        character_pack.export(actor, tmp_path / "actor.zip")


def test_native_pack_rejects_noncanonical_object_before_publication(target, tmp_path):
    outfit(target)
    actor = contracts.ExpressionActorTarget("world", target.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    bad = io.BytesIO()
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(bad, "w") as dest:
        for name in source.namelist():
            rewritten = name.removesuffix(".json") + ".txt" if name.startswith("objects/") else name
            dest.writestr(rewritten, source.read(name))
    bad.seek(0)
    with pytest.raises(contracts.ExpressionInvalidError):
        character_pack.import_preview(bad, replace(actor, actor_id="winifred"))
    assert not (worlds.world_root(target.owner_id) / target.kind / "winifred").exists()


def test_native_pack_rebases_own_card_image_urls_to_imported_actor(target, tmp_path):
    root = worlds.world_root(target.owner_id)
    assets.put_in(assets.version_dir(root, target.actor_id, target.version_id), "gallery_1", png(), "png")
    original = f"/api/worlds/{target.owner_id}/characters/{target.actor_id}/versions/{target.version_id}/images/gallery_1"
    unrelated = f"/api/worlds/{target.owner_id}/characters/seraphine/versions/default/images/gallery_1"
    card = characters.read_card(root, target.actor_id, target.version_id)
    card["data"]["description"] = f"![portrait]({original}?v=old) ![other]({unrelated})"
    characters.update_version(root, target.actor_id, target.version_id, card)
    actor = contracts.ExpressionActorTarget("world", target.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    destination = replace(actor, owner_id=worlds.create_world("Saltmarch"), actor_id="winifred")
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, destination)
    character_pack.import_commit(preview["token"], destination, "detached")
    imported_root = worlds.world_root(destination.owner_id)
    imported = characters.read_card(imported_root, destination.actor_id, target.version_id)
    expected = f"/api/worlds/{destination.owner_id}/characters/{destination.actor_id}/versions/{target.version_id}/images/gallery_1"
    assert imported["data"]["description"] == f"![portrait]({expected}?v=old) ![other]({unrelated})"
    assert assets.image_path(imported_root, destination.actor_id, target.version_id, "gallery_1") is not None
