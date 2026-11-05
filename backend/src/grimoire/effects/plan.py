"""Planning an effect against one campaign graph (Sprint 021): preconditions, never by name.

``plan`` is pure. It reads a director-only 017 ``GraphIndex`` at the event's sequence and says,
per operation, which relation an ``end`` stops or that a ``begin`` adds a new one. Preconditions are
the real guard against applying an effect twice: once a fact has ended it cannot be ended again,
whatever the event ledger says.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import RelationRef
from grimoire.core.knowledge import Moment
from grimoire.graph import GraphIndex, Relation

from .model import EffectError, EffectProposal, Op

_PROBE = "probe"


@dataclass(frozen=True, slots=True)
class EndStep:
    op: int
    relation: RelationRef


@dataclass(frozen=True, slots=True)
class BeginStep:
    op: int


Step = EndStep | BeginStep


def records_fact(relation: Relation, op: Op) -> bool:
    """Whether ``relation`` records ``op``'s predicate and endpoints (unordered when symmetric)."""
    if relation.predicate != op.predicate:
        return False
    if op.symmetric:
        return relation.endpoints() == {op.subject, op.object}
    return (relation.subject, relation.object) == (op.subject, op.object)


def candidates(index: GraphIndex, op: Op) -> list:
    """Every decodable relation entry recording ``op``'s fact, at any time, in id order."""
    found = [entry for entry in index.history(op.subject, requested_by=DIRECTOR)
             if isinstance(entry.relation, Relation) and records_fact(entry.relation, op)]
    return sorted(found, key=lambda entry: entry.ref.relation_id)


def plan(proposal: EffectProposal, index: GraphIndex, *, landed: frozenset[int] = frozenset()) -> tuple[Step, ...]:
    """Per operation, what to write; refusals are ``EffectError`` naming the operation index.

    ``landed`` (030's adoption) names operations whose change is already in the graph: they are not planned
    (their own record would fail their precondition), but their agent must still exist.
    """
    if type(index) is not GraphIndex or index.campaign_id != proposal.campaign_id:
        raise EffectError("campaign_mismatch")
    as_of = proposal.moment.sequence
    steps: list[Step] = []
    for number, op in enumerate(proposal.operations):
        if op.agent is not None and op.agent not in index.instances:  # never a namesake, never a future instance
            raise EffectError("missing_agent", (str(number),))
        if number in landed:  # already written: not re-planned, but a begin still needs both endpoints
            if op.kind == "begin" and not {op.subject, op.object} <= index.instances:
                raise EffectError("missing_endpoint", (str(number),))
            continue
        entries = candidates(index, op)
        if op.kind == "end":
            walkable = [e for e in entries if e.traversable and e.relation.holds_at(as_of)]
            if len(walkable) > 1:
                raise EffectError("ambiguous_relation", (str(number),))
            if not walkable:
                raise EffectError("precondition_failed", (str(number),))
            relation = walkable[0].relation
            if relation.ending is not None or relation.since.sequence >= as_of:
                raise EffectError("precondition_failed", (str(number),))
            steps.append(EndStep(number, relation.ref))
            continue
        if not {op.subject, op.object} <= index.instances:
            raise EffectError("missing_endpoint", (str(number),))
        probe = Relation(RelationRef(proposal.campaign_id, _PROBE), op.predicate, op.subject, op.object,
                         Moment(as_of), _PROBE, _PROBE)
        if any(entry.relation.overlaps(probe) for entry in entries):
            raise EffectError("precondition_failed", (str(number),))
        steps.append(BeginStep(number))
    # Every other act needs its agent's instance file too: a holdings line's agent and an ability user.
    if proposal.holdings is not None and not proposal.holdings.agents() <= index.instances:
        raise EffectError("missing_agent", ("holdings",))
    for attempt in proposal.ability_attempts:
        if attempt.actor not in index.instances:
            raise EffectError("missing_agent", (attempt.ability_id,))
    return tuple(steps)
