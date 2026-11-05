"""A synthetic cast for Sprint 029: Mara (a PC) vaults a collapsing bridge; Iris and Sola bend light.

One world-less chat ``q-1#chat/t-1`` has three character actors: Mara (``human:ana``'s PC), Iris (her
light bending has a no-force limit) and Sola (the same power without the limit), both director-controlled.
Every name and fact is synthetic.
"""

from __future__ import annotations

from grimoire.adjudication import ActionFrame, Factor, Stake
from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR, Moment
from grimoire.core.ability_use import AbilityAttempt
from grimoire.core.authority import PlayerContribution
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from ability_fixtures import EMBER, iris, light_bending, person
from chat_fixtures import Clock, Ids
from ledger_helpers import OWNER, open_authority
from store_helpers import owner_handle

ANA, BOB = "human:ana", "human:bob"
ADJUDICATOR = "app:adjudicator"
Q = "q-1"
CHAT = ChatRef(Q, "t-1")
MARA, IRIS, SOLA = (InstanceRef(Q, "character", name) for name in ("mara-1", "iris-1", "sola-1"))
MARA_DEF, IRIS_DEF = person("mara", "Mara"), iris()
SOLA_DEF = person("sola", "Sola", light_bending(force_limit=False))


def moment(sequence: int = 10, turn: str = "t10", scene: str = "s1") -> Moment:
    return Moment(sequence, scene, turn)


ACROBAT = Factor("acrobat", "favours", "Mara trained as a rope dancer.", 2, "relation:r-acrobat")
GAP = Factor("gap", "hinders", "The gap is wider than a long stride.", 5, "event:e-gap")
COLLAPSE = Factor("collapse", "hinders", "The far span is already falling.", 9, "event:e-collapse")
FALL = Stake("fall", "She drops into the gorge.")
SATCHEL = Stake("satchel", "Her satchel goes into the river.")


def bridge(*, factors=(ACROBAT, GAP, COLLAPSE), stakes=(FALL, SATCHEL), attempt_id: str = "c10", actor=MARA,
           at: Moment | None = None, ability: AbilityAttempt | None = None,
           intent: str = "Vault the gap before the span falls.") -> ActionFrame:
    return ActionFrame(attempt_id, actor, CHAT, at or moment(), intent, tuple(factors), tuple(stakes),
                       "labeller:test", ability)


def vault(text: str = "I run and vault the gap!", *, cid: str = "c10", by: str = ANA, turn: str = "t10",
          kind: str = "action", actor_id: str = "mara-1") -> PlayerContribution:
    return PlayerContribution(Q, cid, "s1", turn, actor_id, by, kind, text)


def ability(who: InstanceRef = IRIS, needs=("light", "concealment"), *, attempt_id: str = "director:a-1",
            effect_id: str | None = "veil", conditions=("ambient_light",)) -> AbilityAttempt:
    return AbilityAttempt(attempt_id, who, "light_bending", frozenset(needs), "labeller:test", effect_id,
                          frozenset(conditions))


def power_frame(attempt: AbilityAttempt, *, factors=(), stakes=(FALL,), intent: str = "Use the power.") -> ActionFrame:
    return ActionFrame(attempt.attempt_id, attempt.actor, CHAT, moment(), intent, tuple(factors), tuple(stakes),
                       "labeller:test", attempt)


def build_library(store: LibraryStore) -> None:
    """The world, the chat and its three actors; control is recorded once the authority is open."""
    txn = store.transaction()
    txn.put(SourceManifest(EMBER, OWNER, "Emberfall", {"character/mara": "r1", "character/iris": "r1",
                                                       "character/sola": "r1"}), None)
    for definition in (MARA_DEF, IRIS_DEF, SOLA_DEF):
        txn.put(RecordEnvelope(definition.ref, definition_to_payload(definition), name=definition.name,
                               extra={"schema": ENTITY_SCHEMA}), None)
    txn.commit(owner_handle())
    started = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER).start_chat(
        Launch("character", MARA_DEF.ref), attach=[(IRIS_DEF.ref, "character"), (SOLA_DEF.ref, "character")]
    ).commit()
    assert (started.chat, started.actors) == (CHAT, (MARA, IRIS, SOLA))


class Table:
    """An open library: its durable authority, the adjudication key and the players' trusted handles."""

    def __init__(self, store: LibraryStore) -> None:
        self.store = store
        self.authority = open_authority(store)
        self.key = self.authority.handle_for(ADJUDICATOR, trusted=True)
        self.owner = self.authority.handle_for(OWNER, trusted=True)

    def control(self, actor_id: str, controller: str | None) -> None:
        SovereigntyLedger(self.authority).record(Q, ControlChange(Q, actor_id, controller), self.owner)

    def seat(self) -> Table:
        """Mara is Ana's PC; Iris and Sola are the director's."""
        self.control("mara-1", ANA)
        self.control("iris-1", DIRECTOR)
        self.control("sola-1", DIRECTOR)
        return self

    def player(self, principal: str = ANA):
        return self.authority.handle_for(principal, trusted=True)

    def library(self) -> AdjudicationLibrary:
        return AdjudicationLibrary(self.store, self.authority, self.key)

    def close(self) -> None:
        self.authority.close()
