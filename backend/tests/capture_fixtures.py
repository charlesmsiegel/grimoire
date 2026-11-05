"""The 030 proving scenario, synthetic: Tam throws one ordinary ball at a wild wisp and they become allies.

One accepted effect: holdings ``consume(tam, ordinary-ball, 1)`` plus ``begin(allied_with, tam, wisp)``,
attempt ``throw-1``. Tam is director-controlled and starts with 3 balls. The partial states are built the
ways the repo can make them: a prefix of the transaction's publish order (relations, then the event, then
holder files), or a sync that delivered only some files. Every name is synthetic.
"""

from __future__ import annotations

from grimoire.core import DIRECTOR
from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.effects import ActedLine, EffectHoldings, EffectProposal, Op
from grimoire.effectstore import EffectChange, EffectRecovery
from grimoire.holdings import Line, Operation
from grimoire.holdingstore import HoldingsLibrary
from grimoire.store import LibraryStore

from effects_fixtures import C, CHAT, CONTROLS, TAM, Handles, effects_library, moment, seed
from graph_fixtures import seed_campaign
from holdings_fixtures import holdings_library
from store_helpers import owner_handle

WISP = InstanceRef(C, "creature", "wisp")
BALL = "ordinary-ball"


def throw(*, attempt_id: str = "throw-1", sequence: int = 7, turn: str | None = None) -> EffectProposal:
    return EffectProposal(C, CHAT, moment(sequence, turn), "Tam's ball catches the wisp; it stays as an ally.",
                          (Op("begin", "allied_with", TAM, WISP, TAM),), "director", attempt_id=attempt_id,
                          holdings=EffectHoldings((ActedLine(Line.consume(TAM, BALL, 1), TAM),)))


def seed_capture(store: LibraryStore, handles: Handles) -> None:
    """The 021 cast and chat, the wisp, and Tam's 3 balls."""
    seed(store, handles)
    seed_campaign(store, (WISP,))
    seeded = HoldingsLibrary(store, owner_handle(), clock=lambda: "2026-10-30T10:00:00Z", owner="human:owner")
    seeded.apply(Operation("balls", (Line.add(TAM, BALL, 3),))).commit()  # before any acceptance writes


def planned(store: LibraryStore, handles: Handles, proposal: EffectProposal | None = None) -> EffectChange:
    return effects_library(store, handles).accept(proposal or throw(), controls=CONTROLS)


def publish(store: LibraryStore, change: EffectChange, *, relations: bool = False, event: bool = False,
            holders: bool = False) -> None:
    """Commit only some of an acceptance's files, as a partial publication leaves them."""
    txn = store.transaction()
    puts = []
    if relations:
        puts += [put for relation in change.relation_changes for put in relation.puts]
    if event:
        puts += list(change.puts)
    if holders:
        puts += [put for part in change.holdings_changes for put in part.puts]
    for record, expected in puts:
        txn.put(record, expected)
    txn.commit(change.library.handle)


def recovery(store: LibraryStore, handles: Handles) -> EffectRecovery:
    return EffectRecovery(effects_library(store, handles))


def balls(store: LibraryStore) -> int:
    return holdings_library(store).read(TAM).counts().get(BALL, 0)


def allies(store: LibraryStore) -> list:
    """Every unretracted allied_with record between Tam and the wisp."""
    from graph_fixtures import relation_library
    graph = relation_library(store).graph(C, requested_by=DIRECTOR)
    found = []
    for ref in graph.etags:
        relation = graph.index.relation(ref.relation_id, requested_by=DIRECTOR).relation
        if relation.predicate == "allied_with" and relation.retraction is None:
            found.append(relation)
    return found


def problems(store: LibraryStore, handles: Handles) -> list[str]:
    found = effects_library(store, handles).continuity(C, as_of=100, requested_by=DIRECTOR).problems
    return sorted(problem.code for problem in found)


__all__ = ["BALL", "WISP", "RelationRef", "allies", "balls", "planned", "problems", "publish", "recovery",
           "seed_capture", "throw"]
