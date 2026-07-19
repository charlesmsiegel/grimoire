import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app


class FakeOpenRouter:
    def __init__(self, deltas):
        self.deltas = deltas

    async def stream(self, messages, cfg):
        for d in self.deltas:
            yield d


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def _world(client, name="W"):
    return client.post("/api/worlds", json={"name": name}).json()["id"]


def _campaign(client, name="Run"):
    wid = _world(client)
    return wid, client.post("/api/campaigns", json={"name": name, "world": wid}).json()["id"]


# ---- config (unchanged behavior) ----
def test_config_never_leaks_key(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    body = client.get("/api/config").json()
    assert body["key_set"] is True
    assert "sk-or-secret" not in json.dumps(body)


# ---- llm connections ----
def test_llm_connections_seeded_by_migration(client):
    ids = {c["id"] for c in client.get("/api/llm-connections").json()}
    assert ids == {"openrouter", "claude"}


def test_create_read_update_delete_connection(client):
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "z.ai GLM",
        "base_url": "https://api.z.ai/v4", "api_key": "sk-z", "model": "glm-4.6",
        "post_process": "strict",
    })
    assert r.status_code == 200
    cid = r.json()["id"]

    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["kind"] == "openai_compatible"
    assert detail["key_set"] is True
    assert "api_key" not in detail
    assert detail["models"] == []

    r = client.put(f"/api/llm-connections/{cid}", json={"name": "z.ai GLM (renamed)"})
    assert r.json()["name"] == "z.ai GLM (renamed)"

    assert client.delete(f"/api/llm-connections/{cid}").json() == {"ok": True}
    assert client.get(f"/api/llm-connections/{cid}").status_code == 404


def test_connection_never_leaks_key(client):
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://x", "api_key": "sk-secret"})
    cid = r.json()["id"]
    body = client.get(f"/api/llm-connections/{cid}").json()
    assert body["key_set"] is True
    assert "sk-secret" not in json.dumps(body)


def test_update_connection_not_found_404(client):
    assert client.put("/api/llm-connections/nope", json={"name": "x"}).status_code == 404


def test_delete_connection_not_found_404(client):
    assert client.delete("/api/llm-connections/nope").status_code == 404


def test_models_refresh_400_for_openrouter_and_claude(client):
    assert client.post("/api/llm-connections/openrouter/models/refresh").status_code == 400
    assert client.post("/api/llm-connections/claude/models/refresh").status_code == 400


def test_models_refresh_404_for_missing_connection(client):
    assert client.post("/api/llm-connections/nope/models/refresh").status_code == 404


class FakeModelsClient:
    def __init__(self, models=None, error=None):
        self.models = models or []
        self.error = error
        self.calls = []

    async def list_models(self, base_url, key):
        self.calls.append((base_url, key))
        if self.error:
            raise self.error
        return self.models


def test_models_refresh_fetches_and_caches(client):
    from grimoire.llm import LLMError
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://x", "api_key": "sk-x"})
    cid = r.json()["id"]
    fake = FakeModelsClient(models=[
        {"id": "glm-4.6", "name": "GLM-4.6", "context": 128000, "prompt": None, "completion": None}])
    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: fake

    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 200
    assert r.json()["models"] == fake.models
    assert fake.calls == [("https://x", "sk-x")]

    # persisted: a plain GET now shows the cached list without another fetch
    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["models"] == fake.models
    assert detail["fetched_at"]


def test_models_refresh_upstream_error_normalized(client):
    from grimoire.llm import LLMError
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://x", "api_key": "sk-x"})
    cid = r.json()["id"]
    fake = FakeModelsClient(error=LLMError("auth", "bad key"))
    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: fake

    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 502
    assert r.json()["kind"] == "auth"


def test_models_refresh_route_write_hidden_if_connection_changes_during_the_fetch(client):
    # This must exercise the actual ROUTE, not just cached_models()'s gate
    # (that's already covered store-side in test_llm_connections_store.py) —
    # a route bug (e.g. capturing rev AFTER the fetch, or conditionally
    # skipping the write instead of writing unconditionally) wouldn't be
    # caught by a test that bypasses the route and pokes the store directly.
    # The fake's list_models mutates the connection AS PART OF its own
    # execution — since the route awaits it before writing the sidecar,
    # this reproduces "someone edited the connection while the fetch was
    # in flight" without needing real threading/concurrency.
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://old", "api_key": "sk-x"})
    cid = r.json()["id"]

    class MutatingFakeClient:
        async def list_models(self, base_url, key):
            store.llm_connections.update_connection(cid, base_url="https://mutated-during-fetch")
            return [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}]

    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: MutatingFakeClient()
    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 200
    assert r.json()["models"] == [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}]

    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["models"] == []  # the stale write never surfaces
    assert detail["base_url"] == "https://mutated-during-fetch"  # the mutation itself did land


def test_models_refresh_route_write_hidden_after_delete_and_recreate_during_fetch(client):
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Reused Name", "base_url": "https://old", "api_key": "sk-x"})
    cid = r.json()["id"]

    class DeleteRecreateFakeClient:
        async def list_models(self, base_url, key):
            store.llm_connections.delete_connection(cid)
            new_id = store.llm_connections.create_connection(
                "openai_compatible", "Reused Name", base_url="https://new")
            assert new_id == cid  # same freed slug — the whole point of this race
            return [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}]

    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: DeleteRecreateFakeClient()
    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 200

    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["models"] == []  # the stale write never surfaces on the recreated connection


# ---- worlds ----
def test_config_system_prompt_and_quote_color_roundtrip(client):
    client.put("/api/config", json={"system_prompt": "Never speak for the PC.", "quote_color": "on"})
    body = client.get("/api/config").json()
    assert body["system_prompt"] == "Never speak for the PC."
    assert body["quote_color"] == "on"
    assert "openrouter_key" not in body


def test_config_provider_roundtrip(client):
    r = client.put("/api/config", json={"provider": "claude", "claude_model": "sonnet"})
    assert r.status_code == 200
    assert r.json()["provider"] == "claude"
    r = client.get("/api/config")
    assert r.json()["provider"] == "claude"
    assert r.json()["claude_model"] == "sonnet"


def test_config_rejects_unknown_provider(client):
    r = client.put("/api/config", json={"provider": "gemini"})
    assert r.status_code == 422


def test_data_dir_reports_env_override(client, tmp_path):
    body = client.get("/api/config/data-dir").json()
    assert body["data_dir"] == str(tmp_path)
    assert body["source"] == "env"


def test_world_crud(client):
    wid = _world(client, "Drowned Realm")
    assert wid == "drowned-realm"
    assert [w["id"] for w in client.get("/api/worlds").json()] == [wid]
    client.put(f"/api/worlds/{wid}", json={"name": "Renamed"})
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["name"] == "Renamed"
    assert client.delete(f"/api/worlds/{wid}").status_code == 200
    assert client.get("/api/worlds").json() == []


def test_world_delete_blocked_while_campaigns_reference_it(client):
    """Deleting a world returns 409 if campaigns still reference it."""
    wid, cid = _campaign(client, "Run")
    r = client.delete(f"/api/worlds/{wid}")
    assert r.status_code == 409
    assert "Run" in r.json()["detail"]
    # after deleting the campaign, deletion succeeds
    assert client.delete(f"/api/campaigns/{cid}").status_code == 200
    assert client.delete(f"/api/worlds/{wid}").status_code == 200


def test_world_module_none_rejected(client):
    wid = _world(client)
    r = client.put(f"/api/worlds/{wid}/module", json={"module": "none"})
    assert r.status_code == 400


def test_world_entity_crud(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/locations", json={"name": "Drowned Library", "body": "Keeper"}).json()["id"]
    assert eid == "drowned-library"
    assert [e["id"] for e in client.get(f"/api/worlds/{wid}/locations").json()] == [eid]
    client.put(f"/api/worlds/{wid}/locations/{eid}", json={"body": "Updated"})
    assert client.get(f"/api/worlds/{wid}/locations/{eid}").json()["body"].strip() == "Updated"
    assert client.delete(f"/api/worlds/{wid}/locations/{eid}").status_code == 200


def test_world_character_container_crud(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    cid = r.json()["character"]
    assert cid == "seraphine"
    # default version exists
    got = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert [v["id"] for v in got["versions"]] == ["default"]
    # add a version
    vid = client.post(f"/api/worlds/{wid}/characters/{cid}/versions",
                      json={"name": "Corrupted", "card": got["versions"][0]["card"]}).json()["version"]
    assert vid == "corrupted"
    # world counts include characters
    assert client.get(f"/api/worlds/{wid}").json()["counts"]["characters"] == 1
    # an unmatched deep path 404s
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/extra/nope").status_code == 404
    # delete
    assert client.delete(f"/api/worlds/{wid}/characters/{cid}").status_code == 200


def test_character_import_export_json(client):
    wid = _world(client)
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Imported", "description": "x", "extensions": {}}}
    files = {"file": ("c.json", io.BytesIO(json.dumps(card).encode()), "application/json")}
    r = client.post(f"/api/worlds/{wid}/characters/import", files=files, data={"format": "json"})
    assert r.status_code == 200
    cid, vid = r.json()["character"], r.json()["version"]
    exp = client.get(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/export", params={"format": "json"})
    assert exp.status_code == 200
    assert json.loads(exp.content)["data"]["name"] == "Imported"


def test_character_image_routes(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images"
    # absent
    assert client.get(base).json() == []
    assert client.get(f"{base}/avatar").status_code == 404
    # upload
    files = {"file": ("a.png", io.BytesIO(b"\x89PNGdata"), "image/png")}
    r = client.put(f"{base}/avatar", files=files)
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    listed = client.get(base).json()
    assert [(i["name"], i["ext"]) for i in listed] == [("avatar", "png")] and listed[0]["v"]
    got = client.get(f"{base}/avatar")
    assert got.status_code == 200 and got.content == b"\x89PNGdata"
    assert got.headers["content-type"].startswith("image/png")
    # bad type -> 400
    bad = client.put(f"{base}/avatar", files={"file": ("a.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")})
    assert bad.status_code == 400
    # delete
    assert client.delete(f"{base}/avatar").status_code == 200
    assert client.get(f"{base}/avatar").status_code == 404


def test_campaign_image_route_serves_copied_avatar(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    client.put(f"/api/worlds/{wid}/characters/{chid}/versions/default/images/avatar",
               files={"file": ("a.png", io.BytesIO(b"PNGBYTES"), "image/png")})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    got = client.get(f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/avatar")
    assert got.status_code == 200 and got.content == b"PNGBYTES"


def test_campaign_character_image_routes_isolated(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    world_base = f"/api/worlds/{wid}/characters/{chid}/versions/default/images"
    client.put(f"{world_base}/avatar", files={"file": ("a.png", io.BytesIO(b"world-bytes"), "image/png")})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})

    camp_base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"
    r = client.put(f"{camp_base}/avatar", files={"file": ("b.png", io.BytesIO(b"campaign-bytes"), "image/png")})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}

    # campaign copy changed; world's shared copy untouched
    assert client.get(f"{camp_base}/avatar").content == b"campaign-bytes"
    assert client.get(f"{world_base}/avatar").content == b"world-bytes"

    assert client.delete(f"{camp_base}/avatar").status_code == 200
    assert client.get(f"{camp_base}/avatar").status_code == 404
    assert client.get(f"{world_base}/avatar").content == b"world-bytes"


def test_campaign_character_image_promote_swaps_avatar(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"
    client.put(f"{base}/avatar", files={"file": ("a.png", io.BytesIO(b"old"), "image/png")})
    client.put(f"{base}/gallery_1", files={"file": ("g.png", io.BytesIO(b"new"), "image/png")})

    assert client.post(f"{base}/gallery_1/promote").status_code == 200
    assert client.get(f"{base}/avatar").content == b"new"
    assert client.get(f"{base}/gallery_1").content == b"old"
    assert client.post(f"{base}/gallery_9/promote").status_code == 404


def test_campaign_avatar_focus_endpoint_round_trip(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"

    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).status_code == 404
    client.put(f"{base}/avatar", files={"file": ("a.png", io.BytesIO(b"img"), "image/png")})
    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).json() == {"ok": True}
    detail = client.get(f"/api/campaigns/{cid}/characters/{chid}").json()
    assert detail["versions"][0]["avatar_focus"] == 30


def test_campaign_avatar_focus_on_inherited_world_avatar(client):
    """A thin campaign never copies the character into the campaign root, so the
    avatar the campaign sees is still the world's own file. Setting focus must
    gate on that inherited image (not a croot-only existence check), write
    campaign-side, and read back as the campaign's authoritative focus."""
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    client.put(f"/api/worlds/{wid}/characters/{chid}/versions/default/images/avatar",
               files={"file": ("a.png", io.BytesIO(b"world-img"), "image/png")})
    croot = store.campaigns.campaign_root(cid)
    assert not (croot / "characters" / chid).exists()   # never materialized

    base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"
    r = client.put(f"{base}/avatar/focus", json={"focus": 40})
    assert r.status_code == 200 and r.json() == {"ok": True}
    detail = client.get(f"/api/campaigns/{cid}/characters/{chid}").json()
    assert detail["versions"][0]["avatar_focus"] == 40


def test_campaign_copy_image_from_greeting_inherited_greeting(client):
    """A thin campaign never copies a greeting's assets either; copying an
    inherited greeting's image into a character must still find the source
    through the overlay while writing the character-side copy to the campaign."""
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": chid, "version": "default"}).json()["id"]
    wroot = store.worlds.world_root(wid)
    store.assets.put_image(wroot, gid, "default", "embed-abc123def456", b"art", "png", base="greetings")
    croot = store.campaigns.campaign_root(cid)
    assert not (croot / "greetings" / f"{gid}.md").exists()   # never materialized

    copy_url = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/copy-from-greeting"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "avatar"})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    assert client.get(f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/avatar").content == b"art"


def test_campaign_copy_image_from_greeting(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    gid = client.post(f"/api/campaigns/{cid}/greetings",
                      json={"name": "Opener", "character": chid, "version": "default"}).json()["id"]
    root = store.campaigns.campaign_root(cid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png", base="greetings")

    copy_url = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/copy-from-greeting"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "avatar"})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    assert client.get(f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/avatar").content == b"art"


def test_campaign_copy_image_from_greeting_gallery_skips_inherited_slot(client):
    """The free gallery_N slot must account for the character's inherited
    world-side gallery images too, or a campaign-side copy can reuse a name
    that shadows one of them."""
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    wroot = store.worlds.world_root(wid)
    store.assets.put_image(wroot, chid, "default", "gallery_1", b"world-gallery", "png")
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": chid, "version": "default"}).json()["id"]
    store.assets.put_image(wroot, gid, "default", "embed-abc123def456", b"art", "png", base="greetings")

    copy_url = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/copy-from-greeting"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "gallery"})
    assert r.status_code == 200 and r.json()["name"] == "gallery_2"
    base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"
    assert client.get(f"{base}/gallery_1").content == b"world-gallery"  # inherited slot untouched
    assert client.get(f"{base}/gallery_2").content == b"art"


def test_character_image_promote_swaps_avatar(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images"
    client.put(f"{base}/avatar", files={"file": ("a.png", io.BytesIO(b"old"), "image/png")})
    client.put(f"{base}/gallery_1", files={"file": ("g.png", io.BytesIO(b"new"), "image/png")})

    r = client.post(f"{base}/gallery_1/promote")
    assert r.status_code == 200

    got = client.get(f"{base}/avatar")
    assert got.content == b"new"
    assert got.headers["cache-control"] == "no-cache"
    assert client.get(f"{base}/gallery_1").content == b"old"


def test_character_image_promote_missing_404(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/images/gallery_9/promote")
    assert r.status_code == 404


def test_avatar_focus_endpoint_round_trip(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images"
    # no avatar yet -> 404
    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).status_code == 404
    client.put(f"{base}/avatar", files={"file": ("a.png", io.BytesIO(b"img"), "image/png")})
    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).json() == {"ok": True}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert detail["versions"][0]["avatar_focus"] == 30
    chars = client.get(f"/api/worlds/{wid}/characters").json()
    assert chars[0]["avatar_focus"] == 30
    assert chars[0]["gallery_count"] == 0 and chars[0]["localized_count"] == 0
    # promoting a new image invalidates the crop
    client.put(f"{base}/gallery_1", files={"file": ("g.png", io.BytesIO(b"g"), "image/png")})
    client.post(f"{base}/gallery_1/promote")
    assert client.get(f"/api/worlds/{wid}/characters/{cid}").json()["versions"][0]["avatar_focus"] is None


def test_entity_images_crud_promote_and_has_image(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/locations", json={"name": "Warehouse Nine"}).json()["id"]
    base = f"/api/worlds/{wid}/locations/{eid}/images"

    assert client.get(f"/api/worlds/{wid}/locations").json()[0]["has_image"] is False
    assert client.get(base).json() == []

    r = client.put(f"{base}/avatar", files={"file": ("w.png", io.BytesIO(b"day"), "image/png")})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    client.put(f"{base}/gallery_1", files={"file": ("n.png", io.BytesIO(b"night"), "image/png")})

    assert client.get(f"/api/worlds/{wid}/locations").json()[0]["has_image"] is True
    assert {i["name"] for i in client.get(base).json()} == {"avatar", "gallery_1"}
    assert client.get(f"{base}/avatar").content == b"day"

    assert client.post(f"{base}/gallery_1/promote").status_code == 200
    assert client.get(f"{base}/avatar").content == b"night"
    assert client.get(f"{base}/gallery_1").content == b"day"

    assert client.delete(f"{base}/gallery_1").status_code == 200
    assert client.get(f"{base}/gallery_1").status_code == 404


def test_entity_images_unknown_kind_404(client):
    wid = _world(client)
    assert client.get(f"/api/worlds/{wid}/potions/x/images").status_code == 404
    assert client.put(f"/api/worlds/{wid}/potions/x/images/avatar",
                      files={"file": ("a.png", io.BytesIO(b"x"), "image/png")}).status_code == 404


def test_campaign_entity_images_served(client):
    _, cid = _campaign(client)
    eid = client.post(f"/api/campaigns/{cid}/locations", json={"name": "Crypt"}).json()["id"]
    base = f"/api/campaigns/{cid}/locations/{eid}/images"
    client.put(f"{base}/avatar", files={"file": ("c.png", io.BytesIO(b"img"), "image/png")})
    assert client.get(f"{base}/avatar").content == b"img"
    assert client.get(f"/api/campaigns/{cid}/locations").json()[0]["has_image"] is True


def test_campaign_greeting_image_delete_unknown_kind_404(client):
    # write surface stays entity-only: greetings is not an accepted kind for
    # campaign-scoped image mutation routes either.
    _, cid = _campaign(client)
    gid = client.post(f"/api/campaigns/{cid}/greetings",
                      json={"name": "Opener", "character": "mira", "version": "v1"}).json()["id"]
    assert client.delete(f"/api/campaigns/{cid}/greetings/{gid}/images/embed-x").status_code == 404


def test_greeting_images_served_readonly(client):
    wid = _world(client)
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": "mira", "version": "v1"}).json()["id"]
    # no PUT route for greeting images: store the asset directly
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")

    base = f"/api/worlds/{wid}/greetings/{gid}/images"
    assert [i["name"] for i in client.get(base).json()] == ["embed-abc123def456"]
    r = client.get(f"{base}/embed-abc123def456")
    assert r.status_code == 200 and r.content == b"art"
    assert r.headers["content-type"] == "image/png"

    # write surface stays entity-only: greetings is not an accepted kind
    assert client.put(f"{base}/other",
                      files={"file": ("a.png", io.BytesIO(b"x"), "image/png")}).status_code == 404
    assert client.delete(f"{base}/embed-abc123def456").status_code == 404
    assert client.post(f"{base}/embed-abc123def456/promote").status_code == 404
    # and unknown kinds still 404 on GET
    assert client.get(f"/api/worlds/{wid}/potions/x/images").status_code == 404


def test_image_serving_revalidates_with_etag(client):
    """no-cache without a validator forced a full re-download of every image
    on every view; an mtime+size ETag lets the browser get 304s instead."""
    wid = _world(client)
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": "mira", "version": "v1"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")
    url = f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456"

    r = client.get(url)
    assert r.status_code == 200 and r.content == b"art"
    etag = r.headers["etag"]
    assert r.headers["cache-control"] == "no-cache"

    r304 = client.get(url, headers={"If-None-Match": etag})
    assert r304.status_code == 304 and r304.content == b""

    # promotions swap bytes under a stable URL: the swap must invalidate
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"new-art", "png",
                           base="greetings")
    r2 = client.get(url, headers={"If-None-Match": etag})
    assert r2.status_code == 200 and r2.content == b"new-art"


def test_image_subjects_routes_roundtrip_and_validation(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": cid, "version": "default"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")
    base = f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456/subjects"

    assert client.get(base).json() == {"subjects": []}
    assert client.put(base, json={"subjects": [cid]}).status_code == 200
    assert client.get(base).json() == {"subjects": [cid]}
    assert client.get(f"/api/worlds/{wid}/greetings/{gid}/subjects").json() == {
        "embed-abc123def456": [cid]}

    # validation: unknown cid -> 400; unknown image/greeting -> 404
    assert client.put(base, json={"subjects": ["ghost"]}).status_code == 400
    assert client.put(f"/api/worlds/{wid}/greetings/{gid}/images/nope/subjects",
                      json={"subjects": [cid]}).status_code == 404
    assert client.get(f"/api/worlds/{wid}/greetings/missing/images/x/subjects").status_code == 404


def test_appearances_and_copy_from_greeting(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": cid, "version": "default"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")
    client.put(f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456/subjects",
               json={"subjects": [cid]})

    apps = client.get(f"/api/worlds/{wid}/characters/{cid}/appearances").json()
    assert [(a["gid"], a["greeting_name"], a["name"]) for a in apps] == [
        (gid, "Opener", "embed-abc123def456")]
    assert apps[0]["url"].startswith(
        f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456?v=")

    copy_url = f"/api/worlds/{wid}/characters/{cid}/versions/default/images/copy-from-greeting"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "avatar"})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar").content == b"art"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "gallery"})
    assert r.json()["name"] == "gallery_1"
    assert client.post(copy_url, json={"gid": gid, "name": "missing", "slot": "gallery"}).status_code == 404
    assert client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "banner"}).status_code == 400


def test_untagged_images_route_and_empty_marker(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": cid, "version": "default"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")

    r = client.get(f"/api/worlds/{wid}/subjects/untagged")
    assert [(a["gid"], a["greeting_name"], a["name"]) for a in r.json()] == [
        (gid, "Opener", "embed-abc123def456")]
    assert r.json()[0]["url"].startswith(
        f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456?v=")
    # an explicit [] PUT marks it reviewed and removes it from the queue
    client.put(f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456/subjects",
               json={"subjects": []})
    assert client.get(f"/api/worlds/{wid}/subjects/untagged").json() == []


def test_character_import_garbage_400(client):
    wid = _world(client)
    files = {"file": ("c.json", io.BytesIO(b"nonsense"), "application/json")}
    r = client.post(f"/api/worlds/{wid}/characters/import", files=files, data={"format": "json"})
    assert r.status_code == 400


def test_chub_import_route(client, monkeypatch):
    from grimoire.store import cards, chub

    wid = _world(client)
    png = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                        "data": {"name": "Imp", "extensions": {}}}, "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = client.post(f"/api/worlds/{wid}/characters/import/chub",
                     json={"url": "https://chub.ai/characters/creator/imp"})
    assert r.status_code == 200
    body = r.json()
    assert body["character"] and body["version"]
    assert body["updated"] is False
    assert body["gallery"] == {"attempted": 0, "stored": 0}
    assert body["lore"] == {"lorebooks_found": 0, "created": []}


def test_chub_import_route_updates_in_place_when_already_linked(client, monkeypatch):
    from grimoire.store import cards, chub

    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source", json={"url": "creator/abelha"})

    png = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                        "data": {"name": "Abelha Updated", "extensions": {}}}, "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/abelha/chara_card_v2.png",
    })
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = client.post(f"/api/worlds/{wid}/characters/import/chub",
                     json={"url": "creator/abelha", "into": cid, "into_version": "default"})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "character": cid, "version": "default", "updated": True,
        "gallery": {"attempted": 0, "stored": 0},
        "lore": {"lorebooks_found": 0, "created": []},
    }
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert [v["id"] for v in detail["versions"]] == ["default"]  # no new version
    assert detail["versions"][0]["card"]["data"]["name"] == "Abelha Updated"


def test_chub_import_route_direct_url_not_chub(client, monkeypatch):
    from grimoire.store import cards

    wid = _world(client)
    png = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                        "data": {"name": "Direct", "extensions": {}}}, "png")
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = client.post(f"/api/worlds/{wid}/characters/import/chub",
                     json={"url": "https://example.com/card.png"})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] is False
    assert body["gallery"] == {"attempted": 0, "stored": 0}
    assert body["lore"] == {"lorebooks_found": 0, "created": []}

    detail = client.get(f"/api/worlds/{wid}/characters/{body['character']}").json()
    assert detail["versions"][0]["card"]["data"]["name"] == "Direct"
    assert detail["versions"][0]["chub_source"] == "https://example.com/card.png"
    assert detail["versions"][0]["is_chub"] is False


def test_chub_import_route_bad_url(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/characters/import/chub", json={"url": "not a url"})
    assert r.status_code == 400


def test_chub_import_route_unreachable(client, monkeypatch):
    from grimoire.store import chub

    wid = _world(client)
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: None)
    r = client.post(f"/api/worlds/{wid}/characters/import/chub", json={"url": "creator/missing"})
    assert r.status_code == 404


def _version_chub_source(detail, vid):
    return next(v for v in detail["versions"] if v["id"] == vid)["chub_source"]


def test_chub_source_routes(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]

    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == ""

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                     json={"url": "creator/slug"})
    assert r.status_code == 200 and r.json() == {"chub_source": "https://chub.ai/characters/creator/slug"}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == "https://chub.ai/characters/creator/slug"

    r = client.delete(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source")
    assert r.status_code == 200 and r.json() == {"chub_source": ""}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == ""


def test_chub_source_is_per_version_route(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions",
                json={"name": "futa", "card": {"spec": "chara_card_v3", "spec_version": "3.0",
                                               "data": {"name": "Abelha", "extensions": {}}}})
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                json={"url": "creator/abelha-main"})

    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == "https://chub.ai/characters/creator/abelha-main"
    assert _version_chub_source(detail, "futa") == ""  # sibling version untouched


def test_chub_source_route_accepts_an_arbitrary_url(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Direct"}).json()["character"]

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                     json={"url": "https://example.com/cards/direct.png"})
    assert r.status_code == 200
    assert r.json() == {"chub_source": "https://example.com/cards/direct.png"}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == "default")
    assert version["chub_source"] == "https://example.com/cards/direct.png"
    assert version["is_chub"] is False


def test_chub_source_route_bad_url(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                     json={"url": "not a url"})
    assert r.status_code == 400


def test_chub_source_route_unknown_character_or_version(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]

    r = client.post(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-source",
                     json={"url": "creator/slug"})
    assert r.status_code == 404
    r = client.delete(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-source")
    assert r.status_code == 404

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-source",
                     json={"url": "creator/slug"})
    assert r.status_code == 404
    r = client.delete(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-source")
    assert r.status_code == 404


def test_chub_gallery_and_lorebooks_routes(client, monkeypatch):
    from grimoire.store import chub

    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                json={"url": "creator/abelha"})

    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": True, "related_lorebooks": [7],
    })
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg"])
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (b"\xff\xd8\xffJPEGDATA", "image/jpeg"))
    monkeypatch.setattr(chub, "fetch_lorebook_node", lambda lid: {
        "definition": {"embedded_lorebook": {"entries": [{"keys": ["k"], "content": "x"}]}},
    })

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-gallery")
    assert r.status_code == 200
    events = [json.loads(line[len("data:"):].strip())
              for line in r.text.splitlines() if line.startswith("data:")]
    assert events == [
        {"total": 1}, {"done": 1, "total": 1}, {"summary": {"attempted": 1, "stored": 1}},
    ]

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-lorebooks")
    assert r.status_code == 200
    body = r.json()
    assert body["lorebooks_found"] == 1 and len(body["created"]) == 1


def test_chub_gallery_and_lorebooks_routes_require_a_link(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-gallery")
    assert r.status_code == 404
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-lorebooks")
    assert r.status_code == 404


def test_chub_gallery_and_lorebooks_routes_unknown_character_or_version(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]

    assert client.post(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-gallery").status_code == 404
    assert client.post(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-lorebooks").status_code == 404
    assert client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-gallery").status_code == 404
    assert client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-lorebooks").status_code == 404


def test_chub_unlinked_route(client):
    wid = _world(client)
    assert client.get(f"/api/worlds/{wid}/characters/chub-unlinked").json() == {"versions": []}

    linked = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{linked}/versions/default/chub-source",
                json={"url": "creator/abelha"})
    unlinked = client.post(f"/api/worlds/{wid}/characters", json={"name": "Loose End"}).json()["character"]

    r = client.get(f"/api/worlds/{wid}/characters/chub-unlinked")
    assert r.status_code == 200
    assert r.json() == {"versions": [
        {"character": unlinked, "character_name": "Loose End", "version": "default", "version_name": "Loose End"},
    ]}


def test_character_book_import_route(client):
    wid = _world(client)
    card = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {
        "name": "Sera",
        "character_book": {"entries": [{"keys": ["pact"], "content": "the salt pact", "name": "Pact"}]},
        "extensions": {},
    }}
    cid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Sera", "card": card}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/lorebook/import")
    assert r.status_code == 200
    created = r.json()["created"]
    assert len(created) == 1 and created[0]["kind"] == "lore"
    assert any(e["id"] == created[0]["id"] for e in client.get(f"/api/worlds/{wid}/lore").json())


def test_character_book_import_empty(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/lorebook/import")
    assert r.status_code == 200 and r.json() == {"created": []}


def test_world_tag_vocabulary_crud(client):
    wid = _world(client)
    tid = client.post(f"/api/worlds/{wid}/tags", json={"name": "Student"}).json()["id"]
    assert tid == "student"
    assert client.get(f"/api/worlds/{wid}/tags").json() == {"student": "Student"}
    client.put(f"/api/worlds/{wid}/tags/{tid}", json={"name": "Pupil"})
    assert client.get(f"/api/worlds/{wid}/tags").json() == {"student": "Pupil"}
    assert client.delete(f"/api/worlds/{wid}/tags/{tid}").status_code == 200
    assert client.get(f"/api/worlds/{wid}/tags").json() == {}
    assert client.put(f"/api/worlds/{wid}/tags/ghost", json={"name": "X"}).status_code == 404


def test_campaign_local_pc_create_seat_and_sync(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # create a campaign-local PC (overlay; not written to the world)
    r = client.post(f"/api/campaigns/{cid}/pcs", json={
        "name": "Mara", "tags": ["rebel"],
        "persona": {"name": "Mara", "pronouns": "she/her", "summary": "outlaw", "description": "On the run."}})
    assert r.status_code == 200
    assert r.json()["pc"] == "mara"
    # lists at campaign scope, absent at world scope
    assert [p["id"] for p in client.get(f"/api/campaigns/{cid}/pcs").json()] == ["mara"]
    assert client.get(f"/api/worlds/{wid}/pcs").json() == []
    # seat as player with explicit version
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": "mara", "version": "default"}).status_code == 200
    assert {"kind": "pcs", "id": "mara", "role": "player", "name": "Mara"} in \
        client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    # re-seat in a second scene with version omitted -> resolved from the campaign
    sid2 = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S2"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid2}/cast",
                       json={"kind": "pcs", "id": "mara"}).status_code == 200
    # no spurious incoming sync change for the local PC
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []


def test_scene_location_set_get_and_move(client):
    wid, cid = _campaign(client)
    a = client.post(f"/api/campaigns/{cid}/locations",
                    json={"name": "Salt Cathedral", "body": "A drowned basilica."}).json()["id"]
    b = client.post(f"/api/campaigns/{cid}/locations",
                    json={"name": "Drowned Market", "body": "Shallow stalls."}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # first set: silent
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location", json={"location": a}).json() == \
        {"ok": True, "moved": False, "name": "Salt Cathedral"}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == []
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/location").json() == \
        {"current": {"id": a, "name": "Salt Cathedral"}, "visited": []}
    # move: announces
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location", json={"location": b}).json() == \
        {"ok": True, "moved": True, "name": "Drowned Market"}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == [
        {"role": "assistant", "content": "*The scene moves to Drowned Market.*"}]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/location").json() == \
        {"current": {"id": b, "name": "Drowned Market"}, "visited": [{"id": a, "name": "Salt Cathedral"}]}


def test_scene_location_unknown_404(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location",
                      json={"location": "nope"}).status_code == 404


def test_scene_context_breakdown(client):
    wid, cid = _campaign(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert body["model"]
    labels = [s["label"] for s in body["sections"]]
    assert "Character descriptions" in labels
    assert all(s["tokens"] > 0 for s in body["sections"])
    assert body["total_tokens"] == sum(s["tokens"] for s in body["sections"])


def test_edit_message_route(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "sk-test"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "helo"})
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0", json={"content": "hello"}).json() == {"ok": True}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"][0]["content"] == "hello"
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/9", json={"content": "x"}).status_code == 400


def test_edit_message_route_resolves_roll_macro_once(client):
    # #137 regression: an edit can introduce a macro too -- it must resolve
    # once at edit time, not re-roll on every later context rebuild.
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "sk-test"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "helo"})
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0",
              json={"content": "I roll {{roll:1d20}} to hit."})
    content = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"][0]["content"]
    assert "{{roll" not in content


def test_edit_message_route_refuses_a_manual_roll_line(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "2d6"})
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0", json={"content": "9001"})
    assert r.status_code == 400
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert "9001" not in msgs[0]["content"]


def test_cast_detail_for_character_and_pc(client):
    wid, cid = _campaign(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.",
                     "personality": "cold", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    client.post(f"/api/campaigns/{cid}/pcs", json={
        "name": "Mara", "persona": {"name": "Mara", "pronouns": "she/her", "summary": "outlaw", "description": "On the run."}})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "pcs", "id": "mara", "version": "default"})

    c = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/seraphine").json()
    assert c["name"] == "Seraphine" and "Drowned King" in c["body"] and "cold" in c["body"]
    p = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/pcs/mara").json()
    assert p["name"] == "Mara" and "On the run." in p["body"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/ghost").status_code == 404


def test_world_pc_crud_and_tag_validation(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/tags", json={"name": "Student"})
    r = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara", "tags": ["student"]})
    pid = r.json()["pc"]
    assert pid == "elara"
    assert client.get(f"/api/worlds/{wid}/pcs/{pid}").json()["meta"]["tags"] == ["student"]
    assert client.get(f"/api/worlds/{wid}").json()["counts"]["pcs"] == 1
    assert client.post(f"/api/worlds/{wid}/pcs", json={"name": "Rook", "tags": ["ghost"]}).status_code == 400
    assert client.delete(f"/api/worlds/{wid}/pcs/{pid}").status_code == 200


def test_entity_keys_via_routes(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/lore", json={"name": "Salt Pact", "body": "p", "keys": "pact"}).json()["id"]
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]["keys"] == "pact"
    client.put(f"/api/worlds/{wid}/lore/{eid}", json={"keys": "pact, salt"})
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]["keys"] == "pact, salt"


def test_unknown_kind_404(client):
    wid = _world(client)
    assert client.get(f"/api/worlds/{wid}/weapons").status_code == 404


# ---- campaigns ----
def test_campaign_create_copies_world(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Drowned Library", "body": "Keeper"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/locations/drowned-library").json()["body"].strip() == "Keeper"


def test_campaign_missing_world_400(client):
    assert client.post("/api/campaigns", json={"name": "X", "world": "nope"}).status_code == 400


# ---- sync ----
def test_incoming_and_accept_flow(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore", json={"name": "Salt Pact", "body": "pact"})
    # a brand-new world record was never materialized into the campaign: it
    # reads through live, no incoming item to accept
    pend = client.get(f"/api/campaigns/{cid}/incoming").json()
    assert pend == []
    assert client.get(f"/api/campaigns/{cid}/lore/salt-pact").json()["body"].strip() == "pact"
    # once the campaign edits its own copy (materializing it) and the world
    # changes again, that diverges into a real incoming conflict; accept takes
    # the world's version by reverting the campaign copy to inherited
    client.put(f"/api/campaigns/{cid}/lore/salt-pact", json={"body": "campaign edit"})
    client.put(f"/api/worlds/{wid}/lore/salt-pact", json={"body": "world edit"})
    pend = client.get(f"/api/campaigns/{cid}/incoming").json()
    assert [p["status"] for p in pend] == ["conflict"]
    client.post(f"/api/campaigns/{cid}/incoming/accept", json={"refs": [{"kind": "lore", "id": "salt-pact"}]})
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
    assert client.get(f"/api/campaigns/{cid}/lore/salt-pact").json()["body"].strip() == "world edit"


def test_reject_flow(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore", json={"name": "Pact", "body": "p"})
    client.post(f"/api/campaigns/{cid}/incoming/reject", json={"refs": [{"kind": "lore", "id": "pact"}]})
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
    # reject only clears the pending marker -- it does not tombstone, so the
    # world's still-live record continues to read through the campaign overlay
    assert client.get(f"/api/campaigns/{cid}/lore/pact").json()["body"].strip() == "p"


def test_world_push_view(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/locations", json={"name": "A", "body": "a"})
    # a brand-new world record is inherited (live), so it never shows as pending
    rows = client.get(f"/api/worlds/{wid}/campaigns").json()
    assert rows == [{"id": cid, "name": "Run", "pending": {"new": 0, "update": 0, "conflict": 0}}]
    # materializing the campaign's own copy (a no-op edit) and then editing the
    # world again produces a real pending update
    client.put(f"/api/campaigns/{cid}/locations/a", json={"body": "a"})
    client.put(f"/api/worlds/{wid}/locations/a", json={"body": "a2"})
    rows = client.get(f"/api/worlds/{wid}/campaigns").json()
    assert rows == [{"id": cid, "name": "Run", "pending": {"new": 0, "update": 1, "conflict": 0}}]


# ---- cast & suggestions ----
def test_cast_and_suggestions_flow(client):
    wid = _world(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Drowned King"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Docks"}).json()["id"]
    # manual appearance
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "seraphine", "version": "default"}).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json() == [
        {"kind": "characters", "id": "seraphine", "role": "npc", "name": "Seraphine"}]
    # suggestion surfaces drowned-king, then dismiss hides it
    sugg = client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json()
    assert [s["character"] for s in sugg] == ["drowned-king"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/suggestions/dismiss", json={"character": "drowned-king"})
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json() == []


def test_cast_pc_and_character_as_player(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Desmond"})
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # a PC casts as player automatically
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": "elara"}).status_code == 200
    # a character cast explicitly as player
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "desmond", "role": "player"}).status_code == 200
    cast = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    assert {"kind": "pcs", "id": "elara", "role": "player", "name": "Elara"} in cast
    assert {"kind": "characters", "id": "desmond", "role": "player", "name": "Desmond"} in cast
    roster = client.get(f"/api/campaigns/{cid}/appearances").json()
    assert {r["kind"] for r in roster} == {"pcs", "characters"}


def test_recasting_with_different_role_or_version_409(client):
    wid = _world(client)
    got = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    cid = got["character"]
    # add a second version so a version-mismatch is possible
    base = client.get(f"/api/worlds/{wid}/characters/{cid}").json()["versions"][0]["card"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions", json={"name": "Alt", "card": base})
    camp = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{camp}/scenes", json={"title": "S"}).json()["id"]
    # first appearance locks version=default, role=npc
    client.post(f"/api/campaigns/{camp}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    # re-cast as player -> role lock conflict
    assert client.post(f"/api/campaigns/{camp}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "seraphine", "role": "player"}).status_code == 409
    # re-cast with a different version -> version lock conflict
    assert client.post(f"/api/campaigns/{camp}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "seraphine", "version": "alt"}).status_code == 409


def test_cast_supplied_version_purged_campaign_side_404(client):
    """A cast naming a version the campaign has purged from a materialized (but
    unlocked) actor must 404 rather than revive it from the world via the lock."""
    wid = _world(client)
    ch = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()["character"]
    base = client.get(f"/api/worlds/{wid}/characters/{ch}").json()["versions"][0]["card"]
    client.post(f"/api/worlds/{wid}/characters/{ch}/versions", json={"name": "Alt", "card": base})
    camp = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{camp}/scenes", json={"title": "S"}).json()["id"]
    # materialize the actor campaign-side and delete the alt version
    assert client.delete(f"/api/campaigns/{camp}/characters/{ch}/versions/alt").status_code == 200
    # casting the purged version must 404, not resurrect it
    assert client.post(f"/api/campaigns/{camp}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": ch, "version": "alt"}).status_code == 404
    versions = {v["id"] for v in client.get(f"/api/campaigns/{camp}/characters/{ch}").json()["versions"]}
    assert "alt" not in versions   # not revived from the world


class CapturingOpenRouter:
    def __init__(self):
        self.messages = None

    async def stream(self, messages, cfg):
        self.messages = messages
        for d in ["ok"]:
            yield d


def test_chat_injects_system_message(client):
    wid = _world(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "the drowned keeper", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})

    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap

    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hello"}) as r:
        for _ in r.iter_lines():
            pass
    assert cap.messages[0]["role"] == "system"
    assert "the drowned keeper" in cap.messages[0]["content"]
    assert cap.messages[-1] == {"role": "user", "content": "hello"}


# ---- scenes (re-homed chat) ----
def test_chat_missing_key_returns_409(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"


def test_chat_missing_key_ok_for_claude_provider(client):
    # No openrouter_key is set, but the claude provider doesn't need one — the
    # 409 missing_key guard must be skipped. The `client` fixture already
    # overrides routes.get_llm with a FakeOpenRouter, standing in for whatever
    # provider is configured.
    client.put("/api/config", json={"provider": "claude"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert resp.status_code == 200


def test_chat_streams_and_persists(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert resp.status_code == 200
    assert 'data: {"delta": "Hel"}' in resp.text
    assert 'data: {"done": true}' in resp.text
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1] == {"role": "assistant", "content": "Hello"}


def test_chat_resolves_roll_macro_once_and_persists_stably(client):
    # #137 regression: a {{roll:}}/{{random:}} macro in a sent message must
    # resolve ONCE, at persist time -- not get re-rolled every time the
    # context is rebuilt from the (now historical) stored message.
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "I roll {{roll:1d20}} to hit."})
    assert resp.status_code == 200
    stored = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    user_msg = next(m for m in stored if m["role"] == "user")
    assert "{{roll" not in user_msg["content"]  # resolved at write time, not left raw

    def _user_line(msgs):
        return next(m["content"] for m in msgs if m["role"] == "user")

    first = _user_line(store.context.build_messages(cid, sid))
    second = _user_line(store.context.build_messages(cid, sid))
    assert first == second == user_msg["content"]


def test_llm_reply_resolves_roll_macro_once_and_persists_stably(client):
    # Same #137 regression, on the reply side: _persist_reply must resolve
    # macros before writing the model's narration to history.
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(
        ["The die shows {{roll:1d20}}."])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "roll"})
    assert resp.status_code == 200
    stored = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    reply = next(m for m in stored if m["role"] == "assistant")
    assert "{{roll" not in reply["content"]


def test_retry_regenerates_without_adding_a_user_turn(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_regenerate_replaces_the_last_assistant_post(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "old reply")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "Hello"}]


def test_regenerate_excludes_the_dropped_post_from_the_prompt(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "old reply")
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/regenerate") as r:
        for _ in r.iter_lines():
            pass
    assert cap.messages[-1] == {"role": "user", "content": "hi"}


def test_regenerate_with_guidance_appends_a_system_steer(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "old reply")
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/regenerate",
                       json={"guidance": "make her angrier"}) as r:
        for _ in r.iter_lines():
            pass
    assert cap.messages[-1] == {
        "role": "system",
        "content": "Regenerate your previous reply. Guidance from the player: make her angrier",
    }
    # the dropped assistant reply still isn't in the prompt
    assert {"role": "assistant", "content": "old reply"} not in cap.messages
    # and the guidance is transient — not in the stored transcript
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert all("make her angrier" not in m["content"] for m in msgs)


def test_regenerate_after_a_failed_turn_behaves_like_retry(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_regenerate_empty_scene_returns_400(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate").status_code == 400


def test_regenerate_sole_opening_post_returns_400(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "assistant", "the greeting")
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate").status_code == 400
    # the opening post is untouched
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "assistant", "content": "the greeting"}]


def test_regenerate_past_a_trailing_roll_returns_400(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "a reply")
    store.scenes.append_message(cid, sid, "assistant", "\U0001F3B2 2d6 = 7",
                                 speaker=store.scenes.ROLL_SPEAKER)
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 400
    # the reply and the roll line both survive the failed regenerate attempt
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert len(msgs) == 3


def test_regenerate_missing_key_returns_409(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"


def test_scene_rename_and_delete(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Old"}).json()["id"]
    new_id = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "New Name"}).json()["id"]
    assert new_id.endswith("new-name")
    assert client.put(f"/api/campaigns/{cid}/scenes/{new_id}", json={"title": "  "}).status_code == 400
    assert client.delete(f"/api/campaigns/{cid}/scenes/{new_id}").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_scene_missing_404(client):
    _wid, cid = _campaign(client)
    assert client.delete(f"/api/campaigns/{cid}/scenes/nope").status_code == 404


# ---- greetings & plot maps (2b) ----
def test_greeting_present_cast_roundtrips_over_api(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Rowan"})
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Arrival: Mara & Rowan", "character": "mara",
                            "version": "default", "body": "Mara and Rowan.",
                            "present": ["mara", "rowan"]}).json()["id"]
    read = client.get(f"/api/worlds/{wid}/greetings/{gid}").json()
    assert read["meta"]["present"] == ["mara", "rowan"]


def test_greeting_crud_import_edges_and_start(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    got = client.get(f"/api/worlds/{wid}/characters/seraphine").json()
    card = got["versions"][0]["card"]
    card["data"]["first_mes"] = "You meet Seraphine."
    client.put(f"/api/worlds/{wid}/characters/seraphine/versions/default", json={"card": card})
    imported = client.post(f"/api/worlds/{wid}/greetings/import",
                           json={"character": "seraphine", "version": "default"}).json()["greetings"]
    assert len(imported) == 1
    g2 = client.post(f"/api/worlds/{wid}/greetings",
                     json={"name": "Reckoning", "character": "seraphine", "version": "default",
                           "body": "It ends here."}).json()["id"]
    client.put(f"/api/worlds/{wid}/greetings/{imported[0]}/edges", json={"leads_to": [g2]})
    read = client.get(f"/api/worlds/{wid}/greetings/{imported[0]}").json()
    assert read["meta"]["character"] == "seraphine"
    assert read["edges"]["leads_to"] == [g2]   # edges surfaced on read for the editor
    # the target greeting surfaces its predecessors (what unlocks it) for the view sidebar
    assert client.get(f"/api/worlds/{wid}/greetings/{g2}").json()["predecessors"] == [imported[0]]
    assert [x["id"] for x in client.get(f"/api/worlds/{wid}/greetings").json()] == sorted([imported[0], g2])

    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    avail = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert {x["id"]: x["available"] for x in avail}[imported[0]] is True
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": imported[0]})
    assert r.status_code == 200
    sid = r.json()["id"]
    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["messages"][0]["content"] == "You meet Seraphine."
    # starting again on a non-empty scene -> 409
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g2}).status_code == 409


def test_start_from_greeting_unknown_404(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": "nope"}).status_code == 404


def test_start_from_greeting_retitles_scene(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "A Chance Meeting", "character": "vex", "version": ver,
        "body": "Hi."}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": g})
    assert r.status_code == 200
    new_sid = r.json()["id"]
    assert new_sid != sid and "a-chance-meeting" in new_sid
    scene = client.get(f"/api/campaigns/{cid}/scenes/{new_sid}").json()
    assert scene["meta"]["title"] == "A Chance Meeting"


def test_available_greetings_after_param(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    g1 = client.post(f"/api/worlds/{wid}/greetings",
                     json={"name": "Alpha", "character": "seraphine", "version": "default",
                           "body": "A."}).json()["id"]
    g2 = client.post(f"/api/worlds/{wid}/greetings",
                     json={"name": "Reckoning", "character": "seraphine", "version": "default",
                           "body": "R."}).json()["id"]
    client.put(f"/api/worlds/{wid}/greetings/{g1}/edges", json={"leads_to": [g2]})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                      json={"greeting": g1}).json()["id"]
    avail = client.get(f"/api/campaigns/{cid}/greetings/available", params={"after": sid}).json()
    assert avail[0]["id"] == g2 and avail[0]["unlocked"] is True
    # no param: same shape, nothing flagged
    plain = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert all(x["unlocked"] is False for x in plain)
    assert client.get(f"/api/campaigns/{cid}/greetings/available",
                      params={"after": "nope"}).status_code == 404


def test_opener_streams_without_persisting(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "Begin in a tavern."}) as r:
        body = "".join(r.iter_text())
    assert "Hel" in body and "lo" in body and '"done": true' in body
    # ephemeral: the scene is untouched
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == []


def test_opener_requires_key(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "x"}).status_code == 409


def test_first_post_adopts_opener_onto_empty_scene(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                       json={"text": "  Mist rolls in.  "}).status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "assistant", "content": "Mist rolls in."}]
    # a second first-post is refused once the scene has messages
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                       json={"text": "again"}).status_code == 409
    # empty text is rejected on a fresh scene
    sid2 = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid2}/first-post",
                       json={"text": "   "}).status_code == 400


# ---- lorebook / world-info import (2c) ----
def test_lorebook_parse_then_import(client):
    wid = _world(client)
    book = {"entries": {
        "0": {"key": ["pact"], "comment": "Salt Pact", "content": "It binds."},
        "1": {"key": ["docks"], "comment": "The Docks", "content": "Wet planks."},
    }}
    files = {"file": ("wi.json", io.BytesIO(json.dumps(book).encode()), "application/json")}
    parsed = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files,
                         data={"format": "lorebook"})
    assert parsed.status_code == 200
    entries = parsed.json()["entries"]
    assert {e["name"] for e in entries} == {"Salt Pact", "The Docks"}
    # parse writes nothing
    assert client.get(f"/api/worlds/{wid}/lore").json() == []

    # route the docks entry to locations, keep the other as lore, then commit
    for e in entries:
        if e["name"] == "The Docks":
            e["category"] = "locations"
    created = client.post(f"/api/worlds/{wid}/lorebook/import", json={"entries": entries})
    assert created.status_code == 200
    kinds = {c["kind"] for c in created.json()["created"]}
    assert kinds == {"lore", "locations"}
    assert [e["name"] for e in client.get(f"/api/worlds/{wid}/lore").json()] == ["Salt Pact"]
    assert [e["name"] for e in client.get(f"/api/worlds/{wid}/locations").json()] == ["The Docks"]


def test_lorebook_parse_bad_file_400(client):
    wid = _world(client)
    files = {"file": ("x.json", io.BytesIO(b"not json"), "application/json")}
    r = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files, data={"format": "lorebook"})
    assert r.status_code == 400


def test_lorebook_import_unknown_category_400(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/lorebook/import",
                    json={"entries": [{"name": "X", "keys": [], "body": "y", "category": "bogus"}]})
    assert r.status_code == 400


def test_lorebook_imported_key_activates_in_builder(client):
    # end-to-end sanity: an imported keyed entry feeds the context builder
    wid = _world(client)
    book = {"entries": {"0": {"key": ["leviathan"], "comment": "Leviathan", "content": "the beast"}}}
    files = {"file": ("wi.json", io.BytesIO(json.dumps(book).encode()), "application/json")}
    entries = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files,
                          data={"format": "lorebook"}).json()["entries"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    # commit into the CAMPAIGN root via the store (campaign-scoped lore the builder reads)
    import grimoire.store as store
    store.lorebook.commit(store.campaigns.campaign_root(cid), entries)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "the leviathan rises")
    msgs = store.context.build_messages(cid, sid)
    assert any("the beast" in m["content"] for m in msgs if m["role"] == "system")


def test_localize_endpoint_streams_and_rewrites(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    card = {
        "spec": "chara_card_v3", "spec_version": "3.0",
        "data": {"name": "Img", "description": "![a](https://h/a.png)",
                 "alternate_greetings": []},
    }
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    cid, vid = r.json()["character"], r.json()["version"]

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    monkeypatch.setattr(store.fetch, "download_url", lambda url: (png, "png"))

    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    assert resp.status_code == 200
    events = [json.loads(line[len("data:"):].strip())
              for line in resp.text.splitlines() if line.startswith("data:")]
    assert events[0] == {"total": 1}
    assert events[-1]["summary"]["localized"] == 1

    exported = client.get(
        f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/export?format=json").json()
    assert "/api/worlds/" in exported["data"]["description"]


def test_localize_endpoint_no_refs_short_circuits(client):
    wid = client.post("/api/worlds", json={"name": "W2"}).json()["id"]
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Plain", "description": "no images", "alternate_greetings": []}}
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    cid, vid = r.json()["character"], r.json()["version"]
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[0] == {"total": 0}
    assert events[-1]["summary"]["total"] == 0


def test_localize_endpoint_404_for_missing_character(client):
    wid = client.post("/api/worlds", json={"name": "W3"}).json()["id"]
    resp = client.post(f"/api/worlds/{wid}/characters/ghost/versions/v/localize")
    assert resp.status_code == 404


def test_localize_endpoint_404_for_missing_version(client):
    wid = client.post("/api/worlds", json={"name": "W4"}).json()["id"]
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Real"}).json()["character"]
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghostver/localize")
    assert resp.status_code == 404


def _import_card(client, wid, description):
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "X", "description": description, "alternate_greetings": []}}
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    return r.json()["character"], r.json()["version"]


def test_localize_endpoint_emits_error_frame_on_generator_failure(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "WE"}).json()["id"]
    cid, vid = _import_card(client, wid, "![a](https://h/a.png)")

    def boom(*a, **k):
        yield {"total": 1}
        raise RuntimeError("kaboom")

    monkeypatch.setattr(store.localize, "localize_card", boom)
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[0] == {"total": 1}
    assert events[-1]["error"]["kind"] == "localize"


def test_localize_endpoint_persists_partial_rewrites_on_mid_stream_failure(client, monkeypatch):
    # If the stream dies after some fields were already rewritten, those
    # rewrites must still land on disk — not be silently discarded.
    wid = client.post("/api/worlds", json={"name": "WP"}).json()["id"]
    cid, vid = _import_card(client, wid, "![a](https://h/a.png)")

    def partial(card, *a, **k):
        yield {"total": 2}
        card["data"]["description"] = "![a](/api/worlds/w/local)"
        yield {"done": 1, "total": 2, "applied": 1}
        raise RuntimeError("kaboom")

    monkeypatch.setattr(store.localize, "localize_card", partial)
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[-1]["error"]["kind"] == "localize"

    exported = client.get(
        f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/export?format=json").json()
    assert exported["data"]["description"] == "![a](/api/worlds/w/local)"


def test_localize_endpoint_does_not_persist_when_nothing_localized(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "WN"}).json()["id"]
    cid, vid = _import_card(client, wid, "see https://h/page now")  # a non-image link
    monkeypatch.setattr(store.fetch, "download_url", lambda url: None)  # never an image

    calls = []
    monkeypatch.setattr(store.characters, "update_version",
                        lambda *a, **k: calls.append(a))

    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[-1]["summary"]["localized"] == 0
    assert events[-1]["summary"]["skipped"] == 1
    assert calls == []  # changed-gating skipped the write


class FakeOpenRouterComplete:
    """A completer whose reply can be a single string (existing single-call
    tests) or a list consumed one-per-call, in order (multi-step flows, e.g.
    absorb's extraction complete() followed by the audit's complete()).
    `calls` counts total complete()/stream() invocations."""
    def __init__(self, text):
        self._texts = text if isinstance(text, list) else [text]
        self.calls = 0

    def _next(self) -> str:
        i = min(self.calls, len(self._texts) - 1)
        self.calls += 1
        return self._texts[i]

    async def stream(self, messages, cfg):
        yield self._next()

    async def complete(self, messages, cfg):
        return self._next()


def _world_char(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Aese", "version_name": "main"}).json()["character"]
    return wid, cid


def test_get_tagline_absent_is_empty(client):
    wid, cid = _world_char(client)
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": ""}


def test_put_tagline_saves(client):
    wid, cid = _world_char(client)
    r = client.put(f"/api/worlds/{wid}/characters/{cid}/tagline",
                   json={"tagline": "A snowleopardgirl."})
    assert r.json() == {"ok": True}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": "A snowleopardgirl."}


def test_post_tagline_generate_from_model(client):
    wid, cid = _world_char(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("A silent snowleopardgirl.\nignored second line")
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/tagline/generate")
    assert r.status_code == 200
    assert r.json() == {"tagline": "A silent snowleopardgirl."}
    # preview only: generate does not persist until the caller saves via PUT
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": ""}


def test_post_tagline_generate_requires_key(client):
    wid, cid = _world_char(client)
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/tagline/generate")
    assert r.status_code == 409


# ---- scene calendar ----
def test_campaign_create_writes_calendar_with_region(client):
    from grimoire.store import campaigns, calendars
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid, "region": "GB"}).json()["id"]
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    assert cfg["primary"]["region"] == "GB"


def test_campaign_create_defaults_region_us(client):
    from grimoire.store import campaigns, calendars
    _wid, cid = _campaign(client)
    assert calendars.read_calendar(campaigns.campaign_root(cid))["primary"]["region"] == "US"


def test_create_campaign_with_calendar_provider(client):
    wid = _world(client, "Faerun")
    cid = client.post("/api/campaigns",
                      json={"name": "FR", "world": wid, "calendar": "hebrew"}).json()["id"]
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    assert cfg["primary"]["provider"] == "hebrew"
    assert cfg["confirmed"] is True
    r = client.post("/api/campaigns", json={"name": "X", "world": wid, "calendar": "bogus"})
    assert r.status_code == 400
    names = [c["name"] for c in client.get("/api/campaigns").json()]
    assert "X" not in names


def test_datetime_get_put_roundtrip(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["current"] is None
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-12-25"})
    assert r.json() == {"ok": True, "advanced": False, "friendly": "25 December 2026",
                        "id": "001--2026-12-25--s"}
    sid = r.json()["id"]  # first date set renames the scene
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["current"]["native"] == "2026-12-25"
    assert got["current"]["weekday"] == "Friday"
    assert "Christmas Day" in got["current"]["holidays_today"]
    assert got["history"] == ["2026-12-25"]


def test_datetime_put_bad_date_is_400(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-13-40"})
    assert r.status_code == 400


def test_character_birthdate_route_sets_meta(client):
    wid = _world(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()["character"]
    r = client.put(f"/api/worlds/{wid}/characters/{chid}/birthdate", json={"birthdate": "1985-03-14"})
    assert r.json() == {"ok": True}
    assert client.get(f"/api/worlds/{wid}/characters/{chid}").json()["meta"]["birthdate"] == "1985-03-14"


def test_datetime_get_includes_cast_age(client):
    wid = _world(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()["character"]
    client.put(f"/api/worlds/{wid}/characters/{chid}/birthdate", json={"birthdate": "1990-12-25"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-12-25"})
    sid = r.json()["id"]  # first date set renames the scene
    cast = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["current"]["cast"]
    assert cast == [{"kind": "characters", "id": chid, "name": "Seraphine",
                     "age": 36, "birthday_today": True}]


def test_calendar_config_get_put(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["primary"]["region"] == "US"
    cfg = {"primary": {"provider": "gregorian", "region": "GB",
            "custom_holidays": [{"name": "Founding Day", "month": 4, "day": 12}], "anchor": None},
           "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).json() == {"ok": True}
    got = client.get(f"/api/campaigns/{cid}/calendar").json()
    assert got["primary"]["region"] == "GB"
    assert got["primary"]["custom_holidays"][0]["name"] == "Founding Day"


def test_calendar_config_confirmed_flag(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["confirmed"] is False
    cfg = {"primary": {"provider": "gregorian", "region": "US", "custom_holidays": [], "anchor": None},
           "secondary": None, "confirmed": True}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).json() == {"ok": True}
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["confirmed"] is True


def test_calendar_config_rejects_malformed_custom_holiday(client):
    _wid, cid = _campaign(client)
    bad = {"primary": {"provider": "gregorian", "region": "US",
            "custom_holidays": [{"name": "Oops", "month": 13}], "anchor": None}, "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=bad).status_code == 400
    nameless = {"primary": {"provider": "gregorian", "region": "US",
            "custom_holidays": [{"month": 4, "day": 12}], "anchor": None}, "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=nameless).status_code == 400
    bogus = {"primary": {"provider": "bogus", "region": "US", "custom_holidays": [], "anchor": None},
             "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=bogus).status_code == 400


def test_calendar_months_campaign_and_world(client):
    wid, cid = _campaign(client)
    # default gregorian
    r = client.get(f"/api/campaigns/{cid}/calendar/months", params={"year": 2024})
    assert r.status_code == 200
    assert r.json()["months"][1] == {"key": "02", "name": "February", "days": 29}
    # switch the campaign to hebrew and re-read
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    cfg["primary"]["provider"] = "hebrew"
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).status_code == 200
    months = client.get(f"/api/campaigns/{cid}/calendar/months", params={"year": 5786}).json()["months"]
    assert len(months) == 12 and months[2]["key"] == "Kislev"
    # world-level (defaults to gregorian)
    r = client.get(f"/api/worlds/{wid}/calendar/months", params={"year": 2026})
    assert r.status_code == 200 and len(r.json()["months"]) == 12
    # errors
    assert client.get("/api/campaigns/nope/calendar/months", params={"year": 2026}).status_code == 404
    assert client.get(f"/api/campaigns/{cid}/calendar/months", params={"year": "abc"}).status_code == 422


def test_scene_datetime_with_hebrew_primary(client):
    _wid, cid = _campaign(client)
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    cfg["primary"]["provider"] = "hebrew"
    client.put(f"/api/campaigns/{cid}/calendar", json=cfg)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime",
                   json={"datetime": "5786-kislev-25"})
    assert r.status_code == 200
    assert r.json()["friendly"].startswith("25 Kislev 5786")
    sid = r.json()["id"]  # first date set renames the scene
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["current"]["native"] == "5786-Kislev-25"   # normalized casing
    assert got["history"] == ["5786-Kislev-25"]


def test_absorb_returns_preview_without_persisting(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered the crypt.")
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"one_line": "They entered.", "summary": "The party entered the crypt.",'
        ' "keywords": ["crypt"], "timeline_events": []}')
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    body = r.json()
    assert body["one_line"] == "They entered." and body["keywords"] == ["crypt"]
    assert client.get(f"/api/campaigns/{cid}/chronicle").json() == []  # not persisted yet


def test_absorb_writes_dossier_for_present_character(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Aese", "version_name": "main"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "aese", "version": "main", "role": "npc"})
    store.scenes.append_message(cid, sid, "user", "Aese served tea.")
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Aese is a shy snowleopardgirl who now trusts the owner.")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    croot = store.campaigns.campaign_root(cid)
    assert "Aese is a shy snowleopardgirl" in store.dossiers.read(croot, "aese")


def test_absorb_survives_dossier_failure(client):
    # A dossier generation error must not fail the absorb (the loop swallows per character).
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Aese", "version_name": "main"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "aese", "version": "main", "role": "npc"})
    store.scenes.append_message(cid, sid, "user", "hi")
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})

    class Fake:  # 1st complete() = extraction (ok); later complete() = dossier (boom)
        def __init__(self):
            self.calls = 0

        async def stream(self, m, cfg):
            yield "{}"

        async def complete(self, m, cfg):
            self.calls += 1
            if self.calls == 1:
                return '{"one_line": "ok", "summary": "s", "keywords": [], "timeline_events": []}'
            raise RuntimeError("dossier boom")

    client.app.dependency_overrides[routes.get_llm] = lambda: Fake()
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200 and r.json()["one_line"] == "ok"
    assert store.dossiers.read(store.campaigns.campaign_root(cid), "aese") == ""  # failed write skipped


def test_absorb_empty_scene_is_400(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 400


def test_save_chronicle_persists_and_lists(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                   json={"one_line": "They entered.", "summary": "In the crypt.",
                         "keywords": ["crypt"],
                         "timeline_events": [{"date": "2026-01-01", "text": "Entered."}]})
    assert r.status_code == 200 and r.json()["one_line"] == "They entered."
    listed = client.get(f"/api/campaigns/{cid}/chronicle").json()
    assert len(listed) == 1 and listed[0]["summary"] == "In the crypt."
    assert store.scenes.read_scene(cid, sid)["meta"]["done"] == "true"  # scene marked done


def test_absorb_missing_key_returns_409(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered.")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"


def test_absorb_upstream_error_returns_502(client):
    from grimoire.openrouter import OpenRouterError

    class FakeRaises:
        async def complete(self, messages, cfg):
            raise OpenRouterError("bad_response", "boom")

    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered.")
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeRaises()
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert resp.status_code == 502 and resp.json()["kind"] == "bad_response"


def test_absorb_returns_edits_without_persisting(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    croot = store.campaigns.campaign_root(cid)
    ch = store.characters.create_character(croot, "Seraphine", "main", store.characters.blank_card("Seraphine"))[0]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.appearances.appear(cid, sid, "characters", ch, "main", "npc")
    store.scenes.append_message(cid, sid, "user", "We fought.")
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],'
        f' "character_state_edits": [{{"id": "{ch}", "current_state": "hurt"}}],'
        ' "lore_edits": [], "authored_edits": []}')
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["edits"][0]["kind"] == "character_state" and body["edits"][0]["after"] == "hurt"
    assert store.playstate.read_state(croot, ch) is None  # not persisted


# ---- absorb: mechanics audit step (Phase 5, Task 9) ----

ABSORB_JSON = '{"one_line": "o", "summary": "s", "keywords": [], "timeline_events": []}'
AUDIT_OK = '{"warnings": ["Mara claimed a hit with no roll"], "sheet_deltas": []}'


@pytest.fixture
def module_scene(client):
    """A pool-basic campaign with one sheeted, PRESENT cast member seated as
    role="player" -- the dossier loop only fires for role="npc", so the
    absorb's dossier step makes zero extra LLM calls and the audit's call
    count stays exact. Its character id is "mara" (slugified from "Mara"),
    matched by the sheet_delta tests below."""
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    assert chid == "mara"
    store.sheets.write(cid, "characters", chid, "medium", {"health": 3}, expected=None)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]  # captures baseline
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
               json={"kind": "characters", "id": chid, "version": "default", "role": "player"})
    store.scenes.append_message(cid, sid, "user", "Mara took a hit but shrugged it off.")
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    return cid, sid


@pytest.fixture
def plain_scene(client):
    """A scene in a moduleless campaign: audit must skip with zero extra calls."""
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We walked into town.")
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    return cid, sid


def test_absorb_runs_audit_on_module_campaign(client, module_scene):
    cid, sid = module_scene
    fake = FakeOpenRouterComplete([ABSORB_JSON, AUDIT_OK])  # 2 sequential completes
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    body = r.json()
    assert body["mechanics"]["status"] == "ok"
    assert body["mechanics"]["warnings"] == ["Mara claimed a hit with no roll"]
    assert fake.calls == 2


def test_absorb_moduleless_skips_audit(client, plain_scene):
    cid, sid = plain_scene
    fake = FakeOpenRouterComplete([ABSORB_JSON])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["mechanics"]["status"] == "skipped" and fake.calls == 1


def test_absorb_all_invalid_scope_fails_with_no_audit_call(client, module_scene):
    """Every scoped sheet unreadable -> failed, and the audit never calls the
    LLM at all (the absorb's own extraction call is the only one)."""
    cid, sid = module_scene
    p = store.sheets._campaign_path(cid, "characters", "mara")
    p.write_text("{not json", encoding="utf-8")
    fake = FakeOpenRouterComplete([ABSORB_JSON])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["mechanics"]["status"] == "failed"
    assert body["mechanics"]["dropped"][0]["id"] == "characters:mara"
    assert fake.calls == 1                    # no audit LLM call for an all-invalid scope


def test_absorb_audit_schema_failure_is_failed_not_clean(client, module_scene):
    cid, sid = module_scene
    for bad in ("{}", '{"warnings": null, "sheet_deltas": null}', "utter garbage"):
        client.app.dependency_overrides[routes.get_llm] = \
            lambda b=bad: FakeOpenRouterComplete([ABSORB_JSON, b])
        body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
        assert body["one_line"]                       # prose absorb intact
        assert body["mechanics"]["status"] == "failed"
        assert body["mechanics"]["reason"]


def test_absorb_dropped_delta_degrades(client, module_scene):
    cid, sid = module_scene
    bad_delta = ('{"warnings": [], "sheet_deltas": [{"id": "characters:mara", '
                 '"field": "athletics", "value": 5, "note": "static tamper"}]}')
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete([ABSORB_JSON, bad_delta])
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["mechanics"]["status"] == "degraded"
    assert body["mechanics"]["dropped"]


def test_absorb_survives_audit_pipeline_crash(client, module_scene, monkeypatch):
    """Never-fail-absorb: an exception ANYWHERE in the audit pipeline
    (here: materialize) yields mechanics failed, absorb 200 + intact prose."""
    cid, sid = module_scene
    from grimoire.store import audit as audit_mod
    monkeypatch.setattr(audit_mod, "materialize",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete([ABSORB_JSON, AUDIT_OK])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    body = r.json()
    assert body["one_line"] and body["mechanics"]["status"] == "failed"
    assert "boom" in body["mechanics"]["reason"]


def test_audit_retry_endpoint(client, module_scene):
    cid, sid = module_scene
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete([AUDIT_OK])
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/audit").json()
    assert body["mechanics"]["status"] == "ok" and body["edits"] == []


def test_audit_retry_endpoint_400_without_module(client, plain_scene):
    cid, sid = plain_scene
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete([AUDIT_OK])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/audit")
    assert r.status_code == 400


def test_chronicle_put_applies_sheet_edit_and_reports_conflicts(client, module_scene):
    cid, sid = module_scene   # a materialized sheet StagedEdit, applied then replayed
    edits, dropped = store.audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "health", "value": 5, "note": ""}]})
    assert dropped == [] and edits
    sheet_edit = edits[0]
    save = {"one_line": "x", "summary": "y", "keywords": [], "timeline_events": [],
            "edits": [sheet_edit]}
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=save).json()
    assert r["applied"] == [sheet_edit["id"]] and r["sheet_failures"] == []
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=save).json()
    assert r["applied"] == [] and r["sheet_failures"][0]["kind"] == "conflict"


def test_scene_suggestions_returns_resolved(client):
    wid = _world(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    ann = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Ann", "version_name": "main"}).json()["character"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"suggestions": [{"title": "T", "premise": "P",'
        f' "cast": ["characters:{ann}"], "location": ""}}]}}')
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 200
    s = r.json()["suggestions"][0]
    assert s["title"] == "T" and s["premise"] == "P"
    assert s["cast"][0] == {"kind": "characters", "id": ann, "name": "Ann"}
    assert s["location"] is None


def test_scene_suggestions_missing_key_returns_409(client):
    _, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 409 and r.json()["kind"] == "missing_key"


def test_put_chronicle_applies_approved_edits(client):
    _, cid = _campaign(client)
    croot = store.campaigns.campaign_root(cid)
    ch = store.characters.create_character(croot, "Seraphine", "main", store.characters.blank_card("Seraphine"))[0]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.appearances.appear(cid, sid, "characters", ch, "main", "npc")
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={
        "one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
        "edits": [{"id": f"character_state:{ch}", "kind": "character_state",
                   "target": {"kind": "characters", "id": ch}, "field": "current_state", "after": "Loyal."}]})
    assert r.json()["applied"] == [f"character_state:{ch}"]
    assert r.json()["sheet_failures"] == []
    assert store.playstate.read_state(croot, ch)["current_state"] == "Loyal."


def test_put_chronicle_reports_sheet_failures(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    store.sheets.write(cid, "characters", chid, "medium", {"health": 0}, expected=None)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]  # captures baseline
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
               json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    edits, dropped = store.audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": f"characters:{chid}", "field": "health", "value": 2, "note": ""}]})
    assert dropped == [] and edits

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={
        "one_line": "o", "summary": "s", "keywords": [], "timeline_events": [], "edits": edits})
    body = r.json()
    assert body["applied"] == [edits[0]["id"]] and body["sheet_failures"] == []
    assert store.sheets.read(cid, "characters", chid)["fields"]["health"] == 2

    r2 = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={   # replay: reported, not skipped
        "one_line": "o", "summary": "s", "keywords": [], "timeline_events": [], "edits": edits})
    body2 = r2.json()
    assert body2["applied"] == []
    assert body2["sheet_failures"] == [
        {"id": edits[0]["id"], "kind": "conflict", "reason": body2["sheet_failures"][0]["reason"]}]


def _apply_lore_change(client, cid):
    croot = store.campaigns.campaign_root(cid)
    store.entities.create_entity(croot, "lore", "Pact", body="old body")
    edit = {"id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
            "label": "The Pact — lore", "field": "body", "before": "old body",
            "after": "old body\nnew line", "authored": False}
    store.absorb.apply_edits(cid, [edit], "s1")


def test_get_changes_returns_name_scene_and_diff(client):
    _, cid = _campaign(client)
    _apply_lore_change(client, cid)  # records under scene "s1" (never created -> title falls back)
    out = client.get(f"/api/campaigns/{cid}/changes").json()
    assert len(out) == 1
    rec = out[0]
    assert rec["ref"] == {"kind": "lore", "id": "pact"} and rec["name"] == "Pact"
    assert rec["scene"]["id"] == "s1"  # deleted/unknown scene -> title falls back to id
    ops = [d["op"] for d in rec["fields"][0]["diff"]]
    assert "insert" in ops


def test_get_changes_resolves_inherited_character_name(client):
    """A character_state edit can target a world character never materialized
    into the campaign; its name must still resolve (through the overlay) so
    the change-history row isn't silently dropped."""
    wid, cid = _campaign(client)
    wroot = store.worlds.world_root(wid)
    ch, _ = store.characters.create_character(wroot, "Seraphine")
    edit = {"id": f"character_state:{ch}", "kind": "character_state",
            "target": {"kind": "characters", "id": ch}, "field": "current_state",
            "label": "Seraphine — state", "before": "", "after": "Wary of the docks.",
            "authored": False}
    store.absorb.apply_edits(cid, [edit], "s1")
    out = client.get(f"/api/campaigns/{cid}/changes").json()
    assert len(out) == 1
    assert out[0]["ref"] == {"kind": "characters", "id": ch}
    assert out[0]["name"] == "Seraphine"


def test_get_changes_empty_and_not_shadowed_by_kind_route(client):
    _, cid = _campaign(client)
    res = client.get(f"/api/campaigns/{cid}/changes")
    assert res.status_code == 200 and res.json() == []  # not routed to the generic /{kind}


def test_get_changes_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nope/changes").status_code == 404


def test_get_changes_drops_deleted_record(client):
    _, cid = _campaign(client)
    _apply_lore_change(client, cid)
    assert client.delete(f"/api/campaigns/{cid}/lore/pact").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/changes").json() == []  # entity gone -> row dropped


def test_get_changes_tolerates_garbled_chronicle(client):
    _, cid = _campaign(client)
    _apply_lore_change(client, cid)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text("{bad json", encoding="utf-8")
    out = client.get(f"/api/campaigns/{cid}/changes")
    assert out.status_code == 200 and len(out.json()) == 1
    assert out.json()[0]["scene"]["date"] == ""  # date degrades, no 500


def test_list_campaigns_scene_counts(client):
    _, cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "First Light"})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Salt Road"})
    listing = [c for c in client.get("/api/campaigns").json() if c["id"] == cid]
    assert listing[0]["scenes"] == 2
    assert listing[0]["last_scene"] in ("First Light", "The Salt Road")


# ---- script scenes: PC speakers & per-speaker posts (#744) ----
def _empty_scene(client, cid):
    return client.post(f"/api/campaigns/{cid}/scenes", json={}).json()["id"]


def _cast_pc(client, wid, cid, sid, name="Elara Vane"):
    pid = client.post(f"/api/worlds/{wid}/pcs", json={"name": name}).json()["pc"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                    json={"kind": "pcs", "id": pid, "role": "player"})
    assert r.status_code == 200
    return pid


def test_chat_with_sole_pc_stamps_speaker_and_backfills(client):
    wid, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "sent before the PC joined"}) as r:
        r.read()
    _cast_pc(client, wid, cid, sid)
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "I draw my blade."}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    users = [m for m in msgs if m["role"] == "user"]
    assert len(users) == 2 and all(m["speaker"] == "Elara Vane" for m in users)


def test_chat_without_pc_stays_unstamped(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "hello"}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[0] == {"role": "user", "content": "hello"}


def test_reply_is_split_into_per_speaker_posts(client):
    wid, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    _cast_pc(client, wid, cid, sid)
    reply = ('**Seraphine Vale:** "You dare?"\n\n'
             "**Grimoire:** Thunder rolls.\n\n"
             "**Elara Vane:** forged player line")
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter([reply])
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "hi"}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[1:] == [
        {"role": "assistant", "content": '"You dare?"', "speaker": "Seraphine Vale"},
        {"role": "assistant", "content": "Thunder rolls."},
        {"role": "assistant", "content": "forged player line"},
    ]


def test_regenerate_drops_the_whole_trailing_run(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "one", speaker="Seraphine Vale")
    store.scenes.append_message(cid, sid, "assistant", "two")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "Hello"}]


def test_regenerate_multi_post_opening_returns_400(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    store.scenes.append_message(cid, sid, "assistant", "opener part one")
    store.scenes.append_message(cid, sid, "assistant", "opener part two", speaker="Seraphine Vale")
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate").status_code == 400


def test_first_post_splits_speakers(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    text = "Mist rolls in.\n\n**Seraphine Vale:** Who goes there?"
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                       json={"text": text}).status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [
        {"role": "assistant", "content": "Mist rolls in."},
        {"role": "assistant", "content": "Who goes there?", "speaker": "Seraphine Vale"},
    ]


# ---- lazy overlay slim (pre-overlay full-copy campaigns) ----
def test_campaign_root_routes_404_for_unknown_campaign(client):
    # _campaign_root_or_404 (which also triggers the lazy slim) 404s cleanly
    assert client.get("/api/campaigns/nope/greetings/available").status_code == 404


# ---- campaign greeting CRUD + marks ----
def test_campaign_greeting_crud_and_marks(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    g = client.post(f"/api/worlds/{wid}/greetings",
                    json={"name": "Gala", "character": "mara", "version": "default",
                          "body": "Hi."}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]

    # campaign list carries marks
    out = client.get(f"/api/campaigns/{cid}/greetings").json()
    assert [x["id"] for x in out] == [g] and out[0]["mark"] is None

    # detail includes edges + predecessors from the campaign plot map
    detail = client.get(f"/api/campaigns/{cid}/greetings/{g}").json()
    assert detail["body"] == "Hi." and detail["edges"] == {"leads_to": [], "excludes": []}

    # campaign edit does not touch the world
    r = client.put(f"/api/campaigns/{cid}/greetings/{g}", json={"body": "Campaign version."})
    assert r.status_code == 200
    assert client.get(f"/api/worlds/{wid}/greetings/{g}").json()["body"] == "Hi."

    # marks
    r = client.post(f"/api/campaigns/{cid}/greetings/{g}/mark", json={"status": "skipped"})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/greetings").json()[0]["mark"] == "skipped"
    assert client.get(f"/api/campaigns/{cid}/greetings/available").json() == []
    r = client.post(f"/api/campaigns/{cid}/greetings/nope/mark", json={"status": "skipped"})
    assert r.status_code == 404

    # create + edges + delete campaign-local greeting
    g2 = client.post(f"/api/campaigns/{cid}/greetings",
                     json={"name": "Local", "character": "mara", "version": "default",
                           "body": "Local."}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/greetings/{g2}/edges", json={"leads_to": [g]})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/greetings/{g}").json()["predecessors"] == [g2]
    assert client.delete(f"/api/campaigns/{cid}/greetings/{g2}").status_code == 200


def test_campaign_greeting_mark_played_conflicts(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    g = client.post(f"/api/worlds/{wid}/greetings",
                    json={"name": "Gala", "character": "mara", "version": "default",
                          "body": "Hi."}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 200
    r = client.post(f"/api/campaigns/{cid}/greetings/{g}/mark", json={"status": "completed"})
    assert r.status_code == 409


# ---- campaign characters / pcs / pick / import ----
def _campaign_with_actor(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters",
                json={"name": "Mara", "version_name": "young"})
    client.post(f"/api/worlds/{wid}/characters/mara/versions",
                json={"name": "veteran", "card": {"spec": "chara_card_v3",
                                                  "spec_version": "3.0",
                                                  "data": {"name": "Mara"}}})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    return wid, cid


def test_campaign_character_read_and_edit(client):
    wid, cid = _campaign_with_actor(client)
    chars = client.get(f"/api/campaigns/{cid}/characters").json()
    assert [c["id"] for c in chars] == ["mara"]
    detail = client.get(f"/api/campaigns/{cid}/characters/mara").json()
    assert {v["id"] for v in detail["versions"]} == {"young", "veteran"}
    # campaign card edit leaves the world untouched
    card = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "C-Mara"}}
    r = client.put(f"/api/campaigns/{cid}/characters/mara/versions/young", json={"card": card})
    assert r.status_code == 200
    world = client.get(f"/api/worlds/{wid}/characters/mara").json()
    young = next(v for v in world["versions"] if v["id"] == "young")
    assert young["card"]["data"]["name"] == "Mara"


def test_campaign_pick_and_import_version(client):
    wid, cid = _campaign_with_actor(client)
    # unknown version -> 404 (checked while unlocked; after a pick the lock wins)
    assert client.post(f"/api/campaigns/{cid}/characters/mara/pick-version",
                       json={"version": "bogus"}).status_code == 404
    r = client.post(f"/api/campaigns/{cid}/characters/mara/pick-version",
                    json={"version": "young"})
    assert r.status_code == 200
    detail = client.get(f"/api/campaigns/{cid}/characters/mara").json()
    assert [v["id"] for v in detail["versions"]] == ["young"]
    # any pick on a locked actor -> 409, even naming a version the pick purged
    assert client.post(f"/api/campaigns/{cid}/characters/mara/pick-version",
                       json={"version": "veteran"}).status_code == 409
    # import replaces the pick
    r = client.post(f"/api/campaigns/{cid}/characters/mara/import-version",
                    json={"version": "veteran"})
    assert r.status_code == 200
    detail = client.get(f"/api/campaigns/{cid}/characters/mara").json()
    assert [v["id"] for v in detail["versions"]] == ["veteran"]
    # locked actor refuses new campaign versions
    assert client.post(f"/api/campaigns/{cid}/characters/mara/versions",
                       json={"name": "extra", "card": {"spec": "chara_card_v3",
                                                       "spec_version": "3.0",
                                                       "data": {"name": "X"}}}).status_code == 409


def test_campaign_import_requires_lock_and_actor_kind(client):
    wid, cid = _campaign_with_actor(client)
    assert client.post(f"/api/campaigns/{cid}/characters/mara/import-version",
                       json={"version": "veteran"}).status_code == 409
    assert client.post(f"/api/campaigns/{cid}/locations/somewhere/pick-version",
                       json={"version": "x"}).status_code == 404


def test_campaign_pc_read_and_versions(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara", "tags": []})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    detail = client.get(f"/api/campaigns/{cid}/pcs/elara").json()
    assert detail["meta"]["id"] == "elara"
    r = client.put(f"/api/campaigns/{cid}/pcs/elara", json={"tags": ["anything-goes"]})
    assert r.status_code == 200
    r = client.post(f"/api/campaigns/{cid}/pcs/elara/versions",
                    json={"name": "older", "persona": {"name": "Elara", "pronouns": "",
                                                       "summary": "", "description": "x"}})
    assert r.status_code == 200


# ---- gzip ----
def test_large_json_responses_are_gzipped(client):
    wid = _world(client)
    body = "lorem ipsum " * 500  # ~6KB, comfortably past the compression floor
    client.post(f"/api/worlds/{wid}/lore", json={"name": "Big", "body": body})
    r = client.get(f"/api/worlds/{wid}/lore/big", headers={"accept-encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert r.json()["body"].strip() == body.strip()


# ---- image caching ----
def _png_upload(client, wid, cid):
    return client.put(
        f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar",
        files={"file": ("a.png", io.BytesIO(b"png-bytes"), "image/png")})


def test_versioned_image_url_is_immutable_unversioned_revalidates(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Ada"}).json()["character"]
    assert _png_upload(client, wid, cid).status_code == 200
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar"
    plain = client.get(base)
    assert plain.headers["cache-control"] == "no-cache"
    assert plain.headers.get("etag")
    v = client.get(f"/api/worlds/{wid}/characters/{cid}/versions/default/images").json()[0]["v"]
    versioned = client.get(f"{base}?v={v}")
    assert versioned.status_code == 200
    assert "immutable" in versioned.headers["cache-control"]


def test_entity_list_exposes_image_version(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Docks"})
    up = client.put(f"/api/worlds/{wid}/locations/docks/images/avatar",
                    files={"file": ("a.png", io.BytesIO(b"png-bytes"), "image/png")})
    assert up.status_code == 200
    items = client.get(f"/api/worlds/{wid}/locations").json()
    assert items[0]["has_image"] is True
    assert items[0]["image_v"]
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Bare"})
    items = client.get(f"/api/worlds/{wid}/locations").json()
    bare = next(i for i in items if i["id"] == "bare")
    assert bare["has_image"] is False and bare.get("image_v") is None


def test_campaign_detail_embeds_world_name(client):
    client.post("/api/worlds", json={"name": "Drowned Realm"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": "drowned-realm"}).json()["id"]
    meta = client.get(f"/api/campaigns/{cid}").json()["meta"]
    assert meta["world"] == "drowned-realm"
    assert meta["world_name"] == "Drowned Realm"


# ---- batch cast ----
def test_cast_batch_seats_everyone_in_one_request(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Bran"})
    client.post(f"/api/campaigns/{cid}/pcs", json={"name": "Mara", "persona": {
        "name": "Mara", "pronouns": "", "summary": "", "description": ""}})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast/batch", json={"refs": [
        {"kind": "characters", "id": "sera"},
        {"kind": "characters", "id": "bran"},
        {"kind": "pcs", "id": "mara"},
    ]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "added": 3, "skipped": []}
    cast = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    assert {(a["kind"], a["id"]) for a in cast} == {
        ("characters", "sera"), ("characters", "bran"), ("pcs", "mara")}


def test_cast_batch_skips_conflicting_members_and_keeps_going(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Bran"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # lock sera as npc; batch-seating her as player is the 409 the serial loop tolerated
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "sera"})
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast/batch", json={"refs": [
        {"kind": "characters", "id": "sera", "role": "player"},
        {"kind": "characters", "id": "bran"},
    ]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "added": 1, "skipped": ["characters/sera"]}
    cast = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    assert {(a["kind"], a["id"]) for a in cast} == {("characters", "sera"), ("characters", "bran")}


def test_cast_batch_unknown_actor_404s(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast/batch", json={"refs": [
        {"kind": "characters", "id": "ghost"},
    ]})
    assert r.status_code == 404


# ---- image thumbnails ----
def _real_png(w=1200, h=800):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (180, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


def test_image_w_param_serves_downscaled_webp(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar"
    png = _real_png()
    client.put(base, files={"file": ("a.png", io.BytesIO(png), "image/png")})
    full = client.get(base)
    assert full.headers["content-type"] == "image/png" and len(full.content) == len(png)
    thumb = client.get(f"{base}?w=320")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    assert len(thumb.content) < len(png) / 4
    # w + v caches immutable, like any versioned image URL
    v = client.get(f"/api/worlds/{wid}/characters/{cid}/versions/default/images").json()[0]["v"]
    both = client.get(f"{base}?w=320&v={v}")
    assert "immutable" in both.headers["cache-control"]


def test_image_w_param_falls_back_to_original_when_not_decodable(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar"
    client.put(base, files={"file": ("a.png", io.BytesIO(b"not an image"), "image/png")})
    r = client.get(f"{base}?w=320")
    assert r.status_code == 200
    assert r.content == b"not an image"  # original bytes, not an error


def test_appearances_and_untagged_carry_versioned_thumb_urls(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": cid, "version": "default"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", _real_png(), "png",
                           base="greetings")
    base = f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456"

    ut = client.get(f"/api/worlds/{wid}/subjects/untagged").json()[0]
    assert ut["url"].startswith(f"{base}?v=")
    assert "w=320" in ut["thumb"] and "v=" in ut["thumb"]

    client.put(f"{base}/subjects", json={"subjects": [cid]})
    app = client.get(f"/api/worlds/{wid}/characters/{cid}/appearances").json()[0]
    assert app["url"].startswith(f"{base}?v=")
    assert "w=320" in app["thumb"] and "v=" in app["thumb"]
    # the thumb URL actually serves a small webp
    t = client.get(app["thumb"])
    assert t.headers["content-type"] == "image/webp"


def _campaign_with_greetings(client, n):
    wid = _world(client)
    ann = client.post(f"/api/worlds/{wid}/characters", json={"name": "Ann"}).json()["character"]
    gids = [client.post(f"/api/worlds/{wid}/greetings",
                        json={"name": f"Opening {i}", "character": ann, "version": "default",
                              "body": f"Opening body {i}"}).json()["id"]
            for i in range(n)]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    return cid, gids


def test_scene_suggestions_rank_greetings_when_more_than_two(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    cid, gids = _campaign_with_greetings(client, 3)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"suggestions": [], "greeting_picks": ["' + gids[2] + '", "ghost", "' + gids[0] + '"]}')
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 200
    assert r.json()["greeting_picks"] == [gids[2], gids[0]]


def test_scene_suggestions_skip_ranking_at_two_or_fewer(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    cid, gids = _campaign_with_greetings(client, 2)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"suggestions": [], "greeting_picks": ["' + gids[0] + '"]}')
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 200
    assert r.json()["greeting_picks"] == []  # nothing was ranked, nothing honored


def test_datetime_get_returns_creation_hint_as_suggested(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "S", "suggested_date": "2026-07-10"}).json()["id"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["current"] is None and got["suggested"] == "2026-07-10"


def test_datetime_suggested_falls_back_to_chronicle_date(client):
    _wid, cid = _campaign(client)
    store.chronicle.absorb(cid, {"id": "001--old", "one_line": "x", "summary": "y",
                                 "keywords": [], "cast": [], "location": "",
                                 "date": "2026-07-04T21:30"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["suggested"] == "2026-07-04"  # time-of-day never reaches the date input


def test_datetime_suggested_is_null_without_signals_and_once_dated(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["suggested"] is None
    sid2 = client.post(f"/api/campaigns/{cid}/scenes",
                       json={"title": "S2", "suggested_date": "2026-07-10"}).json()["id"]
    sid2 = client.put(f"/api/campaigns/{cid}/scenes/{sid2}/datetime",
                      json={"datetime": "2026-07-12"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid2}/datetime").json()["suggested"] is None


def test_scene_suggestions_include_dates_and_next_date(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    _wid, cid = _campaign(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        '{"suggestions": [{"title": "T", "premise": "P", "cast": [], "location": "",'
        ' "date": "2026-07-10"}], "next_date": "2026-07-08"}')
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["date"] == "2026-07-10"
    assert r.json()["next_date"] == "2026-07-08"


# ---- offscreen (pcless) scenes ----
def test_scene_pcless_flag_roundtrip(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    normal = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    listing = {s["id"]: s["pcless"] for s in client.get(f"/api/campaigns/{cid}/scenes").json()}
    assert listing[sid] is True and listing[normal] is False
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["meta"]["pcless"] == "true"
    assert store.scenes.is_pcless(cid, sid) is True
    assert store.scenes.is_pcless(cid, normal) is False
    assert store.scenes.is_pcless(cid, "missing") is False


def test_offscreen_scene_rejects_player_seating(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    pid = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara"}).json()["pc"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": pid}).status_code == 400
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Desmond"})
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "desmond", "role": "player"}).status_code == 400
    # NPCs still seat fine
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "desmond"}).status_code == 200


def test_offscreen_context_has_director_section_and_absent_pc(client):
    wid, cid = _campaign(client)
    # the PC enters the campaign roster by being seated in a *different* scene
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    _cast_pc(client, wid, cid, other, name="Elara Vane")
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    sections = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()["sections"]
    labels = {s["label"]: s["text"] for s in sections}
    assert "director's notes" in labels["Offscreen scene"]
    assert "Elara Vane" in labels["Offscreen scene"]           # named as not-present
    assert "Elara Vane" in labels["Absent player characters"]  # persona as reference
    normal = {s["label"] for s in
              client.get(f"/api/campaigns/{cid}/scenes/{other}/context").json()["sections"]}
    assert "Offscreen scene" not in normal


def test_offscreen_opener_uses_third_person_instruction(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "the cult meets"}) as r:
        r.read()
    assert "offscreen scene" in cap.messages[0]["content"].lower()
    assert "No player character is present" in cap.messages[0]["content"]


def test_offscreen_chat_never_persists_the_director_note(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouter(["**Grimoire:** The cult convenes."])
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "the guard grows suspicious"}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs and all(m["role"] == "assistant" for m in msgs)
    assert "guard grows suspicious" not in json.dumps(msgs)


def test_offscreen_chat_director_note_expands_roll_macro(client):
    # #137 regression: build_director_messages appended the note raw -- an
    # offscreen director note's macros must resolve before reaching the model.
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "Resolve {{roll:1d20}}."}) as r:
        r.read()
    note = next(m for m in cap.messages if m["role"] == "user")["content"]
    assert "{{roll" not in note


def test_offscreen_chat_empty_note_sends_continue(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": ""}) as r:
        r.read()
    assert [m for m in cap.messages if m["role"] == "user"] == \
        [{"role": "user", "content": "Continue the scene."}]


def test_empty_chat_in_a_normal_scene_is_an_ephemeral_npc_round(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "assistant", "The tavern hums.")
    client.put("/api/config", json={"openrouter_key": "k"})
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": " "}) as r:
        r.read()
    # the continue instruction rides the call but is never persisted
    assert cap.messages[-1] == {"role": "user", "content": "Continue the scene."}
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert all(m["role"] == "assistant" for m in msgs)


def test_greeting_pcless_roundtrip_and_availability(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Cabal", "character": "vex", "version": ver,
        "body": "The cult meets.", "pcless": True}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/greetings/{g}").json()["meta"]["pcless"] is True
    client.put(f"/api/campaigns/{cid}/greetings/{g}", json={"pcless": False})
    assert client.get(f"/api/campaigns/{cid}/greetings/{g}").json()["meta"]["pcless"] is False
    client.put(f"/api/campaigns/{cid}/greetings/{g}", json={"pcless": True})
    avail = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert [a["pcless"] for a in avail if a["id"] == g] == [True]


def test_offscreen_greeting_stamps_scene_and_substitutes_pc_name(client):
    wid, cid = _campaign(client)
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    _cast_pc(client, wid, cid, other, name="Elara Vane")
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Cabal", "character": "vex", "version": ver,
        "body": "While {{user}} sleeps, the cult convenes.", "pcless": True}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": g})
    assert r.status_code == 200
    sid = r.json()["id"]
    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["meta"]["pcless"] == "true"          # plain scene got flagged
    assert "While Elara Vane sleeps" in scene["messages"][0]["content"]


def test_pc_greeting_cannot_start_an_offscreen_scene(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Meet", "character": "vex", "version": ver, "body": "Hi {{user}}."}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 409


def test_offscreen_greeting_rejects_a_scene_with_players(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    _cast_pc(client, wid, cid, sid)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Cabal", "character": "vex", "version": ver, "body": "x",
        "pcless": True}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 409


class FakeCompleter:
    def __init__(self, text):
        self.text = text

    async def complete(self, messages, cfg):
        self.messages = messages
        return self.text


def test_offscreen_suggestions_filter_player_cast(client):
    wid, cid = _campaign(client)
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    pid = _cast_pc(client, wid, cid, other, name="Elara Vane")
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    # seating vex copies him into the campaign, making his token valid for suggestions
    client.post(f"/api/campaigns/{cid}/scenes/{other}/cast", json={"kind": "characters", "id": "vex"})
    client.put("/api/config", json={"openrouter_key": "k"})
    fake = FakeCompleter(json.dumps({"suggestions": [{
        "title": "Plot", "premise": "The cult schemes.",
        "cast": ["characters:vex", f"pcs:{pid}"], "location": ""}]}))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    out = client.post(f"/api/campaigns/{cid}/scene-suggestions?offscreen=true").json()
    assert out["suggestions"][0]["cast"] == [{"kind": "characters", "id": "vex", "name": "Vex"}]
    assert "offscreen" in fake.messages[0]["content"].lower()
    assert f"pcs:{pid}" not in fake.messages[1]["content"]  # players withheld from the cast list


def test_offscreen_suggestions_rank_only_offscreen_greetings(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    for name in ("Alpha", "Beta", "Gamma"):
        client.post(f"/api/campaigns/{cid}/greetings", json={
            "name": name, "character": "vex", "version": ver, "body": "x", "pcless": True})
    client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Normal", "character": "vex", "version": ver, "body": "y"})
    client.put("/api/config", json={"openrouter_key": "k"})
    fake = FakeCompleter(json.dumps({"suggestions": [], "greeting_picks": ["alpha", "beta"]}))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    out = client.post(f"/api/campaigns/{cid}/scene-suggestions?offscreen=true").json()
    assert "Available greetings" in fake.messages[1]["content"]
    assert "Normal" not in fake.messages[1]["content"]
    assert out["greeting_picks"] == ["alpha", "beta"]


# ---- campaign EPUB export ----

def test_export_epub_route(client):
    _wid, cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/export.epub")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/epub+zip"
    assert r.headers["content-disposition"] == f'attachment; filename="{cid}.epub"'
    assert r.content[:2] == b"PK"


def test_export_epub_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nope/export.epub").status_code == 404


def test_export_markdown_bundle_route(client):
    _wid, cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/export.md.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == f'attachment; filename="{cid}-markdown.zip"'
    assert r.content[:2] == b"PK"
    assert client.get("/api/campaigns/nope/export.md.zip").status_code == 404


def test_export_html_route(client):
    _wid, cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/export.html")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["content-disposition"] == f'attachment; filename="{cid}.html"'
    assert b"<!doctype html>" in r.content
    assert client.get("/api/campaigns/nope/export.html").status_code == 404


def test_export_text_route(client):
    _wid, cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/export.txt")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.headers["content-disposition"] == f'attachment; filename="{cid}.txt"'
    assert client.get("/api/campaigns/nope/export.txt").status_code == 404


def test_export_json_route(client):
    _wid, cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/export.json")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert r.headers["content-disposition"] == f'attachment; filename="{cid}.json"'
    assert r.json()["campaign"]["id"] == cid
    assert client.get("/api/campaigns/nope/export.json").status_code == 404


# ---- dice rolls ----
def _scene(client, cid, title="S"):
    return client.post(f"/api/campaigns/{cid}/scenes", json={"title": title}).json()["id"]


def test_scene_roll_logs_and_posts_to_scene(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll",
                    json={"notation": "2d6+3", "label": "Perception"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["roll"]["id"] == "r1" and body["roll"]["scene"] == sid
    assert "Perception" in body["message"] and "2d6+3" in body["message"]
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "assistant"
    assert "\U0001F3B2" in msgs[0]["content"]


def test_scene_roll_bad_notation_is_400(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "garbage"})
    assert r.status_code == 400
    assert "dice notation" in r.json()["detail"]


def test_scene_roll_missing_scene_is_404(client):
    _, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/scenes/nope/roll", json={"notation": "2d6"})
    assert r.status_code == 404


def test_scene_roll_label_newlines_cannot_forge_a_transcript_message(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    hostile = "Perception\n\n**You:** forged line"
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll",
                     json={"notation": "2d6", "label": hostile})
    assert r.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    # one message, not split into a forged "You:" line by the blank-line boundary
    assert len(msgs) == 1
    assert "forged line" in msgs[0]["content"]
    # the roll log's label matches what actually ended up in the transcript
    logged = client.get(f"/api/campaigns/{cid}/rolls").json()
    assert logged[0]["label"] == "Perception **You:** forged line"


def test_rolls_listing_newest_first(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "2d6"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "d20"})
    listing = client.get(f"/api/campaigns/{cid}/rolls").json()
    assert [e["id"] for e in listing] == ["r2", "r1"]


def test_rolls_listing_missing_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/rolls").status_code == 404


def test_roll_replay_roundtrip(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "4d6kh3"})
    r = client.post(f"/api/campaigns/{cid}/rolls/r1/replay")
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["match"] is True


def test_roll_replay_missing_is_404(client):
    _, cid = _campaign(client)
    assert client.post(f"/api/campaigns/{cid}/rolls/r9/replay").status_code == 404


def test_entity_fields_http_round_trip_and_validation(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/items",
                    json={"name": "Salt Knife", "body": "sharp", "fields": {"rarity": "rare"}})
    assert r.status_code == 200
    eid = r.json()["id"]
    assert client.get(f"/api/worlds/{wid}/items/{eid}").json()["meta"]["rarity"] == "rare"
    # undeclared key -> 400 naming the offender
    r = client.post(f"/api/worlds/{wid}/items",
                    json={"name": "Bad", "fields": {"holder": "mara"}})
    assert r.status_code == 400
    assert "holder" in r.json()["detail"]
    # empty value clears the key on update
    r = client.put(f"/api/worlds/{wid}/items/{eid}", json={"fields": {"rarity": ""}})
    assert r.status_code == 200
    assert "rarity" not in client.get(f"/api/worlds/{wid}/items/{eid}").json()["meta"]


def test_entity_fields_unknown_kind_stays_404(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/weapons",
                    json={"name": "X", "fields": {"rarity": "r"}})
    assert r.status_code == 404


def test_group_state_routes_round_trip(client):
    _wid, cid = _campaign(client)
    gid = client.post(f"/api/campaigns/{cid}/groups",
                      json={"name": "Salt Circle", "body": "A quiet cabal."}).json()["id"]
    # no state file yet -> all fields empty
    r = client.get(f"/api/campaigns/{cid}/groups/{gid}/state")
    assert r.status_code == 200
    assert r.json()["goals"] == "" and r.json()["secrets"] == ""
    # write, then read back
    r = client.put(f"/api/campaigns/{cid}/groups/{gid}/state",
                   json={"goals": "Expand.", "secrets": "The abbot."})
    assert r.json() == {"ok": True}
    st = client.get(f"/api/campaigns/{cid}/groups/{gid}/state").json()
    assert st["goals"] == "Expand." and st["secrets"] == "The abbot." and st["updated"]
    # PUT is a full snapshot: an omitted field defaults to "" and clears
    client.put(f"/api/campaigns/{cid}/groups/{gid}/state", json={"goals": "Expand."})
    assert client.get(f"/api/campaigns/{cid}/groups/{gid}/state").json()["secrets"] == ""
    # unknown group -> 404 on both verbs
    assert client.get(f"/api/campaigns/{cid}/groups/no-such/state").status_code == 404
    assert client.put(f"/api/campaigns/{cid}/groups/no-such/state", json={}).status_code == 404


# ---- modules (#160) ----
def test_modules_api(client):
    listed = client.get("/api/modules").json()
    assert {m["id"] for m in listed} >= {"d20-basic", "pool-basic"}
    detail = client.get("/api/modules/pool-basic").json()
    assert detail["manifest"]["name"] == "Basic Pool"
    assert "medium" in detail["sheets"]["sheet_types"]
    assert detail["errors"] == []
    assert client.get("/api/modules/ghost").status_code == 404

    created = client.post("/api/modules", json={"name": "Homebrew"}).json()
    assert created["id"] == "homebrew"
    assert client.delete("/api/modules/homebrew").json()["ok"] is True
    assert client.delete("/api/modules/pool-basic").status_code == 400


def _seed_content_module(client, tmp_path, mid="contentmod", statted=False):
    import json as _json
    home = tmp_path  # GRIMOIRE_HOME is already tmp_path via the client fixture
    d = home / "modules" / mid
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Content Test\n---\n", encoding="utf-8")
    sheets_def = {"groups": {}, "sheet_types": {}}
    if statted:
        sheets_def["sheet_types"]["trinket"] = {
            "label": "Trinket", "kind": "items", "groups": [],
            "fields": [{"key": "power", "type": "dots", "max": 5}],
        }
    (d / "sheets.json").write_text(_json.dumps(sheets_def), encoding="utf-8")
    cd = d / "content" / "items"
    cd.mkdir(parents=True)
    (cd / "lantern.md").write_text(
        "---\nname: Lantern of Winnowing\nkeys: lantern\n---\nA soft lantern.\n", encoding="utf-8")
    if statted:
        (cd / "lantern.sheet.json").write_text(
            _json.dumps({"sheet_type": "trinket", "fields": {"power": 2}}), encoding="utf-8")
    return mid


def test_module_content_read(client, tmp_path):
    mid = _seed_content_module(client, tmp_path)
    r = client.get(f"/api/modules/{mid}/content/items/lantern")
    assert r.status_code == 200
    assert r.json()["name"] == "Lantern of Winnowing"
    assert r.json()["body"] == "A soft lantern.\n"
    assert client.get(f"/api/modules/{mid}/content/items/nope").status_code == 404
    assert client.get("/api/modules/ghost/content/items/lantern").status_code == 404


def test_instantiate_into_world(client, tmp_path):
    mid = _seed_content_module(client, tmp_path, statted=True)
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/items/instantiate/{mid}/lantern")
    assert r.status_code == 200
    eid = r.json()["id"]
    entity = client.get(f"/api/worlds/{wid}/items/{eid}").json()
    assert entity["meta"]["name"] == "Lantern of Winnowing"
    sheet = client.get(f"/api/worlds/{wid}/sheets/{mid}/items/{eid}").json()["sheet"]
    assert sheet["sheet_type"] == "trinket"
    assert sheet["fields"]["power"] == 2


def test_instantiate_into_campaign(client, tmp_path):
    mid = _seed_content_module(client, tmp_path)  # not statted -- no sheet expected
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": mid})
    r = client.post(f"/api/campaigns/{cid}/items/instantiate/{mid}/lantern")
    assert r.status_code == 200
    eid = r.json()["id"]
    entity = client.get(f"/api/campaigns/{cid}/items/{eid}").json()
    assert entity["meta"]["name"] == "Lantern of Winnowing"
    assert client.get(f"/api/campaigns/{cid}/sheets/items/{eid}").json()["sheet"] is None


def test_instantiate_unknown_content_404(client, tmp_path):
    mid = _seed_content_module(client, tmp_path)
    wid = _world(client)
    assert client.post(f"/api/worlds/{wid}/items/instantiate/{mid}/ghost").status_code == 404
    assert client.post(f"/api/worlds/{wid}/items/instantiate/ghostmod/lantern").status_code == 404


def _seed_content_module_bad_sheet_type(client, tmp_path, mid="badsheetmod"):
    """Content whose sidecar names a sheet_type the module doesn't declare --
    simulates the module pack changing out from under an instantiate between
    the content read and the sheet write."""
    import json as _json
    home = tmp_path
    d = home / "modules" / mid
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Bad Sheet Test\n---\n", encoding="utf-8")
    # No "trinket" sheet type declared here -- the sidecar below references it anyway.
    (d / "sheets.json").write_text(_json.dumps({"groups": {}, "sheet_types": {}}), encoding="utf-8")
    cd = d / "content" / "items"
    cd.mkdir(parents=True)
    (cd / "lantern.md").write_text(
        "---\nname: Lantern of Winnowing\nkeys: lantern\n---\nA soft lantern.\n", encoding="utf-8")
    (cd / "lantern.sheet.json").write_text(
        _json.dumps({"sheet_type": "trinket", "fields": {"power": 2}}), encoding="utf-8")
    return mid


def test_instantiate_into_world_rolls_back_entity_on_sheet_write_failure(client, tmp_path):
    mid = _seed_content_module_bad_sheet_type(client, tmp_path)
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/items/instantiate/{mid}/lantern")
    assert r.status_code == 400
    # The entity was created before the sheet write failed -- it must not survive.
    assert client.get(f"/api/worlds/{wid}/items/lantern-of-winnowing").status_code == 404
    assert client.get(f"/api/worlds/{wid}/items").json() == []


def test_instantiate_into_campaign_rolls_back_entity_on_sheet_write_failure(client, tmp_path):
    mid = _seed_content_module_bad_sheet_type(client, tmp_path)
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": mid})
    r = client.post(f"/api/campaigns/{cid}/items/instantiate/{mid}/lantern")
    assert r.status_code == 400
    assert client.get(f"/api/campaigns/{cid}/items/lantern-of-winnowing").status_code == 404
    assert client.get(f"/api/campaigns/{cid}/items").json() == []


def test_sheets_write_can_raise_module_not_found():
    """Verify that store.sheets._validate_write_target can raise ModuleNotFound
    when the module pack is missing (TOCTOU race scenario between resolve and
    _validate_write_target in sheets.write)."""
    # Call _validate_write_target directly with a non-existent module to confirm
    # ModuleNotFound is one of its possible exceptions
    with pytest.raises(store.modules.ModuleNotFound):
        store.sheets._validate_write_target("nonexistent-module-xyz", "items", "test", "trinket")


def test_instantiate_into_campaign_exception_handling_covers_module_not_found(client, tmp_path):
    """Verify that post_campaign_instantiate's exception handler correctly catches
    both SheetError (existing) and ModuleNotFound (TOCTOU race) by confirming the
    route properly handles ModuleNotFound raised during sheet write."""
    from unittest.mock import patch
    mid = _seed_content_module(client, tmp_path, statted=True)
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": mid})

    # Set up a patch that raises ModuleNotFound when sheets.write is called
    # This simulates the TOCTOU race condition
    def mock_write_raises_module_not_found(*args, **kwargs):
        raise store.modules.ModuleNotFound(mid)

    with patch("grimoire.store.sheets.write", side_effect=mock_write_raises_module_not_found):
        # Post request should return 400 (not 500) because the exception is now caught
        r = client.post(f"/api/campaigns/{cid}/items/instantiate/{mid}/lantern")
        assert r.status_code == 400, f"Expected 400 but got {r.status_code}: {r.json()}"
        # Verify entity was rolled back
        items = client.get(f"/api/campaigns/{cid}/items").json()
        assert items == [], "Entity should have been rolled back"


def test_campaign_module_binding_api(client):
    wid, cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r == {"setting": "", "resolved": None, "source": None}

    assert client.put(f"/api/worlds/{wid}/module",
                      json={"module": "pool-basic"}).json()["ok"] is True
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r["resolved"] == "pool-basic" and r["source"] == "world"

    client.put(f"/api/campaigns/{cid}/module", json={"module": "none"})
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r["resolved"] is None and r["setting"] == "none"

    client.put(f"/api/campaigns/{cid}/module", json={"module": "d20-basic"})
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r["resolved"] == "d20-basic" and r["source"] == "campaign"

    assert client.put(f"/api/campaigns/{cid}/module",
                      json={"module": "ghost"}).status_code == 404
    assert client.put(f"/api/worlds/{wid}/module",
                      json={"module": "ghost"}).status_code == 404


def test_campaign_module_rebind_clears_baselines(client):
    """Scene-start sheet baselines (audit.py, mechanics Phase 5) are pinned to
    the module that was live when captured -- a rebind invalidates all of
    them, so put_campaign_module must clear the campaign's baseline file."""
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"})
    assert store.audit.read_baselines(cid) != {}

    client.put(f"/api/campaigns/{cid}/module", json={"module": "d20-basic"})
    assert store.audit.read_baselines(cid) == {}

    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T2"})
    assert store.audit.read_baselines(cid) != {}

    client.put(f"/api/campaigns/{cid}/module", json={"module": "none"})
    assert store.audit.read_baselines(cid) == {}


def test_world_module_rebind_clears_non_overridden_campaign_baselines(client):
    wid = _world(client)
    cid_a = client.post("/api/campaigns", json={"name": "A", "world": wid}).json()["id"]
    cid_b = client.post("/api/campaigns", json={"name": "B", "world": wid}).json()["id"]
    client.put(f"/api/worlds/{wid}/module", json={"module": "pool-basic"})
    client.put(f"/api/campaigns/{cid_b}/module", json={"module": "d20-basic"})  # override

    client.post(f"/api/campaigns/{cid_a}/scenes", json={"title": "T"})
    client.post(f"/api/campaigns/{cid_b}/scenes", json={"title": "T"})
    assert store.audit.read_baselines(cid_a) != {}
    assert store.audit.read_baselines(cid_b) != {}

    client.put(f"/api/worlds/{wid}/module", json={"module": "d20-basic"})
    assert store.audit.read_baselines(cid_a) == {}   # inherited world default -> cleared
    assert store.audit.read_baselines(cid_b) != {}   # own override -> untouched


def test_campaign_module_put_serializes_on_campaign_lock(client, module_scene):
    """Paused-writer proof for put_campaign_module (routes.py ~2952-2962): the
    rebind (set_campaign_module + clear_baselines) runs under sheets.lock_for(cid),
    so a rebind PUT genuinely blocks behind any writer already holding that
    campaign's sheet lock instead of racing it and landing a stale-baseline
    window. Deleting the `with store.sheets.lock_for(cid):` wrapper from that
    route makes this test fail (the PUT thread completes immediately, before
    the lock is released)."""
    import threading
    cid, sid = module_scene
    assert store.audit.read_baselines(cid) != {}
    result: dict = {}
    with store.sheets.lock_for(cid):
        def putter():
            result["resp"] = client.put(f"/api/campaigns/{cid}/module", json={"module": "none"})
        t = threading.Thread(target=putter)
        t.start()
        t.join(1.5)
        assert t.is_alive()                             # blocked behind the held lock
        assert store.audit.read_baselines(cid) != {}     # rebind hasn't run yet
    t.join(1.5)
    assert not t.is_alive()
    assert result["resp"].status_code == 200
    assert store.audit.read_baselines(cid) == {}


def test_world_module_put_serializes_on_affected_campaign_lock(client):
    """Paused-writer proof for put_world_module (routes.py ~505-534): it takes
    every affected (non-overridden) campaign's sheet lock via an ExitStack
    before rebinding, so a concurrent writer holding one of those campaigns'
    locks blocks the whole PUT -- not just that one campaign's slice of it.
    Deleting the ExitStack/lock_for wrapper from that route makes this test
    fail the same way as the campaign-PUT variant above."""
    import threading
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.put(f"/api/worlds/{wid}/module", json={"module": "pool-basic"})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"})
    assert store.audit.read_baselines(cid) != {}
    result: dict = {}
    with store.sheets.lock_for(cid):
        def putter():
            result["resp"] = client.put(f"/api/worlds/{wid}/module", json={"module": "d20-basic"})
        t = threading.Thread(target=putter)
        t.start()
        t.join(1.5)
        assert t.is_alive()
        assert store.audit.read_baselines(cid) != {}
    t.join(1.5)
    assert not t.is_alive()
    assert result["resp"].status_code == 200
    assert store.audit.read_baselines(cid) == {}


def test_world_module_put_locks_newly_inheriting_campaign(client):
    """Regression test for the Phase 5 Task 11 audit finding: put_world_module
    used to enumerate "affected" (non-overridden) campaigns from metadata
    BEFORE acquiring any campaign sheet lock, then lock only that stale set.
    A concurrent campaign-module PUT that clears a campaign's override --
    making it newly-inheriting -- between that enumeration and the world
    route's rebind would never be locked by the world route at all, so a
    paused writer on that campaign could let it resolve the old world module
    and write while/after the new default published, stranding stale
    baselines. put_world_module now locks EVERY campaign of the world
    (regardless of override) up front, then re-reads each override under the
    lock before deciding whether to clear baselines -- so a campaign-module
    PUT clearing X's override and a world-module PUT both contend on X's
    sheet lock instead of racing around it.

    X starts with a per-campaign override; Y is already inheriting. With X's
    sheet lock held by a paused writer, both a campaign PUT (clearing X's
    override) and a world PUT (changing the world default) must block on
    X's lock -- proving the world route now enumerates/locks under the
    lock, not before it. After the lock releases, both PUTs must succeed,
    and X must end up with clean baselines regardless of which PUT actually
    performs the clear (whichever wins the race to X's lock first observes
    X still overridden and leaves it alone; the other -- running after --
    sees X freshly inheriting and clears it). Y, already inheriting, must
    also be cleared by the world PUT."""
    import threading
    wid = _world(client)
    cid_x = client.post("/api/campaigns", json={"name": "X", "world": wid}).json()["id"]
    cid_y = client.post("/api/campaigns", json={"name": "Y", "world": wid}).json()["id"]
    client.put(f"/api/worlds/{wid}/module", json={"module": "pool-basic"})
    client.put(f"/api/campaigns/{cid_x}/module", json={"module": "d20-basic"})  # override

    client.post(f"/api/campaigns/{cid_x}/scenes", json={"title": "T"})
    client.post(f"/api/campaigns/{cid_y}/scenes", json={"title": "T"})
    assert store.audit.read_baselines(cid_x) != {}
    assert store.audit.read_baselines(cid_y) != {}

    results: dict = {}
    with store.sheets.lock_for(cid_x):
        def clear_override():
            results["campaign"] = client.put(
                f"/api/campaigns/{cid_x}/module", json={"module": ""})
        t_a = threading.Thread(target=clear_override)
        t_a.start()
        t_a.join(1.5)
        assert t_a.is_alive()                             # blocked behind the held lock

        def rebind_world():
            results["world"] = client.put(
                f"/api/worlds/{wid}/module", json={"module": "d20-basic"})
        t_b = threading.Thread(target=rebind_world)
        t_b.start()
        t_b.join(1.5)
        assert t_b.is_alive()                             # also blocked: world PUT now
                                                            # locks every campaign of wid,
                                                            # including X, up front
        assert store.audit.read_baselines(cid_x) != {}     # untouched while X's lock is held

    t_a.join(2)
    t_b.join(2)
    assert not t_a.is_alive()
    assert not t_b.is_alive()
    assert results["campaign"].status_code == 200
    assert results["world"].status_code == 200

    assert store.audit.read_baselines(cid_x) == {}
    assert store.audit.read_baselines(cid_y) == {}


def test_create_campaign_with_module(client):
    _world(client)
    r = client.post("/api/campaigns",
                    json={"name": "Mechanical", "world": "w", "module": "pool-basic"})
    cid = r.json()["id"]
    assert client.get(f"/api/campaigns/{cid}/module").json()["resolved"] == "pool-basic"
    assert client.post(
        "/api/campaigns",
        json={"name": "Broken", "world": "w", "module": "ghost"}).status_code == 404


# ---- styles ----
def test_style_crud_and_builtin_list(client):
    r = client.get("/api/styles").json()
    ids = {s["id"] for s in r}
    assert "gothic-horror" in ids
    assert all(s["built_in"] for s in r if s["id"] == "gothic-horror")

    r = client.post("/api/styles", json={"name": "Cozy Mystery", "description": "Gentle.",
                                         "tags": ["cozy"], "body": "Keep it warm."})
    assert r.status_code == 200
    sid = r.json()["id"]

    detail = client.get(f"/api/styles/{sid}").json()
    assert detail["meta"]["name"] == "Cozy Mystery"
    assert detail["meta"]["built_in"] is False
    assert detail["body"].strip() == "Keep it warm."

    assert client.put(f"/api/styles/{sid}", json={"body": "Warmer."}).status_code == 200
    assert client.get(f"/api/styles/{sid}").json()["body"].strip() == "Warmer."

    assert client.delete(f"/api/styles/{sid}").status_code == 200
    assert client.get(f"/api/styles/{sid}").status_code == 404


def test_style_unknown_id_404(client):
    assert client.get("/api/styles/nope-not-real").status_code == 404
    assert client.put("/api/styles/nope-not-real", json={"body": "x"}).status_code == 404
    assert client.delete("/api/styles/nope-not-real").status_code == 404


def test_builtin_style_cannot_be_edited_or_deleted(client):
    assert client.put("/api/styles/gothic-horror", json={"body": "nope"}).status_code == 400
    assert client.delete("/api/styles/gothic-horror").status_code == 400


def test_duplicate_style_creates_an_editable_copy(client):
    r = client.post("/api/styles/gothic-horror/duplicate")
    assert r.status_code == 200
    new_id = r.json()["id"]
    detail = client.get(f"/api/styles/{new_id}").json()
    assert detail["meta"]["built_in"] is False
    assert detail["meta"]["name"] == "Gothic Horror (copy)"
    assert client.put(f"/api/styles/{new_id}", json={"body": "edited"}).status_code == 200


def test_config_default_style_roundtrip(client):
    r = client.put("/api/config", json={"default_style_id": "gothic-horror"})
    assert r.json()["default_style_id"] == "gothic-horror"
    assert client.get("/api/config").json()["default_style_id"] == "gothic-horror"


def test_campaign_style_roundtrip(client):
    wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/style").json() == {"style_id": ""}
    r = client.put(f"/api/campaigns/{cid}/style", json={"style_id": "noir-detective"})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/style").json() == {"style_id": "noir-detective"}
    # visible on the campaign meta too, for free
    assert client.get(f"/api/campaigns/{cid}").json()["meta"]["style_id"] == "noir-detective"


def test_campaign_style_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nope/style").status_code == 404
    assert client.put("/api/campaigns/nope/style", json={"style_id": "noir-detective"}).status_code == 404


def test_scene_style_roundtrip(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/style").json() == {"style_id": ""}
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/style", json={"style_id": "pulp-adventure"})
    assert r.status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/style").json() == {"style_id": "pulp-adventure"}


def test_scene_style_unknown_scene_404(client):
    wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/style").status_code == 404
    assert client.put(f"/api/campaigns/{cid}/scenes/nope/style", json={"style_id": "pulp-adventure"}).status_code == 404


# ---- sheets (#161) ----
def test_campaign_sheet_routes(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]

    base = f"/api/campaigns/{cid}/sheets/characters/{chid}"
    assert client.get(base).json()["sheet"] is None
    r = client.put(base, json={"sheet_type": "medium", "fields": None})
    assert r.json()["ok"] is True
    got = client.get(base).json()["sheet"]
    assert got["sheet_type"] == "medium" and got["errors"] == []
    assert "sight_pool" in got["derived"]

    idx = client.get(f"/api/campaigns/{cid}/sheets").json()
    assert idx["coverage"]["characters"]["sheeted"] == 1
    assert ["characters", chid] in idx["refs"]

    # unknown sheet_type -> 400 (matching `expected` clears the CAS gate first)
    snap = {"sheet_type": got["sheet_type"], "fields": got["fields"], "gen": got["gen"]}
    assert client.put(base, json={"sheet_type": "ghost", "expected": snap}).status_code == 400
    # a stale/omitted `expected` on an existing sheet -> 409, not 400
    assert client.put(base, json={"sheet_type": "medium"}).status_code == 409
    live = client.get(base).json()["sheet"]
    r = client.delete(base, params={"gen": live["gen"]})
    assert r.json()["ok"] is True
    assert client.get("/api/campaigns/nope/sheets").status_code == 404


# expected -- mandatory whole-sheet CAS on the sheet PUT route (mechanics
# Phase 5, Task 3). Brief's placeholder sheet_type "adventurer" is adapted to
# pool-basic's real "medium" characters type, reusing this file's _campaign
# helper in place of the brief's placeholder `module_campaign` fixture.
def test_sheet_put_cas(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]

    base = f"/api/campaigns/{cid}/sheets/characters/{chid}"
    r = client.put(base, json={"sheet_type": "medium", "fields": None, "expected": None})
    assert r.status_code == 200
    sheet = client.get(base).json()["sheet"]
    snap = {"sheet_type": sheet["sheet_type"], "fields": sheet["fields"], "gen": sheet["gen"]}
    # stale creation assertion -> 409
    r = client.put(base, json={"sheet_type": "medium", "fields": None, "expected": None})
    assert r.status_code == 409
    # matching snapshot with a real field change -> 200; reusing that now-stale
    # snapshot afterwards -> 409 (a same-value write wouldn't re-mint gen and
    # so wouldn't actually invalidate the snapshot -- see sheets._next_gen)
    new_fields = {**sheet["fields"], "vigor": 3}
    r = client.put(base, json={"sheet_type": "medium", "fields": new_fields, "expected": snap})
    assert r.status_code == 200
    r = client.put(base, json={"sheet_type": "medium", "fields": new_fields, "expected": snap})
    assert r.status_code == 409


# gen -- mandatory expected_gen CAS on the sheet DELETE route (mechanics
# Phase 5, Task 4). Brief's placeholder sheet_type "adventurer" is adapted to
# pool-basic's real "medium" characters type, reusing this file's _campaign
# helper (see test_sheet_put_cas above for the pattern).
def test_sheet_delete_cas(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]

    base = f"/api/campaigns/{cid}/sheets/characters/{chid}"
    r = client.put(base, json={"sheet_type": "medium", "fields": None, "expected": None})
    assert r.status_code == 200
    g = client.get(base).json()["sheet"]["gen"]

    # missing/stale gen -> 409, sheet still there
    r = client.delete(base)
    assert r.status_code == 409
    assert client.get(base).json()["sheet"] is not None

    r = client.delete(base, params={"gen": g})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get(base).json()["sheet"] is None

    # gone -- deleting again is False, not a conflict
    r = client.delete(base, params={"gen": g})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_instantiate_still_creates_sheeted_content(client, tmp_path):
    # Regression for the instantiate route's server-side expected=None call
    # (routes.py post_campaign_instantiate) plus its rollback path: a fresh
    # entity is created this request, so the internal sheets.write can never
    # collide with an existing sheet -- both the entity and its sheet must
    # exist afterwards. Reuses test_instantiate_into_campaign's fixture
    # content id, but with statted=True (the Phase 7 instantiate scenario
    # that actually exercises a sheet write).
    mid = _seed_content_module(client, tmp_path, statted=True)
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": mid})
    r = client.post(f"/api/campaigns/{cid}/items/instantiate/{mid}/lantern")
    assert r.status_code == 200
    eid = r.json()["id"]
    entity = client.get(f"/api/campaigns/{cid}/items/{eid}").json()
    assert entity["meta"]["name"] == "Lantern of Winnowing"
    sheet = client.get(f"/api/campaigns/{cid}/sheets/items/{eid}").json()["sheet"]
    assert sheet["sheet_type"] == "trinket"
    assert sheet["fields"]["power"] == 2


def test_campaign_sheet_routes_without_module(client):
    _, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/sheets").json()["coverage"] == {}
    r = client.put(f"/api/campaigns/{cid}/sheets/characters/mara",
                   json={"sheet_type": "medium"})
    assert r.status_code == 400


def test_world_sheet_routes(client):
    wid = _world(client)
    idx = client.get(f"/api/worlds/{wid}/sheets").json()
    assert idx == {"modules": [], "default": ""}
    base = f"/api/worlds/{wid}/sheets/pool-basic/characters/mara"
    assert client.put(base, json={"sheet_type": "medium", "fields": None}).json()["ok"] is True
    assert client.get(base).json()["sheet"]["sheet_type"] == "medium"
    idx = client.get(f"/api/worlds/{wid}/sheets").json()
    assert idx["modules"] == ["pool-basic"]
    cov = client.get(f"/api/worlds/{wid}/sheets/pool-basic").json()
    assert ["characters", "mara"] in cov["refs"]
    assert client.get(f"/api/worlds/{wid}/sheets/ghost").status_code == 404
    assert client.put(f"/api/worlds/{wid}/sheets/ghost/characters/mara",
                      json={"sheet_type": "medium"}).status_code == 404
    gen = client.get(base).json()["sheet"]["gen"]
    assert client.delete(f"{base}?gen={gen}").json()["ok"] is True


def test_world_sheet_put_stale_expected_409(client):
    wid = _world(client)
    base = f"/api/worlds/{wid}/sheets/pool-basic/characters/mara"
    client.put(base, json={"sheet_type": "medium", "fields": None})
    r = client.put(base, json={
        "sheet_type": "medium", "fields": {"vigor": 2},
        "expected": {"sheet_type": "medium", "fields": {}, "gen": "stale"}})
    assert r.status_code == 409


def test_world_sheet_delete_requires_gen_409(client):
    wid = _world(client)
    base = f"/api/worlds/{wid}/sheets/pool-basic/characters/mara"
    client.put(base, json={"sheet_type": "medium", "fields": None})
    assert client.delete(base).status_code == 409  # no ?gen= against an existing sheet
    gen = client.get(base).json()["sheet"]["gen"]
    assert client.delete(f"{base}?gen={gen}").json() == {"ok": True}


# ---- mechanics: roll proposals & manual checks (#162, Phase 4) -------------
def _mech_scene(client, module="pool-basic"):
    """A module-bound campaign with one sheeted, cast character (Mara)."""
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": module})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    if module == "d20-basic":
        store.sheets.write(cid, "characters", chid, "warrior",
                           {"str": 16, "dex": 12, "athletics": 3}, expected=None)
    else:
        store.sheets.write(cid, "characters", chid, "medium",
                           {"vigor": 3, "brawl": 2, "wits": 2, "occult": 1}, expected=None)
    return cid, sid, chid


def _frames(resp):
    return [json.loads(l[len("data: "):]) for l in resp.text.splitlines()
            if l.startswith("data: ")]


def _emit_fence(client, cid, sid, body_json, pre="pre-fence beat.\n"):
    """Re-override get_llm to stream a narration + ```roll fence, then POST a
    normal chat turn — the server cuts the stream and mints a pending record."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouter([pre, "```roll\n", body_json, "\n```", "trailing"])
    return client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "go"})


def test_chat_fence_cuts_and_persists_proposal(client):
    cid, sid, _ = _mech_scene(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(
        ["She lunges—\n", "```roll\n", '{"check": "brawl", "actor": "characters:mara"}',
         "\n```", "trailing"])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "go"})
    assert resp.status_code == 200
    frames = _frames(resp)
    deltas = "".join(f["delta"] for f in frames if "delta" in f)
    assert deltas == "She lunges—\n"                       # pre-fence narration only
    assert "`" not in deltas and "brawl" not in deltas     # no fence chars leaked
    kinds = [next(iter(f)) for f in frames]
    assert kinds.index("proposal") < kinds.index("done")   # proposal precedes done
    rec = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec["status"] == "pending" and rec["payload"]["check"] == "brawl"
    assert rec["payload"]["actor"] == "characters:mara" and rec["payload"]["problems"] == []
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1]["content"].startswith("She lunges")     # fence stripped from transcript
    assert not any("`" in m["content"] for m in msgs)


def test_proposal_accept_walk_and_idempotency(client):
    cid, sid, _ = _mech_scene(client)
    _emit_fence(client, cid, sid,
                '{"check": "brawl", "actor": "characters:mara", "difficulty": 6}')
    rec = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec["status"] == "pending"
    body = {"proposal": rec["id"], "action": "accept",
            "check": "brawl", "actor": "characters:mara", "difficulty": 6, "modifier": 0}

    # mismatched id -> 409, nothing streamed
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                       json={**body, "proposal": "pr-999999"}).status_code == 409

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["The blow ", "lands."])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=body)
    assert resp.status_code == 200 and 'data: {"done": true}' in resp.text
    assert "The blow lands." in "".join(
        f.get("delta", "") for f in _frames(resp))       # continuation streamed live

    entries = client.get(f"/api/campaigns/{cid}/rolls").json()
    tagged = [e for e in entries if e.get("proposal") == rec["id"]]
    assert len(tagged) == 1
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    roll_lines = [m for m in msgs if m["content"].startswith("\U0001F3B2")]
    assert len(roll_lines) == 1 and "Mara" in roll_lines[0]["content"]
    assert "Vigor + Brawl" in roll_lines[0]["content"]
    assert msgs[-1]["content"] == "The blow lands."        # continuation persisted after the roll

    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "narrated"
    assert rec2["resolution"]["roll_id"] == tagged[0]["id"]
    assert "line_intent" in rec2["resolution"]

    # idempotent retry after narrated: immediate done, no new roll, no new line
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=body)
    assert 'data: {"done": true}' in resp.text
    assert len([e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
                if e.get("proposal") == rec["id"]]) == 1
    msgs2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert len([m for m in msgs2 if m["content"].startswith("\U0001F3B2")]) == 1


def _accept_body(rec, **over):
    b = {"proposal": rec["id"], "action": "accept", "check": "brawl",
         "actor": "characters:mara", "difficulty": 6, "modifier": 0}
    b.update(over)
    return b


def _pending(client, cid, sid,
             body_json='{"check": "brawl", "actor": "characters:mara", "difficulty": 6}'):
    _emit_fence(client, cid, sid, body_json)
    return client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]


def _roll_lines(client, cid, sid):
    return [m for m in client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
            if m["content"].startswith("\U0001F3B2")]


def test_proposal_decline(client):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["It never ", "happened."])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                       json={"proposal": rec["id"], "action": "decline"})
    assert resp.status_code == 200 and 'data: {"done": true}' in resp.text
    assert "It never happened." in "".join(f.get("delta", "") for f in _frames(resp))
    assert client.get(f"/api/campaigns/{cid}/rolls").json() == []
    assert _roll_lines(client, cid, sid) == []
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1]["content"] == "It never happened."
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "narrated"


def test_new_send_supersedes(client):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["moving on"])
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "never mind"})
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "superseded"
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                       json=_accept_body(rec)).status_code == 409
    assert client.get(f"/api/campaigns/{cid}/rolls").json() == []


# a send that dies on the missing-key guard must not have durably retired the
# user's pending chip first — supersede only runs once the send is actually
# going to happen (routes.py: after _require_scene *and* _require_key).
def test_chat_missing_key_does_not_supersede_pending_proposal(client):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.put("/api/config", json={"openrouter_key": ""})
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "never mind"})
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["id"] == rec["id"] and rec2["status"] == "pending"


def test_retry_missing_key_does_not_supersede_pending_proposal(client):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.put("/api/config", json={"openrouter_key": ""})
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry")
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["id"] == rec["id"] and rec2["status"] == "pending"


def test_regenerate_missing_key_does_not_supersede_pending_proposal(client):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.put("/api/config", json={"openrouter_key": ""})
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["id"] == rec["id"] and rec2["status"] == "pending"


def test_manual_check_and_availability(client):
    cid, sid, _ = _mech_scene(client)
    actors = client.get(f"/api/campaigns/{cid}/scenes/{sid}/checks").json()["actors"]
    assert len(actors) == 1 and actors[0]["ref"] == "characters:mara"
    ids = {c[0] for c in actors[0]["checks"]}
    assert {"brawl", "perception"} <= ids

    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/check",
                       json={"check": "brawl", "actor": "characters:mara", "difficulty": 6})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["resolution"]["roll_id"] == body["roll"]["id"]
    assert body["roll"].get("proposal") is None
    assert body["message"].startswith("\U0001F3B2") and "Mara" in body["message"]
    entries = client.get(f"/api/campaigns/{cid}/rolls").json()
    assert len(entries) == 1 and entries[0]["id"] == body["roll"]["id"]
    lines = _roll_lines(client, cid, sid)
    assert len(lines) == 1 and lines[0]["content"] == body["message"]


def test_proposal_routes_without_module(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/check",
                       json={"check": "brawl", "actor": "characters:mara"}).status_code == 400
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                       json={"proposal": "pr-1", "action": "accept"}).status_code == 409
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"] is None
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/checks").json()["actors"] == []


def test_follow_up_fence_handoff(client):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(
        ["The blow lands.\n", "```roll\n",
         '{"check": "perception", "actor": "characters:mara"}', "\n```", "x"])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 200
    kinds = [next(iter(f)) for f in _frames(resp)]
    assert "proposal" in kinds and kinds.index("proposal") < kinds.index("done")
    old = rec["id"]
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert any(m["content"] == "The blow lands." for m in msgs)
    new = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert new["status"] == "pending" and new["id"] != old
    assert new["payload"]["check"] == "perception"


def test_recovery_get_after_resolved(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    monkeypatch.setattr(routes, "_continuation_messages",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no continuation")))
    with pytest.raises(RuntimeError):
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert got["status"] == "resolved" and got["resolution"]["roll_id"]


# ---- failure injection at every side-effect boundary ----------------------
def test_accept_resolve_failure_reverts_to_pending(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    monkeypatch.setattr(store.checks, "resolve_check",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 200
    assert any("error" in f for f in _frames(resp))
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "pending"
    assert client.get(f"/api/campaigns/{cid}/rolls").json() == []
    assert _roll_lines(client, cid, sid) == []


def test_accept_superseded_mid_resolve_discards_roll(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    real = store.checks.resolve_check
    def sneaky(*a, **k):
        res = real(*a, **k)
        store.proposals.supersede(cid, sid)
        return res
    monkeypatch.setattr(store.checks, "resolve_check", sneaky)
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 409
    assert client.get(f"/api/campaigns/{cid}/rolls").json() == []
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "superseded"
    assert _roll_lines(client, cid, sid) == []


def test_accept_crash_before_roll_id_heals_on_retry(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    real_append = store.rolls.find_or_append_by_proposal
    real_transition = store.proposals.transition
    state = {"appended": False, "raised": False}
    def tracking_append(*a, **k):
        state["appended"] = True
        return real_append(*a, **k)
    def flaky_transition(*a, **k):
        if state["appended"] and not state["raised"]:
            state["raised"] = True
            raise RuntimeError("crash before roll_id backfill")
        return real_transition(*a, **k)
    monkeypatch.setattr(store.rolls, "find_or_append_by_proposal", tracking_append)
    monkeypatch.setattr(store.proposals, "transition", flaky_transition)
    with pytest.raises(RuntimeError):
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert len(client.get(f"/api/campaigns/{cid}/rolls").json()) == 1
    # restore only these attrs — never monkeypatch.undo(), which would also
    # revert the client fixture's GRIMOIRE_HOME (the monkeypatch is shared).
    monkeypatch.setattr(store.rolls, "find_or_append_by_proposal", real_append)
    monkeypatch.setattr(store.proposals, "transition", real_transition)

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["healed"])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 200
    tagged = [e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
              if e.get("proposal") == rec["id"]]
    assert len(tagged) == 1
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "narrated" and rec2["resolution"]["roll_id"] == tagged[0]["id"]
    assert len(_roll_lines(client, cid, sid)) == 1


def test_accept_crash_before_continuation_heals(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    real_cont = routes._continuation_messages
    monkeypatch.setattr(routes, "_continuation_messages",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no continuation")))
    with pytest.raises(RuntimeError):
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    rec_mid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec_mid["status"] == "resolved"
    assert len(_roll_lines(client, cid, sid)) == 1
    monkeypatch.setattr(routes, "_continuation_messages", real_cont)  # not undo(): see above

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Continued."])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 200
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "narrated"
    assert len(_roll_lines(client, cid, sid)) == 1
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1]["content"] == "Continued."


def test_two_identical_lines_both_survive(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    fixed = {"check": "brawl", "check_label": "Vigor + Brawl", "actor": "characters:mara",
             "actor_label": "Mara", "notation": "5d10 t6",
             "result": store.dice.roll("5d10 t6", seed=1), "tier": "success",
             "difficulty": 6, "modifier": 0, "tier_warnings": []}
    monkeypatch.setattr(store.checks, "resolve_check", lambda *a, **k: dict(fixed))

    rec1 = _pending(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["one"])
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                       json=_accept_body(rec1)).status_code == 200
    rec2 = _pending(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["two"])
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                       json=_accept_body(rec2)).status_code == 200

    lines = _roll_lines(client, cid, sid)
    assert len(lines) == 2 and lines[0]["content"] == lines[1]["content"]
    assert len(client.get(f"/api/campaigns/{cid}/rolls").json()) == 2


class _SupersedingStream:
    def __init__(self, cid, sid):
        self.cid, self.sid = cid, sid

    async def stream(self, messages, cfg):
        yield "stale continuation "
        yield "text"
        store.proposals.supersede(self.cid, self.sid)
        store.scenes.append_message(self.cid, self.sid, "user", "new send")
        store.scenes.append_message(self.cid, self.sid, "assistant", "fresh reply")


def test_continuation_vs_supersede_race(client):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = lambda: _SupersedingStream(cid, sid)
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 200
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "superseded"
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1]["content"] == "fresh reply"
    assert not any("stale continuation" in m["content"] for m in msgs)
    assert len(_roll_lines(client, cid, sid)) == 1


def test_concurrent_resolved_retries_persist_once(client, monkeypatch):
    import threading
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    real_cont = routes._continuation_messages
    monkeypatch.setattr(routes, "_continuation_messages",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    monkeypatch.setattr(routes, "_continuation_messages", real_cont)  # not undo()

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["cont"])
    codes = []
    def racer():
        codes.append(client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                                 json=_accept_body(rec)).status_code)
    threads = [threading.Thread(target=racer) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes == [200, 200]

    tagged = [e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
              if e.get("proposal") == rec["id"]]
    assert len(tagged) == 1
    assert len(_roll_lines(client, cid, sid)) == 1
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert [m["content"] for m in msgs].count("cont") == 1
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "narrated"


def test_crash_mid_continuation_persist_heals(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    real_persist = routes._persist_reply
    def boom_persist(c, s, text):
        store.scenes.append_message(c, s, "assistant", "PARTIAL")
        raise RuntimeError("crash mid persist")
    monkeypatch.setattr(routes, "_persist_reply", boom_persist)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["full continuation"])
    try:
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    except Exception:  # noqa: BLE001
        pass
    rec_mid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec_mid["status"] == "resolved" and "narration_intent" in rec_mid
    assert any(m["content"] == "PARTIAL"
               for m in client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"])
    monkeypatch.setattr(routes, "_persist_reply", real_persist)  # not undo()

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["full continuation"])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 200
    contents = [m["content"] for m in
                client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]]
    assert "PARTIAL" not in contents
    assert contents[-1] == "full continuation" and contents.count("full continuation") == 1
    assert len(_roll_lines(client, cid, sid)) == 1
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "narrated"


def test_manual_roll_in_crash_window_survives_trim(client, monkeypatch):
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    real_persist = routes._persist_reply
    def boom_persist(c, s, text):
        store.scenes.append_message(c, s, "assistant", "PARTIAL")
        raise RuntimeError("crash")
    monkeypatch.setattr(routes, "_persist_reply", boom_persist)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["cont"])
    try:
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    except Exception:  # noqa: BLE001
        pass
    monkeypatch.setattr(routes, "_persist_reply", real_persist)  # not undo()
    mr = client.post(f"/api/campaigns/{cid}/scenes/{sid}/check",
                     json={"check": "perception", "actor": "characters:mara"})
    assert mr.status_code == 200
    manual_line = mr.json()["message"]

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["real continuation"])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 200
    contents = [m["content"] for m in
                client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]]
    assert manual_line in contents
    assert "PARTIAL" not in contents
    assert contents[-1] == "real continuation" and contents.count("real continuation") == 1


def test_concurrent_accept_vs_manual_check_distinct_entries(client):
    import threading
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["cont"])
    barrier = threading.Barrier(2)
    def accept():
        barrier.wait()
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    def manual():
        barrier.wait()
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/check",
                    json={"check": "perception", "actor": "characters:mara"})
    threads = [threading.Thread(target=accept), threading.Thread(target=manual)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    entries = client.get(f"/api/campaigns/{cid}/rolls").json()
    ids = [e["id"] for e in entries]
    assert len(entries) == 2 and len(set(ids)) == 2
    assert sum(1 for e in entries if e.get("proposal") == rec["id"]) == 1
    assert sum(1 for e in entries if e.get("proposal") is None) == 1


def test_valid_json_fence_missing_check_flags_problem(client):
    cid, sid, _ = _mech_scene(client)
    resp = _emit_fence(client, cid, sid, '{"actor": "characters:mara"}')
    assert resp.status_code == 200
    rec = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec["status"] == "pending"
    payload = rec["payload"]
    assert payload["check"] is None
    assert "roll request had no check id" in payload["problems"]


def test_project_resolution_none_when_record_replaced(client):
    # Reproduces the narrow window in finding #2: the route reads status
    # "resolved" for pid, then — before _project_resolution acquires its
    # lock — a supersede + brand-new fence/send replaces the scene's record
    # with a different id. _project_resolution must stop dead: no roll
    # append, no transcript line, no TypeError on a None resolution.
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    old_pid = rec["id"]
    store.proposals.claim(cid, sid, old_pid)
    resolution = store.checks.resolve_check(cid, "brawl", "characters:mara", 6, 0)
    assert store.proposals.transition(cid, sid, old_pid, ("resolving",), "resolved", resolution)

    store.proposals.supersede(cid, sid)
    store.proposals.new(cid, sid, {"check": "brawl", "actor": "characters:mara",
                                   "problems": []})

    result = routes._project_resolution(cid, sid, old_pid)
    assert result is None
    assert [e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
            if e.get("proposal") == old_pid] == []
    assert _roll_lines(client, cid, sid) == []


def test_superseded_same_id_still_projects(client):
    # Existing spec-mandated behavior must survive the new guard: a record
    # that keeps its id but was superseded (status flips resolved ->
    # superseded with no replacement record) still projects — its roll
    # stands in the transcript as history per spec; only the automatic
    # continuation is cancelled elsewhere (commit_narration), not this.
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    pid = rec["id"]
    store.proposals.claim(cid, sid, pid)
    resolution = store.checks.resolve_check(cid, "brawl", "characters:mara", 6, 0)
    assert store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", resolution)

    store.proposals.supersede(cid, sid)  # same id — no new() call follows
    assert store.proposals.get(cid, sid)["status"] == "superseded"

    result = routes._project_resolution(cid, sid, pid)
    assert result is not None and "roll_id" in result and "line_intent" in result
    tagged = [e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
              if e.get("proposal") == pid]
    assert len(tagged) == 1 and tagged[0]["id"] == result["roll_id"]
    assert len(_roll_lines(client, cid, sid)) == 1


def _resolve_then_supersede(client, cid, sid):
    """Drive a proposal to resolved, then supersede it keeping the same id
    (no new() follows) — the same-id superseded state the fix must heal."""
    rec = _pending(client, cid, sid)
    pid = rec["id"]
    store.proposals.claim(cid, sid, pid)
    resolution = store.checks.resolve_check(cid, "brawl", "characters:mara", 6, 0)
    assert store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", resolution)
    store.proposals.supersede(cid, sid)
    assert store.proposals.get(cid, sid)["status"] == "superseded"
    return rec, pid


def test_superseded_same_id_projection_persists_metadata(client):
    # The finding: projection of a same-id superseded record must PERSIST its
    # roll_id AND line_intent onto the stored resolution (a status CAS would
    # have silently lost them once superseded), leaving status superseded.
    cid, sid, _ = _mech_scene(client)
    rec, pid = _resolve_then_supersede(client, cid, sid)

    result = routes._project_resolution(cid, sid, pid)
    assert result is not None

    stored = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert stored["status"] == "superseded"                      # status untouched
    assert stored["resolution"]["roll_id"] == result["roll_id"]  # metadata persisted
    assert "line_intent" in stored["resolution"]
    tagged = [e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
              if e.get("proposal") == pid]
    assert len(tagged) == 1 and tagged[0]["id"] == stored["resolution"]["roll_id"]
    assert len(_roll_lines(client, cid, sid)) == 1


def test_superseded_same_id_crash_before_line_heals_on_stale_post(client, monkeypatch):
    # THE crash window (not one of the spec's two accepted ones): the roll is
    # appended, but the process dies before the 🎲 line is written, leaving a
    # superseded record with a roll logged and no transcript line. The POST
    # route 409s superseded ids, so a stale client's retry must become the
    # recovery path — projecting idempotently before the 409.
    cid, sid, _ = _mech_scene(client)
    rec, pid = _resolve_then_supersede(client, cid, sid)

    real_append = store.scenes.append_message
    state = {"raised": False}
    def flaky_append(*a, **k):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("crash before 🎲 line")
        return real_append(*a, **k)
    monkeypatch.setattr(store.scenes, "append_message", flaky_append)
    with pytest.raises(RuntimeError):
        routes._project_resolution(cid, sid, pid)
    # restore only this attr — never monkeypatch.undo() (shared GRIMOIRE_HOME)
    monkeypatch.setattr(store.scenes, "append_message", real_append)

    # roll logged, no line, but roll_id already persisted on the superseded rec
    tagged = [e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
              if e.get("proposal") == pid]
    assert len(tagged) == 1
    assert _roll_lines(client, cid, sid) == []
    mid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert mid["status"] == "superseded" and mid["resolution"]["roll_id"] == tagged[0]["id"]

    # a stale client retries with the old id: 409, but the line is now healed
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 409
    assert len(_roll_lines(client, cid, sid)) == 1
    healed = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert healed["status"] == "superseded"
    assert "line_intent" in healed["resolution"]
    assert len([e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
                if e.get("proposal") == pid]) == 1

    # second POST heals nothing further — fully idempotent
    resp2 = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp2.status_code == 409
    assert len(_roll_lines(client, cid, sid)) == 1
    assert len([e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
                if e.get("proposal") == pid]) == 1


def test_superseded_while_pending_same_id_post_is_plain_409(client):
    # A record superseded while still pending has no resolution to project:
    # the POST must be a plain 409 — no roll, no line, no projection.
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    store.proposals.supersede(cid, sid)          # same id, still pending -> superseded
    stored = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert stored["status"] == "superseded" and stored["resolution"] is None

    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 409
    assert client.get(f"/api/campaigns/{cid}/rolls").json() == []
    assert _roll_lines(client, cid, sid) == []
    after = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert after["status"] == "superseded" and after["resolution"] is None


def test_new_fence_replacement_heals_crashed_projection(client, monkeypatch):
    # Tail of the crash-window family: after a projection crash (roll tagged,
    # 🎲 line missing) the superseded record is the only recovery handle, and
    # proposals.new() on the next turn's fresh fence ERASES it — a stale retry
    # then 409s on the id mismatch and nothing would ever heal the line. The
    # fence path must heal (project) the current record before replacing it.
    cid, sid, _ = _mech_scene(client)
    rec, pid = _resolve_then_supersede(client, cid, sid)

    real_append = store.scenes.append_message
    state = {"raised": False}
    def flaky_append(*a, **k):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("crash before 🎲 line")
        return real_append(*a, **k)
    monkeypatch.setattr(store.scenes, "append_message", flaky_append)
    with pytest.raises(RuntimeError):
        routes._project_resolution(cid, sid, pid)
    # restore only this attr — never monkeypatch.undo() (shared GRIMOIRE_HOME)
    monkeypatch.setattr(store.scenes, "append_message", real_append)
    assert _roll_lines(client, cid, sid) == []          # roll tagged, no line

    # the next chat turn's model emits a fresh fence, replacing the record —
    # the heal must run at replace time, before the handle is erased
    resp = _emit_fence(client, cid, sid,
                       '{"check": "brawl", "actor": "characters:mara", "difficulty": 6}')
    assert resp.status_code == 200

    assert len(_roll_lines(client, cid, sid)) == 1      # old roll's line healed
    assert len([e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
                if e.get("proposal") == pid]) == 1      # still exactly one roll
    fresh = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert fresh["id"] != pid                           # new record minted...
    assert fresh["status"] == "pending" and fresh["resolution"] is None  # ...untouched

    # a stale retry with the old id is now just a 409 with no side effects
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=_accept_body(rec))
    assert resp.status_code == 409
    assert len(_roll_lines(client, cid, sid)) == 1
    assert len([e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
                if e.get("proposal") == pid]) == 1
    after = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert after["id"] == fresh["id"] and after["status"] == "pending"


def test_new_fence_replacement_projects_resolved_record(client):
    # Simpler variant, no crash injection: a resolved-with-resolution record
    # that never got projected at all (crash before projection started) is
    # replaced by a new fence — the heal projects roll + line exactly once
    # before proposals.new() erases the handle.
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    pid = rec["id"]
    store.proposals.claim(cid, sid, pid)
    resolution = store.checks.resolve_check(cid, "brawl", "characters:mara", 6, 0)
    assert store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", resolution)
    assert client.get(f"/api/campaigns/{cid}/rolls").json() == []   # never projected

    resp = _emit_fence(client, cid, sid,
                       '{"check": "brawl", "actor": "characters:mara", "difficulty": 6}')
    assert resp.status_code == 200

    tagged = [e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
              if e.get("proposal") == pid]
    assert len(tagged) == 1
    assert len(_roll_lines(client, cid, sid)) == 1
    fresh = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert fresh["id"] != pid and fresh["status"] == "pending"


def _resolve_with_crashed_line(client, cid, sid, monkeypatch):
    """A resolved record whose projection crashed between the roll append and
    the 🎲 line append: roll tagged with roll_id persisted, no transcript
    line, record still resolved (recoverable — until something retires it)."""
    rec = _pending(client, cid, sid)
    pid = rec["id"]
    store.proposals.claim(cid, sid, pid)
    resolution = store.checks.resolve_check(cid, "brawl", "characters:mara", 6, 0)
    assert store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", resolution)
    real_append = store.scenes.append_message
    state = {"raised": False}
    def flaky_append(*a, **k):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("crash before 🎲 line")
        return real_append(*a, **k)
    monkeypatch.setattr(store.scenes, "append_message", flaky_append)
    with pytest.raises(RuntimeError):
        routes._project_resolution(cid, sid, pid)
    # restore only this attr — never monkeypatch.undo() (shared GRIMOIRE_HOME)
    monkeypatch.setattr(store.scenes, "append_message", real_append)
    mid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert mid["status"] == "resolved" and "roll_id" in mid["resolution"]
    assert _roll_lines(client, cid, sid) == []
    return rec, pid


def test_plain_chat_heals_projection_before_supersede(client, monkeypatch):
    # Last of the crash-window family: a resolved record whose projection
    # crashed (roll tagged, 🎲 line missing) is still recoverable — but an
    # ordinary NON-fence send used to supersede it without healing, and the
    # frontend never offers superseded records, so no normal user action would
    # ever write the line. The supersede paths must heal first.
    cid, sid, _ = _mech_scene(client)
    rec, pid = _resolve_with_crashed_line(client, cid, sid, monkeypatch)

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["plain reply"])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "onward"})
    assert resp.status_code == 200

    assert len(_roll_lines(client, cid, sid)) == 1      # healed before supersede
    assert len([e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
                if e.get("proposal") == pid]) == 1      # still exactly one roll
    after = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert after["id"] == pid and after["status"] == "superseded"
    assert "line_intent" in after["resolution"]         # metadata persisted
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1]["content"] == "plain reply"         # new turn persisted normally
    roll_idx = next(i for i, m in enumerate(msgs) if m["content"].startswith("\U0001F3B2"))
    assert roll_idx < len(msgs) - 1                     # ...after the healed line


def test_retry_heals_projection_before_supersede(client):
    # Lighter variant on the same heal call path: a resolved-with-resolution
    # record that was never projected at all is retired by /retry — the heal
    # projects roll + line exactly once before the supersede.
    cid, sid, _ = _mech_scene(client)
    rec = _pending(client, cid, sid)
    pid = rec["id"]
    store.proposals.claim(cid, sid, pid)
    resolution = store.checks.resolve_check(cid, "brawl", "characters:mara", 6, 0)
    assert store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", resolution)
    assert client.get(f"/api/campaigns/{cid}/rolls").json() == []   # never projected

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["retried reply"])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry")
    assert resp.status_code == 200

    assert len([e for e in client.get(f"/api/campaigns/{cid}/rolls").json()
                if e.get("proposal") == pid]) == 1
    assert len(_roll_lines(client, cid, sid)) == 1
    after = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert after["id"] == pid and after["status"] == "superseded"


def test_campaign_sheet_creation_route(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    base = f"/api/campaigns/{cid}/sheets/characters/{chid}/creation"
    r = client.put(base, json={"sheet_type": "medium", "spends": {}})
    assert r.status_code == 200
    sheet = r.json()["sheet"]
    assert sheet["sheet_type"] == "medium"
    snap = {"sheet_type": sheet["sheet_type"], "fields": sheet["fields"], "gen": sheet["gen"]}
    # unknown sheet_type -> 400 (matching `expected` clears the CAS gate first)
    r = client.put(base, json={"sheet_type": "ghost", "spends": {}, "expected": snap})
    assert r.status_code == 400
    # a stale/omitted `expected` on an existing sheet -> 409, not 400
    r = client.put(base, json={"sheet_type": "medium", "spends": {}})
    assert r.status_code == 409


def test_campaign_sheet_creation_route_missing_target_404(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    base = f"/api/campaigns/{cid}/sheets/characters/nobody/creation"
    r = client.put(base, json={"sheet_type": "medium", "spends": {}})
    assert r.status_code == 404


def test_world_sheet_creation_route(client):
    wid = _world(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    base = f"/api/worlds/{wid}/sheets/pool-basic/characters/{chid}/creation"
    r = client.put(base, json={"sheet_type": "medium", "spends": {}})
    assert r.status_code == 200
    assert r.json()["sheet"]["sheet_type"] == "medium"
    assert client.put(f"/api/worlds/{wid}/sheets/ghost/characters/{chid}/creation",
                      json={"sheet_type": "medium", "spends": {}}).status_code == 404


def test_world_sheet_creation_route_missing_target_404(client):
    wid = _world(client)
    base = f"/api/worlds/{wid}/sheets/pool-basic/characters/nobody/creation"
    r = client.put(base, json={"sheet_type": "medium", "spends": {}})
    assert r.status_code == 404


def test_advance_route(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    client.put(f"/api/campaigns/{cid}/sheets/characters/{chid}", json={"sheet_type": "medium"})
    # pool-basic's "medium" has advancement block (added in Task 10) -- expect 200
    r = client.post(f"/api/campaigns/{cid}/sheets/characters/{chid}/advance", json={"field": "wits"})
    assert r.status_code == 200
    assert "sheet" in r.json()
    assert client.post(f"/api/campaigns/nope/sheets/characters/{chid}/advance",
                       json={"field": "wits"}).status_code == 404
    # nonexistent target character -- 404, not the 400 a plain SheetError would give
    assert client.post(f"/api/campaigns/{cid}/sheets/characters/nobody/advance",
                       json={"field": "wits"}).status_code == 404


def _user_module(client):
    return client.post("/api/modules", json={"name": "Realm System"}).json()["id"]


def test_module_edit_routes_round_trip(client):
    mid = _user_module(client)
    r = client.put(f"/api/modules/{mid}/manifest",
                   json={"name": "Realm System", "description": "d", "version": "1",
                         "dice": "1d20", "notes": "n", "dry_run": False})
    assert r.status_code == 200 and r.json()["ok"] is True
    group = {"label": "Attributes",
             "fields": [{"key": "strength", "type": "dots", "max": 5}]}
    assert client.put(f"/api/modules/{mid}/groups/attributes",
                      json={"group": group, "dry_run": False}).json()["ok"]
    st = {"label": "Warden", "kind": "characters", "groups": ["attributes"], "fields": []}
    assert client.put(f"/api/modules/{mid}/sheet-types/warden",
                      json={"sheet_type": st, "dry_run": False}).json()["ok"]
    # dry-run rejection carries errors, writes nothing
    bad = {**group, "fields": [{"key": "strength", "type": "nope"}]}
    r = client.put(f"/api/modules/{mid}/groups/attributes",
                   json={"group": bad, "dry_run": True})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["errors"]
    # rename
    r = client.post(f"/api/modules/{mid}/rename",
                    json={"kind": "group", "address": {"from": "attributes"},
                          "to": "traits", "dry_run": False})
    assert r.json()["ok"] is True
    pack = client.get(f"/api/modules/{mid}").json()
    assert "traits" in pack["sheets"]["groups"]
    assert pack["manifest"]["notes"].strip() == "n"


def test_module_edit_builtin_400(client):
    for call in [
        lambda: client.put("/api/modules/d20-basic/manifest",
                           json={"name": "X", "description": "", "version": "",
                                 "dice": "", "notes": "", "dry_run": False}),
        lambda: client.delete("/api/modules/d20-basic/groups/attributes"),
        lambda: client.post("/api/modules/d20-basic/rename",
                            json={"kind": "check", "address": {"from": "a"},
                                  "to": "b", "dry_run": False}),
    ]:
        assert call().status_code == 400


def test_module_edit_unknown_mid_404(client):
    assert client.put("/api/modules/ghost/manifest",
                      json={"name": "X", "description": "", "version": "",
                            "dice": "", "notes": "", "dry_run": False}).status_code == 404


def test_module_duplicate_export_import(client):
    r = client.post("/api/modules/d20-basic/duplicate", json={"name": "My D20"})
    assert r.status_code == 200
    new = r.json()["id"]
    z = client.get(f"/api/modules/{new}/export")
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    r = client.post("/api/modules/import", content=z.content,
                    headers={"content-type": "application/zip"})
    assert r.status_code == 200 and r.json()["id"] not in ("", new)


def test_module_import_413(client):
    r = client.post("/api/modules/import", content=b"x",
                    headers={"content-length": str(20 * 1024 * 1024)})
    assert r.status_code == 413


def test_module_create_delete_routes_use_transactional_path(client):
    r = client.post("/api/modules", json={"name": "Realm System"})
    assert r.status_code == 200 and r.json()["id"]
    mid = r.json()["id"]
    pack = client.get(f"/api/modules/{mid}").json()
    assert pack["id"] == mid
    assert client.delete(f"/api/modules/d20-basic").status_code == 400


def test_module_rule_route(client):
    mid = _user_module(client)
    client.put(f"/api/modules/{mid}/rules/omen",
              json={"flags": {"always": True}, "body": "The omens speak.", "dry_run": False})
    r = client.get(f"/api/modules/{mid}/rules/omen")
    assert r.status_code == 200
    body = r.json()
    assert body["body"].strip() == "The omens speak."
    assert body["meta"].get("always") == "true"
    assert client.get(f"/api/modules/{mid}/rules/ghost-slug").status_code == 404
    assert client.get("/api/modules/ghost/rules/omen").status_code == 404
