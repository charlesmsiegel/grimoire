"""GET /api/search — the keyword sweep's HTTP surface (#33).

The store module owns what matches; this owns the query-string contract: which
combinations are refused, what a hit looks like on the wire, and the fact that
the route sits ahead of the generic `/{kind}` catch-alls (which
`tests/test_route_order.py` checks structurally).
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app

#: Quoted, so the four words are one term rather than four -- "the" alone
#: matches half the store, which would make these assertions about the fixture
#: rather than about the route.
PHRASE = '"owed to the sea"'


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return TestClient(create_app())


@pytest.fixture
def library(client):
    """A world with a piece of lore, and a campaign with a scene naming it."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore",
                json={"name": "The Salt Pact", "body": "Debts are owed to the sea."})
    cid = client.post("/api/campaigns", json={"name": "The Long Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Long Quay"}).json()["id"]
    # A transcript post only ever arrives through the chat stream, so the store
    # is how a fixture writes one.
    store.scenes.append_message(cid, sid, "user", "Everything here is owed to the sea.")
    return wid, cid, sid


def test_a_blank_query_is_an_empty_result(client):
    body = client.get("/api/search").json()
    assert body["hits"] == [] and body["total"] == 0


def test_a_hit_carries_everything_needed_to_open_it(client, library):
    wid, _cid, _sid = library
    body = client.get("/api/search", params={"q": PHRASE}).json()
    assert body["terms"] == ["owed to the sea"]  # the quotes keep it one term
    lore = next(h for h in body["hits"] if h["kind"] == "lore")
    assert lore["scope"] == "world" and lore["root"] == wid and lore["root_name"] == "Realm"
    assert lore["id"] == "the-salt-pact" and lore["name"] == "The Salt Pact"
    assert "owed to the sea" in lore["snippet"].lower()


def test_both_scopes_are_reported_and_counted(client, library):
    body = client.get("/api/search", params={"q": PHRASE}).json()
    assert body["scopes"] == {"world": 1, "campaign": 1}
    assert body["facets"] == {"lore": 1, "scenes": 1}


def test_scope_narrows_the_sweep(client, library):
    body = client.get("/api/search", params={"q": PHRASE, "scope": "campaign"}).json()
    assert {h["scope"] for h in body["hits"]} == {"campaign"}


def test_root_narrows_to_one_record_root(client, library):
    wid, cid, _sid = library
    body = client.get("/api/search",
                      params={"q": "owed", "scope": "campaign", "root": cid}).json()
    assert {h["root"] for h in body["hits"]} == {cid}
    assert client.get("/api/search",
                      params={"q": "owed", "scope": "world", "root": wid}).json()["total"] == 1


def test_root_without_a_scope_is_refused(client, library):
    res = client.get("/api/search", params={"q": "owed", "root": "realm"})
    assert res.status_code == 400 and "scope" in res.json()["detail"]


def test_an_unknown_scope_is_refused(client):
    res = client.get("/api/search", params={"q": "owed", "scope": "galaxy"})
    assert res.status_code == 400


def test_kinds_filter_the_hits(client, library):
    body = client.get("/api/search", params={"q": PHRASE, "kinds": "scenes"}).json()
    assert [h["kind"] for h in body["hits"]] == ["scenes"]
    # The facets still describe everything the query found, so the UI's chips
    # can offer the filter the reader is not currently using.
    assert body["facets"]["lore"] == 1


def test_an_unknown_kind_is_refused(client):
    res = client.get("/api/search", params={"q": "owed", "kinds": "lore,sausages"})
    assert res.status_code == 400 and "sausages" in res.json()["detail"]


def test_the_limit_is_capped_rather_than_trusted(client, library):
    body = client.get("/api/search", params={"q": "owed", "limit": 100000}).json()
    assert len(body["hits"]) <= store.search.MAX_LIMIT


def test_the_literal_route_is_not_shadowed_by_the_entity_catch_alls(client, library):
    """`/search` has to be reached as itself. It shares no prefix with
    `/worlds/{wid}/{kind}`, but the include order is what guarantees that in
    general, so this is the request-level check beside the structural one."""
    assert client.get("/api/search", params={"q": "owed"}).status_code == 200


def test_a_hit_carries_no_internal_keys(client, library):
    """The scorer threads each matched document through to the snippet cutter
    on the hit itself. If that reference ever survives into the response, every
    row ships the full text of the record it matched — the whole store, as
    JSON, on a one-letter query."""
    body = client.get("/api/search", params={"q": "owed"}).json()
    assert body["hits"]
    for hit in body["hits"]:
        assert not [k for k in hit if k.startswith("_")], hit


# ---- mode: keyword and semantic on one route (#34) -------------------------

def configure_embeddings(client, kind="openai_compatible"):
    """Point the store at an embeddings endpoint, through the API."""
    conn = client.post("/api/llm-connections",
                       json={"kind": kind, "name": "Vectors", "model": "",
                             "base_url": "https://vectors.example/v1",
                             "api_key": "sk-x", "post_process": "none"}).json()
    client.put("/api/config", json={"embeddings_connection_id": conn["id"],
                                    "embeddings_model": "embed-1"})
    return conn["id"]


def test_the_answer_says_which_mode_produced_it(client, library):
    body = client.get("/api/search", params={"q": "owed"}).json()
    assert body["mode"] == "keyword" and body["requested_mode"] == "keyword"
    assert body["note"] == ""


def test_semantic_mode_degrades_to_keyword_when_there_is_no_endpoint(client, library):
    """#34's fourth item. A reader who asks for meaning and has no embeddings
    connection gets the keyword answer and is told so — not a 500, and not an
    empty page that looks like "nothing matches"."""
    body = client.get("/api/search", params={"q": "owed", "mode": "semantic"}).json()
    assert body["mode"] == "keyword" and body["requested_mode"] == "semantic"
    assert body["note"]
    assert [h["kind"] for h in body["hits"]]


def test_semantic_mode_answers_semantically_once_an_endpoint_is_configured(
        client, library, monkeypatch):
    configure_embeddings(client)

    class Fake:
        def embed(self, texts, model, key, base_url, deadline=None):
            # Everything points the same way, so every passage is a hit: what
            # this asserts is the wiring, not the ranking.
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(store.semsearch, "_CLIENT", Fake())
    body = client.get("/api/search", params={"q": "owed", "mode": "semantic"}).json()
    assert body["mode"] == "semantic" and body["requested_mode"] == "semantic"
    assert body["corpus"] > 0 and body["hits"]


def test_an_unknown_mode_is_refused(client):
    res = client.get("/api/search", params={"q": "owed", "mode": "vibes"})
    assert res.status_code == 400 and "vibes" in res.json()["detail"]


def test_a_bad_filter_is_a_400_in_semantic_mode_too(client):
    """The vocabulary is the route's, not the mode's: a kind that 400s in one
    mode must not silently return nothing in the other."""
    res = client.get("/api/search", params={"q": "owed", "mode": "semantic",
                                            "kinds": "sausages"})
    assert res.status_code == 400
