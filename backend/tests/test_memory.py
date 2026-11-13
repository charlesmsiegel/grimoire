from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Moment
from grimoire.core.perspective import ActorKnowledge, KnownEvidence
from grimoire.memory import MemorySelection, MemoryCorrection, consolidate_view


def test_stale_selection_does_not_revive_corrected_evidence():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = tuple(KnownEvidence(i, 'perception', 'sight', Moment(n), i) for n, i in enumerate(('old', 'competing', 'new'), 1))
    knowledge = ActorKnowledge(actor, 10, evidence, (), (), ())
    selections = (MemorySelection('one', actor, 'recap', ('old', 'competing'), Moment(3)),
                  MemorySelection('two', actor, 'recap', ('old',), Moment(5)))
    correction = MemoryCorrection('fix', actor, ('old',), ('new',), Moment(4), 'authored')
    result = consolidate_view(knowledge, 'recap', selections, (correction,))
    assert {item.evidence.evidence_id for item in result.current} == {'competing', 'new'}
    assert result.history[0].evidence.evidence_id == 'old'
    assert result.history[0].replacement_ids == ('new',)


def test_historical_view_and_chain_keep_source_links():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = tuple(KnownEvidence(i, 'report', 'letter', Moment(n, f'scene-{n}'), i) for n, i in enumerate(('old', 'middle', 'new'), 1))
    selection = MemorySelection('one', actor, 'recap', ('old',), Moment(3))
    corrections = (MemoryCorrection('fix', actor, ('old',), ('middle',), Moment(4), 'private'),
                   MemoryCorrection('fix-two', actor, ('middle',), ('new',), Moment(5), 'private'))
    before = consolidate_view(ActorKnowledge(actor, 3, evidence, (), (), ()), 'recap', (selection,), corrections)
    after = consolidate_view(ActorKnowledge(actor, 5, evidence, (), (), ()), 'recap', (selection,), corrections)
    assert [i.evidence.evidence_id for i in before.current] == ['old']
    assert [i.evidence.evidence_id for i in after.current] == ['new']
    assert [i.evidence.evidence_id for i in after.history] == ['old', 'middle']
    assert after.history[0].selection_ids == ('one',)
    assert after.history[0].correction_id == 'fix'


def test_unavailable_replacement_never_revives_old_and_limit_is_bounded():
    actor = InstanceRef('camp', 'character', 'alice')
    knowledge = ActorKnowledge(actor, 4, (KnownEvidence('old', 'perception', 'sight', Moment(1), 'old'),), (), (), ())
    selection = MemorySelection('one', actor, 'recap', ('old',), Moment(2))
    correction = MemoryCorrection('fix', actor, ('old',), ('missing',), Moment(3), 'authored')
    view = consolidate_view(knowledge, 'recap', (selection,), (correction,), limit=1)
    assert view.current == () and view.history[0].evidence.evidence_id == 'old'
    assert view.omitted == 1
