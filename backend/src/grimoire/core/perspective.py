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


@dataclass(frozen=True, slots=True)
class ActorKnowledge:
    """Disposable perspective for one actor at one moment, not a canonical record.

    ``history`` holds the actor's superseded beliefs, still theirs to recall.
    ``inherited`` and ``inherited_history`` are ``None`` when not computed;
    ``player_knowledge`` moves current and superseded beliefs the present
    controller did not author into them. Authorship and control are never
    shown here.
    """

    actor: InstanceRef
    as_of: int
    evidence: tuple[KnownEvidence, ...]
    sent: tuple[SentReport, ...]
    current: tuple[KnownBelief, ...]
    history: tuple[KnownBelief, ...]
    inherited: tuple[KnownBelief, ...] | None = None
    inherited_history: tuple[KnownBelief, ...] | None = None


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


def _check_actor(knowledge: object, actor: object) -> InstanceRef:
    if not isinstance(knowledge, Knowledge) or not isinstance(actor, InstanceRef):
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
                       belief.moment, belief.evidence_ids, belief.supersedes)


def _known_beliefs(knowledge: Knowledge, beliefs: Iterable[Belief]) -> tuple[KnownBelief, ...]:
    ordered = sorted(beliefs, key=lambda b: _order(b.moment, b.belief_id))
    return tuple(_known_belief(knowledge, belief) for belief in ordered)


def _beliefs_as_of(knowledge: Knowledge, actor: InstanceRef, as_of: int,
                   keep_proposition) -> tuple[list[Belief], list[Belief]]:
    """The actor's beliefs as of ``as_of``, split into current and superseded."""
    current, history = [], []
    for belief in knowledge.beliefs_held_by(actor):
        if belief.moment.sequence > as_of or not keep_proposition(belief.proposition_id):
            continue
        successor = knowledge.successor_of(belief.belief_id)
        if successor is not None and successor.moment.sequence <= as_of:
            history.append(belief)
        else:
            current.append(belief)
    return current, history


def _keep_all(_value: object) -> bool:
    return True


def _projection(knowledge: Knowledge, actor: InstanceRef, as_of: int,
                keep_item=_keep_all, keep_proposition=_keep_all) -> ActorKnowledge:
    evidence = sorted(
        (item for item in knowledge.evidence_held_by(actor)
         if item.confirmed and item.moment.sequence <= as_of and keep_item(item)),
        key=lambda item: _order(item.moment, item.evidence_id))
    sent = sorted(
        (report for report in knowledge.reports_sent_by(actor)
         if report.sent_at.sequence <= as_of and keep_proposition(report.proposition_id)),
        key=lambda report: _order(report.sent_at, report.evidence_id))
    current, history = _beliefs_as_of(knowledge, actor, as_of, keep_proposition)
    return ActorKnowledge(
        actor=actor, as_of=as_of,
        evidence=tuple(_known_evidence(knowledge, item) for item in evidence),
        sent=tuple(SentReport(r.evidence_id, r.holder, r.proposition_id,
                              knowledge.proposition(r.proposition_id).text, r.channel, r.sent_at)
                   for r in sent),
        current=_known_beliefs(knowledge, current), history=_known_beliefs(knowledge, history),
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
    actor-scoped view may present only these items, which overtly mention the
    subject. They never expose the subject's canonical fields, participants or
    edges, nor permit expanding from it. The subject is never resolved against
    the ledger, so the answer reveals nothing about whether an unheld subject
    exists.
    """
    actor = _check_actor(knowledge, actor)
    as_of = _check_as_of(as_of)
    if not isinstance(subject, Subject):
        raise KnowledgeError("invalid_record")
    if subject.campaign_id not in (None, knowledge.campaign_id):
        raise KnowledgeError("campaign_mismatch")
    return _projection(
        knowledge, actor, as_of,
        keep_item=lambda item: subject in knowledge.subjects_of(item),
        keep_proposition=lambda proposition_id: subject in knowledge.proposition(proposition_id).about,
    )


def _standing(knowledge: Knowledge, proposition_id: str, as_of: int) -> tuple[str, str | None]:
    rulings = [r for r in knowledge.rulings_on(proposition_id) if r.moment.sequence <= as_of]
    if rulings:
        latest = max(rulings, key=lambda r: r.moment.sequence)
        return latest.standing, latest.ruling_id
    if any(report.moment.sequence <= as_of for report in knowledge.reports_of(proposition_id)):
        return "reported", None
    return "unasserted", None


def standing(knowledge: Knowledge, proposition_id: str, *, as_of: int) -> str:
    """Canonical standing: the latest ruling; else ``reported`` once any report is
    received (confirmed or not); else ``unasserted``. Belief never changes it."""
    as_of = _check_as_of(as_of)
    if not isinstance(knowledge, Knowledge):
        raise KnowledgeError("invalid_record")
    knowledge.proposition(proposition_id)  # unknown or malformed ids fail without echoing text
    return _standing(knowledge, proposition_id, as_of)[0]


def director_knowledge(knowledge: Knowledge, *, as_of: int, requested_by: str) -> DirectorKnowledge:
    """The truth projection. ``requested_by=DIRECTOR`` keeps call sites explicit
    and auditable; it is not authorization, which belongs to the executor."""
    as_of = _check_as_of(as_of)
    if not isinstance(knowledge, Knowledge):
        raise KnowledgeError("invalid_record")
    if requested_by != DIRECTOR:
        raise AuthorityError("director knowledge is only for the director perspective")

    def shown(records, moment_of, id_of) -> tuple:
        visible = [r for r in records if moment_of(r).sequence <= as_of]
        return tuple(sorted(visible, key=lambda r: _order(moment_of(r), id_of(r))))

    def when_known(item: Evidence) -> Moment:  # a sent report exists from when it was sent
        return item.sent_at if isinstance(item, Report) and item.sent_at else item.moment

    return DirectorKnowledge(
        campaign_id=knowledge.campaign_id, as_of=as_of,
        events=shown(knowledge.events, lambda e: e.moment, lambda e: e.event_id),
        propositions=tuple(PropositionStanding(p, *_standing(knowledge, p.proposition_id, as_of))
                           for p in shown(knowledge.propositions, lambda p: p.moment,
                                          lambda p: p.proposition_id)),
        rulings=shown(knowledge.rulings, lambda r: r.moment, lambda r: r.ruling_id),
        evidence=shown(knowledge.evidence, when_known, lambda e: e.evidence_id),
        beliefs=shown(knowledge.beliefs, lambda b: b.moment, lambda b: b.belief_id),
        disclosures=shown(knowledge.disclosures, lambda d: d.moment, lambda d: d.disclosure_id),
    )


def _split_by_author(knowledge: Knowledge, beliefs: list[Belief],
                     author: str) -> tuple[tuple[KnownBelief, ...], tuple[KnownBelief, ...]]:
    own = [belief for belief in beliefs if belief.authored_by == author]
    other = [belief for belief in beliefs if belief.authored_by != author]
    return _known_beliefs(knowledge, own), _known_beliefs(knowledge, other)


def player_knowledge(knowledge: Knowledge, player_id: str,
                     controls: Iterable[ControlAssignment], *, as_of: int) -> PlayerKnowledge:
    """What a player may see: their characters' projections and table disclosures.

    Beliefs of a controlled actor that its present controller did not author
    (an NPC-era belief, say) are moved to ``inherited`` / ``inherited_history``,
    never shown as the PC's own. Control is confirmed per actor; ambiguity
    fails closed.
    """
    as_of = _check_as_of(as_of)
    if not isinstance(knowledge, Knowledge):
        raise KnowledgeError("invalid_record")
    if player_id == DIRECTOR:
        raise AuthorityError("the director is not a player")
    controls = tuple(controls)
    if not all(isinstance(c, ControlAssignment) for c in controls):
        raise KnowledgeError("invalid_record")
    candidates = sorted({c.actor_id for c in controls
                         if c.campaign_id == knowledge.campaign_id and c.controller_id == player_id})
    actors = []
    for actor_id in candidates:
        if controller_of(knowledge.campaign_id, actor_id, controls) != player_id:
            continue
        ref = InstanceRef(knowledge.campaign_id, "character", actor_id)
        view = _projection(knowledge, ref, as_of)
        current, history = _beliefs_as_of(knowledge, ref, as_of, _keep_all)
        own_current, inherited = _split_by_author(knowledge, current, player_id)
        own_history, inherited_history = _split_by_author(knowledge, history, player_id)
        actors.append(ActorKnowledge(view.actor, as_of, view.evidence, view.sent, own_current,
                                     own_history, inherited, inherited_history))
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
