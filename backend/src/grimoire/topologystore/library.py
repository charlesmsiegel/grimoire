"""Native file adapter for source topology and campaign path overrides (059).

Trusted authoring service, never a worker tool. Generic definition/instance envelopes retain
native authorization, CAS, journal recovery and opaque byte sync. Configuration instances
have no actor facets. Nothing here moves characters or writes their knowledge or clocks.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256

from grimoire.core.identity import InstanceRef, ScopeRef, SourceRef
from grimoire.graph.index import require_director
from grimoire.store import LibraryStore, RecordEnvelope, RecordNotFound, StaleWrite
from grimoire.topology import Operation, Path, Topology, TopologyError, TopologyIndex
from grimoire.topology.codec import (
    OVERLAY_SCHEMA, decode_operation, decode_topology, encode_operation, encode_topology, fields,
    reference, sequence,
)
from grimoire.topology.model import MAX_OPERATIONS, pinned


def overlay_ref(graph: SourceRef, campaign_id: str) -> InstanceRef:
    pinned(graph)
    if graph.kind != 'topology':
        raise TopologyError('invalid_topology_ref')
    return InstanceRef(campaign_id, 'topology', 'topology-' + sha256(str(graph).encode()).hexdigest())


@dataclass(frozen=True)
class TopologyView:
    index: TopologyIndex
    graph_etag: str
    overlay_etag: str | None


@dataclass(frozen=True)
class TopologyCommit:
    address: SourceRef | InstanceRef
    etag: str
    replayed: bool = False


@dataclass(frozen=True)
class _Overlay:
    operations: tuple[Operation, ...]
    envelope: RecordEnvelope | None
    etag: str | None


def _apply(graph: Topology, operations: tuple[Operation, ...]) -> Topology:
    if len(operations) > MAX_OPERATIONS:
        raise TopologyError('operation_limit')
    if len({o.operation_id for o in operations}) != len(operations):
        raise TopologyError('duplicate_operation')
    paths = {p.path_id: p for p in graph.paths}
    places = {p.ref for p in graph.places}
    for operation in operations:
        if operation.path.path_id not in paths:
            raise TopologyError('unknown_path')
        # Validate every historical operation, not only the last: a damaged receipt cannot
        # make a retry silently claim success, even when a later operation masks its path.
        if operation.path.source not in places or operation.path.target not in places:
            raise TopologyError('unknown_endpoint')
        paths[operation.path.path_id] = operation.path
    return replace(graph, paths=tuple(paths.values()))


@dataclass(frozen=True)
class TopologyChange:
    _library: 'TopologyLibrary'
    _record: RecordEnvelope | None
    _expected: str | None
    _graph: Topology
    _graph_etag: str | None
    _dependencies: tuple[tuple[SourceRef, str], ...]
    _campaign: str | None = None
    _scope_etag: str | None = None
    _operation: Operation | None = None

    def preview(self) -> dict[str, bytes]:
        if self._record is None:
            return {}
        transaction = self._library.store.transaction()
        transaction.put(self._record, self._expected)
        return transaction.preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> TopologyCommit:
        lib, graph = self._library, self._graph
        store = lib.store
        with store.lock:
            if self._campaign is not None:
                # Re-read before retry recognition: a corrupt/missing dependency never falls
                # back to source paths, and an old retry never resurrects an overwritten path.
                current_graph, etag = lib._graph(graph.ref)
                if etag != self._graph_etag:
                    raise StaleWrite([(str(graph.ref), 'topology changed')])
                current_scope = lib._campaign(current_graph, self._campaign)
                overlay = lib._overlay(current_graph, etag, self._campaign)
                old = next((o for o in overlay.operations if o.operation_id == self._operation.operation_id), None)
                if old is not None:
                    if old != self._operation:
                        raise TopologyError('operation_conflict')
                    return TopologyCommit(overlay_ref(graph.ref, self._campaign), overlay.etag, True)
                if current_scope != self._scope_etag or overlay.etag != self._expected:
                    raise StaleWrite([(str(overlay_ref(graph.ref, self._campaign)), 'topology override changed')])
                if self._record is None:
                    raise StaleWrite([(str(overlay_ref(graph.ref, self._campaign)), 'receipt disappeared')])
            if lib._dependencies(graph) != self._dependencies:
                raise StaleWrite([(str(graph.ref), 'place dependencies changed')])
            if self._record is None:
                raise TopologyError('missing_change')
            transaction = store.transaction()
            transaction.put(self._record, self._expected)
            transaction.commit(lib.handle, approvals)
            return TopologyCommit(self._record.address, store.read(self._record.address).etag)


class TopologyLibrary:
    def __init__(self, store: LibraryStore, handle: object):
        self.store = store
        self.handle = handle

    def _graph(self, ref: SourceRef) -> tuple[Topology, str]:
        pinned(ref)
        if ref.kind != 'topology':
            raise TopologyError('invalid_topology_ref')
        found = self.store.check_definition(ref)
        if isinstance(found, str):
            raise TopologyError('unavailable_topology')
        return decode_topology(ref, found.envelope.payload), found.etag

    def _dependencies(self, graph: Topology) -> tuple[tuple[SourceRef, str], ...]:
        pins = []
        with self.store.read_pass():
            for place in graph.places:
                found = self.store.check_definition(place.ref)
                pins.append((place.ref, found if isinstance(found, str) else found.etag))
        return tuple(pins)

    def _campaign(self, graph: Topology, campaign: str) -> str:
        address = overlay_ref(graph.ref, campaign)
        try:
            scope = self.store.read(ScopeRef(campaign))
        except RecordNotFound:
            raise TopologyError('missing_campaign') from None
        # The ID namespace is shared across kinds. Refuse, never allocate a replacement
        # address: two config owners for one graph would make blocks non-deterministic.
        for ref in self.store.campaign_instances(campaign):
            if ref.instance_id == address.instance_id and ref != address:
                raise TopologyError('instance_id_conflict')
        return scope.etag

    def _overlay(self, graph: Topology, graph_etag: str, campaign: str) -> _Overlay:
        address = overlay_ref(graph.ref, campaign)
        try:
            stored = self.store.read(address)
        except RecordNotFound:
            return _Overlay((), None, None)
        envelope = stored.envelope
        v = fields(envelope.payload, 'schema graph graph_etag operations')
        if v['schema'] != OVERLAY_SCHEMA:
            raise TopologyError('unsupported_schema')
        if reference(v['graph']) != graph.ref or envelope.base != str(graph.ref) \
                or envelope.derived_from != str(graph.ref):
            raise TopologyError('overlay_mismatch')
        if v['graph_etag'] != graph_etag or envelope.base_etag != graph_etag:
            raise TopologyError('source_changed')
        operations = tuple(decode_operation(v) for v in sequence(v['operations']))
        _apply(graph, operations)
        return _Overlay(operations, envelope, stored.etag)

    def publish(self, graph: Topology, *, requested_by: object) -> TopologyChange:
        """Preview a create-only source revision. Native manifest/head rules still apply."""
        require_director(requested_by)
        if type(graph) is not Topology:
            raise TopologyError('invalid_topology')
        with self.store.lock:
            dependencies = self._dependencies(graph)
            record = RecordEnvelope(graph.ref, encode_topology(graph))
            return TopologyChange(self, record, None, graph, None, dependencies)

    def view(self, ref: SourceRef, *, requested_by: object, campaign_id: str | None = None) -> TopologyView:
        require_director(requested_by)
        with self.store.read_pass():
            graph, etag = self._graph(ref)
            overlay_etag = None
            if campaign_id is not None:
                self._campaign(graph, campaign_id)
                overlay = self._overlay(graph, etag, campaign_id)
                graph = _apply(graph, overlay.operations)
                overlay_etag = overlay.etag
            names = {}
            for place in graph.places:
                found = self.store.check_definition(place.ref)
                if not isinstance(found, str):
                    names[place.ref] = found.envelope.name or ''
            return TopologyView(TopologyIndex(graph, names), etag, overlay_etag)

    def change_path(self, ref: SourceRef, campaign_id: str, path: Path, *, operation_id: str,
                    provenance: str, expected: str | None, requested_by: object) -> TopologyChange:
        """Replace one existing source path in one campaign; an operation id is retained forever.

        Retry the same id/body to retrieve success after a lost response. The cap refuses further
        operations instead of deleting receipts; source rebase/compaction are separate consumers.
        """
        require_director(requested_by)
        operation = Operation(operation_id, path, provenance)
        with self.store.lock:
            graph, etag = self._graph(ref)
            scope_etag = self._campaign(graph, campaign_id)
            overlay = self._overlay(graph, etag, campaign_id)
            old = next((o for o in overlay.operations if o.operation_id == operation_id), None)
            dependencies = self._dependencies(graph)
            if old is not None:
                if old != operation:
                    raise TopologyError('operation_conflict')
                return TopologyChange(self, None, overlay.etag, graph, etag, dependencies,
                                      campaign_id, scope_etag, operation)
            if overlay.etag != expected:
                raise StaleWrite([(str(overlay_ref(ref, campaign_id)), 'topology override changed')])
            operations = (*overlay.operations, operation)
            _apply(graph, operations)
            payload = dict(schema=OVERLAY_SCHEMA, graph=str(ref), graph_etag=etag,
                           operations=[encode_operation(o) for o in operations])
            # Keep opaque envelope extensions exactly. Strict payload parsing above refuses
            # unknown future fields; it never rewrites them into this older schema.
            record = (replace(overlay.envelope, payload=payload) if overlay.envelope is not None else
                      RecordEnvelope(overlay_ref(ref, campaign_id), payload, derived_from=str(ref),
                                     base=str(ref), base_etag=etag))
            return TopologyChange(self, record, expected, graph, etag, dependencies,
                                  campaign_id, scope_etag, operation)
