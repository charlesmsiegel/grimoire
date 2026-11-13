import pytest
from grimoire.core import DIRECTOR, Event
from grimoire.evidencestore import EvidenceLibrary
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
            original = EvidenceLibrary.append
            def interrupted(self, *args, **kwargs):
                if after:
                    original(self, *args, **kwargs).commit()
                raise OSError('simulated crash')
            with monkeypatch.context() as patch:
                patch.setattr(EvidenceLibrary, 'append', interrupted)
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
            original = EvidenceLibrary.append
            def interrupted(self, *args, **kwargs):
                original(self, *args, **kwargs).commit()
                raise OSError('crash')
            with monkeypatch.context() as patch:
                patch.setattr(EvidenceLibrary, 'append', interrupted)
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
                patch.setattr(EvidenceLibrary, 'append', interrupted)
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
                patch.setattr(EvidenceLibrary, 'append', interrupted)
                with pytest.raises(OSError):
                    lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
        (store.root / 'campaigns' / C / 'instances' / 'character' / 'ines.json').unlink()
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            with pytest.raises(Exception, match='unknown_actor'):
                service(store, a).enqueue(rumor(), moment=m(10), requested_by=DIRECTOR)
