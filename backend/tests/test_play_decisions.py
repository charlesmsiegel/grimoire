"""Synthetic integration of the real scene runner, runtime and durable decision ledger."""
import json
from dataclasses import replace

import pytest
from scene_fixtures import ANA, Table, attach_tamsin, card_library, say
from store_helpers import OWNER
from grimoire.context import Budget
from grimoire.core import Event, Moment, Perception
from grimoire.core.identity import InstanceRef
from grimoire.core.inference import InferencePolicy, InferenceStage
from grimoire.decisionstore import DecisionLibrary
from grimoire.evidencestore import EvidenceLibrary
from grimoire.goals import Agenda, Goal, Approach
from grimoire.scene import SceneRefused
from grimoire.textapi import TextApi
from grimoire.testing.providers import Supersede


def tactics(actor):
    return Agenda(actor.campaign_id, actor, goals=(Goal('cargo', 'Move the cargo', 1, (
        Approach('wait', 'Wait for papers'), Approach('ask', 'Ask for papers'))),))


def wire(table):
    table.runner.decisions = DecisionLibrary(table.store, table.authority,
        table.authority.handle_for('app:decisions', trusted=True))
    return table.runner.decisions


def prepare(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
        scene = table.runner.open(chat)
        return folder, chat, next(a for a in scene.npcs)


@pytest.mark.parametrize('answer,expected,fallback', [('2', 'ask', None), ('nonsense', 'wait', 'unreadable_pick')])
def test_metered_decision_replayed_on_reroll_and_restart(tmp_path, answer, expected, fallback):
    folder, chat, actor = prepare(tmp_path)
    policy = InferencePolicy({'decision': 1}, max_attempts_per_call=1)
    with Table(folder, [say(answer), say('Papers, then.'), say('Still papers.')], policy=policy) as table:
        ledger = wire(table)
        table.runner.set_agendas([tactics(actor)])
        take = table.runner.generate(chat, table.player())
        record = table.runner.open(chat).last
        entry = ledger.get(record.ref, actor).record
        assert entry.chosen == ('cargo', expected)
        assert entry.fallback == fallback
        call = next(c for c in take.calls if c.stage is InferenceStage.DECISION)
        assert call.status == 'accepted' and call.attempts
        again = table.runner.generate(chat, table.player())
        assert all(c.stage is not InferenceStage.DECISION for c in again.calls)
    with Table(folder, [say('Reopened.')], policy=policy) as table:
        wire(table)
        table.runner.set_agendas([tactics(actor)])
        assert all(c.stage is not InferenceStage.DECISION for c in table.runner.generate(chat, table.player()).calls)


def test_disabled_decisions_record_and_removed_or_changed_agenda_refuses(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [say('Wait.')]) as table:
        ledger = wire(table)
        table.runner.set_agendas([tactics(actor)])
        take = table.runner.generate(chat, table.player())
        assert len(take.calls) == 1
        assert ledger.get(table.runner.open(chat).last.ref, actor).record.method == 'policy'
        table.runner.set_agendas([])
        with pytest.raises(SceneRefused) as error:
            table.runner.generate(chat, table.player())
        assert error.value.reason == 'agenda_changed'
        table.runner.set_agendas([replace(tactics(actor), goals=(replace(tactics(actor).goals[0], aim='Changed aim'),))])
        with pytest.raises(SceneRefused) as error:
            table.runner.generate(chat, table.player())
        assert error.value.reason == 'agenda_changed'


def test_inspector_works_without_model_and_refuses_pc_or_foreign(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, runtime=False) as table:
        before = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        data = table.runner.inspect_context(chat, actor)
        assert data['actor'] == str(actor) and data['label'].startswith('Current')
        assert data['system'] and data['items'] and data['budget']['measurement']
        assert {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()} == before
        for who in (table.runner.open(chat).persona, InstanceRef(actor.campaign_id, 'character', 'foreign')):
            with pytest.raises(SceneRefused):
                table.runner.inspect_context(chat, who)
        assert not any(k in data for k in ('hidden', 'held', 'problems', 'notes', 'knowledge', 'basis'))


def test_textapi_constructs_real_decision_library(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, runtime=False) as table:
        api = TextApi(table.store, table.authority, owner=OWNER)
        assert isinstance(api.runner.decisions, DecisionLibrary)
        assert api.runner.inspect_context(chat, actor)['actor'] == str(actor)


def test_paid_decision_ledger_failure_retains_attempt_and_status(tmp_path, monkeypatch):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [say('2')], policy=InferencePolicy({'decision': 1})) as table:
        ledger = wire(table)
        table.runner.set_agendas([tactics(actor)])
        def failed(_record):
            raise OSError('synthetic write failure')
        monkeypatch.setattr(ledger, 'record', failed)
        take = table.runner.generate(chat, table.player())
        assert take.outcome == 'halted' and not take.lines
        decision = next(c for c in take.calls if c.stage is InferenceStage.DECISION)
        assert decision.status == 'accepted' and len(decision.attempts) == 1
        assert ledger.decisions(table.runner.open(chat).last.ref) == ()


@pytest.mark.parametrize('invalidate', ['fence', 'pass', 'agenda'])
def test_stale_paid_decision_never_enters_ledger(tmp_path, invalidate):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [Supersede(1, say('2'))], policy=InferencePolicy({'decision': 1})) as table:
        ledger = wire(table)
        table.runner.set_agendas([tactics(actor)])
        def supersede():
            if invalidate == 'fence':
                table.runner.fence.claim(str(chat))
            elif invalidate == 'pass':
                table.runner.pass_turn(chat, table.player())
            else:
                table.runner.set_agendas([])
        table.harness.transport.on_supersede = supersede
        take = table.runner.generate(chat, table.player())
        assert not take.lines
        assert next(c for c in take.calls if c.stage is InferenceStage.DECISION).attempts
        assert ledger.decisions(table.runner.open(chat).last.ref) == ()


def test_cap_limits_decisions_and_keeps_independent_npc_calls(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [say('2'), say('Papers.'), say('A stamp.')],
               policy=InferencePolicy({'decision': 1})) as table:
        other = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        ledger = wire(table)
        table.runner.set_agendas([tactics(actor), tactics(other)])
        take = table.runner.generate(chat, table.player())
        assert len([c for c in take.calls if c.stage is InferenceStage.DECISION]) == 1
        assert len([c for c in take.calls if c.stage is InferenceStage.NPC_GENERATION]) == 2
        entries = ledger.decisions(table.runner.open(chat).last.ref)
        assert len(entries) == 2
        assert {entry.record.method for entry in entries} == {'model', 'policy'}
        assert table.runner.open(chat).last.intentions_of(take.take)[0].approach_id == 'ask'


def test_no_agenda_means_no_optional_call_or_record(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [say('Hello.')], policy=InferencePolicy({'decision': 1})) as table:
        ledger = wire(table)
        take = table.runner.generate(chat, table.player())
        assert len(take.calls) == 1
        assert ledger.decisions(table.runner.open(chat).last.ref) == ()


def test_inspector_replays_current_pending_selection(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [say('2'), say('Ask.')], policy=InferencePolicy({'decision': 1})) as table:
        wire(table)
        table.runner.set_agendas([tactics(actor)])
        take = table.runner.generate(chat, table.player())
        assert take.lines
        assert 'Ask for papers' in table.runner.inspect_context(chat, actor)['system']
        assert table.runner.open(chat).last.intentions_of(take.take)[0].approach_id == 'ask'
        table.runner.keep(chat, table.player(), take.take)


def test_inspector_has_no_other_npc_or_director_evidence_even_in_metadata(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, runtime=False) as table:
        other = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        EvidenceLibrary(table.store, table.owner, owner=OWNER).append(chat.campaign_id, [
            Event(chat.campaign_id, 'private-director-id', 'PRIVATE DIRECTOR EVENT', Moment(1), 'director'),
            Perception(chat.campaign_id, 'private-other-evidence', other, 'private-director-id', 'sight',
                       'PRIVATE OTHER MEMORY', Moment(1), 'director', confirmed=True),
        ]).commit()
        encoded = json.dumps(table.runner.inspect_context(chat, actor))
        for secret in ('private-director-id', 'PRIVATE DIRECTOR EVENT', 'private-other-evidence', 'PRIVATE OTHER MEMORY'):
            assert secret not in encoded


def test_inspector_damaged_evidence_fails_closed(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    damaged = folder / 'campaigns' / chat.campaign_id / 'events' / 'e-9.json'
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_text(json.dumps({'format': 'grimoire.knowledge-record/1',
        'address': f'campaign:{chat.campaign_id}#event/e-9', 'schema': 'grimoire.event/0', 'payload': {}}))
    with Table(folder, runtime=False) as table:
        with pytest.raises(SceneRefused) as error:
            table.runner.inspect_context(chat, actor)
        assert error.value.reason == 'ledger_damaged'


def test_one_option_skips_provider_and_records_policy(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    one = replace(tactics(actor), goals=(replace(tactics(actor).goals[0],
        approaches=(tactics(actor).goals[0].approaches[0],)),))
    with Table(folder, [say('Wait.')], policy=InferencePolicy({'decision': 1})) as table:
        ledger = wire(table)
        table.runner.set_agendas([one])
        take = table.runner.generate(chat, table.player())
        call = next(c for c in take.calls if c.stage is InferenceStage.DECISION)
        assert call.status == 'not_issued' and call.reason == 'nothing_to_decide'
        assert call.attempts and all(a.outcome == 'not_sent' for a in call.attempts)
        assert ledger.get(table.runner.open(chat).last.ref, actor).record.method == 'policy'


def test_inspector_budget_omissions_have_source_qualified_provenance(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, runtime=False) as table:
        table.runner.post(chat, table.player(), '"Tell me about the lighthouse."')
        table.runner.budget = Budget(4096, lane_caps={"lore": 0})
        data = table.runner.inspect_context(chat, actor)
        assert data["budget"]["tokens"] <= 4096
        assert data["omitted"]
        omitted = next(item for item in data["omitted"] if item["lane"] == "lore")
        assert omitted["reason"] == "lane_cap" and omitted["tokens"] > 0
        assert omitted["provenance"]["source"].startswith("card:mara-vell/lorebook/")
        assert any(item["provenance"].get("source", "").startswith("card:mara-vell/character/")
                   for item in data["items"])


def test_decision_cap_is_shared_by_different_speakers_rerolling_same_turn(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [say('2'), say('First.'), say('Second.')],
               policy=InferencePolicy({'decision': 1})) as table:
        other = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        ledger = wire(table)
        table.runner.set_agendas([tactics(actor), tactics(other)])
        first = table.runner.generate(chat, table.player(), speakers=[actor])
        second = table.runner.generate(chat, table.player(), speakers=[other])
        assert sum(c.stage is InferenceStage.DECISION for t in (first, second) for c in t.calls) == 1
        assert ledger.get(table.runner.open(chat).last.ref, other).record.method == 'policy'
        assert second.lines


def test_running_decision_reservation_blocks_reroll_decision(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [Supersede(1, say('2')), say('Second.')],
               policy=InferencePolicy({'decision': 1})) as table:
        other = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        wire(table)
        table.runner.set_agendas([tactics(actor), tactics(other)])
        rerolled = []
        table.harness.transport.on_supersede = lambda: rerolled.append(
            table.runner.generate(chat, table.player(), speakers=[other]))
        first = table.runner.generate(chat, table.player(), speakers=[actor])
        assert first.state == 'stale'
        assert rerolled[0].lines
        assert all(c.stage is not InferenceStage.DECISION for c in rerolled[0].calls)


def test_not_sent_skip_does_not_spend_turn_decision_cap(tmp_path):
    folder, chat, actor = prepare(tmp_path)
    with Table(folder, [say('First.'), say('2'), say('Second.')],
               policy=InferencePolicy({'decision': 1})) as table:
        other = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        ledger = wire(table)
        one = replace(tactics(actor), goals=(replace(tactics(actor).goals[0],
            approaches=(tactics(actor).goals[0].approaches[0],)),))
        table.runner.set_agendas([one, tactics(other)])
        table.runner.generate(chat, table.player(), speakers=[actor])
        second = table.runner.generate(chat, table.player(), speakers=[other])
        assert any(c.stage is InferenceStage.DECISION and c.status == 'accepted' for c in second.calls)
        assert ledger.get(table.runner.open(chat).last.ref, other).record.method == 'model'


@pytest.mark.parametrize('status', ['accepted', 'failed', 'cancelled', 'not_issued'])
def test_stored_call_without_attempts_preserves_unknown_decision_usage(tmp_path, status):
    folder, chat, actor = prepare(tmp_path)
    policy = InferencePolicy({'decision': 1})
    with Table(folder, [say('2'), say('First.')], policy=policy) as table:
        other = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        wire(table)
        table.runner.set_agendas([tactics(actor), tactics(other)])
        first = table.runner.generate(chat, table.player(), speakers=[actor])
        stored = table.runner.scenes.turns(chat).last
        calls = tuple(replace(call, status=status, attempts=())
                      if call.stage is InferenceStage.DECISION else call for call in first.calls)
        # Save a valid turn with missing attempt detail, then reopen through the real store codec.
        table.runner._write(stored.record.with_take(replace(first, calls=calls)), stored)
    steps = [say('2'), say('Second.')] if status == 'not_issued' else [say('Second.')]
    with Table(folder, steps, policy=policy) as table:
        ledger = wire(table)
        table.runner.set_agendas([tactics(actor), tactics(other)])
        second = table.runner.generate(chat, table.player(), speakers=[other])
        assert second.lines
        assert any(c.stage is InferenceStage.DECISION for c in second.calls) == (status == 'not_issued')
        expected = 'model' if status == 'not_issued' else 'policy'
        assert ledger.get(table.runner.open(chat).last.ref, other).record.method == expected
