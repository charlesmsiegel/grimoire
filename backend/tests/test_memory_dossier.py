"""053: several scenes consolidated into attributable accounts, and corrected identities that older recaps cannot undo."""
import itertools

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Moment
from grimoire.core.perspective import ActorKnowledge, KnownEvidence
from grimoire.memory import MemoryCorrection, MemorySelection, MemorySummary, consolidate_dossier

ACTOR = InstanceRef('camp', 'character', 'alice')
#: three scenes: the visitor names himself, the abbot is mentioned, the visitor is unmasked
EVIDENCE = (
    KnownEvidence('masked', 'perception', 'sight', Moment(1, 'chapel', 't1'), 'The hooded visitor says he is Brother Aldric.'),
    KnownEvidence('abbot', 'report', 'told', Moment(2, 'cloister', 't1'), 'Brother Aldric is the abbot', 'p-abbot'),
    KnownEvidence('unmasked', 'perception', 'sight', Moment(3, 'crypt', 't2'), 'The hood falls: it is Sister Maren.'),
    KnownEvidence('later', 'perception', 'sight', Moment(5, 'crypt', 't3'), 'Maren lights a candle for the abbot.'),
)
RECAP = MemorySummary('recap', ACTOR, 'play', 'visitor-identity', 'The hooded visitor is Brother Aldric, the abbot.',
                      ('masked', 'abbot'), Moment(2))
CORRECTION = MemoryCorrection('fix', ACTOR, ('masked',), ('unmasked',), Moment(4), 'the hood fell in the crypt')
CORRECTED = MemorySummary('corrected', ACTOR, 'play', 'visitor-identity', 'The hooded visitor was Sister Maren in disguise.',
                          ('unmasked',), Moment(4), supersedes='recap')
SELECTION = MemorySelection('picked', ACTOR, 'play', ('masked', 'abbot', 'unmasked'), Moment(4))


def dossier(*summaries, as_of=6, corrections=(CORRECTION,), selections=(SELECTION,), evidence=EVIDENCE, limit=100):
    knowledge = ActorKnowledge(ACTOR, as_of, evidence, (), (), ())
    return consolidate_dossier(knowledge, 'play', selections, corrections, summaries, editor='director', limit=limit)


def topic(result, name='visitor-identity'):
    return next(t for t in result.topics if t.topic == name)


def test_several_scenes_consolidate_into_one_attributable_account():
    result = dossier(RECAP, as_of=2, corrections=())
    [item] = topic(result).current
    assert item.status == 'current' and item.text == RECAP.text
    assert item.scenes == ('chapel', 'cloister') and item.basis == 2
    assert [(e.evidence_id, e.content) for e in item.evidence] == [
        ('masked', EVIDENCE[0].content), ('abbot', EVIDENCE[1].content)]


def test_a_corrected_identity_is_never_overwritten_by_an_older_recap():
    # filed after the correction, from the older scenes, and naming the corrected account to replace
    restated = MemorySummary('restated', ACTOR, 'play', 'visitor-identity', 'The hooded visitor is Brother Aldric.',
                             ('masked', 'abbot'), Moment(6), supersedes='corrected')
    # a later account resting only on uncorrected but older evidence
    older = MemorySummary('older', ACTOR, 'play', 'visitor-identity', 'The visitor must be the abbot.',
                          ('abbot',), Moment(6), supersedes='corrected')
    result = dossier(RECAP, CORRECTED, restated, older)
    entry = topic(result)
    assert [(i.operation_id, i.status) for i in entry.current] == [('corrected', 'current')]
    assert entry.current[0].evidence[0].content == 'The hood falls: it is Sister Maren.'
    history = {i.operation_id: i for i in entry.history}
    assert history['recap'].status == 'stale' and history['recap'].superseded_by == ('corrected',)
    assert history['recap'].obsolete_ids == ('masked',) and history['recap'].replacement_ids == ('unmasked',)
    assert history['restated'].status == 'stale' and history['restated'].replacement_ids == ('unmasked',)
    assert history['older'].status == 'older_basis' and history['older'].basis == 2
    assert result.suppressed == ('masked',)
    # the evidence view agrees: the obsolete percept is history, its replacement current
    assert {i.evidence.evidence_id for i in result.evidence.current} == {'abbot', 'unmasked'}


def test_before_the_correction_the_recap_stands_and_after_it_never_again():
    assert topic(dossier(RECAP, CORRECTED, as_of=3)).current[0].operation_id == 'recap'
    later = MemorySummary('later', ACTOR, 'play', 'visitor-identity', 'Sister Maren mourns the abbot.',
                          ('later',), Moment(6), supersedes='corrected')
    entry = topic(dossier(RECAP, CORRECTED, later))
    assert [i.operation_id for i in entry.current] == ['later']
    assert {i.operation_id: i.status for i in entry.history} == {'recap': 'stale', 'corrected': 'superseded'}


def test_unreconciled_accounts_are_kept_side_by_side():
    other = MemorySummary('other', ACTOR, 'play', 'visitor-identity', 'The visitor is a stranger from the coast.',
                          ('later',), Moment(6))
    entry = topic(dossier(RECAP, CORRECTED, other))
    assert sorted((i.operation_id, i.status) for i in entry.current) == [('corrected', 'contested'), ('other', 'contested')]
    assert entry.current[0].operation_id == 'other'  # the later basis first


def test_an_account_whose_evidence_is_missing_is_unsupported_not_memory():
    result = dossier(RECAP, as_of=2, corrections=(), evidence=EVIDENCE[1:])
    [item] = topic(result).history
    assert item.status == 'unsupported' and item.unavailable_ids == ('masked',)
    assert result.standing() == ()


def test_a_chain_resolves_the_same_in_every_order():
    def chain(*bases):  # each account names the one before it
        cites = {1: 'masked', 2: 'abbot', 5: 'later'}
        return [MemorySummary(f'a{n}', ACTOR, 'play', 'chain', f'Account {n}', (cites[basis],), Moment(6),
                              supersedes=f'a{n - 1}' if n else None) for n, basis in enumerate(bases)]

    for bases, expected in (((5, 2, 1), {'a0': 'current', 'a1': 'older_basis', 'a2': 'older_basis'}),
                            ((2, 5, 1), {'a0': 'superseded', 'a1': 'current', 'a2': 'older_basis'}),
                            ((2, 1, 5), {'a0': 'superseded', 'a1': 'superseded', 'a2': 'current'})):
        for order in itertools.permutations(chain(*bases)):
            result = topic(dossier(*order, corrections=()), 'chain')
            assert {i.operation_id: i.status for i in result.current + result.history} == expected, (bases, order)
    entry = topic(dossier(*chain(2, 1, 5), corrections=()), 'chain')
    history = {i.operation_id: i.superseded_by for i in entry.history}
    assert history == {'a0': ('a1',), 'a1': ('a2',)}


def test_limit_bounds_history_and_never_sheds_a_standing_account():
    accounts = tuple(MemorySummary(f's{n}', ACTOR, 'play', f'topic-{n % 3}', f'Detail {n}', ('later',), Moment(6))
                     for n in range(9))
    stale = MemorySummary('stale', ACTOR, 'play', 'visitor-identity', 'Brother Aldric again.', ('masked',), Moment(6))
    result = dossier(RECAP, stale, *accounts, limit=1)
    assert result.omitted == 1 and len(result.standing()) == 9
    assert [i.operation_id for t in result.topics for i in t.history] == ['stale']


def test_invalid_accounts_are_refused():
    with pytest.raises(ValueError, match='invalid_summary_text'):
        MemorySummary('x', ACTOR, 'play', 'identity', '  ', ('masked',), Moment(1))
    with pytest.raises(ValueError, match='invalid_summary_text'):
        MemorySummary('x', ACTOR, 'play', 'identity', 'a' * 2001, ('masked',), Moment(1))
    with pytest.raises(ValueError, match='invalid_supersedes'):
        MemorySummary('x', ACTOR, 'play', 'identity', 'text', ('masked',), Moment(1), supersedes='x')
    stray = MemorySummary('stray', ACTOR, 'play', 'other-topic', 'text', ('later',), Moment(6), supersedes='recap')
    with pytest.raises(ValueError, match='unknown_supersedes'):
        dossier(RECAP, stray)
    foreign = MemorySummary('f', InstanceRef('camp', 'character', 'bob'), 'play', 't', 'text', ('later',), Moment(6))
    with pytest.raises(ValueError, match='invalid_summary'):
        dossier(foreign)
