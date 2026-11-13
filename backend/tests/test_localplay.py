"""058 synthetic local composition, real storage and runtime with scripted providers."""
import json
from dataclasses import replace

import pytest

from chat_fixtures import Clock
from scene_fixtures import MARA_VELL, Tagged
from grimoire.chatstore import ChatLibrary
from store_helpers import OWNER
from scene_fixtures import ANYA, STORM
from grimoire.chats import Launch
from grimoire.scene.model import MAX_CHAT_ID, MAX_TAGGED_CHAT_ID, contribution_id, turn_id
from grimoire.core.identity import ChatRef

from scene_fixtures import ANA, BOB, Table, attach_tamsin, card_library, say
from grimoire.core import DIRECTOR
from grimoire.core.authority import delegate
from grimoire.core.identity import InstanceRef
from grimoire.ledgers.codec import ControlChange
from grimoire.localplay import LocalPlay
from grimoire.textapi.views import turn_view
from grimoire.scene import SceneRefused, heard_for, table_lines
from grimoire.store import etag
from grimoire.store.layout import secret_parts, is_secret_state


def refuses(reason, fn, *args, **kwargs):
    with pytest.raises(SceneRefused) as error:
        fn(*args, **kwargs)
    assert error.value.reason == reason


def setup(table, chat):
    table.runner.seat(chat, ANA)
    second = attach_tamsin(table.store, table.owner, chat)
    table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, second.instance_id, BOB), table.owner)
    first = table.runner.open(chat).persona
    return first, second


def filled(table, chat, first, second):
    service = LocalPlay(table.runner)
    draft, rev = service.create(chat, table.owner, (first, second))
    rev = service.edit(chat, draft, table.player(), first, '"The beacon is lit."', expected=rev)
    rev = service.edit(chat, draft, table.player(BOB), second, '"The gates are open."', expected=rev)
    return service, draft, rev


def consent(table, chat, service, draft, rev):
    for p in (ANA, BOB):
        service.confirm(chat, draft, table.player(p), expected=rev, share_with_owner=True)


def test_two_pc_atomic_ingestion_narrator_once_and_lost_ack_restart(tmp_path, monkeypatch):
    folder, _, chat = card_library(tmp_path)
    with Table(folder, [say('Thunder rolls over the quay.')]) as table:
        first, second = setup(table, chat)
        service, draft, rev = filled(table, chat, first, second)
        ana_view = service.read(chat, draft, table.player())
        assert len(ana_view.own) == 1 and 'beacon' in ana_view.own[0].text
        assert 'gates' not in repr(ana_view)
        consent(table, chat, service, draft, rev)
        original = table.runner._write
        def lose_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('synthetic acknowledgement lost after commit')
        monkeypatch.setattr(table.runner, '_write', lose_ack)
        with pytest.raises(OSError):
            service.accept(chat, draft, table.owner, expected=rev)
        monkeypatch.setattr(table.runner, '_write', original)
        record = service.accept(chat, draft, table.owner, expected=rev)
        assert [p.actor for p in record.posts] == [first, second]
        assert [p.submitted_by for p in record.posts] == [ANA, BOB]
        assert len({record.contribution_id(i) for i in range(2)}) == 2
        take = table.runner.generate(chat, table.owner, speakers=[], director=True)
        assert len(take.calls) == 1
        assert any('beacon' in line.text for line in table_lines((record,), {}))
        assert any('gates' in line.text for line in heard_for((record,), first, {}))
    with Table(folder, []) as reopened:
        recovered = LocalPlay(reopened.runner).accept(chat, draft, reopened.owner, expected=rev)
        assert recovered.acceptance_id == record.acceptance_id
        assert len(recovered.takes) == 1
        assert len(reopened.runner.open(chat).turns.turns) == 1
        assert recovered.posts == record.posts


def test_consent_edit_reorder_cas_restart_and_command_safety(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        first, second = setup(table, chat)
        service, draft, rev = filled(table, chat, first, second)
        refuses('not_your_character', service.edit, chat, draft, table.player(BOB), first, '"Oops"', expected=rev)
        refuses('not_authenticated', service.read, chat, draft, ANA)
        refuses('consent_required', service.confirm, chat, draft, table.player(), expected=rev)
        consent(table, chat, service, draft, rev)
        refuses('not_in_this_slice', service.edit, chat, draft, table.player(), first, 'Grimoire, pause', expected=rev)
        assert not table.runner.open(chat).paused
        rev2 = service.edit(chat, draft, table.player(), first, '"Changed."', expected=rev)
        refuses('stale_draft', service.edit, chat, draft, table.player(), first, '"Stale."', expected=rev)
        refuses('consent_required', service.accept, chat, draft, table.owner, expected=rev2)
        consent(table, chat, service, draft, rev2)
        rev3 = service.reorder(chat, draft, table.owner, (second, first), expected=rev2)
        refuses('consent_required', service.accept, chat, draft, table.owner, expected=rev3)
    with Table(folder) as table:
        service = LocalPlay(table.runner)
        view = service.read(chat, draft, table.player())
        assert 'Changed' in view.own[0].text
        refuses('consent_required', service.accept, chat, draft, table.owner, expected=rev3)
        consent(table, chat, service, draft, rev3)
        record = service.accept(chat, draft, table.owner, expected=rev3)
        assert record.posts[0].actor == second


def test_control_regain_invalidates_drafts_and_permanently_revokes_delegations(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        first, second = setup(table, chat)
        ledger = table.runner.sovereignty
        grant = delegate('characterization', campaign_id=chat.campaign_id, delegation_id='d1',
                         actor_id=first.instance_id, granted_by=ANA, scope='scene', scene_id=chat.chat_id)
        ledger.record(chat.campaign_id, grant, table.player())
        service, draft, rev = filled(table, chat, first, second)
        consent(table, chat, service, draft, rev)
        for controller in (BOB, ANA):
            ledger.record(chat.campaign_id, ControlChange(chat.campaign_id, first.instance_id, controller), table.owner)
        refuses('controls_changed', service.accept, chat, draft, table.owner, expected=rev)
        assert [r.delegation_id for r in ledger.load(chat.campaign_id).revocations] == ['d1']
    with Table(folder) as table:
        assert [r.delegation_id for r in table.runner.sovereignty.load(chat.campaign_id).revocations] == ['d1']


def test_npc_refusal_private_audiences_and_old_receipt_after_control_change(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        first, second = setup(table, chat)
        scene = table.runner.open(chat)
        npc = next(a for a in scene.npcs)
        service = LocalPlay(table.runner)
        refuses('not_a_pc', service.create, chat, table.owner, (first, npc))
        service, draft, rev = filled(table, chat, first, second)
        rev = service.edit(chat, draft, table.player(), first, 'I whisper to Tamsin Hale: "Secret beacon."',
                           expected=rev, mode='action')
        consent(table, chat, service, draft, rev)
        record = service.accept(chat, draft, table.owner, expected=rev)
        assert record.posts[0].private
        assert all('Secret beacon' not in l.text for l in heard_for((record,), npc, {}))
        assert all('Secret beacon' not in l.text for l in table_lines((record,), {}))
        table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, second.instance_id, ANA), table.owner)
        assert service.accept(chat, draft, table.owner, expected=rev).acceptance_id == record.acceptance_id
        refuses('controls_changed', table.runner.generate, chat, table.owner, speakers=[])


@pytest.mark.parametrize('name', ['local-pc/a/b/../x.json', 'local-pc/a/b/x.json/more', 'local-pc/a/x.json',
                                   'local-pc/a/b/.json', 'local-pc/a/b/x.JSON', 'local-pc/a/b/../x',
                                   'local-pc/a/b/x.json/','local-pc/a/b/..json'])
def test_secret_path_refuses_traversal_and_lookalikes(name):
    with pytest.raises(ValueError):
        secret_parts(name)


def test_local_draft_secret_allowlist():
    assert secret_parts('local-pc/campaign/chat/draft.json') == ('secrets', 'local-pc', 'campaign', 'chat', 'draft.json')
    assert is_secret_state(('secrets', 'local-pc', 'campaign', 'chat', 'draft.json'))


def test_tampered_approval_cannot_mint_consent_and_precommit_failure_is_retryable(tmp_path, monkeypatch):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        first, second = setup(table, chat)
        service, draft, rev = filled(table, chat, first, second)
        name = service._name(chat, draft)
        raw = table.store.read_secret(name)
        altered = json.loads(raw)
        altered['consent'] = [ANA, BOB]
        tx = table.store.transaction()
        tx.put_secret(name, json.dumps(altered).encode(), rev)
        tx.commit(table.owner)
        refuses('invalid_draft', service.read, chat, draft, table.player())
        tx = table.store.transaction()
        tx.put_secret(name, raw, etag(json.dumps(altered).encode()))
        tx.commit(table.owner)
        refuses('consent_required', service.accept, chat, draft, table.owner, expected=rev)
        consent(table, chat, service, draft, rev)
        original = table.runner._write
        def before_commit(*args, **kwargs):
            raise OSError('synthetic failure before commit')
        monkeypatch.setattr(table.runner, '_write', before_commit)
        with pytest.raises(OSError):
            service.accept(chat, draft, table.owner, expected=rev)
        assert not table.runner.open(chat).turns.turns
        monkeypatch.setattr(table.runner, '_write', original)
        record = service.accept(chat, draft, table.owner, expected=rev)
        inputs = [(first, table.player(), '"wrong"', 'auto'),
                  (second, table.player(BOB), '"The gates are open."', 'auto')]
        refuses('acceptance_mismatch', table.runner.submit_combined, chat, table.owner, record.acceptance_id,
                inputs, epoch=record.acceptance_epoch)
        refuses('combined_view_unsupported', turn_view, table.runner.open(chat).turns.turns[0], {})


def test_private_combined_posts_keep_npc_prompt_separate(tmp_path):
    folder, _, chat = card_library(tmp_path)
    with Table(folder, [say('We wait.'), say('Thunder rolls.')]) as table:
        first, second = setup(table, chat)
        chats = ChatLibrary(table.store, table.owner, owner=OWNER, clock=Clock(), new_id=Tagged('npc'))
        _, stamp = chats.read_chat(chat)
        chats.attach_source(chat, MARA_VELL, 'character', expected=stamp).commit()
        table.runner.seat(chat, ANA)
        npc, = table.runner.open(chat).npcs
        service, draft, rev = filled(table, chat, first, second)
        rev = service.edit(chat, draft, table.player(), first, 'I whisper to Tamsin Hale: "Hidden signal."',
                           expected=rev, mode='action')
        consent(table, chat, service, draft, rev)
        service.accept(chat, draft, table.owner, expected=rev)
        take = table.runner.generate(chat, table.owner, speakers=[npc], director=True)
        assert len(take.calls) == 2
        prompts = table.harness.prompts()
        assert len(prompts) == 2
        assert all('Hidden signal' not in prompt for prompt in prompts)
        assert all('The gates are open.' in prompt for prompt in prompts)


def test_oversized_state_never_replaces_readable_draft(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        first, second = setup(table, chat)
        service, draft, rev = filled(table, chat, first, second)
        state, _, _ = service._load(chat, draft)
        state['entries'][0]['text'] = 'x' * (256 * 1024)
        refuses('draft_too_large', service._save, chat, draft, state, rev)
        assert service.read(chat, draft, table.player()).revision == rev


def test_edit_text_bound_refuses_before_write_and_exact_bound_reloads(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        first, second = setup(table, chat)
        service, draft, rev = filled(table, chat, first, second)
        refuses('draft_too_large', service.edit, chat, draft, table.player(), first,
                'x' * 16001, mode='speech', expected=rev)
        assert service.read(chat, draft, table.player()).revision == rev
        updated = service.edit(chat, draft, table.player(), first, 'x' * 16000, mode='speech', expected=rev)
        assert service.read(chat, draft, table.player()).revision == updated
        assert len(service.read(chat, draft, table.player()).own[0].text) == 16000


def test_max_length_chat_accepts_secondary_contribution_and_reloads(tmp_path):
    folder, _, _ = card_library(tmp_path)
    with Table(folder) as table:
        chats = ChatLibrary(table.store, table.owner, owner=OWNER, clock=Clock(),
                            new_id=lambda prefix: 'x' * MAX_CHAT_ID if prefix == 't' else f'{prefix}-long')
        chat = chats.start_chat(Launch('scenario', STORM)).commit().chat
        _, stamp = chats.read_chat(chat)
        chats.set_persona(chat, ANYA, expected=stamp).commit()
        first, second = setup(table, chat)
        service, draft, rev = filled(table, chat, first, second)
        consent(table, chat, service, draft, rev)
        record = service.accept(chat, draft, table.owner, expected=rev)
        assert len(record.turn_id) == 104
        assert record.contribution_id(0) != record.contribution_id(1)
        assert len(record.contribution_id(1)) <= 104
        assert table.runner.open(chat).last.posts == record.posts
        tagged = turn_id(ChatRef(chat.campaign_id, 'x' * MAX_TAGGED_CHAT_ID), 1, '1234abcd')
        assert len(tagged) == 104
        assert len(contribution_id(tagged, 1)) <= 104


def test_combined_ingestion_checks_every_controller_after_routing(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        first, second = setup(table, chat)
        epoch = LocalPlay(table.runner)._epoch(chat)
        inputs = [(first, table.player(), '"First."', 'auto'),
                  (second, table.player(), '"Impersonating the second PC."', 'auto')]
        refuses('not_your_character', table.runner.submit_combined, chat, table.owner,
                'wrong-controller', inputs, epoch=epoch)
        assert not table.runner.open(chat).turns.turns
