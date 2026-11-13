"""Synthetic four-place source topology; names are not identities."""
from grimoire.core.identity import ScopeRef, SourceRef
from grimoire.store import RecordEnvelope, ScopeRecord, SourceManifest
from store_helpers import OWNER, owner_handle
from grimoire.topology import Path, Place, Topology

GRAPH = SourceRef('world:realm', 'topology', 'roads', 'r1')
TOWN, GATE, WOOD, PORT = tuple(SourceRef('world:realm', 'location', x, 'r1')
                              for x in ('town', 'gate', 'wood', 'port'))
OTHER = SourceRef('import:other', 'location', 'town', 'r1')
NAMES = {TOWN: 'Saltmarch', GATE: 'Gate', WOOD: 'Wood', PORT: 'Port'}


def topology():
    return Topology(GRAPH, (Place(TOWN, ('Town',)), Place(GATE, parent=TOWN),
                            Place(WOOD), Place(PORT)),
                    (Path('road', TOWN, GATE, bidirectional=True),
                     Path('bridge', GATE, PORT), Path('trail', TOWN, WOOD),
                     Path('ferry', WOOD, PORT, requires=('ticket',))), 'human:owner')


def seed(store):
    tx = store.transaction()
    tx.put(SourceManifest('world:realm', OWNER), None)
    tx.put(SourceManifest('import:other', OWNER), None)
    for ref, name in {**NAMES, OTHER: 'Saltmarch'}.items():
        tx.put(RecordEnvelope(ref, {}, name=name), None)
    for campaign in ('a', 'b'):
        tx.put(ScopeRecord(ScopeRef(campaign), 'grimoire.scope/0', {}), None)
    tx.commit(owner_handle())
