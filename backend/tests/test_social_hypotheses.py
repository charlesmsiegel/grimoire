"""Synthetic social conjectures must never become director truth or PC interiority."""
from dataclasses import replace

import pytest

from grimoire.core.authority import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.facets import Agent, FacetBundle
from grimoire.core.identity import InstanceRef
from grimoire.core.index import DefinitionIndex
from grimoire.core.knowledge import Belief, Event, Knowledge, Moment, Perception, Proposition, Ruling, Subject
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import actor_knowledge, director_knowledge
from grimoire.core.roster import CampaignRoster
from grimoire.social_hypotheses import Hypothesis, HypothesisError, HypothesisLedger, admit, choose_tactic, recall

C = 'social-test'
SPY, MAYOR, PC = (InstanceRef(C, 'character', x) for x in ('spy', 'mayor', 'pc'))
ROSTER = CampaignRoster(C, tuple(EntityInstance(x, local_name=x.instance_id, added=FacetBundle((Agent(),)))
                                for x in (SPY, MAYOR, PC)))
DEFINITIONS = DefinitionIndex((), ())
CONTROLS = tuple(ControlAssignment(C, x.instance_id, 'player' if x == PC else DIRECTOR)
                 for x in (SPY, MAYOR, PC))


def knowledge():
    event = Event(C, 'gesture', 'The mayor glanced at the sealed letter.', Moment(1), 'synthetic')
    correction = Event(C, 'correction', 'The mayor asked who had opened the letter.', Moment(3), 'synthetic')
    prop = Proposition(C, 'pc-knows', 'The PC knows the secret.', Moment(1), 'synthetic', (Subject('event', 'gesture'),))
    return Knowledge(C, events=(event, correction), propositions=(prop,),
                     rulings=(Ruling(C, 'truth', 'pc-knows', 'refuted', Moment(1), 'director'),),
                     evidence=(Perception(C, 'spy-clue', SPY, 'gesture', 'sight', 'The mayor glanced at the letter.', Moment(1), 'synthetic', True),
                               Perception(C, 'mayor-clue', MAYOR, 'gesture', 'sight', 'The letter is still sealed.', Moment(1), 'synthetic', True),
                               Perception(C, 'new-clue', SPY, 'correction', 'hearing', 'Who opened the letter?', Moment(3), 'synthetic', True)),
                     beliefs=(Belief(C, 'mayor-belief', MAYOR, 'pc-knows', 'rejected', Moment(2), DIRECTOR, ('mayor-clue',)),))


def hypothesis(**changes):
    return replace(Hypothesis('h1', SPY, (MAYOR, PC), 'The mayor thinks the PC knows the secret.',
                              .8, ('spy-clue',), Moment(2), 'director conjecture'), **changes)


def put(ledger, item, **changes):
    args = dict(knowledge=knowledge(), roster=ROSTER, definitions=DEFINITIONS, controls=CONTROLS,
                authored_by=DIRECTOR, expected_revision=ledger.revision)
    args.update(changes)
    return admit(ledger, item, **args)


def tactic(ledger, ident='h1', **changes):
    args = dict(as_of=4, minimum_confidence=.5, credited_tactic='Bluff that the PC already confessed.',
                fallback_tactic='Ask an open question.')
    args.update(changes)
    return choose_tactic(ledger, SPY, ident, **args)


def test_spy_mistakes_mayors_belief_then_revises_tactics_without_changing_truth():
    facts = knowledge()
    director_before = director_knowledge(facts, as_of=4, requested_by=DIRECTOR)
    mayor_before = actor_knowledge(facts, MAYOR, as_of=4)
    first = put(HypothesisLedger(C), hypothesis(), knowledge=facts)
    assert tactic(first).tactic == 'Bluff that the PC already confessed.'
    assert tactic(first).hypothesis_id == 'h1'
    second = put(first, hypothesis(hypothesis_id='h2', confidence=.1, evidence_ids=('new-clue',),
                                   moment=Moment(4), supersedes='h1'), knowledge=facts)
    assert tactic(second, 'h2').tactic == 'Ask an open question.'
    assert recall(second, SPY, as_of=2) == (hypothesis(),)
    assert [h.hypothesis_id for h in recall(second, SPY, as_of=4)] == ['h2']
    assert recall(second, MAYOR, as_of=4) == ()
    assert recall(second, PC, as_of=4) == ()
    assert second.records[0] == first.records[0]
    assert director_knowledge(facts, as_of=4, requested_by=DIRECTOR) == director_before
    assert actor_knowledge(facts, MAYOR, as_of=4) == mayor_before
    assert mayor_before.current[0].stance == 'rejected'
    assert facts.rulings[0].standing == 'refuted'


def test_retry_checks_original_revision_and_current_control():
    h = hypothesis()
    ledger = put(HypothesisLedger(C), h)
    assert put(ledger, h, expected_revision=0) is ledger
    with pytest.raises(HypothesisError):
        put(ledger, replace(h, confidence=.9), expected_revision=0)
    with pytest.raises(HypothesisError):
        put(ledger, h, expected_revision=1)
    pc_controls = tuple(replace(c, controller_id='player') if c.actor_id == 'spy' else c for c in CONTROLS)
    with pytest.raises(HypothesisError):
        put(ledger, h, expected_revision=0, controls=pc_controls)


@pytest.mark.parametrize('changes', [
    {'evidence_ids': ()}, {'evidence_ids': ('spy-clue', 'spy-clue')},
    {'evidence_ids': tuple('e'+str(i) for i in range(9))},
    {'confidence': float('nan')}, {'confidence': float('inf')}, {'confidence': True},
    {'confidence': -0.1}, {'confidence': 1.1}, {'confidence': 10**1000},
    {'inferred_actors': ()}, {'inferred_actors': (MAYOR, PC, SPY)},
    {'claim': ''}, {'claim': 'a'*401}, {'provenance': 'a'*401},
])
def test_invalid_hypothesis_shape(changes):
    with pytest.raises(HypothesisError):
        hypothesis(**changes)


@pytest.mark.parametrize('changes', [
    {'evidence_ids': ('mayor-clue',)}, {'evidence_ids': ('new-clue',)}, {'evidence_ids': ('missing',)},
    {'owner': PC}, {'owner': InstanceRef('elsewhere', 'character', 'spy')},
    {'inferred_actors': (InstanceRef(C, 'character', 'missing'),)},
    {'inferred_actors': (InstanceRef(C, 'creature', 'mayor'),)},
])
def test_bad_evidence_identity_and_authority_fail_closed(changes):
    with pytest.raises(HypothesisError):
        put(HypothesisLedger(C), hypothesis(**changes))


def test_unconfirmed_evidence_and_wrong_author_refused():
    facts = knowledge()
    facts = replace(facts, evidence=tuple(replace(e, confirmed=False) if e.evidence_id == 'spy-clue' else e for e in facts.evidence))
    with pytest.raises(HypothesisError):
        put(HypothesisLedger(C), hypothesis(), knowledge=facts)
    with pytest.raises(HypothesisError):
        put(HypothesisLedger(C), hypothesis(), authored_by='player')


def test_supersession_requires_current_same_chain_and_later_moment():
    first = put(HypothesisLedger(C), hypothesis())
    h2 = hypothesis(hypothesis_id='h2', moment=Moment(4), supersedes='h1')
    for bad in (replace(h2, moment=Moment(2)), replace(h2, inferred_actors=(MAYOR,)), replace(h2, supersedes='missing')):
        with pytest.raises(HypothesisError):
            put(first, bad)
    second = put(first, h2)
    with pytest.raises(HypothesisError):
        put(second, replace(h2, hypothesis_id='h3', moment=Moment(5)))
    with pytest.raises(HypothesisError):
        tactic(second, 'h1')
    with pytest.raises(HypothesisError):
        put(first, h2, expected_revision=0)


def test_first_order_bounds_and_mutable_input_are_defended():
    actors, evidence = [MAYOR], ['spy-clue']
    h = hypothesis(inferred_actors=actors, evidence_ids=evidence)
    actors.clear(); evidence.clear()
    assert h.inferred_actors == (MAYOR,) and h.evidence_ids == ('spy-clue',)
    ledger = HypothesisLedger(C)
    for i in range(64):
        ledger = put(ledger, replace(h, hypothesis_id='h'+str(i)))
    assert len(recall(ledger, SPY, as_of=4)) == 8
    with pytest.raises(HypothesisError):
        put(ledger, replace(h, hypothesis_id='overflow'))
    for limit in (0, 9, True):
        with pytest.raises(HypothesisError):
            recall(ledger, SPY, as_of=4, limit=limit)
    with pytest.raises(HypothesisError):
        tactic(ledger, minimum_confidence=float('nan'))


def test_retry_still_checks_current_roster_and_evidence():
    h = hypothesis()
    ledger = put(HypothesisLedger(C), h)
    missing = CampaignRoster(C, tuple(x for x in ROSTER.instances if x.ref != MAYOR))
    with pytest.raises(HypothesisError):
        put(ledger, h, expected_revision=0, roster=missing)
    facts = knowledge()
    changed = replace(facts, evidence=tuple(replace(e, confirmed=False) if e.evidence_id == 'spy-clue' else e for e in facts.evidence))
    with pytest.raises(HypothesisError):
        put(ledger, h, expected_revision=0, knowledge=changed)


def test_projection_does_not_reveal_future_hypotheses_and_tactics_reject_foreign_owner():
    ledger = put(HypothesisLedger(C), hypothesis())
    assert recall(ledger, SPY, as_of=1) == ()
    with pytest.raises(HypothesisError):
        choose_tactic(ledger, MAYOR, 'h1', as_of=4, minimum_confidence=.5,
                      credited_tactic='Bluff.', fallback_tactic='Wait.')
    with pytest.raises(HypothesisError):
        recall(ledger, SPY, as_of=True)


def test_non_agent_target_and_ambiguous_control_refused():
    inert = replace(ROSTER.instance(MAYOR), added=FacetBundle())
    roster = CampaignRoster(C, tuple(inert if x.ref == MAYOR else x for x in ROSTER.instances))
    with pytest.raises(HypothesisError):
        put(HypothesisLedger(C), hypothesis(), roster=roster)
    with pytest.raises(HypothesisError):
        put(HypothesisLedger(C), hypothesis(), controls=(*CONTROLS, ControlAssignment(C, 'spy', 'player')))
