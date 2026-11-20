"""078: an insulted envoy resents the newcomer; absent guild members do not, until told."""
from dataclasses import replace

import pytest

from grimoire.core.authority import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.facets import Agent, FacetBundle
from grimoire.core.identity import InstanceRef
from grimoire.core.index import DefinitionIndex
from grimoire.core.knowledge import (Belief, Event, Knowledge, Moment, Perception, Proposition, Report,
                                     Subject)
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import actor_knowledge, director_knowledge
from grimoire.core.roster import CampaignRoster
from grimoire.relationships import (MAX_RECORDS, Attitude, AttitudeLedger, Disposition, RelationshipError,
                                    admit, attitudes_of, dispositions, history_of)

C = 'guild-test'
ENVOY, TOLD, AWAY, DOCKHAND, PC = (InstanceRef(C, 'character', x)
                                    for x in ('envoy', 'told', 'away', 'dockhand', 'pc'))
GUILD = InstanceRef(C, 'group', 'guild')
CRATE = InstanceRef(C, 'item', 'crate')
AGENTS = (ENVOY, TOLD, AWAY, DOCKHAND, PC)
ROSTER = CampaignRoster(C, (*(EntityInstance(x, local_name=x.instance_id, added=FacetBundle((Agent(),)))
                              for x in AGENTS),
                            EntityInstance(GUILD, local_name='guild'),
                            EntityInstance(CRATE, local_name='crate')))
DEFINITIONS = DefinitionIndex((), ())
CONTROLS = tuple(ControlAssignment(C, x.instance_id, 'player' if x == PC else DIRECTOR) for x in AGENTS)


def about(*refs):
    return tuple(Subject('instance', str(r)) for r in refs)


def knowledge(*extra_evidence, beliefs=()):
    events = (Event(C, 'insult', 'The newcomer insulted the envoy on the quay.', Moment(1), 'scene:quay/t1',
                    (PC, ENVOY, DOCKHAND)),
              Event(C, 'rain', 'Rain fell on the upper town.', Moment(2), 'scene:town/t1'),
              Event(C, 'shove', 'A masked figure shoved the envoy.', Moment(2), 'scene:quay/t2', (PC, ENVOY)))
    propositions = (
        Proposition(C, 'p1', 'Your guild are thieves and you are their lackey.', Moment(1), 'scene:quay/t1',
                    about(GUILD, ENVOY)),
        Proposition(C, 'p2', 'The newcomer insulted our guild on the quay.', Moment(3), 'scene:hall/t1',
                    about(PC, GUILD)),
        Proposition(C, 'p3', 'Watch yourself.', Moment(2), 'scene:quay/t2'),
        Proposition(C, 'p4', 'It was the newcomer who shoved me.', Moment(2), 'director',
                    (*about(PC), Subject('event', 'shove'))),
        Proposition(C, 'p5', 'I was wrong about your guild.', Moment(6), 'scene:hall/t2', about(GUILD)),
    )
    evidence = (
        Report(C, 'heard', ENVOY, 'p1', 'told', Moment(1), 'scene:quay/t1', True,
               sender=PC, attributed_to=PC, addressed=True, sent_at=Moment(1)),
        Perception(C, 'saw', ENVOY, 'insult', 'sight', 'A sneer and a finger jabbed at the badge.',
                   Moment(1), 'scene:quay/t1', True),
        Report(C, 'overheard', DOCKHAND, 'p1', 'overheard', Moment(1), 'scene:quay/t1', True,
               sender=PC, attributed_to=PC),
        Report(C, 'letter', TOLD, 'p2', 'written', Moment(5), 'scene:hall/t1', True,
               sender=ENVOY, attributed_to=ENVOY, addressed=True, sent_at=Moment(3)),
        Perception(C, 'wet', AWAY, 'rain', 'touch', 'Cold rain on the neck.', Moment(2), 'scene:town/t1', True),
        Report(C, 'warned', ENVOY, 'p3', 'told', Moment(2), 'scene:quay/t2', True, sender=PC),
        Perception(C, 'shoved', ENVOY, 'shove', 'touch', 'A hard shove from behind.', Moment(2),
                   'scene:quay/t2', True),
        *extra_evidence)
    return Knowledge(C, events=events, propositions=propositions, evidence=evidence, beliefs=beliefs)


def attitude(**changes):
    base = Attitude('a1', ENVOY, PC, 'resentment', 'Resents being called a lackey in front of the dockhands.',
                    Moment(1), 'scene:quay/t1', evidence_ids=('heard', 'saw'), degree=3)
    return replace(base, **changes)


def put(ledger, item, **changes):
    args = dict(knowledge=knowledge(), roster=ROSTER, definitions=DEFINITIONS, controls=CONTROLS,
                authored_by=DIRECTOR, expected_revision=ledger.revision)
    args.update(changes)
    return admit(ledger, item, **args)


def refused(reason, ledger, item, **changes):
    with pytest.raises(RelationshipError) as caught:
        put(ledger, item, **changes)
    assert caught.value.reason == reason


def test_envoy_resents_the_insult_and_absent_members_do_not_until_told():
    facts = knowledge()
    director_before = director_knowledge(facts, as_of=9, requested_by=DIRECTOR)
    views_before = {a: actor_knowledge(facts, a, as_of=9) for a in AGENTS}
    ledger = AttitudeLedger(C)
    ledger = put(ledger, attitude(), knowledge=facts)
    ledger = put(ledger, attitude(attitude_id='a2', dimension='trust', degree=-2,
                                  account='Will not take the newcomer at their word.'), knowledge=facts)
    # The same words, overheard, give an opposing perspective.
    ledger = put(ledger, attitude(attitude_id='a3', holder=DOCKHAND, dimension='affection', degree=1,
                                  account='Liked seeing the envoy taken down a peg.', evidence_ids=('overheard',)),
                 knowledge=facts)
    assert [(a.dimension, a.degree) for a in attitudes_of(ledger, ENVOY, as_of=9)] == [('trust', -2),
                                                                                         ('resentment', 3)]
    assert [a.dimension for a in attitudes_of(ledger, DOCKHAND, as_of=9)] == ['affection']
    # The members who were not there hold nothing: not about the PC, and not toward the guild's enemy.
    for absent in (TOLD, AWAY):
        assert attitudes_of(ledger, absent, as_of=9) == ()
        refused('unavailable_grounds', ledger, attitude(attitude_id='x', holder=absent), knowledge=facts)
    refused('unrelated_grounds', ledger, attitude(attitude_id='x', holder=AWAY, evidence_ids=('wet',),
                                                       moment=Moment(2)),
            knowledge=facts)
    # The told member can resent the PC only once the envoy's letter has arrived (moment 5), and only
    # because of the letter; the never-told member still cannot.
    told = attitude(attitude_id='a4', holder=TOLD, evidence_ids=('letter',), degree=1,
                    account='Takes the envoy\'s side, from the letter alone.')
    refused('unavailable_grounds', ledger, replace(told, moment=Moment(4)), knowledge=facts)
    ledger = put(ledger, replace(told, moment=Moment(5)), knowledge=facts)
    assert [a.attitude_id for a in attitudes_of(ledger, TOLD, as_of=9)] == ['a4']
    assert attitudes_of(ledger, TOLD, as_of=4) == ()
    assert attitudes_of(ledger, AWAY, as_of=9) == ()
    # Asymmetry: nobody's attitude toward the envoy, and the PC has none written for them.
    assert attitudes_of(ledger, PC, as_of=9) == ()
    assert all(a.toward == PC for a in ledger.records)
    # Nothing about knowledge or truth changed.
    assert director_knowledge(facts, as_of=9, requested_by=DIRECTOR) == director_before
    assert {a: actor_knowledge(facts, a, as_of=9) for a in AGENTS} == views_before


def test_perceptions_and_anonymous_reports_do_not_identify_the_target():
    ledger = AttitudeLedger(C)
    # A perception's only subject is its event: event participants never identify anyone to the holder.
    refused('unrelated_grounds', ledger, attitude(evidence_ids=('saw',)))
    refused('unrelated_grounds', ledger, attitude(evidence_ids=('shoved',), moment=Moment(2)))
    # The PC really sent "Watch yourself", but the envoy cannot tell who: no attributed source, no about.
    refused('unrelated_grounds', ledger, attitude(evidence_ids=('warned',), moment=Moment(2)))
    # Only the holder's own *confirmed* evidence counts.
    unconfirmed = knowledge()
    unconfirmed = replace(unconfirmed, evidence=tuple(replace(e, confirmed=False) if e.evidence_id == 'heard' else e
                                                      for e in unconfirmed.evidence))
    refused('unavailable_grounds', ledger, attitude(), knowledge=unconfirmed)
    refused('unavailable_grounds', ledger, attitude(evidence_ids=('heard',)), knowledge=unconfirmed)
    refused('unavailable_grounds', ledger, attitude(evidence_ids=('overheard',)))
    refused('unavailable_grounds', ledger, attitude(evidence_ids=('missing',)))


def test_a_belief_about_the_target_grounds_an_attitude_and_others_beliefs_do_not():
    belief = Belief(C, 'b1', ENVOY, 'p4', 'believed', Moment(2), DIRECTOR, ('shoved',))
    facts = knowledge(beliefs=(belief,))
    with_belief = attitude(attitude_id='f1', dimension='fear', degree=2, moment=Moment(2),
                           account='Keeps the newcomer at arm\'s length now.', evidence_ids=(), belief_ids=('b1',))
    ledger = put(AttitudeLedger(C), with_belief, knowledge=facts)
    assert attitudes_of(ledger, ENVOY, as_of=2)[0].belief_ids == ('b1',)
    refused('unavailable_grounds', AttitudeLedger(C), replace(with_belief, moment=Moment(1)), knowledge=facts)
    refused('unavailable_grounds', AttitudeLedger(C), replace(with_belief, holder=DOCKHAND), knowledge=facts)


def test_player_characters_and_non_director_authors_are_refused():
    ledger = AttitudeLedger(C)
    pc_view = attitude(holder=PC, toward=ENVOY)
    refused('pc_interiority', ledger, pc_view)
    refused('npc_authority_required', ledger, attitude(), authored_by='player')
    refused('npc_authority_required', ledger, attitude(), authored_by='narrator')
    refused('unknown_control', ledger, attitude(), controls=tuple(c for c in CONTROLS if c.actor_id != 'envoy'))
    refused('ambiguous_control', ledger, attitude(), controls=(*CONTROLS, ControlAssignment(C, 'envoy', 'player')))
    refused('invalid_control', ledger, attitude(), controls=('envoy',))


def test_targets_resolve_and_groups_are_allowed():
    ledger = put(AttitudeLedger(C), attitude(toward=GUILD, dimension='rivalry', degree=1,
                                             account='Wants to outdo the guild masters.'))
    assert attitudes_of(ledger, ENVOY, as_of=1, toward=GUILD)[0].dimension == 'rivalry'
    assert attitudes_of(ledger, ENVOY, as_of=1, toward=PC) == ()
    refused('not_agent_or_group', AttitudeLedger(C), attitude(toward=CRATE))
    refused('unresolved_actor', AttitudeLedger(C), attitude(toward=InstanceRef(C, 'character', 'missing')))
    refused('unresolved_actor', AttitudeLedger(C), attitude(toward=InstanceRef(C, 'creature', 'pc')))
    inert = CampaignRoster(C, tuple(replace(x, added=FacetBundle()) if x.ref == ENVOY else x
                                    for x in ROSTER.instances))
    refused('not_agent', AttitudeLedger(C), attitude(), roster=inert)
    refused('campaign_mismatch', AttitudeLedger(C), attitude(holder=InstanceRef('other', 'character', 'envoy'),
                                                             toward=InstanceRef('other', 'character', 'pc')))
    with pytest.raises(RelationshipError):
        admit(AttitudeLedger(C), attitude(), knowledge=None, roster=ROSTER, definitions=DEFINITIONS,
              controls=CONTROLS, authored_by=DIRECTOR, expected_revision=0)


def test_a_change_supersedes_the_current_head_and_history_is_kept():
    first = put(AttitudeLedger(C), attitude(attitude_id='t1', dimension='trust', degree=-2,
                                            account='Will not take the newcomer at their word.'))
    apology = Report(C, 'apology', ENVOY, 'p5', 'told', Moment(6), 'scene:hall/t2', True,
                     sender=PC, attributed_to=PC, addressed=True, sent_at=Moment(6))
    facts = knowledge(apology)
    later = attitude(attitude_id='t2', dimension='trust', degree=0, moment=Moment(6), evidence_ids=('apology',),
                     account='Thawed a little after the apology.', supersedes='t1')
    second = put(first, later, knowledge=facts)
    assert [a.attitude_id for a in attitudes_of(second, ENVOY, as_of=9)] == ['t2']
    assert [a.attitude_id for a in attitudes_of(second, ENVOY, as_of=5)] == ['t1']
    assert [a.attitude_id for a in history_of(second, ENVOY, PC, 'trust', as_of=9)] == ['t1', 't2']
    assert history_of(second, ENVOY, PC, 'fear', as_of=9) == ()
    for bad, reason in ((replace(later, supersedes=None), 'unsuperseded_conflict'),
                        (replace(later, supersedes='missing'), 'stale_supersession'),
                        (replace(later, moment=Moment(1), evidence_ids=('heard',)), 'nonmonotonic_revision'),
                        (replace(later, dimension='fear'), 'different_chain')):
        refused(reason, first, bad, knowledge=facts)
    refused('stale_supersession', second, replace(later, attitude_id='t3', moment=Moment(7)), knowledge=facts)
    with pytest.raises(RelationshipError) as caught:
        AttitudeLedger(C, (later,))
    assert caught.value.reason == 'stale_supersession'


def test_retry_is_idempotent_and_still_checks_the_present():
    item = attitude()
    ledger = put(AttitudeLedger(C), item)
    assert put(ledger, item, expected_revision=0) is ledger
    refused('retry_conflict', ledger, replace(item, degree=2), expected_revision=0)
    refused('retry_conflict', ledger, item, expected_revision=1)
    refused('stale_revision', ledger, attitude(attitude_id='a2', dimension='fear', degree=1), expected_revision=0)
    refused('pc_interiority', ledger, item, expected_revision=0,
            controls=tuple(replace(c, controller_id='player') if c.actor_id == 'envoy' else c for c in CONTROLS))
    gone = CampaignRoster(C, tuple(x for x in ROSTER.instances if x.ref != PC))
    refused('unresolved_actor', ledger, item, expected_revision=0, roster=gone)
    refused('invalid_sequence', ledger, item, expected_revision=True)


@pytest.mark.parametrize('changes,reason', [
    ({'evidence_ids': ()}, 'ungrounded_attitude'),
    ({'evidence_ids': ('heard', 'heard')}, 'duplicate_ground'),
    ({'evidence_ids': tuple(f'e{i}' for i in range(9))}, 'budget_exceeded'),
    ({'evidence_ids': tuple(f'e{i}' for i in range(5)), 'belief_ids': tuple(f'b{i}' for i in range(4))},
     'budget_exceeded'),
    ({'evidence_ids': 'heard'}, 'invalid_collection'),
    ({'evidence_ids': ('bad id',)}, 'invalid_id'),
    ({'degree': 4}, 'invalid_degree'), ({'degree': -1}, 'invalid_degree'), ({'degree': True}, 'invalid_degree'),
    ({'degree': 1.5}, 'invalid_degree'), ({'dimension': 'trust', 'degree': -4}, 'invalid_degree'),
    ({'dimension': 'hatred'}, 'unknown_dimension'), ({'dimension': None}, 'unknown_dimension'),
    ({'toward': ENVOY}, 'self_attitude'),
    ({'toward': InstanceRef('other', 'character', 'pc')}, 'campaign_mismatch'),
    ({'toward': 'campaign:guild-test/character/pc'}, 'invalid_actor'),
    ({'moment': 1}, 'invalid_moment'),
    ({'account': ' '}, 'invalid_text'), ({'account': 'a' * 401}, 'invalid_text'), ({'provenance': ''}, 'invalid_text'),
    ({'attitude_id': 'a/1'}, 'invalid_id'), ({'supersedes': ''}, 'invalid_id'),
])
def test_invalid_shapes(changes, reason):
    with pytest.raises(RelationshipError) as caught:
        attitude(**changes)
    assert caught.value.reason == reason


def test_degree_is_optional_and_trust_may_be_negative():
    assert attitude(degree=None).degree is None
    assert attitude(dimension='trust', degree=-3).degree == -3
    assert attitude(dimension='affection', degree=-3).degree == -3


def test_dispositions_hide_ids_grounds_and_provenance():
    ledger = put(AttitudeLedger(C), attitude())
    (view,) = dispositions(ledger, ENVOY, as_of=1)
    assert view == Disposition(PC, 'resentment', attitude().account, 3, Moment(1))
    assert not {'attitude_id', 'evidence_ids', 'belief_ids', 'provenance', 'holder'} & set(Disposition.__slots__)
    assert dispositions(ledger, TOLD, as_of=9) == ()


def test_projection_inputs_are_checked():
    ledger = AttitudeLedger(C)
    for call in (lambda: attitudes_of(ledger, ENVOY, as_of=-1), lambda: attitudes_of(ledger, ENVOY, as_of=True),
                 lambda: attitudes_of(ledger, InstanceRef('other', 'character', 'envoy'), as_of=1),
                 lambda: attitudes_of(ledger, 'envoy', as_of=1),
                 lambda: attitudes_of(ledger, ENVOY, as_of=1, toward='pc'),
                 lambda: history_of(ledger, ENVOY, PC, 'hatred', as_of=1)):
        with pytest.raises(RelationshipError):
            call()


def test_ledger_bounds_and_mutable_input_are_defended():
    grounds = ['heard']
    item = attitude(evidence_ids=grounds)
    grounds.clear()
    assert item.evidence_ids == ('heard',)
    targets = [InstanceRef(C, 'character', f'n{i}') for i in range(MAX_RECORDS + 1)]
    records = tuple(replace(item, attitude_id=f'r{i}', toward=t) for i, t in enumerate(targets))
    assert AttitudeLedger(C, records[:MAX_RECORDS]).revision == MAX_RECORDS
    with pytest.raises(RelationshipError) as caught:
        AttitudeLedger(C, records)
    assert caught.value.reason == 'budget_exceeded'
    for bad, reason in (((item, item), 'duplicate_id'), (('x',), 'invalid_record'),
                        ((replace(item, holder=InstanceRef('o', 'character', 'e'), toward=InstanceRef('o', 'character', 'p')),),
                         'campaign_mismatch')):
        with pytest.raises(RelationshipError) as caught:
            AttitudeLedger(C, bad)
        assert caught.value.reason == reason
