"""Synthetic Wren of a synthetic harbor world and a native store for Sprint 042 tests (no user content)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from chat_fixtures import Clock, Ids
from store_helpers import OWNER, owner_handle

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.entity import EntityDefinition
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload
from grimoire.core.facets import Agent, Communicative, FacetSet, Physical
from grimoire.core.identity import SourceRef
from grimoire.rebase import RebaseError
from grimoire.rebasestore import SourceRebase
from grimoire.store import LibraryStore, RecordEnvelope, RecordNotFound, SourceManifest
from grimoire.store.layout import definition_parts

WORLD, ELSEWHERE, SALTMARCH = "world:harbor", "world:elsewhere", "world:saltmarch"
WREN = SourceRef(WORLD, "character", "wren")
R1, R2 = WREN.at("r1"), WREN.at("r2")
NAMESAKE = SourceRef(ELSEWHERE, "character", "wren", "r1")
VOICE = Communicative(speaks={"speech"}, understands={"speech"})
SECTIONS = {"biography": "Born at sea.", "voice-evidence": "Counts tides aloud."}


def wren(ref=R1, *, name="Wren", facets=(Physical(), Agent(), VOICE), sections=None) -> EntityDefinition:
    return EntityDefinition(ref, name, "individual", FacetSet(list(facets)), "A harbor pilot.",
                            {"sections": dict(SECTIONS if sections is None else sections)})


def envelope(definition: EntityDefinition) -> RecordEnvelope:
    return RecordEnvelope(definition.ref, definition_to_payload(definition), name=definition.name, owner=OWNER,
                          extra={"schema": ENTITY_SCHEMA})


def put(store, *items):
    """Write each item over whatever is there (``None`` when absent)."""
    txn = store.transaction()
    for item in items:
        if isinstance(item, SourceManifest):
            found = store.check_source(item.source)
            expected = None if isinstance(found, str) else found.etag
        else:
            try:
                expected = store.read(item.address).etag
            except RecordNotFound:
                expected = None
        txn.put(item, expected)
    return txn.commit(owner_handle())


def hand_edit(store, ref: SourceRef, **sections) -> None:
    """Rewrite a stored revision's bytes outside the store, as a person editing the file would."""
    path = store.root.joinpath(*definition_parts(ref))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["sections"].update(sections)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def publish(store, definition: EntityDefinition) -> None:
    """A new head revision of Wren."""
    heads = {"character/wren": definition.ref.revision}
    put(store, envelope(definition), SourceManifest(WORLD, OWNER, "Harbor", heads))


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        put(opened, SourceManifest(WORLD, OWNER, "Harbor", {"character/wren": "r1"}), envelope(wren()),
            SourceManifest(ELSEWHERE, OWNER, "Elsewhere"), envelope(wren(NAMESAKE)))
        yield opened


@pytest.fixture
def chats(store):
    return ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)


@pytest.fixture
def rebase(store):
    return SourceRebase(store, owner_handle(), clock=Clock())


def start(chats, ref=R1, *, follow=False):
    """A chat launched from ``ref``: pinned to what it resolves to now, unless ``follow`` opts in to the head."""
    started = chats.start_chat(Launch("character", ref), follow=follow).commit()
    [actor] = started.actors
    return started, actor


def hand_edit_description(store, ref: SourceRef, text: str) -> None:
    path = store.root.joinpath(*definition_parts(ref))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["description"] = text
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def edit_instance(store, actor, **payload):
    stored = store.read(actor)
    put(store, replace(stored.envelope, payload={**stored.envelope.payload, **payload}))


def reason(call):
    with pytest.raises(RebaseError) as caught:
        call()
    return caught.value.reason
