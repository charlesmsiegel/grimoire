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
