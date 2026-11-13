import pytest
from grimoire.core import DIRECTOR, Event
from grimoire.evidencestore import EvidenceLibrary
from grimoire.evidencestore.library import KnowledgeChange
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore
from grimoire.rumorstore import RumorLibrary
from ledger_helpers import OWNER, open_authority
from evidence_fixtures import C, m
from store_helpers import owner_handle
from test_evidencestore_scenarios import seed_actors, append_u
from test_rumor_store import rumor, ALL


def service(store, authority):
    return RumorLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))

@pytest.mark.parametrize('phase', ['enqueue', 'delivery'])
@pytest.mark.parametrize('after', [False, True])
def test_disk_recovery_around_evidence_append(tmp_path, monkeypatch, phase, after):
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = service(store, a)
            if phase == 'delivery':
                lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            original = KnowledgeChange.commit
            def interrupted(self, *args, **kwargs):
                if after:
                    original(self, *args, **kwargs)
                raise OSError('simulated crash')
            with monkeypatch.context() as patch:
                patch.setattr(KnowledgeChange, 'commit', interrupted)
                with pytest.raises(OSError):
                    if phase == 'enqueue':
                        lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
                    else:
                        lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            ev = EvidenceLibrary(store, a.handle_for('app:adjudicator', trusted=True))
            ev.append(C, [Event(C, 'unrelated', 'bell', m(20), 'authored')]).commit()
            lib = service(store, a)
            if phase == 'enqueue':
                lib.enqueue(rumor(), moment=m(21), requested_by=DIRECTOR)
            else:
                lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(21), conditions=ALL, requested_by=DIRECTOR)
            rows, _, _ = lib.trace(C, requested_by=DIRECTOR)
            row = rows['news']
            assert row['enqueued'] and (row['delivered'] if phase == 'delivery' else True)
            assert [x.sequence for x in row['attempts'][phase]] == ([9 if phase == 'enqueue' else 10] if after else [9 if phase == 'enqueue' else 10, 21])
            knowledge = ev.read(C, requested_by=DIRECTOR).knowledge
            assert sum(p.text == 'a distorted account' for p in knowledge.propositions) == 1
            assert sum(e.evidence_id.startswith('rumor-') for e in knowledge.evidence) == (phase == 'delivery')


def test_committed_delivery_reconciles_even_when_route_is_now_blocked(tmp_path, monkeypatch):
    from test_rumor_store import BLOCK
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = service(store, a)
            lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            original = KnowledgeChange.commit
            def interrupted(self, *args, **kwargs):
                original(self, *args, **kwargs)
                raise OSError('crash')
            with monkeypatch.context() as patch:
                patch.setattr(KnowledgeChange, 'commit', interrupted)
                with pytest.raises(OSError):
                    lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            lib = service(store, a)
            result = lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(11), conditions=BLOCK, requested_by=DIRECTOR)
            assert result['pending'] == 0


def test_conflicting_existing_output_fails_closed(tmp_path, monkeypatch):
    from grimoire.core import Proposition
    from grimoire.rumorstore.library import _id
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = service(store, a)
            with monkeypatch.context() as patch:
                def interrupted(*args, **kwargs):
                    raise OSError('crash')
                patch.setattr(KnowledgeChange, 'commit', interrupted)
                with pytest.raises(OSError):
                    lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            EvidenceLibrary(store, a.handle_for('app:adjudicator', trusted=True)).append(C, [Proposition(C, _id(C, 'news', 'proposition'), 'different', m(9), 'authored')]).commit()
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            with pytest.raises(ValueError, match='evidence_conflict'):
                service(store, a).enqueue(rumor(), moment=m(10), requested_by=DIRECTOR)


def test_staged_request_refuses_deleted_recipient(tmp_path, monkeypatch):
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = service(store, a)
            with monkeypatch.context() as patch:
                def interrupted(*args, **kwargs):
                    raise OSError('crash')
                patch.setattr(KnowledgeChange, 'commit', interrupted)
                with pytest.raises(OSError):
                    lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
        (store.root / 'campaigns' / C / 'instances' / 'character' / 'ines.json').unlink()
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            with pytest.raises(Exception, match='unknown_actor'):
                service(store, a).enqueue(rumor(), moment=m(10), requested_by=DIRECTOR)


@pytest.mark.parametrize('phase,kind', [('enqueue', 'request'), ('enqueue', 'attempt'), ('enqueue', 'complete'), ('delivery', 'attempt'), ('delivery', 'complete')])
@pytest.mark.parametrize('after', [False, True])
def test_disk_recovery_at_authority_journal_boundaries(tmp_path, monkeypatch, phase, kind, after):
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = service(store, a)
            if phase == 'delivery':
                lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            original = RumorLibrary._write
            def interrupted(self, campaign, head, entry_kind, **fields):
                matches = entry_kind == kind and (kind == 'request' or fields.get('phase') == phase)
                if matches:
                    if after:
                        original(self, campaign, head, entry_kind, **fields)
                    raise OSError('crash')
                return original(self, campaign, head, entry_kind, **fields)
            with monkeypatch.context() as patch:
                patch.setattr(RumorLibrary, '_write', interrupted)
                with pytest.raises(OSError):
                    if phase == 'enqueue':
                        lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
                    else:
                        lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            ev = EvidenceLibrary(store, a.handle_for('app:adjudicator', trusted=True))
            ev.append(C, [Event(C, 'journal-interloper', 'bell', m(20), 'authored')]).commit()
            lib = service(store, a)
            if phase == 'enqueue':
                lib.enqueue(rumor(), moment=m(21), requested_by=DIRECTOR)
            else:
                lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(21), conditions=ALL, requested_by=DIRECTOR)
            row = lib.trace(C, requested_by=DIRECTOR)[0]['news']
            assert row['enqueued'] and (row['delivered'] if phase == 'delivery' else True)
            knowledge = ev.read(C, requested_by=DIRECTOR).knowledge
            assert sum(p.text == 'a distorted account' for p in knowledge.propositions) == 1
            assert sum(e.evidence_id.startswith('rumor-') for e in knowledge.evidence) == (phase == 'delivery')


def test_invalid_initial_attempts_do_not_poison_request_or_delivery(tmp_path):
    from dataclasses import replace
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = service(store, a)
            with pytest.raises(ValueError):
                lib.enqueue(replace(rumor(), text='x' * 20001), moment=m(9), requested_by=DIRECTOR)
            with pytest.raises(ValueError):
                lib.enqueue(rumor(), moment=m(2_000_000), requested_by=DIRECTOR)
            assert lib.trace(C, requested_by=DIRECTOR)[0] == {}
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            lib = service(store, a)
            assert lib.trace(C, requested_by=DIRECTOR)[0] == {}
            lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            with pytest.raises(ValueError):
                lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(2_000_000), conditions=ALL, requested_by=DIRECTOR)
            assert lib.trace(C, requested_by=DIRECTOR)[0]['news']['attempts']['delivery'] == []
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            lib = service(store, a)
            assert lib.trace(C, requested_by=DIRECTOR)[0]['news']['attempts']['delivery'] == []
            lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
            assert lib.trace(C, requested_by=DIRECTOR)[0]['news']['delivered']
            lib.enqueue(replace(rumor(), communication_id='maximum', text='x' * 20000), moment=m(11), requested_by=DIRECTOR)
            assert lib.trace(C, requested_by=DIRECTOR)[0]['maximum']['enqueued']


@pytest.mark.parametrize('phase,target,nth', [('enqueue', 'ledger', 1), ('enqueue', 'ledger', 2), ('enqueue', 'ledger', 3), ('enqueue', 'evidence', 1), ('delivery', 'ledger', 2), ('delivery', 'ledger', 3), ('delivery', 'evidence', 1)])
@pytest.mark.parametrize('when', ['before', 'after'])
def test_real_file_crash_reopens_and_reconciles(tmp_path, phase, target, nth, when):
    from grimoire.store import ProcessLocker
    from grimoire.testing import Crash, RootedFileOps, SimulatedCrash, Target
    root = tmp_path / 'lib'
    locker = ProcessLocker()
    with LibraryStore.create(root, locker=locker) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            if phase == 'delivery':
                service(store, a).enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
    family = 'proposition' if phase == 'enqueue' else 'evidence'
    pattern = 'lib/authority/rumors/c1/*' if target == 'ledger' else f'lib/campaigns/c1/evidence/{family}/*'
    ops = RootedFileOps(tmp_path, plan=[Crash(Target('replace', target=pattern, nth=nth), when)], record=False)
    with pytest.raises(SimulatedCrash):
        with LibraryStore.open(root, ops=ops, locker=locker) as store:
            with open_authority(store) as a:
                if phase == 'enqueue':
                    service(store, a).enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
                else:
                    service(store, a).advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
    with LibraryStore.open(root, locker=locker) as store:
        with open_authority(store) as a:
            ev = EvidenceLibrary(store, a.handle_for('app:adjudicator', trusted=True))
            ev.append(C, [Event(C, 'intervening', 'bell', m(20), 'authored')]).commit()
            lib = service(store, a)
            if phase == 'enqueue':
                lib.enqueue(rumor(), moment=m(21), requested_by=DIRECTOR)
            else:
                lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(21), conditions=ALL, requested_by=DIRECTOR)
            row = lib.trace(C, requested_by=DIRECTOR)[0]['news']
            assert row['enqueued'] and (row['delivered'] if phase == 'delivery' else True)
            knowledge = ev.read(C, requested_by=DIRECTOR)
            assert not knowledge.quarantined and not knowledge.problems
            assert sum(p.text == 'a distorted account' for p in knowledge.knowledge.propositions) == 1
            assert sum(e.evidence_id.startswith('rumor-') for e in knowledge.knowledge.evidence) == (phase == 'delivery')
