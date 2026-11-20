"""Catalog revision and head publication remain atomic across target-replacement crashes."""
from portrayal_fixtures import manifest
from store_helpers import drop_empty_dirs, owner_handle

from grimoire.catalogs import Catalog, publish_catalog
from grimoire.core.identity import SourceRef
from grimoire.store import LibraryStore, ProcessLocker, RecordEnvelope
from grimoire.testing import Crash, CrashScenario, Target, atomic_outcome, idempotent_recovery, run_plans

SOURCE = 'catalog:shared'
REF = SourceRef(SOURCE, 'catalog', 'cast', 'r1')
MARA = SourceRef(SOURCE, 'character', 'mara', 'r1')


def test_catalog_publication_recovers_as_whole_revision_and_head(tmp_path):
    locker = ProcessLocker()

    def setup(ops, root):
        with LibraryStore.create(root / 'lib', ops=ops, locker=locker) as store:
            tx = store.transaction()
            tx.put(manifest(SOURCE), None)
            tx.put(RecordEnvelope(MARA, {}, name='Mara'), None)
            tx.commit(owner_handle())

    def action(ops, root):
        with LibraryStore.open(root / 'lib', ops=ops, locker=locker) as store:
            publish_catalog(store, owner_handle(), Catalog(REF, (MARA,)),
                            expected=store.read_source(SOURCE).etag)

    def recover(ops, root):
        LibraryStore.open(root / 'lib', ops=ops, locker=locker).close()

    scenario = CrashScenario('056 catalog publication', setup, action, recover,
                             invariants=(atomic_outcome(), idempotent_recovery()), normalize=drop_empty_dirs)
    targets = ('lib/sources/catalog/shared/_source/manifest.json',
               'lib/sources/catalog/shared/_source/records/catalog/cast/r1.json')
    plans = [[Crash(Target('replace', target=target), when)] for target in targets for when in ('before', 'after')]
    report = run_plans(scenario, plans, tmp_path)
    report.assert_ok()
