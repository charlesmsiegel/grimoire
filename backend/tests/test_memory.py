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
    assert view.current == () and view.unavailable == ('missing',) and view.history == ()
    assert view.omitted == 1


def test_many_scenes_and_long_chain_are_bounded_without_recursion():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = tuple(KnownEvidence(f'e{i}', 'perception', 'sight', Moment(i), f'excerpt {i}') for i in range(1201))
    knowledge = ActorKnowledge(actor, 2500, evidence, (), (), ())
    selections = tuple(MemorySelection(f's{i}', actor, 'recap', tuple(f'e{j}' for j in range(i, min(i + 500, 1201))), Moment(1201)) for i in (0, 500, 1000))
    corrections = tuple(MemoryCorrection(f'c{i}', actor, (f'e{i}',), (f'e{i+1}',), Moment(1202 + i), 'authored') for i in range(1100))
    result = consolidate_view(knowledge, 'recap', selections, corrections, limit=1000)
    assert len(result.current) + len(result.history) + len(result.unavailable) == 1000
    assert result.omitted == 201
    assert any(item.evidence.evidence_id == 'e1100' for item in result.current)


def test_missing_replacement_is_unavailable_without_reviving_old():
    actor = InstanceRef('camp', 'character', 'alice')
    knowledge = ActorKnowledge(actor, 4, (KnownEvidence('old', 'perception', 'sight', Moment(1), 'old'),), (), (), ())
    selection = MemorySelection('one', actor, 'recap', ('old',), Moment(2))
    correction = MemoryCorrection('fix', actor, ('old',), ('missing',), Moment(3), 'authored')
    view = consolidate_view(knowledge, 'recap', (selection,), (correction,))
    assert view.current == () and view.unavailable == ('missing',)
    assert view.history[0].replacement_ids == ('missing',)


def test_non_character_actor_memory_uses_qualified_identity():
    actor = InstanceRef('camp', 'item', 'lantern')
    knowledge = ActorKnowledge(actor, 2, (KnownEvidence('seen', 'perception', 'sight', Moment(1), 'a visitor'),), (), (), ())
    selection = MemorySelection('sel', actor, 'recap', ('seen',), Moment(2))
    assert consolidate_view(knowledge, 'recap', (selection,), ()).current[0].evidence.content == 'a visitor'


def test_limit_prioritizes_terminal_replacement_over_history():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = (KnownEvidence('old', 'perception', 'sight', Moment(1), 'old'), KnownEvidence('new', 'perception', 'sight', Moment(2), 'new'))
    selection = MemorySelection('sel', actor, 'recap', ('old',), Moment(2))
    correction = MemoryCorrection('fix', actor, ('old',), ('new',), Moment(3), 'authored')
    view = consolidate_view(ActorKnowledge(actor, 3, evidence, (), (), ()), 'recap', (selection,), (correction,), limit=1)
    assert [item.evidence.evidence_id for item in view.current] == ['new']
    assert view.history == () and view.omitted == 1


def test_bounded_terminal_keeps_editorial_origins():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = tuple(KnownEvidence(i, 'perception', 'sight', Moment(n), i) for n, i in enumerate(('old', 'new'), 1))
    selection = MemorySelection('sel', actor, 'recap', ('old',), Moment(2))
    correction = MemoryCorrection('fix', actor, ('old',), ('new',), Moment(3), 'private')
    view = consolidate_view(ActorKnowledge(actor, 3, evidence, (), (), ()), 'recap', (selection,), (correction,), limit=1)
    assert view.current[0].selection_ids == ('sel',)
    assert view.current[0].correction_ids == ('fix',)
    assert 'private' not in repr(view)


def test_terminal_unions_converging_multihop_origins():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = tuple(KnownEvidence(i, 'perception', 'sight', Moment(n), i) for n, i in enumerate(('a', 'b', 'c', 'd'), 1))
    selections = (MemorySelection('one', actor, 'recap', ('a',), Moment(4)), MemorySelection('two', actor, 'recap', ('b',), Moment(4)))
    corrections = (MemoryCorrection('first', actor, ('a',), ('c',), Moment(5), 'private'), MemoryCorrection('second', actor, ('b',), ('c',), Moment(6), 'private'), MemoryCorrection('last', actor, ('c',), ('d',), Moment(7), 'private'))
    item = consolidate_view(ActorKnowledge(actor, 7, evidence, (), (), ()), 'recap', selections, corrections, limit=1).current[0]
    assert item.selection_ids == ('one', 'two')
    assert item.correction_ids == ('first', 'last', 'second')


def test_unavailable_terminal_keeps_editorial_origins_at_limit_one():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = (KnownEvidence('old', 'perception', 'sight', Moment(1), 'old'),)
    selection = MemorySelection('sel', actor, 'recap', ('old',), Moment(2))
    correction = MemoryCorrection('fix', actor, ('old',), ('missing',), Moment(3), 'private')
    view = consolidate_view(ActorKnowledge(actor, 3, evidence, (), (), ()), 'recap', (selection,), (correction,), limit=1)
    assert view.unavailable == ('missing',)
    item = view.unavailable_details[0]
    assert (item.evidence_id, item.selection_ids, item.correction_ids) == ('missing', ('sel',), ('fix',))
    assert 'private' not in repr(view)


def test_unavailable_terminal_unions_converging_paths():
    actor = InstanceRef('camp', 'character', 'alice')
    evidence = tuple(KnownEvidence(i, 'perception', 'sight', Moment(n), i) for n, i in enumerate(('a', 'b', 'c'), 1))
    selections = (MemorySelection('one', actor, 'recap', ('a',), Moment(3)), MemorySelection('two', actor, 'recap', ('b',), Moment(3)))
    corrections = (MemoryCorrection('first', actor, ('a',), ('c',), Moment(4), 'private'), MemoryCorrection('second', actor, ('b',), ('c',), Moment(5), 'private'), MemoryCorrection('last', actor, ('c',), ('missing',), Moment(6), 'private'))
    view = consolidate_view(ActorKnowledge(actor, 6, evidence, (), (), ()), 'recap', selections, corrections, limit=1)
    item = view.unavailable_details[0]
    assert item.evidence_id == 'missing'
    assert item.selection_ids == ('one', 'two')
    assert item.correction_ids == ('first', 'last', 'second')
