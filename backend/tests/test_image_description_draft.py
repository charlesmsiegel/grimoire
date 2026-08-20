"""The model-drafted first pass at a description.

Preview only — nothing here writes to the store — and available on the two
connection kinds whose client can carry an image.
"""

import base64
import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests.llm_fakes import CapturingOpenRouter, FakeOpenRouterComplete

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    return TestClient(app)


@pytest.fixture
def art(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-secret"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    made = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    cid, vid = made["character"], made["version"]
    client.put(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/gallery_1",
               files={"file": ("a.png", PNG, "image/png")})
    return wid, cid, vid


def _url(wid, cid, vid, name="gallery_1"):
    return (f"/api/worlds/{wid}/characters/{cid}/versions/{vid}"
            f"/images/{name}/description/draft")


def test_a_draft_comes_back_as_a_preview_and_is_not_stored(client, art):
    wid, cid, vid = art
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        "A rain-slick stone quay at dusk.\nFishing boats ride at anchor.")
    r = client.post(_url(wid, cid, vid))
    assert r.status_code == 200
    # Lines are joined, not truncated to the first: a description may run to
    # two or three sentences (unlike a tagline).
    assert r.json()["description"] == (
        "A rain-slick stone quay at dusk. Fishing boats ride at anchor.")
    # Preview only: the store still says nothing about this image.
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {}


def test_the_request_carries_the_image_bytes_and_the_subject_name(client, art):
    wid, cid, vid = art
    fake = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert client.post(_url(wid, cid, vid)).status_code == 200

    user = next(m for m in fake.messages if m["role"] == "user")
    parts = {p["type"]: p for p in user["content"]}
    assert "Seraphine" in parts["text"]["text"]
    uri = parts["image_url"]["image_url"]["url"]
    assert uri.startswith("data:image/png;base64,")
    # The bytes themselves, not a URL the provider could never reach.
    assert base64.b64decode(uri.split(",", 1)[1]) == PNG


def test_a_claude_connection_is_refused_with_a_reason_rather_than_crashing(client, art):
    """`claude_agent` joins message content as a string, so a multimodal message
    would raise deep inside the SDK path. The refusal names the fix."""
    wid, cid, vid = art
    client.put("/api/config", json={"active_connection_id": "claude"})
    client.app.dependency_overrides[routes.get_llm] = CapturingOpenRouter
    r = client.post(_url(wid, cid, vid))
    assert r.status_code == 409
    assert "cannot read images" in str(r.json()["detail"])


def test_drafting_an_image_that_is_not_there_is_a_404(client, art):
    wid, cid, vid = art
    client.app.dependency_overrides[routes.get_llm] = CapturingOpenRouter
    assert client.post(_url(wid, cid, vid, "nope")).status_code == 404


def test_parse_output_joins_and_trims():
    assert store.image_drafts.parse_output("  one \n\n two  \n") == "one two"
    assert store.image_drafts.parse_output("") == ""


def test_data_uri_refuses_a_type_we_cannot_label(tmp_path):
    p = tmp_path / "art.tiff"
    p.write_bytes(b"II*\x00")
    with pytest.raises(ValueError):
        store.image_drafts.data_uri(p)
