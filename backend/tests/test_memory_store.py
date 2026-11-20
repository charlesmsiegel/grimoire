import pytest
from grimoire.core import DIRECTOR
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.memory import MemorySelection
from grimoire.memorystore import MemoryLibrary
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore
from ledger_helpers import OWNER, open_authority
from evidence_fixtures import C, MARA, m
from test_evidencestore_scenarios import seed_actors, append_u
from store_helpers import owner_handle


def test_retry_and_reopen(tmp_path):
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            handle = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), handle)
            library = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            selection = MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8))
            library.select(selection, requested_by=DIRECTOR)
            library.select(selection, requested_by=DIRECTOR)
    with LibraryStore.open(root) as store:
        with open_authority(store) as authority:
            library = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            assert library.view(MARA, 'recap', as_of=8, requested_by=DIRECTOR).current[0].evidence.evidence_id == 'v-mara'


def test_transfer_keeps_previous_editor_material_out_of_pc_view(tmp_path):
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            owner = authority.handle_for(OWNER, trusted=True)
            controls = SovereigntyLedger(authority)
            controls.record(C, ControlChange(C, 'mara', DIRECTOR), owner)
            director = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            director.select(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)), requested_by=DIRECTOR)
            controls.record(C, ControlChange(C, 'mara', 'human:player'), owner)
            player = MemoryLibrary(store, authority, authority.handle_for('human:player', trusted=True))
            assert player.view(MARA, 'recap', as_of=8, requested_by='human:player').current == ()
            with pytest.raises(Exception):
                director.view(MARA, 'recap', as_of=8, requested_by=DIRECTOR)
            assert len(director.inspect(C, requested_by=DIRECTOR)) == 1


def test_conflicting_retry_and_foreign_evidence_are_rejected(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            handle = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), handle)
            library = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            library.select(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)), requested_by=DIRECTOR)
            with pytest.raises(ValueError, match='operation_conflict'):
                library.select(MemorySelection('sel', MARA, 'other', ('v-mara',), m(8)), requested_by=DIRECTOR)
            with pytest.raises(ValueError, match='unavailable_evidence'):
                library.select(MemorySelection('other', MARA, 'recap', ('v-ines',), m(8)), requested_by=DIRECTOR)


def test_actor_ids_remain_reserved_after_instance_deletion(tmp_path):
    from grimoire.memorystore import memory_actor_ids
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            handle = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), handle)
            MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True)).select(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)), requested_by=DIRECTOR)
        (store.root / 'campaigns' / C / 'instances' / 'character' / 'mara.json').unlink()
        assert 'mara' in memory_actor_ids(store, C)


def test_damaged_ledger_fails_closed_on_reopen(tmp_path):
    from grimoire.store.errors import LedgerBroken, LedgerInvalid
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            handle = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), handle)
            MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True)).select(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)), requested_by=DIRECTOR)
    batch = next((root / 'authority' / 'memory' / C).glob('*.json'))
    batch.write_text('{}')
    with LibraryStore.open(root) as store:
        with open_authority(store) as authority:
            with pytest.raises((LedgerBroken, LedgerInvalid)):
                MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True)).view(MARA, 'recap', as_of=8, requested_by=DIRECTOR)


def test_trusted_service_can_direct_npc_but_cannot_edit_pc(tmp_path):
    from grimoire.core import AuthorityError
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            owner = authority.handle_for(OWNER, trusted=True)
            controls = SovereigntyLedger(authority)
            controls.record(C, ControlChange(C, 'mara', DIRECTOR), owner)
            service = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            service.select(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)), requested_by=DIRECTOR)
            controls.record(C, ControlChange(C, 'mara', 'human:player'), owner)
            with pytest.raises(AuthorityError):
                service.select(MemorySelection('sel-two', MARA, 'recap', ('v-mara',), m(8)), requested_by='human:player')


def test_correction_chains_and_competing_correction(tmp_path):
    from grimoire.memory import MemoryCorrection
    from evidence_fixtures import SAM
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            handle = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'sam', DIRECTOR), handle)
            library = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            library.select(MemorySelection('sel', SAM, 'recap', ('v-sam',), m(5)), requested_by=DIRECTOR)
            correction = MemoryCorrection('fix', SAM, ('v-sam',), ('v-sam-member',), m(6), 'authored')
            library.correct(correction, requested_by=DIRECTOR)
            library.select(MemorySelection('stale', SAM, 'recap', ('v-sam',), m(7)), requested_by=DIRECTOR)
            assert [i.evidence.evidence_id for i in library.view(SAM, 'recap', as_of=7, requested_by=DIRECTOR).current] == ['v-sam-member']
            assert [i.evidence.evidence_id for i in library.view(SAM, 'recap', as_of=5, requested_by=DIRECTOR).current] == ['v-sam']
            with pytest.raises(ValueError, match='competing_correction'):
                library.correct(MemoryCorrection('fix-two', SAM, ('v-sam',), ('v-sam-member',), m(8), 'authored'), requested_by=DIRECTOR)


def test_unconfirmed_future_and_foreign_scope_evidence_are_refused(tmp_path):
    from grimoire.core import AuthorityError, InstanceRef
    from evidence_fixtures import ORRIN, SAM
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            handle = authority.handle_for(OWNER, trusted=True)
            controls = SovereigntyLedger(authority)
            for actor in (ORRIN, SAM):
                controls.record(C, ControlChange(C, actor.instance_id, DIRECTOR), handle)
            library = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            for selection in (MemorySelection('unconfirmed', ORRIN, 'recap', ('v-orrin',), m(8)),
                              MemorySelection('future', SAM, 'recap', ('v-sam-member',), m(4))):
                with pytest.raises(ValueError, match='unavailable_evidence'):
                    library.select(selection, requested_by=DIRECTOR)
            with pytest.raises(AuthorityError):
                library.select(MemorySelection('foreign', InstanceRef('other', 'character', 'sam'), 'recap', ('v-sam',), m(8)), requested_by=DIRECTOR)


def test_codec_rejects_malformed_payload_without_echoing_content():
    from grimoire.memorystore.codec import decode, encode
    from grimoire.store.errors import LedgerInvalid
    kind, payload = encode(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)))
    payload['unexpected'] = 'SECRET-TEXT'
    with pytest.raises(LedgerInvalid) as error:
        decode(kind, payload)
    assert 'SECRET-TEXT' not in str(error.value)


def test_long_existing_correction_chain_admission_avoids_recursion():
    from grimoire.memory import MemoryCorrection
    rows = [(MemoryCorrection(f'c{i}', MARA, (f'e{i}',), (f'e{i+1}',), m(i), 'authored'), DIRECTOR, OWNER) for i in range(1100)]
    # Joining an earlier uncorrected source to the long chain must validate its entire path.
    MemoryLibrary._check_record(rows, MemoryCorrection('join', MARA, ('start',), ('e0',), m(1200), 'authored'), DIRECTOR)
    with pytest.raises(ValueError, match='cyclic_correction'):
        MemoryLibrary._check_record(rows, MemoryCorrection('loop', MARA, ('e1100',), ('e0',), m(1200), 'authored'), DIRECTOR)


def test_trusted_human_cannot_claim_director_service_privileges(tmp_path):
    from grimoire.core import AuthorityError
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            owner = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), owner)
            human = MemoryLibrary(store, authority, authority.handle_for('human:unrelated', trusted=True))
            with pytest.raises(AuthorityError):
                human.select(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)), requested_by=DIRECTOR)
            with pytest.raises(AuthorityError):
                human.view(MARA, 'recap', as_of=8, requested_by=DIRECTOR)
            with pytest.raises(AuthorityError):
                human.inspect(C, requested_by=DIRECTOR)


def test_replay_rejects_director_record_authored_by_human(tmp_path):
    from grimoire.ledgers.codec import envelope
    from grimoire.memorystore.codec import encode
    from grimoire.store.errors import LedgerInvalid
    with LibraryStore.create(tmp_path / 'lib') as store:
        with open_authority(store) as authority:
            kind, value = encode(MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8)))
            authority._append(f'memory/{C}', [envelope(kind, authority.now(), editor=DIRECTOR, principal='human:unrelated', value=value)], None)
            service = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            with pytest.raises(LedgerInvalid, match='invalid_memory_ledger'):
                service.inspect(C, requested_by=DIRECTOR)
