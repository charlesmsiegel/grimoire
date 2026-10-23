"""The library API respects actor type, scope, selected version and stale writes."""

import os
import time

import pytest

from grimoire.store import campaigns, characters, image_gc, image_store, pcs, worlds
from grimoire.store import paths as store_paths
from grimoire.store.expression_library import paths, read
from tests.test_expression_packs import archive
from tests.test_expressions_store import apply, outfit, png
from tests.test_expressions_store import target as target


@pytest.mark.parametrize("scope", ["world", "campaign"])
@pytest.mark.parametrize("kind", ["characters", "pcs"])
def test_library_round_trip_in_each_scope(client, scope, kind):
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    actor, version = (characters.create_character(root, "Mara") if kind == "characters"
                      else pcs.create_pc(root, "Winifred", []))
    owner = wid if scope == "world" else campaigns.create_campaign("Saltmarch", wid)
    url = f"/api/{scope}s/{owner}/{kind}/{actor}/versions/{version}/expressions"
    response = client.get(url)
    assert response.status_code == 200, response.text
    empty = response.json()
    assert len(empty["standard_labels"]) == 28
    body = {"expected_effective_revision": empty["effective_revision"],
            "mutations": [{"op": "create-outfit", "outfit": "armor", "metadata": {"label": "Armor"}}]}
    created = client.post(url + "/mutations", json=body)
    assert created.status_code == 200, created.text
    assert client.post(url + "/mutations", json=body).status_code == 409
    revision = created.json()["effective_revision"]
    uploaded = client.put(url + "/armor/neutral/image", data={"expected_effective_revision": revision},
                          files={"file": ("sprite.png", png(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    image = client.get(url + "/armor/neutral/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.get(url + "/export").status_code == 200
    assert client.get(url.replace(f"/versions/{version}/", "/versions/missing/")).status_code == 404


def test_pack_preview_commit_uses_same_validated_target(client):
    wid = worlds.create_world("Realm")
    actor, version = characters.create_character(worlds.world_root(wid), "Mara")
    url = f"/api/worlds/{wid}/characters/{actor}/versions/{version}/expressions"
    response = client.post(url + "/imports/preview", data={"mode": "sillytavern", "outfit_id": "everyday"},
                           files={"file": ("sprites.zip", archive({"neutral.png": png()}).getvalue(), "application/zip")})
    assert response.status_code == 200, response.text
    preview = response.json()
    result = client.post(url + f"/imports/{preview['token']}/commit", json={
        "expected_effective_revision": preview["effective_revision"],
        "selection": [{"file": "neutral.png", "outfit": "everyday", "expression": "neutral", "overwrite": False}]})
    assert result.status_code == 200, result.text
    assert result.json()["outfits"]["everyday"]["expressions"]["neutral"]["available"]


def test_folder_preview_honors_flat_format_and_outfit(client, target):
    url = f"/api/worlds/{target.owner_id}/characters/{target.actor_id}/versions/{target.version_id}/expressions"
    response = client.post(url + "/imports/folder", data={"mode": "sillytavern", "outfit_id": "armor"},
                           files=[("files", ("neutral.png", png(), "image/png"))])
    assert response.status_code == 200, response.text
    assert response.json()["images"][0]["outfit"] == "armor"


def test_revision_cleanup_endpoint_releases_retired_image_for_gc(client, target):
    first = outfit(target)
    image_id = first["outfits"]["everyday"]["expressions"]["neutral"]["image_id"]
    apply(target, {"op": "remove-expression", "outfit": "everyday", "expression": "neutral"})
    apply(target, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"label": "Travel"}})
    old = (time.time() - 7200,) * 2
    for revision in (paths.directory(target) / "revisions").iterdir():
        os.utime(revision, old)
    obj = image_store.read(image_id)
    os.utime(image_store.object_path(image_id), old)
    os.utime(image_store.blob_path(obj.blob_sha256, obj.ext), old)
    url = f"/api/worlds/{target.owner_id}/characters/{target.actor_id}/versions/{target.version_id}/expressions"
    response = client.post(url + "/cleanup")
    assert response.status_code == 200, response.text
    assert response.json()["removed_revisions"] == 1
    assert read.effective(target)["outfits"]["everyday"]["label"] == "Travel"
    report = image_gc.scan(store_paths.home(), cancel=lambda: False)
    assert image_id in {row["id"] for row in report["collectable"]}


@pytest.mark.parametrize("scope", ["world", "campaign"])
@pytest.mark.parametrize("kind", ["characters", "pcs"])
def test_native_actor_route_round_trip(client, scope, kind):
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    actor, _ = (characters.create_character(root, "Mara") if kind == "characters"
                else pcs.create_pc(root, "Winifred", []))
    owner = wid if scope == "world" else campaigns.create_campaign("Saltmarch", wid)
    url = f"/api/{scope}s/{owner}/{kind}"
    packed = client.get(url + f"/{actor}/bundle")
    assert packed.status_code == 200, packed.text
    destination = url + "/imported/bundle/imports"
    preview = client.post(destination + "/preview", files={"file": ("actor.zip", packed.content, "application/zip")})
    assert preview.status_code == 200, preview.text
    result = client.post(destination + f'/{preview.json()["token"]}/commit', json={"disposition": "detached"})
    assert result.status_code == 200, result.text
    assert result.json()["actor_id"] == "imported"
