"""Explicit admission and per-holder projection; never propagation, inference or a truth writer."""
from __future__ import annotations

from collections.abc import Iterable

from grimoire.core.authority import DIRECTOR, AuthorityError, controller_of
from grimoire.core.entity import effective_entity, is_agent
from grimoire.core.identity import InstanceRef
from grimoire.core.index import DefinitionIndex
from grimoire.core.knowledge import Knowledge, Subject
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import actor_knowledge
from grimoire.core.roster import CampaignRoster

from .model import DIMENSIONS, Attitude, AttitudeLedger, Disposition, RelationshipError, sequence


def _resolve(roster: CampaignRoster, definitions: DefinitionIndex, ref: InstanceRef):
    try:
        return effective_entity(roster.instance(ref), definitions)
    except (ValueError, LookupError, TypeError):
        raise RelationshipError('unresolved_actor') from None


def admit(ledger: AttitudeLedger, attitude: Attitude, *, knowledge: Knowledge,
          roster: CampaignRoster, definitions: DefinitionIndex,
          controls: Iterable[ControlAssignment], authored_by: str, now: int,
          expected_revision: int) -> AttitudeLedger:
    """Admit one NPC's attitude change from a consistent caller-owned current snapshot.

    The holder must be a director-controlled ``character`` agent and the change director-authored:
    a player character's feelings are their player's to author, narration never writes one, and a
    group holds no attitude here (community reputation is later work). ``now`` is the campaign's
    current sequence; a change dated after it is refused, because one future-dated record would
    lock its chain. Every ground must be the holder's **own** confirmed evidence, or its own
    current belief, at or before the change's moment, read through 012's actor projection; and at least one ground must overtly
    concern ``toward`` (a report the holder takes ``toward`` to be the source of, or a report or
    belief whose proposition is overtly about ``toward``). Group membership, graph connectivity,
    another actor's evidence and director-only events never count, so an absent member cannot
    acquire an attitude about an episode it has no evidence of.

    Grounds prove access and relevance, never that the account is a fair reading: the director
    authors the interpretation. An exact retry rechecks present authority, identity and grounds
    before returning the same ledger. Persistence and transaction locking belong to a future
    adapter.
    """
    if (type(ledger) is not AttitudeLedger or type(attitude) is not Attitude
            or type(knowledge) is not Knowledge or type(roster) is not CampaignRoster
            or not isinstance(definitions, DefinitionIndex)):
        raise RelationshipError('invalid_input')
    sequence(expected_revision)
    sequence(now)
    if attitude.moment.sequence > now:
        raise RelationshipError('future_moment')
    if attitude.holder.kind != 'character':
        raise RelationshipError('unsupported_holder_kind')
    campaign = ledger.campaign_id
    if any(c != campaign for c in (knowledge.campaign_id, roster.campaign_id, attitude.holder.campaign_id)):
        raise RelationshipError('campaign_mismatch')
    if not is_agent(_resolve(roster, definitions, attitude.holder)):
        raise RelationshipError('not_agent')
    target = _resolve(roster, definitions, attitude.toward)
    if not is_agent(target) and attitude.toward.kind != 'group':
        raise RelationshipError('not_agent_or_group')
    controls = tuple(controls)
    if not all(type(c) is ControlAssignment for c in controls):
        raise RelationshipError('invalid_control')
    try:
        controller = controller_of(campaign, attitude.holder.instance_id, controls)
    except AuthorityError:
        raise RelationshipError('ambiguous_control') from None
    if controller is None:
        raise RelationshipError('unknown_control')
    if controller != DIRECTOR:
        raise RelationshipError('pc_interiority')
    if authored_by != DIRECTOR:
        raise RelationshipError('npc_authority_required')
    _check_grounds(attitude, knowledge)
    for original_revision, existing in enumerate(ledger.records):
        if existing.attitude_id == attitude.attitude_id:
            if existing != attitude or expected_revision != original_revision:
                raise RelationshipError('retry_conflict')
            return ledger
    if expected_revision != ledger.revision:
        raise RelationshipError('stale_revision')
    return AttitudeLedger(campaign, (*ledger.records, attitude))


def _check_grounds(attitude: Attitude, knowledge: Knowledge) -> None:
    own = actor_knowledge(knowledge, attitude.holder, as_of=attitude.moment.sequence)
    evidence = {e.evidence_id: e for e in own.evidence}
    beliefs = {b.belief_id: b for b in own.current}
    if not (set(attitude.evidence_ids) <= evidence.keys() and set(attitude.belief_ids) <= beliefs.keys()):
        raise RelationshipError('unavailable_grounds')
    target = Subject('instance', str(attitude.toward))
    about = {p.proposition_id: p.about for p in knowledge.propositions}

    def overt(proposition_id: str | None) -> bool:
        return proposition_id is not None and target in about.get(proposition_id, ())

    related = any(evidence[i].attributed_to == attitude.toward or overt(evidence[i].proposition_id)
                  for i in attitude.evidence_ids)
    related = related or any(overt(beliefs[i].proposition_id) for i in attitude.belief_ids)
    if not related:
        raise RelationshipError('unrelated_grounds')


def _checked(ledger: AttitudeLedger, holder: InstanceRef, as_of: int) -> None:
    if type(ledger) is not AttitudeLedger or type(holder) is not InstanceRef:
        raise RelationshipError('invalid_input')
    sequence(as_of)
    if holder.campaign_id != ledger.campaign_id:
        raise RelationshipError('campaign_mismatch')


def _npc(ledger: AttitudeLedger, holder: InstanceRef, controls: Iterable[ControlAssignment]) -> None:
    """Director-authored attitudes are an NPC's: once a player controls the holder they are not shown
    as that character's own feelings (012's inherited rule); a player-facing view is later work."""
    controls = tuple(controls)
    if not all(type(c) is ControlAssignment for c in controls):
        raise RelationshipError('invalid_control')
    try:
        controller = controller_of(ledger.campaign_id, holder.instance_id, controls)
    except AuthorityError:
        raise RelationshipError('ambiguous_control') from None
    if controller != DIRECTOR:
        raise RelationshipError('pc_interiority' if controller is not None else 'unknown_control')


def _heads(ledger: AttitudeLedger, holder: InstanceRef, as_of: int,
           controls: Iterable[ControlAssignment]) -> tuple[Attitude, ...]:
    _checked(ledger, holder, as_of)
    _npc(ledger, holder, controls)
    visible = tuple(a for a in ledger.records if a.holder == holder and a.moment.sequence <= as_of)
    superseded = {a.supersedes for a in visible}
    order = {d: i for i, d in enumerate(DIMENSIONS)}
    return tuple(sorted((a for a in visible if a.attitude_id not in superseded),
                        key=lambda a: (str(a.toward), order[a.dimension])))


def attitudes_of(ledger: AttitudeLedger, holder: InstanceRef, *, as_of: int,
                 controls: Iterable[ControlAssignment],
                 toward: InstanceRef | None = None) -> tuple[Attitude, ...]:
    """The exact NPC holder's current attitudes at ``as_of``, optionally toward one actor.

    Only the holder's own chains are read: never another actor's, a group's, or anything derived
    from membership. A holder with none gets an empty answer, which means "no recorded attitude",
    not neutrality. A holder the director does not currently control is refused
    (``pc_interiority``). This pure director/internal projection is not an authentication boundary.
    """
    heads = _heads(ledger, holder, as_of, controls)
    if toward is None:
        return heads
    if type(toward) is not InstanceRef:
        raise RelationshipError('invalid_input')
    return tuple(a for a in heads if a.toward == toward)


def history_of(ledger: AttitudeLedger, holder: InstanceRef, toward: InstanceRef, dimension: str, *,
               as_of: int) -> tuple[Attitude, ...]:
    """One chain as of ``as_of``, oldest first, with grounds: the director's audit view, which keeps
    an NPC era's records readable after a player takes the holder over."""
    _checked(ledger, holder, as_of)
    if type(toward) is not InstanceRef or dimension not in DIMENSIONS:
        raise RelationshipError('invalid_input')
    return tuple(a for a in ledger.records if a.key == (holder, toward, dimension)
                 and a.moment.sequence <= as_of)


def dispositions(ledger: AttitudeLedger, holder: InstanceRef, *, as_of: int,
                 controls: Iterable[ControlAssignment]) -> tuple[Disposition, ...]:
    """The NPC holder's current attitudes as narrative context, without grounds, provenance or labels."""
    return tuple(Disposition(a.toward, a.dimension, a.account, a.degree, a.moment.sequence)
                 for a in _heads(ledger, holder, as_of, controls))
