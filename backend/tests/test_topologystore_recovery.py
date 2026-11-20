"""A committed path override survives interruption; retry never duplicates its receipt."""
from dataclasses import replace

import pytest

from grimoire.core import DIRECTOR
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.topologystore import TopologyLibrary, overlay_ref
from grimoire.testing import Crash, Fail, CrashScenario, Target, run_plans, atomic_outcome, idempotent_recovery, untouched
from grimoire.testing.fileops import snapshot
from store_helpers import drop_empty_dirs, owner_handle
from topology_fixtures import GRAPH, TOWN, PORT, topology, seed


@pytest.mark.parametrize('plan', [
    [Crash(Target('replace', target='lib/.txn/journal.json'), 'after')],
    [Crash(Target('replace', target='lib/campaigns/a/instances/topology/*.json'), 'after')],
    [Fail(Target('replace', target='lib/campaigns/a/instances/topology/*.json'))],
])
def test_override_commit_crash_and_io_failure_retry(tmp_path, plan):
    template = tmp_path / 'template'
    template.mkdir()
    with LibraryStore.create(template / 'lib') as store:
        seed(store)
        TopologyLibrary(store, owner_handle()).publish(topology(), requested_by=DIRECTOR).commit()
    initial = snapshot(template)
    locker = ProcessLocker()

    def setup(ops, root):
        for key, data in initial.items():
            if data is None:
                ops.make_dirs(root / key)
            else:
                ops.write_bytes(root / key, data)

    def action(ops, root):
        with LibraryStore.open(root / 'lib', ops=ops, locker=locker) as store:
            lib = TopologyLibrary(store, owner_handle())
            lib.change_path(GRAPH, 'a', replace(topology().paths[0], blocked=True), operation_id='block',
                            provenance='human:owner', expected=None, requested_by=DIRECTOR).commit()

    def recover(ops, root):
        with LibraryStore.open(root / 'lib', ops=ops, locker=locker):
            pass

    scenario = CrashScenario('topology block', setup, action, recover,
                             invariants=(atomic_outcome(), idempotent_recovery(), untouched('lib/sources/*')),
                             normalize=drop_empty_dirs)
    report = run_plans(scenario, [plan], tmp_path / 'runs')
    report.assert_ok()
    root = tmp_path / 'runs' / 'root'
    action(None, root)
    action(None, root)
    with LibraryStore.open(root / 'lib', locker=locker) as store:
        view = TopologyLibrary(store, owner_handle()).view(GRAPH, campaign_id='a', requested_by=DIRECTOR)
        assert view.index.route(TOWN, PORT, requested_by=DIRECTOR) is None
        operations = store.read(overlay_ref(GRAPH, 'a')).envelope.payload['operations']
        assert [op['operation_id'] for op in operations] == ['block']
