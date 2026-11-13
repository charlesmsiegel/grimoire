"""Synthetic named creatures and a world-less capture scene for Sprint 050."""
from dataclasses import replace

from grimoire.companions import CaptureLibrary, CaptureRequest
from grimoire.core import DIRECTOR
from grimoire.core.identity import ScopeRef
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.graph.codec import relation_from_payload
from grimoire.holdings import Line, Operation
from grimoire.holdingstore import HoldingsLibrary

from chat_fixtures import Clock, Ids
from effects_fixtures import CHAT, Handles, moment
from party_fixtures import C, MARA, SNIK, GRUB, seed as seed_party, party_library
from store_helpers import OWNER

BALL = 'ordinary-ball'


def library(store, handles):
    return CaptureLibrary(store, handles.app, issued=handles.authorizer.issued,
                          clock=Clock(), new_id=Ids(), owner=OWNER)


def seed(store, handles):
    seed_party(store)
    ChatLibrary(store, handles.app, clock=Clock(), new_id=Ids(), owner=OWNER).start_chat(
        Launch('premise', premise='A meeting beside the harbor.'), scope=ScopeRef(C)).commit()
    HoldingsLibrary(store, handles.app, clock=lambda: "2026-01-01T00:00:00Z", owner=OWNER).apply(
        Operation('seed-balls', (Line.add(MARA, BALL, 3),))).commit()


def request(**changes):
    return replace(CaptureRequest('catch-1', CHAT, moment(), MARA, SNIK, 'success', BALL, 1), **changes)


def balls(store, handles):
    return HoldingsLibrary(store, handles.app, clock=Clock()).read(MARA).counts().get(BALL, 0)


def relations(store):
    return [relation_from_payload(ref, value.record.schema, value.record.payload)
            for ref, value in store.campaign_relations(C).records.items()]


def capture(store, handles, req=None):
    lib = library(store, handles)
    return lib.accept(lib.preview(req or request()), controls=())
