"""Strict, versioned ledger values; errors never echo source text."""
from collections.abc import Mapping
from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Moment
from grimoire.store.errors import LedgerInvalid
from .model import MemorySelection, MemoryCorrection


def encode(value):
    common = {'operation_id': value.operation_id, 'actor': str(value.actor),
              'moment': {'sequence': value.moment.sequence, 'scene_id': value.moment.scene_id, 'turn_id': value.moment.turn_id}}
    if type(value) is MemorySelection:
        return 'selection', {**common, 'collection_id': value.collection_id, 'evidence_ids': list(value.evidence_ids)}
    if type(value) is MemoryCorrection:
        return 'correction', {**common, 'obsolete_ids': list(value.obsolete_ids), 'replacement_ids': list(value.replacement_ids), 'provenance': value.provenance}
    raise ValueError('invalid_record')


def decode(kind, data):
    try:
        common = {'operation_id', 'actor', 'moment'}
        extra = {'collection_id', 'evidence_ids'} if kind == 'selection' else {'obsolete_ids', 'replacement_ids', 'provenance'} if kind == 'correction' else None
        if extra is None or not isinstance(data, Mapping) or set(data) != common | extra:
            raise ValueError()
        m = data['moment']
        if not isinstance(m, Mapping) or set(m) != {'sequence', 'scene_id', 'turn_id'}:
            raise ValueError()
        actor, moment = InstanceRef.parse(data['actor']), Moment(m['sequence'], m['scene_id'], m['turn_id'])
        def ids(key):
            value = data[key]
            if not isinstance(value, (list, tuple)):
                raise ValueError()
            return tuple(value)
        if kind == 'selection':
            return MemorySelection(data['operation_id'], actor, data['collection_id'], ids('evidence_ids'), moment)
        return MemoryCorrection(data['operation_id'], actor, ids('obsolete_ids'), ids('replacement_ids'), moment, data['provenance'])
    except (ValueError, TypeError, KeyError):
        raise LedgerInvalid('invalid_memory_record') from None
