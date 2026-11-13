"""Strict opaque topology payloads. Unknown fields refuse interpretation, never disappear on save."""
from collections.abc import Mapping

from grimoire.core.identity import SourceRef
from .model import Operation, Path, Place, Topology, TopologyError

SCHEMA = 'grimoire.topology/1'
OVERLAY_SCHEMA = 'grimoire.topology-overlay/1'


def fields(value, keys):
    if not isinstance(value, Mapping) or set(value) != set(keys.split()):
        raise TopologyError('unsupported_payload')
    return value


def sequence(value):
    if not isinstance(value, (list, tuple)):
        raise TopologyError('invalid_sequence')
    return value


def reference(value):
    try:
        return SourceRef.parse(value)
    except (ValueError, TypeError):
        raise TopologyError('invalid_reference') from None


def encode_path(path: Path) -> dict:
    return dict(path_id=path.path_id, source=str(path.source), target=str(path.target),
                bidirectional=path.bidirectional, requires=list(path.requires), blocked=path.blocked)


def decode_path(value) -> Path:
    v = fields(value, 'path_id source target bidirectional requires blocked')
    return Path(v['path_id'], reference(v['source']), reference(v['target']), v['bidirectional'],
                tuple(sequence(v['requires'])), v['blocked'])


def encode_topology(graph: Topology) -> dict:
    return dict(schema=SCHEMA, provenance=graph.provenance,
                places=[dict(ref=str(p.ref), aliases=list(p.aliases),
                             parent=None if p.parent is None else str(p.parent)) for p in graph.places],
                paths=[encode_path(p) for p in graph.paths])


def decode_topology(ref: SourceRef, payload: object) -> Topology:
    v = fields(payload, 'schema provenance places paths')
    if v['schema'] != SCHEMA:
        raise TopologyError('unsupported_schema')
    places = []
    for entry in sequence(v['places']):
        p = fields(entry, 'ref aliases parent')
        places.append(Place(reference(p['ref']), tuple(sequence(p['aliases'])),
                            None if p['parent'] is None else reference(p['parent'])))
    return Topology(ref, tuple(places), tuple(decode_path(p) for p in sequence(v['paths'])), v['provenance'])


def encode_operation(op: Operation) -> dict:
    return dict(operation_id=op.operation_id, path=encode_path(op.path), provenance=op.provenance)


def decode_operation(value) -> Operation:
    v = fields(value, 'operation_id path provenance')
    return Operation(v['operation_id'], decode_path(v['path']), v['provenance'])
