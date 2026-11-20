"""Synthetic institutional dissent: collective policy is not member knowledge."""
from dataclasses import replace

import pytest

from grimoire.core.authority import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.facets import Agent, FacetBundle
from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.index import DefinitionIndex
from grimoire.core.knowledge import Event, Knowledge, Moment, Perception, Proposition, Report
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import actor_knowledge
from grimoire.core.roster import CampaignRoster
from grimoire.graph.model import Ending, Relation, Retraction
from grimoire.institutions import Institution, InstitutionState, MemberPreference, InstitutionError, decide

C = 'c-realm'
GUILD = InstanceRef(C, 'group', 'guild')
CHAPTER = InstanceRef(C, 'group', 'chapter')
RIVAL = InstanceRef(C, 'group', 'rival')
LEADER, MEMBER, DISTANT, PC = [InstanceRef(C, 'character', x) for x in ('leader', 'member', 'distant', 'pc')]


def setup():
    roster = CampaignRoster(C, tuple(EntityInstance(r, local_name=r.instance_id,
        added=FacetBundle((Agent(),)) if r.kind == 'character' else FacetBundle())
        for r in (GUILD, CHAPTER, RIVAL, LEADER, MEMBER, DISTANT, PC)))
    edges = tuple(Relation(RelationRef(C, f'm{i}'), 'member_of', a, b, Moment(0), 'authored', 'now')
        for i, (a, b) in enumerate(((LEADER, GUILD), (MEMBER, GUILD), (CHAPTER, GUILD), (DISTANT, CHAPTER))))
    controls = tuple(ControlAssignment(C, r.instance_id, 'player' if r == PC else DIRECTOR)
                     for r in (LEADER, MEMBER, DISTANT, PC))
    state = InstitutionState(Institution(GUILD, LEADER, ('Preserve the guild monopoly',), 'Leader decides'))
    return state, dict(roster=roster, definitions=DefinitionIndex((), ()), relations=edges,
                      controls=controls, authored_by=DIRECTOR)


def choose(state=None, context=None, **kw):
    initial, defaults = setup()
    args = dict(preferences=(MemberPreference(MEMBER, 'deal', 'assist', 'personal choice'),),
                proposal_id='deal', choice='reject', operation_id='op1', expected_revision=0,
                moment=Moment(2), authorization_moment=Moment(2), audience=(PC,), channel='speech', provenance='reviewed choice')
    args.update(kw)
    return decide(state or initial, **(context or defaults), **args)


def test_guild_dissent_does_not_teach_distant_chapter():
    initial, context = setup()
    state, decision = choose(initial, context)
    assert decision.choice == 'reject' and decision.dissent[0].choice == 'assist'
    assert state.revision == 1 and initial.decisions == ()
    rival = Institution(RIVAL, LEADER, ('Open trade to everyone',), 'Leader decides')
    assert rival.goals != state.institution.goals
    # Acceptance and delivery are separate existing contracts, not side effects of decide.
    rejection = Event(C, 'rejection', 'The guild rejects the deal', Moment(2), 'accepted leader choice')
    assistance = Event(C, 'assistance', 'The member secretly assists the PC', Moment(3), 'accepted member action')
    proposition = Proposition(C, 'rejected', 'The guild rejected the deal', Moment(2), 'public announcement')
    knowledge = Knowledge(C, events=(rejection, assistance), propositions=(proposition,), evidence=(
        Report(C, 'heard', PC, 'rejected', 'speech', Moment(2), 'direct receipt', True,
               sender=LEADER, attributed_to=LEADER),
        Perception(C, 'helped', PC, 'assistance', 'sight', 'The member hands me the key', Moment(3), 'witness', True)))
    assert len(actor_knowledge(knowledge, PC, as_of=3).evidence) == 2
    assert actor_knowledge(knowledge, DISTANT, as_of=3).evidence == ()
    assert actor_knowledge(knowledge, LEADER, as_of=3).evidence == ()
    assert choose(state, context)[0] == state  # exact retry with original revision
    assert knowledge.events == tuple(sorted((rejection, assistance), key=lambda e: e.event_id))


def test_retry_keeps_complete_request_and_rechecks_control():
    state, context = setup()
    agreed = MemberPreference(LEADER, 'deal', 'reject', 'first reason')
    current, _ = choose(state, context, preferences=(agreed,))
    for patch in ({'preferences': (replace(agreed, provenance='different reason'),)}, {'audience': (MEMBER,)}):
        with pytest.raises(InstitutionError, match='operation_conflict'):
            choose(current, context, preferences=(agreed,), **patch) if 'preferences' not in patch else choose(current, context, **patch)
    revoked = dict(context, controls=tuple(replace(c, controller_id='player') if c.actor_id == 'leader' else c for c in context['controls']))
    with pytest.raises(InstitutionError, match='npc_authority'):
        choose(current, revoked, preferences=(agreed,))


@pytest.mark.parametrize('patch, reason', [
    ({'expected_revision': 1}, 'stale_revision'),
    ({'authored_by': 'player'}, 'npc_authority'),
    ({'preferences': (MemberPreference(PC, 'deal', 'assist', 'invented'),)}, 'npc_authority'),
    ({'preferences': (MemberPreference(MEMBER, 'other', 'assist', 'choice'),)}, 'proposal_mismatch'),
    ({'audience': (InstanceRef('c-other', 'character', 'pc'),)}, 'identity'),
])
def test_invalid_decisions(patch, reason):
    state, context = setup()
    if 'authored_by' in patch:
        context['authored_by'] = patch['authored_by']; patch = {}
    with pytest.raises(InstitutionError, match=reason):
        choose(state, context, **patch)


def test_membership_time_duplicates_and_identity():
    state, context = setup()
    for edges, reason in ((context['relations'][1:], 'not_member'),
                         (context['relations'] + (context['relations'][0],), 'duplicate_relation'),
                         ((replace(context['relations'][0], ending=Ending(Moment(1), 'left', 'now')),)
                          + context['relations'][1:], 'not_member')):
        with pytest.raises(InstitutionError, match=reason):
            choose(state, dict(context, relations=edges))
    with pytest.raises(InstitutionError, match='identity'):
        choose(replace(state, institution=replace(state.institution, leader=InstanceRef(C, 'item', 'leader'))), context)


def test_chapter_membership_and_immutable_inputs():
    state, context = setup()
    prefs = [MemberPreference(DISTANT, 'deal', 'accept', 'chapter preference')]
    audience = [PC]
    next_state, decision = choose(state, context, preferences=prefs, audience=audience)
    prefs.clear(); audience.clear()
    assert len(decision.dissent) == 1 and decision.audience == (PC,)
    assert next_state.decisions == (decision,)


def test_limits_refuse_overflow_and_ambiguous_preferences():
    state, context = setup()
    with pytest.raises(InstitutionError, match='limit'):
        choose(state, dict(context, relations=[context['relations'][0]] * 257))
    p = MemberPreference(MEMBER, 'deal', 'assist', 'choice')
    with pytest.raises(InstitutionError, match='duplicate_preference'):
        choose(state, context, preferences=(p, p))
    with pytest.raises(InstitutionError, match='limit'):
        choose(state, context, preferences=[p] * 65)


def test_second_group_can_choose_conflicting_policy_and_leadership_changes_recheck():
    original, context = setup()
    rival = InstitutionState(Institution(RIVAL, MEMBER, ('Open trade to everyone',), 'Leader decides'))
    link = Relation(RelationRef(C, 'rival-member'), 'member_of', MEMBER, RIVAL, Moment(0), 'authored', 'now')
    rival_context = dict(context, relations=context['relations'] + (link,))
    rival_result, rival_choice = choose(rival, rival_context, preferences=(), choice='accept')
    guild_result, guild_choice = choose(original, context)
    assert (guild_choice.choice, rival_choice.choice) == ('reject', 'accept')
    assert guild_result.institution.group != rival_result.institution.group
    changed = replace(guild_result, institution=replace(original.institution, leader=DISTANT))
    with pytest.raises(InstitutionError, match='operation_conflict'):
        choose(changed, context)
    next_state, choice = choose(changed, context, operation_id='op2', expected_revision=1)
    assert choice.request.institution.leader == DISTANT and next_state.revision == 2


def test_duplicate_membership_with_distinct_ids_and_input_budget():
    state, context = setup()
    duplicate = replace(context['relations'][0], ref=RelationRef(C, 'extra'))
    with pytest.raises(InstitutionError, match='duplicate_membership'):
        choose(state, dict(context, relations=context['relations'] + (duplicate,)))
    for i in range(64):
        state, _ = choose(state, context, operation_id=f'op{i}', expected_revision=i)
    with pytest.raises(InstitutionError, match='limit'):
        choose(state, context, operation_id='overflow', expected_revision=64)
    assert state.revision == 64


def test_no_implicit_membership_through_more_than_one_chapter():
    state, context = setup()
    edges = tuple(r for r in context['relations'] if r.subject != MEMBER)
    edges += (Relation(RelationRef(C, 'subchapter'), 'member_of', RIVAL, CHAPTER, Moment(0), 'authored', 'now'),
              Relation(RelationRef(C, 'nested-member'), 'member_of', MEMBER, RIVAL, Moment(0), 'authored', 'now'))
    with pytest.raises(InstitutionError, match='not_member'):
        choose(state, dict(context, relations=edges))


@pytest.mark.parametrize('patch', [
    {'expected_revision': True}, {'expected_revision': -1}, {'moment': 2},
    {'preferences': 'private text'}, {'preferences': (object(),)},
    {'audience': 'private text'}, {'audience': (PC, PC)},
    {'choice': ''}, {'choice': 'secret\ntext'}, {'operation_id': 'Secret Name'},
])
def test_malformed_request_is_refused_without_private_text(patch):
    with pytest.raises(InstitutionError) as caught:
        choose(**patch)
    assert 'private' not in str(caught.value) and 'secret' not in str(caught.value)


def test_future_member_wrong_campaign_and_nonagent_fail_closed():
    state, context = setup()
    future = tuple(replace(r, since=Moment(4)) if r.subject == LEADER else r for r in context['relations'])
    with pytest.raises(InstitutionError, match='not_member'):
        choose(state, dict(context, relations=future))
    with pytest.raises(InstitutionError, match='identity'):
        choose(state, dict(context, roster=CampaignRoster('c-other', ())))
    nonagent = CampaignRoster(C, tuple(replace(i, added=FacetBundle()) if i.ref == MEMBER else i
                                     for i in context['roster'].instances))
    with pytest.raises(InstitutionError, match='not_agent'):
        choose(state, dict(context, roster=nonagent))


def test_retry_refuses_removed_member_and_ambiguous_control():
    state, context = setup()
    current, _ = choose(state, context)
    removed = tuple(r for r in context['relations'] if r.subject != MEMBER)
    with pytest.raises(InstitutionError, match='not_member'):
        choose(current, dict(context, relations=removed))
    ambiguous = context['controls'] + (ControlAssignment(C, LEADER.instance_id, 'player'),)
    with pytest.raises(InstitutionError, match='npc_authority'):
        choose(current, dict(context, controls=ambiguous))


def test_retry_authorizes_membership_now_without_changing_request_identity():
    state, context = setup()
    current, decision = choose(state, context)
    assert choose(current, context, authorization_moment=Moment(4)) == (current, decision)
    for actor in (LEADER, MEMBER):
        for change in ({'ending': Ending(Moment(3), 'left', 'now')},
                       {'retraction': Retraction('never belonged', 'now')}):
            relations = tuple(replace(r, **change) if r.subject == actor else r
                              for r in context['relations'])
            with pytest.raises(InstitutionError, match='not_member'):
                choose(current, dict(context, relations=relations), authorization_moment=Moment(4))


@pytest.mark.parametrize('authorization_moment', [Moment(1), 4, None])
def test_authorization_time_cannot_precede_request_or_be_malformed(authorization_moment):
    with pytest.raises(InstitutionError, match='authorization_moment'):
        choose(authorization_moment=authorization_moment)


def test_current_membership_cannot_authorize_a_backdated_decision():
    state, context = setup()
    relations = tuple(replace(r, since=Moment(3)) if r.subject == LEADER else r
                      for r in context['relations'])
    with pytest.raises(InstitutionError, match='not_member'):
        choose(state, dict(context, relations=relations), authorization_moment=Moment(4))
