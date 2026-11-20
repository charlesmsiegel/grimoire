"""053 end to end: an NPC's consolidated memory over several scenes, through the durable ledger, the app's
own runner wiring, the context inspector and a generated brief. Synthetic library; every model call is scripted."""
import json

import pytest
from scene_fixtures import ANA, Table, card_library, say
from store_helpers import OWNER

from grimoire.core import DIRECTOR, Event, Moment, Perception, Proposition, Report
from grimoire.evidencestore import EvidenceLibrary
from grimoire.goals import Agenda, Approach, Goal, Holds, Limit
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.memory import PLAY_COLLECTION, MemoryCorrection, MemorySelection, MemorySummary
from grimoire.memorystore import MemoryLibrary
from grimoire.play import SceneRunner
from grimoire.scene import SceneRefused
from grimoire.textapi import TextApi

MASKED = 'The hooded visitor says he is Brother Aldric.'
ABBOT = 'A novice whispers that Brother Aldric is the abbot.'
UNMASKED = 'The hood falls in the crypt: it is Sister Maren.'
RECAP_TEXT = 'The hooded visitor is Brother Aldric, the abbot.'
CORRECTED_TEXT = 'The hooded visitor was Sister Maren in disguise.'


def scenes(table, chat, actor):
    """Three scenes of the NPC's own confirmed perceptions."""
    c = chat.campaign_id
    records = []
    for n, (scene, evidence, text) in enumerate((('chapel', 'masked', MASKED), ('cloister', 'abbot', ABBOT),
                                                 ('crypt', 'unmasked', UNMASKED)), 1):
        records += [Event(c, f'ev-{evidence}', f'event {n}', Moment(n, scene, 't1'), 'director'),
                    Perception(c, evidence, actor, f'ev-{evidence}', 'sight', text, Moment(n, scene, 't1'), 'director',
                               confirmed=True)]
    EvidenceLibrary(table.store, table.owner, owner=OWNER).append(c, records).commit()


def remember(memory, actor):
    """A recap of the first two scenes, the unmasking's correction, then the older recap filed again."""
    recap = MemorySummary('recap', actor, PLAY_COLLECTION, 'visitor-identity', RECAP_TEXT, ('masked', 'abbot'), Moment(2))
    memory.select(MemorySelection('picked', actor, PLAY_COLLECTION, ('masked', 'abbot'), Moment(2)), requested_by=DIRECTOR)
    memory.summarize(recap, requested_by=DIRECTOR)
    memory.correct(MemoryCorrection('unmasking', actor, ('masked',), ('unmasked',), Moment(3), 'the hood fell'),
                   requested_by=DIRECTOR)
    memory.summarize(MemorySummary('corrected', actor, PLAY_COLLECTION, 'visitor-identity', CORRECTED_TEXT,
                                   ('unmasked',), Moment(3), supersedes='recap'), requested_by=DIRECTOR)
    # a backlog recap filed after the correction, naming the corrected account to replace
    memory.summarize(MemorySummary('backlog', actor, PLAY_COLLECTION, 'visitor-identity', RECAP_TEXT,
                                   ('masked', 'abbot'), Moment(3), supersedes='corrected'), requested_by=DIRECTOR)


@pytest.fixture
def played(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder, runtime=False) as table:
        table.runner.seat(chat, ANA)
        actor = next(a for a in table.runner.open(chat).npcs)
        scenes(table, chat, actor)
        api = TextApi(table.store, table.authority, owner=OWNER)
        remember(api.memory, actor)
    return folder, chat, actor


def test_the_app_packs_the_corrected_identity_and_the_inspector_traces_it(played):
    folder, chat, actor = played
    with Table(folder, runtime=False) as table:
        api = TextApi(table.store, table.authority, owner=OWNER)  # the app's own runner wiring
        data = api.runner.inspect_context(chat, actor)
        lines = [item['line'] for item in data['items']]
        assert f'You remember: {CORRECTED_TEXT}' in lines
        assert not any(RECAP_TEXT in line or MASKED in line for line in lines)
        assert RECAP_TEXT not in data['system'] and MASKED not in data['system']
        assert f'You perceived (sight): {UNMASKED}' in lines and f'You perceived (sight): {ABBOT}' in lines
        summary = next(item for item in data['items'] if item['provenance']['kind'] == 'summary')
        assert summary['provenance'] == {'kind': 'summary', 'name': 'visitor-identity', 'record': 'corrected',
                                         'evidence': ['unmasked']}
        # the inspector's evidence for each remembered detail, and the history kept beside it
        dossier = api.memory.dossier(actor, PLAY_COLLECTION, as_of=3, requested_by=DIRECTOR)
        [entry] = dossier.topics
        [current] = entry.current
        assert [(e.evidence_id, e.content, e.moment.scene_id) for e in current.evidence] == [
            ('unmasked', UNMASKED, 'crypt')]
        assert {i.operation_id: (i.status, i.replacement_ids) for i in entry.history} == {
            'recap': ('stale', ('unmasked',)), 'backlog': ('stale', ('unmasked',))}
        assert [e.content for e in next(i for i in entry.history if i.operation_id == 'recap').evidence] == [MASKED, ABBOT]
        audit = {value.operation_id: (editor, principal) for value, editor, principal
                 in api.memory.inspect(chat.campaign_id, requested_by=DIRECTOR)}
        assert audit['backlog'] == (DIRECTOR, 'app:memory')
        # before the unmasking the recap was the NPC's memory
        before = api.memory.dossier(actor, PLAY_COLLECTION, as_of=2, requested_by=DIRECTOR)
        assert [i.text for i in before.standing()] == [RECAP_TEXT]


def test_a_generated_brief_carries_the_consolidated_memory(played):
    folder, chat, _ = played
    with Table(folder, [say('Sister Maren, then.')]) as table:
        table.runner.memory = MemoryLibrary(table.store, table.authority,
                                            table.authority.handle_for('app:memory', trusted=True))
        take = table.runner.generate(chat, table.player())
        assert take.lines
        [prompt] = table.harness.prompts()
        assert CORRECTED_TEXT in prompt and RECAP_TEXT not in prompt and MASKED not in prompt


def test_a_damaged_memory_ledger_refuses_rather_than_packing_without_it(played):
    folder, chat, actor = played
    next((folder / 'authority' / 'memory' / chat.campaign_id).glob('*.json')).write_text('{}')
    with Table(folder, runtime=False) as table:
        api = TextApi(table.store, table.authority, owner=OWNER)
        with pytest.raises(SceneRefused) as error:
            api.runner.inspect_context(chat, actor)
        assert error.value.reason == 'memory_unreadable'


def test_an_agenda_chooses_from_the_corrected_projection(tmp_path):
    """A limit gated on a corrected report no longer binds, and the corrected text never returns via the agenda."""
    folder, chat, _ = card_library(tmp_path)
    with Table(folder, runtime=False) as table:
        table.runner.seat(chat, ANA)
        mara = next(a for a in table.runner.open(chat).npcs)
    rule = 'Never move contraband while the harbour is watched'
    plan = Agenda(chat.campaign_id, mara, goals=(Goal('cargo', 'Get the cargo past the harbour', 50, (
        Approach('smuggle', 'Ask the pilot to run the cargo tonight', tags=frozenset({'smuggle'})),
        Approach('wait', 'Tell the pilot the cargo can wait')),),),
        limits=(Limit('lie-low', rule, forbids=frozenset({'smuggle'}), when=(Holds('watch', stances={'aware'}),)),))
    with Table(folder, [say('Honest papers.'), say('North channel.')], agendas=[plan]) as table:
        c = chat.campaign_id
        EvidenceLibrary(table.store, table.owner, owner=OWNER).append(c, [
            Proposition(c, 'watch', 'The quay is watched', Moment(1), 'director'),
            Proposition(c, 'clear', 'The watch was a false alarm', Moment(1), 'director'),
            Report(c, 'warned', mara, 'watch', 'told', Moment(2), 'scene:quay', confirmed=True),
            Report(c, 'cleared', mara, 'clear', 'told', Moment(3), 'scene:quay', confirmed=True)]).commit()
        table.runner.post(chat, table.player(), '"Is the cargo safe?"')
        table.runner.generate(chat, table.player())  # no consolidated memory: the warning binds
        memory = MemoryLibrary(table.store, table.authority, table.authority.handle_for('app:memory', trusted=True))
        memory.correct(MemoryCorrection('false-alarm', mara, ('warned',), ('cleared',), Moment(3), 'no watch'),
                       requested_by=DIRECTOR)
        table.runner.memory = memory
        table.runner.generate(chat, table.player())
        record = table.runner.open(chat).pending
        assert [(i.approach_id, i.refusals) for i in record.intentions] == [('wait', ('lie-low',)), ('smuggle', ())]
        before, after = table.harness.prompts()
        assert rule in before and rule not in after and 'The quay is watched' not in after


def test_the_runner_takes_memory_only_on_a_trusted_issued_app_key(played):
    folder, _, _ = played
    with Table(folder, runtime=False) as table:
        for handle in (table.authority.handle_for('app:memory'), table.authority.handle_for('human:ana', trusted=True)):
            with pytest.raises(SceneRefused) as error:
                SceneRunner(table.store, table.authority, table.owner, owner=OWNER,
                            memory=MemoryLibrary(table.store, table.authority, handle))
            assert error.value.reason == 'bad_memory'


def test_ledger_rules_for_accounts(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder, runtime=False) as table:
        table.runner.seat(chat, ANA)
        actor = next(a for a in table.runner.open(chat).npcs)
        scenes(table, chat, actor)
        memory = MemoryLibrary(table.store, table.authority, table.authority.handle_for('app:memory', trusted=True))
        account = MemorySummary('a', actor, PLAY_COLLECTION, 'visitor-identity', RECAP_TEXT, ('masked',), Moment(1))
        memory.summarize(account, requested_by=DIRECTOR)
        memory.summarize(account, requested_by=DIRECTOR)  # an exact retry is a no-op
        with pytest.raises(ValueError, match='operation_conflict'):
            memory.summarize(MemorySummary('a', actor, PLAY_COLLECTION, 'visitor-identity', 'changed', ('masked',),
                                           Moment(1)), requested_by=DIRECTOR)
        with pytest.raises(ValueError, match='unknown_supersedes'):
            memory.summarize(MemorySummary('b', actor, PLAY_COLLECTION, 'other-topic', 'text', ('masked',), Moment(1),
                                           supersedes='a'), requested_by=DIRECTOR)
        with pytest.raises(ValueError, match='unavailable_evidence'):  # not yet perceived at that moment
            memory.summarize(MemorySummary('c', actor, PLAY_COLLECTION, 'visitor-identity', 'text', ('unmasked',),
                                           Moment(1)), requested_by=DIRECTOR)
        with pytest.raises(ValueError, match='backdated'):
            memory.summarize(MemorySummary('d', actor, PLAY_COLLECTION, 't', 'text', ('masked',), Moment(0)),
                             requested_by=DIRECTOR)
        # a player who takes the character over never reads the director's accounts as their own
        SovereigntyLedger(table.authority).record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id,
                                                                                  'human:bob'), table.owner)
        player = MemoryLibrary(table.store, table.authority, table.authority.handle_for('human:bob', trusted=True))
        assert player.dossier(actor, PLAY_COLLECTION, as_of=3, requested_by='human:bob').topics == ()
    with Table(folder, runtime=False) as table:
        rows = MemoryLibrary(table.store, table.authority,
                             table.authority.handle_for('app:memory', trusted=True)).inspect(chat.campaign_id,
                                                                                             requested_by=DIRECTOR)
        assert [value for value, _, _ in rows] == [account]
        assert json.dumps([value.text for value, _, _ in rows]) == json.dumps([RECAP_TEXT])
