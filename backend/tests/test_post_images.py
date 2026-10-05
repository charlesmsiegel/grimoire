"""`store.post_images`: whether a connection can be sent images, and how many
(#377) -- plus the settings and the catalog field that answer it."""

import importlib

import pytest

from grimoire import catalog, store
from grimoire.store import config, llm_connections, post_images


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return tmp_path


def _conn(kind="openrouter", vision="", model="m"):
    cid = llm_connections.create_connection(kind, f"{kind}-{vision or 'auto'}-{model}",
                                            api_key="k", model=model, vision=vision)
    return llm_connections.read_connection_raw(cid)


def _catalog(conn, rows):
    llm_connections.set_cached_models(conn["id"], rows, conn["rev"])


# ---- catalog ----
def test_catalog_entry_reads_input_modalities():
    assert catalog.entry({"id": "m", "architecture": {"input_modalities": ["text", "image"]}})["vision"] is True
    assert catalog.entry({"id": "m", "architecture": {"input_modalities": ["text"]}})["vision"] is False
    assert catalog.entry({"id": "m"})["vision"] is None
    assert catalog.entry({"id": "m", "architecture": "x"})["vision"] is None
    assert catalog.entry({"id": "m", "architecture": {"input_modalities": "image"}})["vision"] is None


# ---- capability ----
def test_claude_never_reads_images_whatever_it_says(home):
    assert post_images.capability(_conn("claude", "on")) == "no"
    assert post_images.capability(None) == "no"


def test_the_override_wins(home):
    assert post_images.capability(_conn(vision="on")) == "yes"
    assert post_images.capability(_conn(vision="off")) == "no"


def test_auto_reads_the_cached_catalog(home):
    conn = _conn()
    assert post_images.capability(conn) == "unknown"
    _catalog(conn, [{"id": "m", "vision": True}])
    assert post_images.capability(conn) == "yes"
    _catalog(conn, [{"id": "m", "vision": False}])
    assert post_images.capability(conn) == "no"
    _catalog(conn, [{"id": "m", "vision": None}])
    assert post_images.capability(conn) == "unknown"
    _catalog(conn, [{"id": "other", "vision": True}])
    assert post_images.capability(conn) == "unknown"
    _catalog(conn, [{"id": "m"}])  # a sidecar written before #377
    assert post_images.capability(conn) == "unknown"


def test_an_unknown_stored_vision_value_reads_as_auto(home):
    conn = {**_conn(), "vision": "sometimes"}
    assert post_images.capability(conn) == "unknown"


# ---- limit / images_for / reach ----
def test_limit_is_zero_while_off_and_the_default_when_on(home):
    assert post_images.limit() == 0
    config.write_config(send_images="on")
    assert post_images.limit() == 3


@pytest.mark.parametrize("raw, want", [("abc", 3), ("-2", 3), ("0", 0), ("5", 5)])
def test_limit_reads_fail_soft(home, raw, want):
    config.write_config(send_images="on", send_images_limit=raw)
    assert post_images.limit() == want


def test_images_for_needs_both_the_setting_and_the_capability(home):
    yes, no = _conn(vision="on"), _conn(vision="off")
    assert post_images.images_for(yes) == 0
    config.write_config(send_images="on")
    assert post_images.images_for(yes) == 3
    assert post_images.images_for(no) == 0
    assert post_images.reach(no) == "no"
    assert post_images.reach(yes) == "yes"
    config.write_config(send_images="off")
    assert post_images.reach(yes) == "off"


# ---- routes ----
def test_send_images_round_trips_and_is_validated(client):
    cfg = client.get("/api/config").json()
    assert cfg["send_images"] == "off" and cfg["send_images_limit"] == "3"
    assert cfg["send_images_reach"] == "off"
    assert client.put("/api/config", json={"send_images": "maybe"}).status_code == 400
    r = client.put("/api/config", json={"send_images": "on", "send_images_limit": "2"})
    assert r.status_code == 200
    body = client.get("/api/config").json()
    assert body["send_images"] == "on" and body["send_images_limit"] == "2"


def test_reach_describes_the_chat_connection(client):
    cid = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Seraphine", "api_key": "k", "model": "m"}).json()["id"]
    client.put("/api/config", json={"active_connection_id": cid, "send_images": "on"})
    assert client.get("/api/config").json()["send_images_reach"] == "unknown"
    client.put(f"/api/llm-connections/{cid}", json={"vision": "on"})
    assert client.get("/api/config").json()["send_images_reach"] == "yes"


def test_connection_vision_is_constrained_and_round_trips(client):
    bad = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Mara", "api_key": "k", "vision": "sometimes"})
    assert bad.status_code == 422
    cid = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Mara", "api_key": "k", "vision": "on"}).json()["id"]
    assert client.get(f"/api/llm-connections/{cid}").json()["vision"] == "on"
    client.put(f"/api/llm-connections/{cid}", json={"vision": ""})
    assert client.get(f"/api/llm-connections/{cid}").json()["vision"] == ""
