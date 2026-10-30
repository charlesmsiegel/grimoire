"""Read projections of a ``Knowledge`` ledger, one explicit perspective at a time.

Every projection names its perspective and an ``as_of`` sequence; there is no
"read everything" accessor for actor-facing consumers. The actor projection
fails closed: only the actor's own confirmed evidence, the reports it knowingly
sent and its own beliefs, never events, standing, true senders or other actors'
records. The director projection is a separate type. Nothing here calls a model.

``admit_belief`` is the authoring-time gate that keeps a player-controlled
actor's beliefs in the player's hands (``authority``'s protected ``belief``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .authority import CONTRIBUTION_DOMAINS, DIRECTOR, AuthorityError, PlayerContribution, controller_of
from .identity import InstanceRef
from .knowledge import (
    Belief, Disclosure, Event, Evidence, Knowledge, KnowledgeError, Moment, Perception,
    Proposition, Report, Ruling, Subject,
)
from .model import ControlAssignment


# --- projected values ----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class KnownEvidence:
    """One confirmed item of the actor's own evidence, as the actor has it."""

    evidence_id: str
    kind: str  # "perception" or "report"
    channel: str
    moment: Moment
    content: str  # the percept, or the reported proposition's text
    proposition_id: str | None = None
    attributed_to: InstanceRef | None = None


@dataclass(frozen=True, slots=True)
class SentReport:
    """A report the actor knowingly sent; no receipt time or confirmation."""

    evidence_id: str
    recipient: InstanceRef
    proposition_id: str
    text: str
    channel: str
    sent_at: Moment


@dataclass(frozen=True, slots=True)
class KnownBelief:
    belief_id: str
    proposition_id: str
    text: str
    stance: str
    moment: Moment
    evidence_ids: tuple[str, ...]
    supersedes: str | None
    authored_by: str


@dataclass(frozen=True, slots=True)
class ActorKnowledge:
    """Disposable perspective for one actor at one moment, not a canonical record.

    ``history`` holds the actor's superseded beliefs, still theirs to recall.
    ``inherited`` is ``None`` when not computed; ``player_knowledge`` fills it
    with current beliefs the present controller did not author.
    """

    actor: InstanceRef
    as_of: int
    evidence: tuple[KnownEvidence, ...]
    sent: tuple[SentReport, ...]
    current: tuple[KnownBelief, ...]
    history: tuple[KnownBelief, ...]
    inherited: tuple[KnownBelief, ...] | None = None


@dataclass(frozen=True, slots=True)
class TableEvent:
    """An event the table may read out of character from ``moment``; no PC learns it."""

    event_id: str
    summary: str
    moment: Moment


@dataclass(frozen=True, slots=True)
class PlayerKnowledge:
    player_id: str
    as_of: int
    actors: tuple[ActorKnowledge, ...]
    table_events: tuple[TableEvent, ...]


@dataclass(frozen=True, slots=True)
class PropositionStanding:
    proposition: Proposition
    standing: str
    ruling_id: str | None


@dataclass(frozen=True, slots=True)
class DirectorKnowledge:
    """Director truth as of ``as_of``: never hand this to an actor prompt."""

    campaign_id: str
    as_of: int
    events: tuple[Event, ...]
    propositions: tuple[PropositionStanding, ...]
    rulings: tuple[Ruling, ...]
    evidence: tuple[Evidence, ...]  # unconfirmed items included; see ``confirmed``
    beliefs: tuple[Belief, ...]
    disclosures: tuple[Disclosure, ...]


# --- projections -----------------------------------------------------------------

def _check_as_of(as_of: object) -> int:
    if type(as_of) is not int or as_of < 0:
        raise KnowledgeError("invalid_as_of")
    return as_of


def _check_actor(knowledge: Knowledge, actor: object) -> InstanceRef:
    if not isinstance(actor, InstanceRef):
        raise KnowledgeError("invalid_record")
    if actor.campaign_id != knowledge.campaign_id:
        raise KnowledgeError("campaign_mismatch")
    return actor


def _order(moment: Moment, record_id: str) -> tuple[int, str]:
    return (moment.sequence, record_id)


def _known_evidence(knowledge: Knowledge, item: Evidence) -> KnownEvidence:
    if isinstance(item, Perception):
        return KnownEvidence(item.evidence_id, "perception", item.channel, item.moment, item.percept)
    return KnownEvidence(item.evidence_id, "report", item.channel, item.moment,
                         knowledge.proposition(item.proposition_id).text,
                         item.proposition_id, item.attributed_to)


def _known_belief(knowledge: Knowledge, belief: Belief) -> KnownBelief:
    return KnownBelief(belief.belief_id, belief.proposition_id,
                       knowledge.proposition(belief.proposition_id).text, belief.stance,
                       belief.moment, belief.evidence_ids, belief.supersedes, belief.authored_by)


def _projection(knowledge: Knowledge, actor: InstanceRef, as_of: int,
                evidence_filter=None, proposition_filter=None) -> ActorKnowledge:
    index = knowledge._index
    keep_item = evidence_filter or (lambda item: True)
    keep_proposition = proposition_filter or (lambda proposition_id: True)
    evidence = sorted(
        (item for item in index.evidence_by_holder.get(actor, ())
         if item.confirmed and item.moment.sequence <= as_of and keep_item(item)),
        key=lambda item: _order(item.moment, item.evidence_id))
    sent = sorted(
        (report for report in index.sent_by.get(actor, ())
         if report.sent_at.sequence <= as_of and keep_proposition(report.proposition_id)),
        key=lambda report: _order(report.sent_at, report.evidence_id))
    current, history = [], []
    for belief in index.beliefs_by_holder.get(actor, ()):
        if belief.moment.sequence > as_of or not keep_proposition(belief.proposition_id):
            continue
        successor = index.superseded_by.get(belief.belief_id)
        superseded = successor is not None and successor.moment.sequence <= as_of
        (history if superseded else current).append(belief)
    ordered = lambda beliefs: tuple(_known_belief(knowledge, b) for b in
                                    sorted(beliefs, key=lambda b: _order(b.moment, b.belief_id)))
    return ActorKnowledge(
        actor=actor, as_of=as_of,
        evidence=tuple(_known_evidence(knowledge, item) for item in evidence),
        sent=tuple(SentReport(r.evidence_id, r.holder, r.proposition_id,
                              knowledge.proposition(r.proposition_id).text, r.channel, r.sent_at)
                   for r in sent),
        current=ordered(current), history=ordered(history),
    )


def actor_knowledge(knowledge: Knowledge, actor: InstanceRef, *, as_of: int) -> ActorKnowledge:
    """What ``actor`` perceived, was told, sent and concluded, as of ``as_of``.

    A function of the actor's own evidence, own beliefs and the addressed
    reports it sent; no other record can change it. Empty when the actor has
    none: absence is the normal, fail-closed answer.
    """
    return _projection(knowledge, _check_actor(knowledge, actor), _check_as_of(as_of))


def knowledge_about(knowledge: Knowledge, actor: InstanceRef, subject: Subject, *,
                    as_of: int) -> ActorKnowledge:
    """The actor projection restricted to items about ``subject``.

    The single consumer contract for graph and retrieval views (017, 031): an
    actor-scoped view may present only these items. Holding them licenses
    referring to the subject; it never exposes the subject's canonical fields,
    participants or edges, nor permits expanding from it. The subject is never
    resolved against the ledger, so the answer reveals nothing about whether
    an unheld subject exists.
    """
    actor = _check_actor(knowledge, actor)
    as_of = _check_as_of(as_of)
    if not isinstance(subject, Subject):
        raise KnowledgeError("invalid_record")
    if subject.campaign_id not in (None, knowledge.campaign_id):
        raise KnowledgeError("campaign_mismatch")
    return _projection(
        knowledge, actor, as_of,
        evidence_filter=lambda item: subject in knowledge.subjects_of(item),
        proposition_filter=lambda proposition_id: subject in knowledge.proposition(proposition_id).about,
    )


def _standing(knowledge: Knowledge, proposition_id: str, as_of: int) -> tuple[str, str | None]:
    index = knowledge._index
    rulings = [r for r in index.rulings_by_proposition.get(proposition_id, ())
               if r.moment.sequence <= as_of]
    if rulings:
        latest = max(rulings, key=lambda r: r.moment.sequence)
        return latest.standing, latest.ruling_id
    if any(report.moment.sequence <= as_of
           for report in index.reports_by_proposition.get(proposition_id, ())):
        return "reported", None
    return "unasserted", None


def standing(knowledge: Knowledge, proposition_id: str, *, as_of: int) -> str:
    """Canonical standing: the latest ruling; else ``reported`` once any report is
    received (confirmed or not); else ``unasserted``. Belief never changes it."""
    as_of = _check_as_of(as_of)
    if proposition_id not in knowledge._index.propositions:
        raise KnowledgeError("unknown_proposition", (f"proposition:{proposition_id}",))
    return _standing(knowledge, proposition_id, as_of)[0]


def director_knowledge(knowledge: Knowledge, *, as_of: int, requested_by: str) -> DirectorKnowledge:
    """The truth projection. ``requested_by=DIRECTOR`` keeps call sites explicit
    and auditable; it is not authorization, which belongs to the executor."""
    as_of = _check_as_of(as_of)
    if requested_by != DIRECTOR:
        raise AuthorityError("director knowledge is only for the director perspective")
    upto = lambda moment: moment.sequence <= as_of
    return DirectorKnowledge(
        campaign_id=knowledge.campaign_id, as_of=as_of,
        events=tuple(e for e in knowledge.events if upto(e.moment)),
        propositions=tuple(PropositionStanding(p, *_standing(knowledge, p.proposition_id, as_of))
                           for p in knowledge.propositions if upto(p.moment)),
        rulings=tuple(r for r in knowledge.rulings if upto(r.moment)),
        evidence=tuple(e for e in knowledge.evidence
                       if upto(e.sent_at if isinstance(e, Report) and e.sent_at else e.moment)),
        beliefs=tuple(b for b in knowledge.beliefs if upto(b.moment)),
        disclosures=tuple(d for d in knowledge.disclosures if upto(d.moment)),
    )


def player_knowledge(knowledge: Knowledge, player_id: str,
                     controls: Iterable[ControlAssignment], *, as_of: int) -> PlayerKnowledge:
    """What a player may see: their characters' projections and table disclosures.

    Current beliefs of a controlled actor that its present controller did not
    author (an NPC-era belief, say) are moved to ``inherited``, never shown as
    the PC's own. Control is confirmed per actor; ambiguity fails closed.
    """
    as_of = _check_as_of(as_of)
    if player_id == DIRECTOR:
        raise AuthorityError("the director is not a player")
    controls = tuple(controls)
    candidates = sorted({c.actor_id for c in controls
                         if c.campaign_id == knowledge.campaign_id and c.controller_id == player_id})
    actors = []
    for actor_id in candidates:
        if controller_of(knowledge.campaign_id, actor_id, controls) != player_id:
            continue
        view = _projection(knowledge, InstanceRef(knowledge.campaign_id, "character", actor_id), as_of)
        own = tuple(b for b in view.current if b.authored_by == player_id)
        inherited = tuple(b for b in view.current if b.authored_by != player_id)
        actors.append(ActorKnowledge(view.actor, as_of, view.evidence, view.sent, own,
                                     view.history, inherited))
    if not actors:
        raise AuthorityError("this player controls no character in the campaign")
    first_shown: dict = {}
    for disclosure in knowledge.disclosures:
        if disclosure.moment.sequence <= as_of:
            known = first_shown.get(disclosure.event_id)
            if known is None or disclosure.moment.sequence < known.sequence:
                first_shown[disclosure.event_id] = disclosure.moment
    table = sorted((TableEvent(event_id, knowledge.event(event_id).summary, moment)
                    for event_id, moment in first_shown.items()),
                   key=lambda t: _order(t.moment, t.event_id))
    return PlayerKnowledge(player_id, as_of, tuple(actors), tuple(table))


# --- authoring-time PC sovereignty --------------------------------------------------

class BeliefAuthorityError(AuthorityError):
    """A belief its author may not write. Like ``AuthorityError``, never an ``OSError``."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason,))


def admit_belief(belief: Belief, proposition: Proposition,
                 controls: Iterable[ControlAssignment],
                 contribution: PlayerContribution | None = None) -> Belief:
    """Admit a newly authored belief, or refuse it.

    A director-controlled holder's beliefs are the director's. A
    player-controlled holder's belief must be the controller's, backed by
    their own ``interiority`` contribution from the same scene and turn whose
    text is the proposition (Wave 1's verbatim rule). Delegated belief
    authoring is not supported yet and fails closed.
    """
    if not isinstance(belief, Belief) or not isinstance(proposition, Proposition):
        raise KnowledgeError("invalid_record")
    if (proposition.proposition_id, proposition.campaign_id) != (belief.proposition_id,
                                                                 belief.campaign_id):
        raise KnowledgeError("invalid_record", (f"belief:{belief.belief_id}",
                                                f"proposition:{proposition.proposition_id}"))
    if belief.holder.kind != "character":
        raise BeliefAuthorityError("unsupported_holder_kind")
    controller = controller_of(belief.campaign_id, belief.holder.instance_id, tuple(controls))
    if controller is None:
        raise BeliefAuthorityError("unknown_control")
    if controller == DIRECTOR:
        if (belief.authored_by != DIRECTOR or belief.contribution_id is not None
                or contribution is not None):
            raise BeliefAuthorityError("wrong_author")
        return belief
    if belief.authored_by != controller:
        raise BeliefAuthorityError("pc_belief_authorship")
    if not (isinstance(contribution, PlayerContribution)
            and contribution.campaign_id == belief.campaign_id
            and contribution.actor_id == belief.holder.instance_id
            and contribution.submitted_by == controller
            and contribution.contribution_id == belief.contribution_id
            and "belief" in CONTRIBUTION_DOMAINS[contribution.kind]
            and (contribution.scene_id, contribution.turn_id)
            == (belief.moment.scene_id, belief.moment.turn_id)):
        raise BeliefAuthorityError("invalid_expression")
    if proposition.text.strip() != contribution.text.strip():
        raise BeliefAuthorityError("not_verbatim")
    return belief
