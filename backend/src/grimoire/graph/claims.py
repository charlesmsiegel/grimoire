"""Claims: what an actor thinks is connected, read only from its own projection (Sprint 017).

A ``ClaimShape`` gives one 012 proposition a typed, **positive, present-tense** relational
reading. Its endpoints must be overt referents of the proposition (in its ``about``), so a shape
can never identify "the masked man" as the duke. A shape is the proposition's meaning, not a
fact: a false rumour stays a claim with an attributed source and a stance.

``claims_about`` reads exactly one ``ActorKnowledge`` (012's projection) and lists, for one node,
the actor's held claims that name it, without the other endpoint and without expanding to
anything else, so 012's contract holds: subjects never appear in an actor projection, and an
actor view never permits expanding from a subject. No canonical graph is an input.

``claim_matches`` and ``check_subjects`` are director-side.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Knowledge, KnowledgeError, Subject
from grimoire.core.perspective import ActorKnowledge, KnownEvidence, SentReport

from .index import GraphIndex, GraphProblem, Relation, check_as_of, require_director
from .model import PREDICATES, GraphError


@dataclass(frozen=True, slots=True)
class ClaimShape:
    """Proposition ``proposition_id`` asserts ``predicate(subject, object)`` now (positive, present tense)."""

    campaign_id: str
    proposition_id: str
    predicate: str
    subject: InstanceRef
    object: InstanceRef

    def __post_init__(self) -> None:
        if type(self.campaign_id) is not str or type(self.proposition_id) is not str:
            raise GraphError("invalid_field")
        if type(self.subject) is not InstanceRef or type(self.object) is not InstanceRef:
            raise GraphError("invalid_field")
        if type(self.predicate) is not str or self.predicate not in PREDICATES:
            raise GraphError("unknown_predicate")
        if {self.subject.campaign_id, self.object.campaign_id} != {self.campaign_id}:
            raise GraphError("campaign_mismatch")
        if self.subject == self.object:
            raise GraphError("self_relation")

    def names(self, node: InstanceRef) -> bool:
        return node in (self.subject, self.object)


class ClaimSet:
    """Claim shapes validated against the ledger they belong to. Keeps only the campaign and the shapes."""

    __slots__ = ("campaign_id", "shapes")

    def __init__(self, knowledge: Knowledge, shapes: Iterable[ClaimShape]) -> None:
        if not isinstance(knowledge, Knowledge):
            raise GraphError("invalid_field")
        found: dict[str, ClaimShape] = {}
        for shape in shapes:
            if type(shape) is not ClaimShape:
                raise GraphError("invalid_field")
            name = (f"proposition:{shape.proposition_id}",)
            if shape.campaign_id != knowledge.campaign_id:
                raise GraphError("campaign_mismatch", name)
            try:
                proposition = knowledge.proposition(shape.proposition_id)
            except KnowledgeError:
                raise GraphError("unknown_proposition", name) from None
            overt = set(proposition.about)
            if not all(Subject("instance", str(end)) in overt for end in (shape.subject, shape.object)):
                raise GraphError("not_overt", name)
            if shape.proposition_id in found:
                raise GraphError("duplicate_claim", name)
            found[shape.proposition_id] = shape
        object.__setattr__(self, "campaign_id", knowledge.campaign_id)
        object.__setattr__(self, "shapes", MappingProxyType(found))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("a ClaimSet is immutable")

    def __repr__(self) -> str:
        return f"ClaimSet({self.campaign_id!r}, {len(self.shapes)} shapes)"

    def __eq__(self, other: object) -> bool:  # by content, so two reads of one ledger compare equal (020)
        if type(other) is not ClaimSet:
            return NotImplemented
        return self.campaign_id == other.campaign_id and dict(self.shapes) == dict(other.shapes)

    def __hash__(self) -> int:
        return hash((self.campaign_id, frozenset(self.shapes.items())))


@dataclass(frozen=True, slots=True)
class NodeClaim:
    """One held claim naming the node. ``position`` is subject/object, or ``either`` for a symmetric predicate."""

    proposition_id: str
    predicate: str
    position: str
    text: str
    heard: tuple[KnownEvidence, ...]
    sent: tuple[SentReport, ...]
    stance: str | None
    inherited_stance: str | None


@dataclass(frozen=True, slots=True)
class NodeClaims:
    actor: InstanceRef
    as_of: int
    node: InstanceRef
    items: tuple[NodeClaim, ...]


def claims_about(view: ActorKnowledge, claims: ClaimSet, node: InstanceRef) -> NodeClaims:
    """The actor's held claims that name ``node``: from ``view`` only, never the other endpoint.

    An unknown node and an existing-but-unheld one give the same empty answer.
    """
    if type(view) is not ActorKnowledge:
        raise GraphError("not_actor_knowledge")
    if type(claims) is not ClaimSet or type(node) is not InstanceRef:
        raise GraphError("invalid_field")
    if view.actor.campaign_id != claims.campaign_id or node.campaign_id != claims.campaign_id:
        raise GraphError("campaign_mismatch")
    heard: dict[str, list[KnownEvidence]] = {}
    sent: dict[str, list[SentReport]] = {}
    texts: dict[str, str] = {}
    for item in view.evidence:
        if item.kind == "report" and item.proposition_id is not None:
            heard.setdefault(item.proposition_id, []).append(item)
            texts[item.proposition_id] = item.content
    for report in view.sent:
        sent.setdefault(report.proposition_id, []).append(report)
        texts[report.proposition_id] = report.text
    stances = {belief.proposition_id: belief.stance for belief in view.current}
    inherited = {belief.proposition_id: belief.stance for belief in view.inherited or ()}
    for belief in (*view.current, *(view.inherited or ())):
        texts[belief.proposition_id] = belief.text
    items = []
    for proposition_id, text in texts.items():
        shape = claims.shapes.get(proposition_id)
        if shape is None or not shape.names(node):
            continue
        if PREDICATES[shape.predicate].symmetric:
            position = "either"
        else:
            position = "subject" if shape.subject == node else "object"
        items.append(NodeClaim(proposition_id, shape.predicate, position, text,
                               tuple(heard.get(proposition_id, ())), tuple(sent.get(proposition_id, ())),
                               stances.get(proposition_id), inherited.get(proposition_id)))
    items.sort(key=lambda item: (item.predicate, item.proposition_id))
    return NodeClaims(view.actor, view.as_of, node, tuple(items))


def _same_campaign(index: GraphIndex, campaign_id: str) -> None:
    if type(index) is not GraphIndex:
        raise GraphError("invalid_field")
    if index.campaign_id != campaign_id:
        raise GraphError("campaign_mismatch")


def claim_matches(index: GraphIndex, claims: ClaimSet, proposition_id: str, *, as_of: int,
                  requested_by: str) -> tuple[str, ...]:
    """Director-only: canonical relations walkable at ``as_of`` that match the claim. Creates no ruling."""
    require_director(requested_by)
    as_of = check_as_of(as_of)
    if type(claims) is not ClaimSet:
        raise GraphError("invalid_field")
    _same_campaign(index, claims.campaign_id)
    shape = claims.shapes.get(proposition_id) if type(proposition_id) is str else None
    if shape is None:
        raise GraphError("unknown_claim")
    return index._matching(shape.predicate, shape.subject, shape.object, as_of)


def check_subjects(knowledge: Knowledge, index: GraphIndex, *, requested_by: str) -> tuple[GraphProblem, ...]:
    """Director-side: relation and instance subjects cited by propositions and rulings that do not hold.

    Definition subjects are not checked here (they resolve against the definition index).
    """
    require_director(requested_by)
    if not isinstance(knowledge, Knowledge):
        raise GraphError("invalid_field")
    _same_campaign(index, knowledge.campaign_id)
    entries = index._entries
    problems: list[GraphProblem] = []
    citing = [(f"proposition:{p.proposition_id}", p.moment.sequence, p.about, "about_future_relation")
              for p in knowledge.propositions]
    citing += [(f"ruling:{r.ruling_id}", r.moment.sequence, r.grounds, "ruling_cites_future_relation")
               for r in knowledge.rulings]
    for cited_by, sequence, subjects, future in citing:
        for subject in subjects:
            text = str(subject)
            if subject.kind == "instance":
                if InstanceRef.parse(subject.ref) not in index.instances:
                    problems.append(GraphProblem("unknown_instance", (), cited_by, text))
                continue
            if subject.kind != "relation":
                continue
            entry = entries.get(subject.ref)
            code = None
            if entry is None:
                code = "unknown_relation"
            elif entry.undecodable is not None:
                code = "undecodable_relation"
            elif entry.unsupported:
                code = "unsupported_relation"
            elif entry.retracted:
                code = "retracted_relation"
            elif isinstance(entry.relation, Relation) and entry.relation.since.sequence > sequence:
                code = future
            if code is not None:
                problems.append(GraphProblem(code, (subject.ref,), cited_by, text))
    return tuple(sorted(problems, key=GraphProblem.sort_key))
