"""A model reply carrying an art handle, through the real turn.

The contract `_persist_reply` owes this feature: a handle becomes markdown, or
it becomes nothing, and either way it never reaches the transcript.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests.llm_fakes import FakeOpenRouter


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    return TestClient(app)


def _described_campaign(client):
    """A campaign whose library holds one described image."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-secret"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    store.campaign_images.put_image(cid, "coastline", b"\x89PNG\r\n\x1a\n", "png")
    store.image_descriptions.set_in(store.campaign_images.images_dir(cid), "coastline",
                                    "A hand-drawn map of the northern coastline.")
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    return cid, sid


def _reply(client, cid, sid, text, prompt="go on"):
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter([text])
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": prompt})
    assert resp.status_code == 200
    stored = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    return next(m["content"] for m in stored if m["role"] == "assistant")


def test_a_valid_handle_lands_as_markdown(client):
    cid, sid = _described_campaign(client)
    got = _reply(client, cid, sid, "The coast unrolls.\n\n[[art:campaign:coastline]]")
    assert "[[art:" not in got
    assert (f"![A hand-drawn map of the northern coastline.]"
            f"(/api/campaigns/{cid}/images/coastline)") in got
    assert "The coast unrolls." in got


def test_an_unresolvable_handle_leaves_the_prose_alone(client):
    cid, sid = _described_campaign(client)
    got = _reply(client, cid, sid, "The coast unrolls. [[art:campaign:no-such-image]]")
    assert "[[art:" not in got
    assert "The coast unrolls." in got


def test_a_handle_for_a_real_but_undescribed_image_is_dropped(client):
    """Statelessness' one hole, closed: art nobody wrote up cannot be reached
    by composing a plausible handle for it."""
    cid, sid = _described_campaign(client)
    store.campaign_images.put_image(cid, "secret-map", b"\x89PNG\r\n\x1a\n", "png")
    got = _reply(client, cid, sid, "Look. [[art:campaign:secret-map]]")
    assert "[[art:" not in got
    assert "secret-map" not in got


def test_a_handle_never_becomes_a_post_of_its_own(client):
    """It is resolved before `split_reply`, so a handle on its own line between
    two speaker markers cannot be split off as an empty narrator post."""
    cid, sid = _described_campaign(client)
    _reply(client, cid, sid, "[[art:campaign:coastline]]")
    stored = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assistant = [m for m in stored if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"].startswith("![")


def test_a_reply_with_no_handles_is_untouched(client):
    cid, sid = _described_campaign(client)
    got = _reply(client, cid, sid, "Just narration, with a [link](http://example.invalid).")
    assert got == "Just narration, with a [link](http://example.invalid)."
