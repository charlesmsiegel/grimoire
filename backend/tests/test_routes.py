import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app


class FakeOpenRouter:
    def __init__(self, deltas):
        self.deltas = deltas

    async def stream(self, messages, model, key):
        for d in self.deltas:
            yield d


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    # store.home() reads GRIMOIRE_HOME on every call, so no reload is needed.
    # Do NOT reload `routes`: it would rebuild get_openrouter into a new function
    # object, breaking the dependency_overrides key that main's router references.
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def test_config_never_leaks_key(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    body = client.get("/api/config").json()
    assert body["key_set"] is True
    assert "openrouter_key" not in body
    assert "sk-or-secret" not in json.dumps(body)


def test_chat_missing_key_returns_409(client):
    cid = client.post("/api/conversations", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/conversations/{cid}/chat", json={"content": "hi"})
    assert resp.status_code == 409
    assert resp.json()["kind"] == "missing_key"


def test_chat_streams_and_persists(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    cid = client.post("/api/conversations", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/conversations/{cid}/chat", json={"content": "hi"})
    assert resp.status_code == 200
    assert 'data: {"delta": "Hel"}' in resp.text
    assert 'data: {"done": true}' in resp.text
    conv = client.get(f"/api/conversations/{cid}").json()
    assert conv["messages"][-1] == {"role": "assistant", "content": "Hello"}
