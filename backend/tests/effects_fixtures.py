"""A synthetic campaign for Sprint 021 tests: a PC, two NPCs, two keys, a guild and one 107 chat (no real library).

Wren is the player character (controlled by ``human:ana``); Tam is director-controlled; Ines has no
controller. Wren carries both keys from sequence 1. Every name is synthetic.
"""

from __future__ import annotations

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR, Moment
from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef
from grimoire.core.model import ControlAssignment
from grimoire.effects import EffectProposal, Op
from grimoire.effectstore import Endorsement, TurnEffectsLibrary
from grimoire.graphstore import RelationLibrary
from grimoire.store import LibraryStore

from chat_fixtures import Clock, Ids
from graph_fixtures import seed_campaign
from store_helpers import OWNER, authorizer

C = "c"
ANA, BOB = "human:ana", "human:bob"


def actor(name: str, kind: str = "character") -> InstanceRef:
    return InstanceRef(C, kind, name)


WREN, TAM, INES = actor("wren"), actor("tam"), actor("ines")
KEY1, KEY2, GUILD = actor("key-1", "item"), actor("key-2", "item"), actor("guild", "organization")
CAST = (WREN, TAM, INES, KEY1, KEY2, GUILD)
CHAT = ChatRef(C, "t-1")
CONTROLS = (ControlAssignment(C, "wren", ANA), ControlAssignment(C, "tam", DIRECTOR))


def contribution(cid: str = "c7", *, actor_id: str = "wren", turn: str = "t7", scene: str = "s1", kind: str = "action",
                 by: str = ANA, text: str = "I give Tam the iron key.") -> PlayerContribution:
    return PlayerContribution(C, cid, scene, turn, actor_id, by, kind, text)


def moment(sequence: int = 7, turn: str | None = None, scene: str = "s1") -> Moment:
    return Moment(sequence, scene, turn or f"t{sequence}")


def give(item: InstanceRef = KEY1, giver: InstanceRef = WREN, to: InstanceRef = TAM, *, backed: str | None = "c7",
         sequence: int = 7, turn: str | None = None, scene: str = "s1", agent: InstanceRef | None | str = "giver",
         summary: str = "Wren gives Tam the iron key.", proposed_by: str = "narration",
         chat: ChatRef = CHAT) -> EffectProposal:
    who = giver if agent == "giver" else agent
    return EffectProposal(C, chat, moment(sequence, turn, scene), summary,
                          (Op("end", "carries", giver, item, who, backed), Op("begin", "carries", to, item, who, backed)),
                          proposed_by)


class Handles:
    """One authorizer for the library: its trusted app handle is the outcome key; players get human handles."""

    def __init__(self) -> None:
        self.authorizer = authorizer()
        self.app = self.authorizer.handle_for(OWNER, trusted=True)
        self._players: dict[str, object] = {}

    def player(self, principal: str = ANA):
        if principal not in self._players:
            self._players[principal] = self.authorizer.handle_for(principal, trusted=True)
        return self._players[principal]

    def endorse(self, proposal: EffectProposal, cid: str = "c7", principal: str = ANA) -> Endorsement:
        return Endorsement(cid, self.player(principal), proposal.fingerprint)


def seed(store: LibraryStore, handles: Handles) -> None:
    """The cast, a premise chat in campaign ``c`` (``t-1``) and Wren carrying both keys from sequence 1."""
    seed_campaign(store, CAST)
    chats = ChatLibrary(store, handles.app, clock=Clock(), new_id=Ids(), owner=OWNER)
    change = chats.start_chat(Launch("premise", premise="A night at the harbor."), scope=ScopeRef(C))
    change.commit()
    relations = RelationLibrary(store, handles.app, clock=Clock(), new_id=_Prefixed("seed"), owner=OWNER)
    for item in (KEY1, KEY2):
        relations.add(C, "carries", WREN, item, since=Moment(1), provenance="director").commit()


class _Prefixed:
    def __init__(self, tag: str) -> None:
        self.tag, self.count = tag, 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.tag}-{self.count}"


def effects_library(store: LibraryStore, handles: Handles, *, new_id=None, clock=None) -> TurnEffectsLibrary:
    return TurnEffectsLibrary(store, handles.app, issued=handles.authorizer.issued, clock=clock or Clock(),
                              new_id=new_id or Ids(), owner=OWNER)
