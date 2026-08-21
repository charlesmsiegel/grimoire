"""The model catalog: one normalized shape, and the route that lists a
provider the reader has described but not yet saved (#149).

The saved-connection half lives in `test_routes.py` beside the rest of the
connection routes; what is here is the shared normalizer and the preview route
the setup wizard and the New-connection form depend on.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import catalog, routes
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from tests.llm_fakes import FakeCatalog


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


# ---- the normalizer ----
def test_an_entry_keeps_missing_metadata_missing():
    """`None` and `0` are different facts: an unpriced model and a free one
    read the same way if absence is defaulted, and the combobox renders
    nothing for the first and "Free" for the second."""
    assert catalog.entry({"id": "local-model"}) == {
        "id": "local-model", "name": "local-model", "context": None,
        "prompt": None, "completion": None}


def test_a_null_pricing_block_does_not_raise():
    """An endpoint sending `"pricing": null` is not hypothetical, and one bad
    field must not empty a catalog of three hundred."""
    assert catalog.entry({"id": "m", "pricing": None})["prompt"] is None


def test_entries_are_sorted_by_id():
    """One ordering for every provider. It used to be the frontend's job for
    OpenRouter's catalog alone and nobody's for a custom endpoint's, so the
    same combobox listed differently depending on which connection was open."""
    assert [m["id"] for m in catalog.entries([{"id": "b"}, {"id": "a"}, {"id": "c"}])] == \
        ["a", "b", "c"]


def test_a_record_with_no_id_is_dropped_rather_than_raising():
    """The id is what gets stored on the connection, so a record without one
    cannot be selected — and it is not a reason to leave the picker empty."""
    assert [m["id"] for m in catalog.entries([{"name": "nameless"}, {"id": "a"}])] == ["a"]


def test_a_non_object_row_is_dropped_too():
    assert catalog.entries(["not-a-model", {"id": "a"}]) == [
        {"id": "a", "name": "a", "context": None, "prompt": None, "completion": None}]


# ---- the preview route ----
def test_preview_lists_an_unsaved_connections_provider(client):
    """The wizard's whole job is picking a model for a connection that does
    not exist yet, so the catalog has to be reachable before there is an id."""
    fake = FakeCatalog(models=[{"id": "a/b", "name": "B", "context": 8,
                                "prompt": "0", "completion": "0"}])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    r = client.post("/api/model-catalog", json={"kind": "openrouter", "api_key": "sk-typed"})

    assert r.status_code == 200
    assert r.json() == {"models": fake.models}
    assert [(c["kind"], c["api_key"]) for c in fake.listed] == [("openrouter", "sk-typed")]


def test_preview_carries_a_typed_base_url(client):
    fake = FakeCatalog(models=[])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    client.post("/api/model-catalog", json={
        "kind": "openai_compatible", "base_url": "http://127.0.0.1:8080/v1", "api_key": ""})

    assert [(c["kind"], c["base_url"]) for c in fake.listed] == [
        ("openai_compatible", "http://127.0.0.1:8080/v1")]


def test_preview_needs_no_key_at_all(client):
    """OpenRouter's catalog is public, and the wizard lists it before the
    reader has typed anything — which is what makes the model field usable on
    the step where the key is still being entered."""
    fake = FakeCatalog(models=[])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    assert client.post("/api/model-catalog", json={"kind": "openrouter"}).status_code == 200
    assert fake.listed[0]["api_key"] == ""


def test_preview_refuses_the_kind_with_no_catalog(client):
    fake = FakeCatalog(models=[])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    r = client.post("/api/model-catalog", json={"kind": "claude"})

    assert r.status_code == 400
    assert fake.listed == []


def test_preview_rejects_a_kind_that_is_not_a_kind(client):
    assert client.post("/api/model-catalog", json={"kind": "telepathy"}).status_code == 422


def test_preview_normalizes_an_upstream_failure(client):
    """Same taxonomy as everything else that talks to a provider (#213): the
    form shows "couldn't load model list" from the status, not from a guess."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(error=LLMError("network", "connection refused"))

    r = client.post("/api/model-catalog", json={
        "kind": "openai_compatible", "base_url": "http://127.0.0.1:9/v1"})

    assert (r.status_code, r.json()["kind"]) == (502, "network")


def test_preview_caches_nothing(client):
    """There is no `rev` to tag a cache entry with — the connection being
    described may never be saved, and the next keystroke in the base-URL field
    would invalidate it anyway."""
    fake = FakeCatalog(models=[{"id": "a/b", "name": "B", "context": None,
                                "prompt": None, "completion": None}])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post("/api/model-catalog", json={"kind": "openrouter", "api_key": "sk-typed"})

    assert client.get("/api/llm-connections/openrouter").json()["models"] == []


def test_preview_does_not_touch_a_stored_key(client):
    """The credentials come off the wire, never off disk: a preview for a
    connection the reader is *editing* must exercise what they typed, not the
    key the connection already has."""
    fake = FakeCatalog(models=[])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-stored"})

    client.post("/api/model-catalog", json={"kind": "openrouter", "api_key": "sk-typed"})

    assert fake.listed[0]["api_key"] == "sk-typed"
