"""Binding an archive requires the same live actor incarnation."""

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.store import campaigns, characters, image_store, overlay, worlds
from grimoire.store.expression_library import character_pack, contracts, packs, read
from tests.test_expressions_inheritance import campaign_target
from tests.test_expressions_store import outfit, png
from tests.test_expressions_store import target as target


def test_native_pack_recreated_identical_actor_cannot_bind(target, tmp_path, monkeypatch):
    monkeypatch.setattr(character_pack, "_cache", lambda: tmp_path / "previews")
    outfit(target)
    local = campaign_target(target)
    actor = contracts.ExpressionActorTarget("campaign", local.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    world_root = worlds.world_root(target.owner_id)
    characters.delete_character(world_root, target.actor_id)
    recreated_id, _ = characters.create_character(world_root, "Mara")
    assert recreated_id == target.actor_id
    cid = campaigns.create_campaign("Saltmarch copy", target.owner_id)
    destination = replace(actor, owner_id=cid)
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, destination)
    assert not preview["can_bind"]
    with pytest.raises(contracts.ExpressionConflictError):
        character_pack.import_commit(preview["token"], destination, "bind")
    assert not (campaigns.campaign_root(cid) / target.kind / target.actor_id).exists()


@pytest.mark.parametrize("spelling", ["Image-Refs", "IMAGE-REFS"])
def test_native_pack_case_alias_cannot_reference_unbundled_local_image(target, tmp_path, monkeypatch, spelling):
    monkeypatch.setattr(character_pack, "_cache", lambda: tmp_path / "previews")
    local_only = image_store.ingest(png(), "png").id
    actor = contracts.ExpressionActorTarget(target.scope, target.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    crafted = io.BytesIO()
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(crafted, "w") as output:
        for name in source.namelist():
            output.writestr(name, source.read(name))
        output.writestr(f"actor/assets/{target.version_id}/{spelling}/avatar.json",
                        json.dumps({"format": 1, "image": local_only}))
    crafted.seek(0)
    destination = replace(actor, actor_id="winifred")
    with pytest.raises(contracts.ExpressionInvalidError):
        character_pack.import_preview(crafted, destination)
    assert not (worlds.world_root(target.owner_id) / target.kind / destination.actor_id).exists()


def test_native_parent_creation_failure_preserves_campaign_inheritance(target, tmp_path, monkeypatch):
    monkeypatch.setattr(character_pack, "_cache", lambda: tmp_path / "previews")
    outfit(target)
    actor = contracts.ExpressionActorTarget(target.scope, target.owner_id, target.kind, target.actor_id)
    archive = tmp_path / "actor.zip"
    character_pack.export(actor, archive)
    local = campaign_target(target)
    destination = replace(actor, scope="campaign", owner_id=local.owner_id)
    final = campaigns.campaign_root(local.owner_id) / target.kind / target.actor_id
    before = read.effective(local)
    with archive.open("rb") as stream:
        preview = character_pack.import_preview(stream, destination)
    original = Path.mkdir

    def refuse_parent(path, *args, **kwargs):
        if path == final.parent:
            raise PermissionError("synthetic parent creation failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse_parent)
    with pytest.raises(PermissionError, match="synthetic parent creation failure"):
        character_pack.import_commit(preview["token"], destination, "detached")
    assert not final.exists()
    assert f"{target.kind}/{target.actor_id}" not in overlay.view(local.owner_id).off
    assert read.effective(local) == before


def test_native_oversized_cards_refused_before_loading_bodies(target, tmp_path, monkeypatch):
    directory = worlds.world_root(target.owner_id) / target.kind / target.actor_id
    total = sum(path.stat().st_size for path in directory.iterdir() if path.suffix in (".md", ".json"))
    monkeypatch.setattr(packs, "MAX_UPLOAD", total - 1)

    def refuse_unbounded_read(*args, **kwargs):
        pytest.fail("Overbudget actor bodies were loaded before rejection")

    monkeypatch.setattr(characters, "snapshot", refuse_unbounded_read)
    actor = contracts.ExpressionActorTarget(target.scope, target.owner_id, target.kind, target.actor_id)
    destination = tmp_path / "actor.zip"
    with pytest.raises(contracts.ExpressionBudgetError):
        character_pack.export(actor, destination)
    assert not destination.exists()
