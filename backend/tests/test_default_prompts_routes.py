"""Dedicated settings routes must not fall into the generic entity surface."""

import pytest

from grimoire import store


@pytest.fixture
def scope(client):
    wid = store.worlds.create_world("Realm")
    return wid, store.campaigns.create_campaign("Saltmarch", wid)


@pytest.mark.parametrize("kind", ["worlds", "campaigns"])
def test_default_prompt_get_put_and_clear(client, scope, kind):
    wid, cid = scope
    selected = wid if kind == "worlds" else cid
    base = f"/api/{kind}/{selected}/default-prompt"
    initial = client.get(base)
    assert initial.status_code == 200
    assert initial.json()["status"] == "empty"
    text = "\n  The river freezes.\n\n# Standing details\n  "
    saved = client.put(base, json={"text": text})
    assert saved.status_code == 200
    assert saved.json()["text"] == text
    assert client.get(base).json() == saved.json()
    cleared = client.put(base, json={"text": " \n "})
    assert cleared.status_code == 200
    assert cleared.json()["text"] == "" and cleared.json()["status"] == "empty"


def test_campaign_put_never_writes_the_inherited_world(client, scope):
    wid, cid = scope
    store.default_prompts.set_world(wid, "The river freezes.")
    result = client.put(f"/api/campaigns/{cid}/default-prompt", json={"text": "The harbor stays open."})
    assert result.status_code == 200
    assert result.json()["world"] == {"id": wid, "text": "The river freezes.", "status": "ok"}
    assert store.default_prompts.read_world(wid)["text"] == "The river freezes."


@pytest.mark.parametrize("body", [{}, {"text": None}, {"text": 42}, {"text": True}, {"text": {}}, {"text": []}])
@pytest.mark.parametrize("kind", ["worlds", "campaigns"])
def test_default_prompt_requires_a_string(client, scope, kind, body):
    selected = scope[0] if kind == "worlds" else scope[1]
    assert client.put(f"/api/{kind}/{selected}/default-prompt", json=body).status_code == 422


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize("kind", ["worlds", "campaigns"])
def test_default_prompt_unknown_scope_is_404(client, kind, method):
    kwargs = {"json": {"text": "The river freezes."}} if method == "put" else {}
    assert getattr(client, method)(f"/api/{kind}/seraphine/default-prompt", **kwargs).status_code == 404


def test_publication_failure_does_not_claim_a_save(client, scope, monkeypatch):
    wid, cid = scope
    store.default_prompts.set_campaign(cid, "The river freezes.")
    path = store.campaigns.campaign_root(cid) / "default_prompt.md"
    before = path.read_bytes()
    real_write = store.atomic.write_text

    def refuse(selected, text):
        if selected == path:
            raise PermissionError("publication refused")
        return real_write(selected, text)

    monkeypatch.setattr(store.atomic, "write_text", refuse)
    response = client.put(f"/api/campaigns/{cid}/default-prompt", json={"text": "The harbor stays open."})
    assert response.status_code == 409
    assert path.read_bytes() == before
    assert store.default_prompts.read_world(wid)["status"] == "empty"
