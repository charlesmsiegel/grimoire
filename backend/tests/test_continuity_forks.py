"""Real synthetic libraries: archive edited history without replaying accepted effects."""

from dataclasses import replace

import pytest

import grimoire.continuitystore.forks as forks
from grimoire.continuity import scene_revision
from grimoire.continuity.archive import FORK_SCHEMA, fingerprint
from grimoire.continuitystore import ContinuityLibrary, resolve_fork
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import ContinuityRef
from grimoire.core.inference import InferenceStage
from grimoire.ledgers import DurableAuthority
from grimoire.scene import CallRecord, Label, Take, TurnEffect, turn_from_payload, turn_to_payload
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, ProcessLocker, ScopeRecord, WriteDenied, etag
from grimoire.store.envelope import scope_record_document
from grimoire.store.jsonfmt import render, thaw
from grimoire.store.layout import address_parts
from grimoire.sync import ResolveRefused, plan_sync, semantic, sync_until_quiet
from grimoire.sync.semantic import resolution_risk
from grimoire.testing import Crash, CrashScenario, Target, run_with_fault
from grimoire.testing.fileops import snapshot
from effects_fixtures import CHAT, effects_library, give
from ledger_fixtures import Device, build_base, pay
from ledger_helpers import fixed_clock
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE, handles
from test_effect_continuity import write_turn
from test_sync_ledger import ledger_files

PATH = 'campaigns/c/turns/t-1.000001.json'


def bind(d):
    d.authority = DurableAuthority.open(d.store, [OWNER])
    if not d.authority.active_grants():
        for i, scope in enumerate(('campaigns', 'sources', 'kept')):
            d.authority.add_grant(Grant(f'owner-{i}', OWNER, frozenset({Permission.APPLY}),
                                       ResourcePath.parse(scope), OWNER))
    d.handles.authorizer = d.authority
    d.handles.app = d.authority.handle_for(OWNER, trusted=True)
    d.handles._players = {}
    d.effects = effects_library(d.store, d.handles, clock=d.clock)


@pytest.fixture
def pair(tmp_path):
    base = build_base(tmp_path)
    made = {n: Device(base, tmp_path, n) for n in (DESKTOP, PHONE)}
    write_turn(made[DESKTOP].store, made[DESKTOP].handles)
    sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles())
    made[DESKTOP].accept(give())
    write_turn(made[PHONE].store, made[PHONE].handles, text='The key was destroyed.')
    for d in made.values(): bind(d)
    yield made
    for d in made.values():
        d.authority.close()
        d.close()


def args(pair, operation='retain-1'):
    hs = {n: d.handles.app for n, d in pair.items()}
    stores = [pair[n].store for n in (DESKTOP, PHONE)]
    token = semantic.reviews(plan_sync(*stores, hs))[0].token
    return (*stores, 'c', operation, token, DESKTOP, hs, {n: {OWNER} for n in pair})


def resolve(pair, operation='retain-1'):
    return resolve_fork(*args(pair, operation))


def test_retains_exact_originals_restores_then_syncs_effect_once_and_repeats(pair):
    before = {n: d.store.read_exact(PATH) for n, d in pair.items()}
    ledgers = {n: ledger_files(d.store) for n, d in pair.items()}
    request = args(pair)
    result = resolve_fork(*request)
    assert result.status == 'pending_sync'
    for n, d in pair.items():
        assert d.store.read_exact(PATH) == before[DESKTOP]
        assert ledger_files(d.store) == ledgers[n]
        receipt = ContinuityLibrary(d.store, d.handles.app).inspect_fork('c', 'retain-1')
        assert receipt['scenes'][0]['variants'][PHONE]['text'].encode() == before[PHONE]
        assert receipt['scenes'][0]['variants'][DESKTOP]['text'].encode() == before[DESKTOP]
        snap = ContinuityLibrary(d.store, d.handles.app).snapshot('c')
        assert not any('retain-1' in p for p in snap.pins)
    assert resolve_fork(*request).status == 'pending_sync'
    hs = request[-2]
    assert not sync_until_quiet(request[0], request[1], hs).conflicts
    assert ledger_files(request[0]) == ledger_files(request[1]) == ledgers[DESKTOP]
    assert resolve_fork(*request).status == 'already_applied'
    assert len(request[0].campaign_accepted('c').records) == 1
    assert request[0].read(ContinuityRef('c', 'retain-1')).etag == request[1].read(ContinuityRef('c', 'retain-1')).etag
    with pytest.raises(AuthorityError):
        ContinuityLibrary(request[0], hs[DESKTOP]).inspect_fork('c', 'retain-1', requested_by='actor')


@pytest.mark.parametrize('first', [DESKTOP, PHONE])
def test_interrupted_commit_reopen_retry_preserves_originals(pair, monkeypatch, first):
    request = args(pair)
    original = {n: d.store.read_exact(PATH) for n, d in pair.items()}
    commit = forks._commit_device
    seen = []
    def interrupt(device, transaction, handle):
        seen.append(device)
        if len(seen) == 2: raise KeyboardInterrupt('between devices')
        return commit(device, transaction, handle)
    monkeypatch.setattr(forks, '_device_order', lambda devices: (first, PHONE if first == DESKTOP else DESKTOP))
    monkeypatch.setattr(forks, '_commit_device', interrupt)
    with pytest.raises(KeyboardInterrupt): resolve_fork(*request)
    for n, d in pair.items():
        if n == first:
            archive = d.store.read(ContinuityRef('c', 'retain-1')).record.payload
            assert archive['scenes'][0]['variants'][n]['text'].encode() == original[n]
        else:
            assert d.store.read_exact(PATH) == original[n]
        d.authority.close()
        root = d.store.root
        d.close()
        d.store = LibraryStore.open(root)
        bind(d)
    monkeypatch.setattr(forks, '_commit_device', commit)
    reopened = list(request)
    reopened[0:2] = [pair[n].store for n in (DESKTOP, PHONE)]
    reopened[-2] = {n: d.handles.app for n, d in pair.items()}
    assert resolve_fork(*reopened).status == 'pending_sync'
    assert all(d.store.read_exact(PATH) == original[DESKTOP] for d in pair.values())
    assert not sync_until_quiet(reopened[0], reopened[1], reopened[-2]).conflicts
    assert resolve_fork(*reopened).status == 'already_applied'


@pytest.mark.parametrize('mutation', ['scene', 'declaration', 'event'])
@pytest.mark.parametrize('first', [DESKTOP, PHONE])
def test_changed_causal_premises_refuse_interrupted_resume(pair, monkeypatch, mutation, first):
    request = args(pair)
    commit = forks._commit_device
    pending = PHONE if first == DESKTOP else DESKTOP
    def interrupt(device, transaction, handle):
        if device == pending: raise KeyboardInterrupt()
        return commit(device, transaction, handle)
    monkeypatch.setattr(forks, '_device_order', lambda devices: (first, pending))
    monkeypatch.setattr(forks, '_commit_device', interrupt)
    with pytest.raises(KeyboardInterrupt): resolve_fork(*request)
    d = pair[pending]
    if mutation == 'scene': write_turn(d.store, d.handles, text='Changed after interruption.')
    elif mutation == 'declaration':
        current_turn = next(iter(d.store.campaign_turns('c').records.values()))
        holder = next(iter(d.store.campaign_holdings('c').records.values()))
        payload = {'source': {'path': PATH, 'revision': scene_revision(current_turn.record.payload)},
                   'dependent': {'path': '/'.join(address_parts(holder.record.address)), 'revision': holder.etag},
                   'reason': 'causal_action', 'certainty': 'known'}
        tx = d.store.transaction()
        tx.put(ScopeRecord(ContinuityRef('c', 'new-declaration'), 'grimoire.continuity-dependency/1', payload, owner=OWNER), None)
        tx.commit(d.handles.app)
    else:
        d.accept(pay(sequence=11))
    snapshot = {n: dict(d.store.scan().files) for n, d in pair.items()}
    monkeypatch.setattr(forks, '_commit_device', commit)
    with pytest.raises(ResolveRefused, match='changed|premise|stale'): resolve_fork(*request)
    assert snapshot == {n: dict(d.store.scan().files) for n, d in pair.items()}


def test_stale_token_operation_reuse_and_foreign_owner_refuse(pair):
    request = list(args(pair))
    request[4] = etag(b'stale')
    with pytest.raises(ResolveRefused): resolve_fork(*request)
    request = list(args(pair))
    forged = dict(request[-2]); forged[PHONE] = pair[DESKTOP].handles.app
    with pytest.raises(AuthorityError): resolve_fork(*request[:-2], forged, request[-1])
    resolve_fork(*request)
    request[5] = PHONE
    with pytest.raises(ResolveRefused): resolve_fork(*request)


@pytest.mark.parametrize('change', ['unknown', 'label', 'effect', 'calls'])
def test_refuses_nontranscript_bookkeeping_loss(pair, change):
    d = pair[PHONE]
    stored = next(iter(d.store.campaign_turns('c').records.values()))
    payload = thaw(stored.record.payload)
    turn = turn_from_payload(stored.record.address, stored.record.schema, stored.record.payload)
    source = pair[DESKTOP]
    if change == 'unknown':
        payload['future_bookkeeping'] = {'newer': True}
    elif change == 'label':
        payload = turn_to_payload(replace(turn, labels=(Label(1, 'proposed', 7, by=OWNER),)))
    elif change == 'effect':
        # Both are valid native states; source was accepting while losing device completed it.
        proposal = give(turn=turn.turn_id, scene=CHAT.chat_id)
        original_effect = TurnEffect(1, 'accepting', proposal, (), OWNER, (), 'e-actual')
        source_stored = source.store.read(turn.ref)
        source_turn = turn_from_payload(turn.ref, source_stored.record.schema, source_stored.record.payload)
        tx = source.store.transaction()
        tx.put(replace(source_stored.record, payload=turn_to_payload(replace(source_turn, effects=(original_effect,)))), source_stored.etag)
        tx.commit(source.handles.app)
        payload = turn_to_payload(replace(turn, effects=(replace(original_effect, state='accepted'),)))
    else:
        call = CallRecord('main', InferenceStage.DIRECTOR_GENERATION, None, 1, 'accepted')
        take = Take(1, 'proposed', 'generated', (), calls=())
        source_stored = source.store.read(turn.ref)
        source_turn = turn_from_payload(turn.ref, source_stored.record.schema, source_stored.record.payload)
        tx = source.store.transaction()
        tx.put(replace(source_stored.record, payload=turn_to_payload(replace(source_turn, takes=(take,)))), source_stored.etag)
        tx.commit(source.handles.app)
        # A take changes the semantic source too, so repin the accepted event to the valid source.
        event = next(iter(source.store.campaign_accepted('c').records.values()))
        ep = thaw(event.record.payload)
        ep['continuity_sources']['pins'][0]['revision'] = scene_revision(source.store.read(turn.ref).record.payload)
        tx = source.store.transaction(); tx.put(replace(event.record, payload=ep), event.etag); tx.commit(source.handles.app)
        payload = turn_to_payload(replace(turn, takes=(replace(take, calls=(call,)),)))
    tx = d.store.transaction(); tx.put(replace(stored.record, payload=payload), stored.etag); tx.commit(d.handles.app)
    before = {n: dict(d.store.scan().files) for n, d in pair.items()}
    with pytest.raises(ResolveRefused, match='bookkeeping'): resolve_fork(*args(pair))
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}


def test_divergent_offline_continuation_refuses_and_preview_marks_potential(pair):
    d = pair[PHONE]
    turn = write_turn(d.store, d.handles, number=2, text='I continue without a key.')
    stored = d.store.read(turn.ref)
    tx = d.store.transaction()
    tx.put(replace(stored.record, payload={**stored.record.payload, 'follows': 't-1.000001'}), stored.etag)
    tx.commit(d.handles.app)
    snapshot = ContinuityLibrary(d.store, d.handles.app).snapshot('c')
    root = snapshot.pins[PATH]
    impact = ContinuityLibrary(d.store, d.handles.app).preview('c', (root,), 'fork')
    assert any(i.target.path.endswith('000002.json') and i.certainty == 'potential' for i in impact.impacts)
    with pytest.raises(ResolveRefused, match='continuation|descendant'): resolve_fork(*args(pair))


def test_denied_archive_grant_and_size_cap_leave_canon_intact(pair, monkeypatch):
    request = args(pair)
    before = {n: dict(d.store.scan().files) for n, d in pair.items()}
    monkeypatch.setattr(forks, 'MAX_FORK_BYTES', 100)
    with pytest.raises(ResolveRefused, match='size|capacity|limit'): resolve_fork(*request)
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}
    monkeypatch.undo()
    # An owner identity still needs a path grant; both transactions are checked first.
    d = pair[PHONE]
    d.authority.revoke_grant('owner-0')
    d.authority.add_grant(Grant('turn-only', OWNER, frozenset({Permission.APPLY}),
                                ResourcePath.parse('campaigns/c/turns'), OWNER))
    with pytest.raises(WriteDenied): resolve_fork(*request)
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}


def test_selected_source_must_satisfy_every_accepted_pin(pair):
    # This effect was accepted against the contradictory edited fiction.
    pair[PHONE].accept(pay(sequence=12, chat=CHAT))
    request = list(args(pair))
    before = {n: dict(d.store.scan().files) for n, d in pair.items()}
    for selected in (DESKTOP, PHONE):
        request[5] = selected
        with pytest.raises(ResolveRefused, match='satisfy|review'):
            resolve_fork(*request)
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}


@pytest.mark.parametrize('mutation', ['text', 'digest', 'unknown', 'identity', 'premises', 'schema'])
def test_malformed_known_archive_fails_closed_in_all_readers(pair, mutation):
    resolve(pair)
    d = pair[DESKTOP]
    stored = d.store.read(ContinuityRef('c', 'retain-1'))
    payload = thaw(stored.record.payload)
    if mutation == 'text': payload['scenes'][0]['variants'][PHONE]['text'] = '{bad'
    elif mutation == 'digest': payload['scenes'][0]['variants'][PHONE]['before'] = etag(b'bad')
    elif mutation == 'unknown': payload['unexpected'] = True
    elif mutation == 'identity': payload['operation_id'] = 'another-operation'
    elif mutation == 'premises': payload['premises'][PHONE]['campaigns/c/turns/absent.json'] = etag(b'new')
    schema = 'future.fork/99' if mutation == 'schema' else FORK_SCHEMA
    payload['fingerprint'] = fingerprint(payload)
    # An owner's external edit is not a supported immutable transaction, but readers must
    # still diagnose its damage rather than silently treat malformed archives as history.
    path = d.store.root / 'campaigns/c/continuity/retain-1.json'
    path.write_bytes(render(scope_record_document(replace(stored.record, schema=schema, payload=payload), None)))
    assert semantic.reviews(plan_sync(pair[DESKTOP].store, pair[PHONE].store))
    library = ContinuityLibrary(d.store, d.handles.app)
    assert library.snapshot('c').problems
    with pytest.raises(ValueError): library.inspect_fork('c', 'retain-1')
    assert resolution_risk(d.store, 'campaigns/c/turns/t-2.000001.json', PHONE)


def test_native_receipt_restore_transaction_recovers_atomically(pair, tmp_path):
    request = args(pair)
    original = pair[PHONE].store.read_exact(PATH)
    source = pair[DESKTOP].store.read_exact(PATH)
    # Preserve the actual seeded native libraries, then replay their trees through FileOps.
    trees = {n: snapshot(d.store.root) for n, d in pair.items()}
    locker = ProcessLocker()
    def setup(ops, root):
        for n, tree in trees.items():
            for path, raw in tree.items():
                target = root / n / path
                if raw is None: ops.make_dirs(target)
                else: ops.write_bytes(target, raw)
    def action(ops, root):
        with LibraryStore.open(root / DESKTOP, ops=ops, locker=locker) as left, LibraryStore.open(root / PHONE, ops=ops, locker=locker) as right:
            with DurableAuthority.open(left, [OWNER], clock=fixed_clock()) as la, DurableAuthority.open(right, [OWNER], clock=fixed_clock()) as ra:
                hs = {DESKTOP: la.handle_for(OWNER, trusted=True), PHONE: ra.handle_for(OWNER, trusted=True)}
                resolve_fork(left, right, 'c', 'retain-1', request[4], DESKTOP, hs, request[-1])
    def recover(ops, root):
        for n in (DESKTOP, PHONE):
            LibraryStore.open(root / n, ops=ops, locker=locker).close()
    scenario = CrashScenario('atomic fork receipt and turn restore', setup, action, recover)
    receipt = 'phone/campaigns/c/continuity/retain-1.json'
    for path in (receipt, 'phone/' + PATH):
        for timing in ('before', 'after'):
            run = run_with_fault(scenario, [Crash(Target('replace', target=path), timing)], tmp_path / 'fault')
            assert not run.violations, run.violations
            restored = run.recovered['phone/' + PATH] == source
            assert restored == (receipt in run.recovered)
            if not restored: assert run.recovered['phone/' + PATH] == original


def test_post_sync_noop_accepts_receipt_preserving_holdings_merge(pair):
    pair[PHONE].accept(pay(sequence=13))
    request = args(pair)
    resolve_fork(*request)
    assert not sync_until_quiet(request[0], request[1], request[-2]).conflicts
    before = {n: dict(d.store.scan().files) for n, d in pair.items()}
    assert resolve_fork(*request).status == 'already_applied'
    assert len(request[0].campaign_accepted('c').records) == 2
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}


def test_existing_receipt_collision_and_missing_owner_configuration_refuse(pair):
    request = args(pair)
    d = pair[PHONE]
    tx = d.store.transaction()
    tx.put(ScopeRecord(ContinuityRef('c', 'retain-1'), 'some.other/1', {}, owner=OWNER), None)
    tx.commit(d.handles.app)
    before = {n: dict(d.store.scan().files) for n, d in pair.items()}
    with pytest.raises(ResolveRefused, match='receipt'):
        resolve_fork(*request)
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}
    request = list(request)
    request[-1] = {n: {'human:forged'} for n in pair}
    with pytest.raises(AuthorityError): resolve_fork(*request)


@pytest.mark.parametrize('mutation', ['scene', 'event'])
def test_inputs_changed_after_scene_validation_refuse_before_first_publish(pair, monkeypatch, mutation):
    request = args(pair)
    validate = forks._validate_restoration
    expected = {}
    def race(plan, scenes, source):
        validate(plan, scenes, source)
        if mutation == 'scene':
            write_turn(pair[PHONE].store, pair[PHONE].handles, text='Changed during scene validation.')
        else:
            pair[PHONE].accept(pay(sequence=14))
        expected.update({n: dict(d.store.scan().files) for n, d in pair.items()})
    monkeypatch.setattr(forks, '_validate_restoration', race)
    with pytest.raises((ResolveRefused, WriteDenied)):
        resolve_fork(*request)
    assert expected == {n: dict(d.store.scan().files) for n, d in pair.items()}
    assert all(not d.store.campaign_continuity('c').records for d in pair.values())


@pytest.mark.parametrize('principal,trusted', [(OWNER, False), ('human:other', True)])
def test_issued_but_untrusted_or_nonowner_handle_refuses(pair, principal, trusted):
    request = list(args(pair))
    request[-2] = {**request[-2], PHONE: pair[PHONE].authority.handle_for(principal, trusted=trusted)}
    before = {n: dict(d.store.scan().files) for n, d in pair.items()}
    with pytest.raises(AuthorityError): resolve_fork(*request)
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}


def test_legacy_same_chat_history_cannot_gain_proof_from_a_newer_pinned_event(pair):
    desk, phone = pair[DESKTOP], pair[PHONE]
    # Establish one agreed historical event lacking scene ancestry.
    tx = phone.store.transaction()
    tx.put_exact(PATH, desk.store.read_exact(PATH), etag(phone.store.read_exact(PATH)))
    tx.commit(phone.handles.app)
    event = next(iter(desk.store.campaign_accepted('c').records.values()))
    payload = thaw(event.record.payload)
    payload.pop('continuity_sources')
    tx = desk.store.transaction()
    tx.put(replace(event.record, payload=payload), event.etag)
    tx.commit(desk.handles.app)
    hs = {n: d.handles.app for n, d in pair.items()}
    assert not sync_until_quiet(desk.store, phone.store, hs).conflicts
    desk.accept(pay(sequence=15, chat=CHAT))
    write_turn(phone.store, phone.handles, text='Offline revision after legacy agreement.')
    before = {n: dict(d.store.scan().files) for n, d in pair.items()}
    with pytest.raises(ResolveRefused, match='uncertain|legacy'):
        resolve_fork(*args(pair))
    assert before == {n: dict(d.store.scan().files) for n, d in pair.items()}


def test_post_sync_noop_accounts_for_unrelated_planned_campaign_deletion(pair):
    path = 'campaigns/c/chats/t-3.json'
    assert pair[PHONE].store.read_exact(path) is not None
    (pair[PHONE].store.root / path).unlink()
    request = args(pair)
    resolve_fork(*request)
    assert not sync_until_quiet(request[0], request[1], request[-2]).conflicts
    assert all(d.store.read_exact(path) is None for d in pair.values())
    assert resolve_fork(*request).status == 'already_applied'
