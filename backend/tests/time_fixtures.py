"""Sprint 116's Time page over one synthetic library: the table's card chat (Mara Vell, the director's NPC; Anya Rook,
the owner's persona; Tamsin Hale, Bob's), with the campaign clock started on 1024-03-01 10:00, a harbour guild (a
group instance) whose one member (Mara, unless a test names another) is by a canonical ``member_of`` relation, and the campaign's 020 objective events:
three of the director's, and one of the turn in which Anya promised to bring the map. Scripted generation only; no
model is called."""

from __future__ import annotations

import contextlib
from pathlib import Path

from store_helpers import OWNER
from table_fixtures import DIRECTOR_VIEW, Played, played, say

from grimoire.core import Event, Moment
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, instance_to_payload
from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.evidencestore import EvidenceLibrary
from grimoire.graph.codec import RELATION_SCHEMA, relation_to_payload
from grimoire.graph.model import Relation
from grimoire.store import RecordEnvelope, ScopeRecord

__all__ = ["DIRECTOR_VIEW", "PROMISE", "Played", "events", "scope_bytes", "timed"]

PROMISE = '"I promise to bring you the harbour map by the festival."'
#: The director's objective events: (event id, summary) at campaign sequences 1, 2 and 3.
EVENTS = (("ev-asks", "Mara asks the party to find the harbour map."),
          ("ev-refuses", "Mara says she will not carry the map herself."),
          ("ev-guild", "The guild's council meets behind closed doors."))


def scope_bytes(played_: Played) -> bytes:
    return (played_.table.store.root / "campaigns" / played_.chat.campaign_id / "scope.json").read_bytes()


def guild(played_: Played) -> InstanceRef:
    return InstanceRef(played_.chat.campaign_id, "group", "harbour-guild")


def _group(played_: Played, member: str) -> None:
    store, campaign = played_.table.store, played_.chat.campaign_id
    group = guild(played_)
    who = InstanceRef.parse(played_.cast[member])
    relation = Relation(RelationRef(campaign, "member-in-guild"), "member_of", who, group, Moment(0), "authored",
                        "2026-01-01T00:00:00Z")
    txn = store.transaction()
    txn.put(RecordEnvelope(group, instance_to_payload(EntityInstance(group, local_name="Harbour Guild")), owner=OWNER,
                           extra={"schema": ENTITY_SCHEMA}), None)
    txn.put(ScopeRecord(relation.ref, RELATION_SCHEMA, relation_to_payload(relation), owner=OWNER), None)
    txn.commit(played_.table.owner)


def events(played_: Played, records: list) -> None:
    EvidenceLibrary(played_.table.store, played_.table.owner, owner=OWNER).append(played_.chat.campaign_id,
                                                                                   records).commit()


def _evidence(played_: Played) -> str:
    """The director's three events and Anya's promise in its own turn; the promise's turn id."""
    chat, c = str(played_.chat), played_.chat.campaign_id
    played_("post", chat=chat, text=PROMISE)
    played_("generate", chat=chat)
    scene = played_.api.runner.open(played_.chat)
    anya = InstanceRef.parse(played_.cast["Anya Rook"])
    turn = [record.turn_id for record in scene.records if any(post.actor == anya for post in record.posts)][-1]
    found = [Event(c, event_id, summary, Moment(n, "harbour", "t1"), "director")
             for n, (event_id, summary) in enumerate(EVENTS, 1)]
    found.append(Event(c, "ev-promise", "Anya promises to bring the harbour map.",
                       Moment(4, played_.chat.chat_id, turn), "director"))
    events(played_, found)
    return turn


@contextlib.contextmanager
def timed(tmp_path: Path, *, clock: bool = True, group: bool = True, member: str = "Mara Vell"):
    """The table with the clock started (``clock``) and the guild (``group``) with ``member`` its one member by a
    ``member_of`` relation; yields ``(played, promise turn)``."""
    with played(tmp_path, [say("Mara nods slowly.")]) as table:
        turn = _evidence(table)
        if group:
            _group(table, member)
        if clock:
            campaign = table.chat.campaign_id
            etag = table("chronicle.clock", campaign=campaign, **DIRECTOR_VIEW)["etag"]
            table("chronicle.clock_commit", campaign=campaign, **DIRECTOR_VIEW, operation="initialize",
                  operation_id="start", expected=etag, provider="gregorian", date="1024-03-01", minute=600)
        yield table, turn
