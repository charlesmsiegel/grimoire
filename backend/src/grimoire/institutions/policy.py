"""One explicit leader choice, under a caller-owned frozen authority snapshot.

The caller supplies current canonical relations and roster together. This pure
boundary cannot authenticate a human or lock a live store; a future adapter must
bind its review to that snapshot. Nothing here delivers reports, commits actions,
or treats institutional membership as evidence (057's distant-chapter boundary).
"""
from grimoire.core.authority import DIRECTOR, AuthorityError, controller_of
from grimoire.core.entity import effective_entity, is_agent
from grimoire.core.index import DefinitionIndex
from grimoire.core.knowledge import Moment
from grimoire.core.model import ControlAssignment
from grimoire.core.roster import CampaignRoster
from grimoire.graph.model import Relation

from .model import (
    CollectiveDecision, DecisionRequest, InstitutionError, InstitutionState, sequence,
)


def decide(state, *, preferences, proposal_id, choice, operation_id, expected_revision,
           roster, definitions, relations, controls, authored_by, moment, authorization_moment, audience,
           channel, provenance):
    """Return ``(new_state, decision)``; exact retry returns the existing state.

    Unilateral leader authority is deliberate, not an inferred majority vote.
    A preference is an NPC's authored intention, never an executed member action.
    ``authorization_moment`` is the caller's current snapshot time, at or after
    ``moment``; it is deliberately excluded from immutable retry identity.
    """
    if type(state) is not InstitutionState or not isinstance(roster, CampaignRoster) or not isinstance(definitions, DefinitionIndex):
        raise InstitutionError('invalid_field')
    request = DecisionRequest(state.institution, operation_id, proposal_id, choice, preferences,
                              expected_revision, moment, audience, channel, provenance, authored_by)
    if type(authorization_moment) is not Moment or authorization_moment.sequence < moment.sequence:
        raise InstitutionError('authorization_moment')
    institution = state.institution
    if roster.campaign_id != institution.group.campaign_id:
        raise InstitutionError('identity')
    relations = sequence(relations, 256)
    controls = sequence(controls, 256)
    if not all(type(c) is ControlAssignment for c in controls):
        raise InstitutionError('invalid_field')
    if not all(type(r) is Relation for r in relations):
        raise InstitutionError('invalid_field')
    if len({r.ref for r in relations}) != len(relations):
        raise InstitutionError('duplicate_relation')

    def entity(ref, *, agent=False):
        try:
            result = effective_entity(roster.instance(ref), definitions)
        except (ValueError, LookupError, TypeError):
            raise InstitutionError('identity') from None
        if agent and not is_agent(result):
            raise InstitutionError('not_agent')
        return result

    entity(institution.group)
    for relation in relations:
        if relation.ref.campaign_id != roster.campaign_id:
            raise InstitutionError('identity')
        entity(relation.subject); entity(relation.object)

    def members_at(sequence_number):
        active = []
        seen = set()
        for relation in relations:
            if relation.predicate == 'member_of' and relation.holds_at(sequence_number):
                pair = (relation.subject, relation.object)
                if pair in seen:
                    raise InstitutionError('duplicate_membership')
                seen.add(pair)
                active.append(relation)
        # One explicit chapter hop only; no graph closure or knowledge propagation.
        chapters = {r.subject for r in active if r.object == institution.group and r.subject.kind == 'group'}
        return {r.subject for r in active if r.object == institution.group or r.object in chapters}

    # The original moment is part of the receipt; authorization time belongs to
    # the current snapshot. Both must qualify, so neither a removed member's
    # retry nor a new member's backdated choice can bypass the membership gate.
    members = members_at(moment.sequence) & members_at(authorization_moment.sequence)

    def npc_member(ref):
        entity(ref, agent=True)
        try:
            controller = controller_of(roster.campaign_id, ref.instance_id, controls)
        except AuthorityError:
            raise InstitutionError('npc_authority') from None
        if authored_by != DIRECTOR or controller != DIRECTOR:
            raise InstitutionError('npc_authority')
        if ref not in members:
            raise InstitutionError('not_member')

    npc_member(institution.leader)
    for preference in request.preferences:
        npc_member(preference.actor)
        if preference.proposal_id != proposal_id:
            raise InstitutionError('proposal_mismatch')
    for actor in request.audience:
        entity(actor, agent=True)
    # Authorization is checked even for a retry: receipt lookup is not an access bypass.
    for prior in state.decisions:
        if prior.operation_id == operation_id:
            if prior.request != request:
                raise InstitutionError('operation_conflict')
            return state, prior
    if expected_revision != state.revision:
        raise InstitutionError('stale_revision')
    if state.decisions and moment.sequence < state.decisions[-1].request.moment.sequence:
        raise InstitutionError('stale_moment')
    decision = CollectiveDecision(request)
    return InstitutionState(institution, state.revision + 1, state.decisions + (decision,)), decision
