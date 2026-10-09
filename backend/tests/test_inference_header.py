"""The header and a new scene ask the resolver (slice C, Task 8).

`GET /config` names what CHAT would run on -- `active_connection`, `ready` and
`health` -- from the one display resolve it already made for
`send_images_reach`, and summarises the roles from one `translate.global_view`
with `cascade.role_selection`, which resolves nothing. A new scene stamps the
model chat would run on in its campaign, from the scene routes' one marked
display resolve.

Invented connection ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire.store import inference_keys as keys
from grimoire.store.inference import migrate
from grimoire.store.inference import resolve as inference

from . import inference_baseline as base


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _migrated(client) -> str:
    """A format-2 store: the seeded openrouter at `vendor/active`, a keyed
    `spare` connection, two presets and one campaign."""
    cid = base._fresh(client)["cid"]
    base._spare(client)
    base._presets()
    got = migrate.ensure()
    assert got.state == "done", got
    assert keys.is_current(store.read_config())
    return cid


def _put(client, body: dict, cid: str = "") -> None:
    url = f"/api/campaigns/{cid}/inference" if cid else "/api/inference/settings"
    got = client.put(url, json=body)
    assert got.status_code == 200, got.text


def _primary(provider: str, model: str, preset: str = "") -> dict:
    return {"roles": {"primary": {"selection": {
        "provider": provider, "model": model, "preset": preset}}}}


def test_config_reports_the_resolved_primary_on_format_2(client):
    _migrated(client)
    _put(client, _primary("spare", "vendor/chosen", "warm"))
    # A legacy key an older build left naming the seeded connection: nothing
    # reads it now. (This build seeds it below format 2 only, slice I.)
    store.config.write_config(active_connection_id="openrouter")
    assert store.read_config()["active_connection_id"] == "openrouter"
    client.app.state.health.record(store.llm_connections.read_connection_raw("spare"))

    body = client.get("/api/config").json()

    assert body["active_connection_id"] == "spare"
    assert body["active_connection"] == {"id": "spare", "kind": "openrouter",
                                         "name": "spare", "model": "vendor/chosen"}
    assert body["ready"] is True
    assert body["health"]["state"] == "ok"
    roles = body["inference"]["roles"]
    assert roles["primary"] == {"provider_name": "spare", "model": "vendor/chosen",
                                "preset_name": "warm"}
    # Fast and Decision inherit the Primary.
    assert roles["fast"] == roles["decision"] == roles["primary"]
    assert body["inference"]["embedding_on"] is False


def test_the_header_follows_a_keyless_primary_to_not_ready(client):
    _migrated(client)
    keyless = base._connection(client, "keyless")
    _put(client, _primary(keyless, "vendor/keyless"))
    body = client.get("/api/config").json()
    assert body["active_connection"]["id"] == keyless
    assert body["ready"] is False


def test_ready_is_the_seams_decision_capability_included(client):
    """`ready` is `inference.refusal` of chat's resolution, not a third copy of
    the credential rule: a keyed Primary the catalog says cannot generate is
    refused every turn, so the header must not say ready."""
    _migrated(client)
    rev = store.llm_connections.read_connection_raw("spare")["rev"]
    store.llm_connections.set_cached_models(
        "spare", [{"id": "vendor/embedder", "outputs": ["embeddings"]}], rev)
    _put(client, _primary("spare", "vendor/embedder"))
    assert inference.refusal(inference.resolve("chat")) is not None

    body = client.get("/api/config").json()

    assert body["active_connection"]["id"] == "spare"
    assert body["ready"] is False


def test_config_inference_summary_makes_no_resolve_calls(client, monkeypatch):
    """One resolve per config read: the marked display read that answers the
    header and `send_images_reach`. The role summary resolves nothing."""
    _migrated(client)
    _put(client, _primary("spare", "vendor/chosen"))
    calls: list[tuple] = []
    real = inference.resolve

    def counting(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(inference, "resolve", counting)
    body = client.get("/api/config").json()
    assert body["inference"]["roles"]["primary"]["model"] == "vendor/chosen"
    assert len(calls) == 1, calls
    assert calls[0][0] == ("chat",)


def test_the_role_summary_reads_a_legacy_store_through_the_translation(client):
    """Format 1: the Primary is the active connection, read as a role."""
    base._fresh(client)
    body = client.get("/api/config").json()
    assert body["inference"]["roles"]["primary"] == {
        "provider_name": "OpenRouter", "model": "vendor/active", "preset_name": ""}
    assert body["active_connection"]["id"] == "openrouter"


def test_a_new_scene_stamps_the_resolved_model(client):
    cid = _migrated(client)
    _put(client, _primary("spare", "vendor/global"))
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Docks"}).json()["id"]
    assert store.scenes.read_scene(cid, sid)["meta"]["model"] == "vendor/global"

    # A campaign's own Primary is what chat runs on there.
    _put(client, _primary("openrouter", "vendor/campaign"), cid)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Quay"}).json()["id"]
    assert store.scenes.read_scene(cid, sid)["meta"]["model"] == "vendor/campaign"

