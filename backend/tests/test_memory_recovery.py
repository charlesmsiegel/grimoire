import pytest
from grimoire.core import DIRECTOR
from grimoire.evidencestore import EvidenceLibrary
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.memory import MemorySelection
from grimoire.memorystore import MemoryLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import Crash, RootedFileOps, Target
from ledger_helpers import OWNER, open_authority
from evidence_fixtures import C, MARA, m
from test_evidencestore_scenarios import seed_actors, append_u
from store_helpers import owner_handle


@pytest.mark.parametrize('when', ['before', 'after'])
def test_crash_at_ledger_publication_reopen_and_retry(tmp_path, when):
    root = tmp_path / 'lib'
    locker = ProcessLocker()
    with LibraryStore.create(root, locker=locker) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), authority.handle_for(OWNER, trusted=True))
    selection = MemorySelection('sel', MARA, 'recap', ('v-mara',), m(8))
    ops = RootedFileOps(tmp_path, plan=[Crash(Target('replace', target='lib/authority/memory/c1/*'), when)], record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(root, ops=ops, locker=locker) as store:
            with open_authority(store) as authority:
                MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True)).select(selection, requested_by=DIRECTOR)
    with LibraryStore.open(root, locker=locker) as store:
        with open_authority(store) as authority:
            library = MemoryLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            library.select(selection, requested_by=DIRECTOR)
            assert len(library.inspect(C, requested_by=DIRECTOR)) == 1
            assert library.view(MARA, 'recap', as_of=8, requested_by=DIRECTOR).current[0].evidence.evidence_id == 'v-mara'
