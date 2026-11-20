import pytest
from grimoire.rumors import CampaignTime as Day
from grimoire.rumors import Rumor, DeliveryConditions
from evidence_fixtures import DUKE, MARA, INES, TAVI, m
from grimoire.core.identity import InstanceRef


def test_campaign_time_and_route_validation():
    base = dict(communication_id='r1', sender=MARA, recipient=INES, source_evidence_id='v-mara', text='news', channel='told', attributed_to=MARA, confidence=80, distortion='none', provenance='authored', authored_at=m(8), sent_on=Day(10), due_on=Day(31), route=DeliveryConditions(True, True, True, True))
    assert Rumor(**base).due_on == Day(31)
    for key, value in [('sent_on', 10), ('due_on', Day(9)), ('due_on', None), ('confidence', 101)]:
        with pytest.raises(ValueError):
            Rumor(**(base | {key: value}))


# -- dispatches: a sender's past never changes when its delayed report arrives --------------------------

from grimoire.core import Dispatch, Knowledge, KnowledgeError, Proposition, Report
from grimoire.core.perspective import actor_knowledge, knowledge_about
from grimoire.core.knowledge import Subject

LETTER = Proposition('c1', 'letter', 'the duke has fled', m(2), 'authored', about=(Subject('instance', str(DUKE)),))
SENT = Dispatch('c1', 'letter-report', MARA, INES, 'letter', 'courier', m(3))
ARRIVED = Report('c1', 'letter-report', INES, 'letter', 'courier', m(9), 'authored', True, MARA, MARA, True, m(3))


def ledger(*records, dispatches=(SENT,)):
    return Knowledge('c1', propositions=[LETTER], evidence=list(records), dispatches=dispatches)


def test_a_dispatch_is_sent_from_its_send_moment_whether_or_not_it_arrived():
    before, after = ledger(), ledger(ARRIVED)
    for as_of in (2, 3, 8, 9, 20):
        assert actor_knowledge(before, MARA, as_of=as_of) == actor_knowledge(after, MARA, as_of=as_of)
    (sent,) = actor_knowledge(after, MARA, as_of=20).sent
    assert (sent.evidence_id, sent.recipient, sent.text, sent.sent_at) == ('letter-report', INES, 'the duke has fled', m(3))
    assert actor_knowledge(before, INES, as_of=20).evidence == ()  # the recipient still learns only on arrival
    assert [e.evidence_id for e in actor_knowledge(after, INES, as_of=20).evidence] == ['letter-report']
    # Without its dispatch the same report appears in the sender's past only once it is there: the leak.
    assert actor_knowledge(Knowledge('c1', propositions=[LETTER]), MARA, as_of=20).sent == ()
    assert len(actor_knowledge(Knowledge('c1', propositions=[LETTER], evidence=[ARRIVED]), MARA, as_of=20).sent) == 1


def test_unknown_dispatches_show_no_send_and_a_mismatched_arrival_is_refused():
    other = Report('c1', 'other', INES, 'letter', 'told', m(4), 'authored', True, MARA, MARA, True, m(4))
    assert actor_knowledge(ledger(other, dispatches=None), MARA, as_of=20).sent == ()  # fail closed
    assert [s.evidence_id for s in actor_knowledge(ledger(other), MARA, as_of=20).sent] == ['letter-report', 'other']
    for wrong in (Report('c1', 'letter-report', TAVI, 'letter', 'courier', m(9), 'authored', True, MARA, MARA, True, m(3)),
                  Report('c1', 'letter-report', INES, 'letter', 'courier', m(9), 'authored', True, MARA, MARA, True, m(2)),
                  Report('c1', 'letter-report', INES, 'letter', 'courier', m(9), 'authored', True)):
        with pytest.raises(KnowledgeError, match='dispatch_mismatch'):
            ledger(wrong)
    with pytest.raises(KnowledgeError, match='duplicate_id'):
        ledger(dispatches=(SENT, SENT))
    with pytest.raises(KnowledgeError, match='campaign_mismatch'):
        ledger(dispatches=(Dispatch('c2', 'x', InstanceRef('c2', 'character', 'a'), InstanceRef('c2', 'character', 'b'), 'letter', 'told', m(3)),))


def test_a_dispatch_without_its_proposition_or_subject_is_not_shown():
    assert actor_knowledge(Knowledge('c1', dispatches=(SENT,)), MARA, as_of=20).sent == ()  # quarantined proposition
    duke = Subject('instance', str(DUKE))
    assert len(knowledge_about(ledger(), MARA, duke, as_of=20).sent) == 1
    assert knowledge_about(ledger(), MARA, Subject('instance', str(INES)), as_of=20).sent == ()
    copied = __import__('pickle').loads(__import__('pickle').dumps(ledger(ARRIVED)))
    assert copied == ledger(ARRIVED) and copied.dispatches == (SENT,)
