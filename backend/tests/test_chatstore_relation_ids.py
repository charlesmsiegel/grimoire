"""107's actor allocator never reuses an instance id that a relation names (Sprint 017, spec-gate M4).

A hand-deleted endpoint's id must not go to a different individual, who would silently inherit
its relations. Both allocation paths are covered: ``attach_source`` and ``start_chat`` into an
existing scope. The id ``mara-2`` belongs to no roster of the chat being extended.
"""

from __future__ import annotations

import json

import pytest

from grimoire.chats import Launch
from grimoire.core import InstanceRef, Moment
from grimoire.core.identity import ChatRef, ScopeRef
from grimoire.store import LibraryStore

from chat_fixtures import ANYA, MARA, chat_library, seed_sources
from graph_fixtures import instance_record, relation_library
from store_helpers import owner_handle

Q = "q-1"
MARA_1, MARA_2 = InstanceRef(Q, "character", "mara-1"), InstanceRef(Q, "character", "mara-2")


def _setup(store: LibraryStore, relation: str | None) -> ChatRef:
    """Scope q-1 with chat t-1 (Mara, actor mara-1), a hand-made instance mara-2, a second chat t-2 (Anya)."""
    seed_sources(store)
    chats = chat_library(store)
    chats.start_chat(Launch("character", MARA)).commit()
    txn = store.transaction()
    txn.put(instance_record(MARA_2), None)
    txn.commit(owner_handle())
    folder = store.root / "campaigns" / Q / "relations"
    if relation == "decodable":
        relation_library(store).add(Q, "knows", MARA_2, MARA_1, since=Moment(0), provenance="director").commit()
    elif relation in ("unsupported", "undecodable"):
        payload = {"predicate": "mentors" if relation == "unsupported" else "knows", "subject": str(MARA_2),
                   "object": str(MARA_1 if relation == "unsupported" else MARA_2), "since": {"sequence": 0},
                   "provenance": "hand", "recorded_at": "t"}
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "r-9.json").write_text(json.dumps({
            "format": "grimoire.scope-record/1", "address": f"campaign:{Q}#relation/r-9",
            "schema": "grimoire.relation/0", "payload": payload}))
    (store.root / "campaigns" / Q / "instances" / "character" / "mara-2.json").unlink()  # a hand deletion
    second = chats.start_chat(Launch("character", ANYA), scope=ScopeRef(Q)).commit().chat
    return second


@pytest.mark.parametrize("relation, expected", [
    (None, "mara-2"),  # control: with nothing naming it, the freed id is reused (the test can fail)
    ("decodable", "mara-3"),
    ("unsupported", "mara-3"),
    ("undecodable", "mara-3"),  # campaign_taken_ids scans relation payloads raw: an undecodable file protects too
])
def test_attach_source_never_reuses_a_relation_endpoint_id(tmp_path, relation, expected):
    with LibraryStore.create(tmp_path / "lib") as store:
        second = _setup(store, relation)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        _, etag = chats.read_chat(second)
        change = chats.attach_source(second, MARA, "character", expected=etag)
        assert [actor.instance_id for actor in change.new_actors] == [expected]


@pytest.mark.parametrize("relation, expected", [(None, "mara-2"), ("decodable", "mara-3")])
def test_start_chat_in_an_existing_scope_never_reuses_a_relation_endpoint_id(tmp_path, relation, expected):
    with LibraryStore.create(tmp_path / "lib") as store:
        _setup(store, relation)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        change = chats.start_chat(Launch("character", MARA), scope=ScopeRef(Q))
        assert [actor.instance_id for actor in change.new_actors] == [expected]


def test_a_chat_commit_rechecks_relation_endpoint_ids(tmp_path):
    """An id a relation came to name between a chat's preview and its commit is not taken by the chat."""
    from grimoire.store import StaleWrite
    with LibraryStore.create(tmp_path / "lib") as store:
        second = _setup(store, None)
        chats = chat_library(store)
        chats._new_id.counts.update({"q": 1, "t": 2})
        _, etag = chats.read_chat(second)
        change = chats.attach_source(second, MARA, "character", expected=etag)
        assert [actor.instance_id for actor in change.new_actors] == ["mara-2"]
        folder = store.root / "campaigns" / Q / "relations"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "r-9.json").write_text(json.dumps({
            "format": "grimoire.scope-record/1", "address": f"campaign:{Q}#relation/r-9",
            "schema": "grimoire.relation/0", "payload": {
                "predicate": "knows", "subject": str(MARA_2), "object": str(MARA_1), "since": {"sequence": 0},
                "provenance": "hand", "recorded_at": "t"}}))
        with pytest.raises(StaleWrite):
            change.commit()
