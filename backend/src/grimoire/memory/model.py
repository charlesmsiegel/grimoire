"""Typed memory editorial operations and disposable evidence views."""
from dataclasses import dataclass
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.core.perspective import KnownEvidence


def _ids(ids):
    if type(ids) is not tuple or not 1 <= len(ids) <= 500 or len(set(ids)) != len(ids):
        raise ValueError('invalid_evidence_ids')
    for value in ids:
        validate_segment(value, 'evidence_id')


def _common(operation_id, actor, moment):
    validate_segment(operation_id, 'operation_id')
    if type(actor) is not InstanceRef or actor.kind != 'character' or type(moment) is not Moment:
        raise ValueError('invalid_record')


@dataclass(frozen=True, slots=True)
class MemorySelection:
    operation_id: str
    actor: InstanceRef
    collection_id: str
    evidence_ids: tuple[str, ...]
    moment: Moment

    def __post_init__(self):
        _common(self.operation_id, self.actor, self.moment)
        validate_segment(self.collection_id, 'collection_id')
        _ids(self.evidence_ids)


@dataclass(frozen=True, slots=True)
class MemoryCorrection:
    operation_id: str
    actor: InstanceRef
    obsolete_ids: tuple[str, ...]
    replacement_ids: tuple[str, ...]
    moment: Moment
    provenance: str

    def __post_init__(self):
        _common(self.operation_id, self.actor, self.moment)
        _ids(self.obsolete_ids)
        _ids(self.replacement_ids)
        if set(self.obsolete_ids) & set(self.replacement_ids) or type(self.provenance) is not str or not self.provenance.strip():
            raise ValueError('invalid_correction')


@dataclass(frozen=True, slots=True)
class MemoryItem:
    evidence: KnownEvidence
    replacement_ids: tuple[str, ...] = ()
    selection_ids: tuple[str, ...] = ()
    correction_id: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryView:
    actor: InstanceRef
    collection_id: str
    as_of: int
    current: tuple[MemoryItem, ...]
    history: tuple[MemoryItem, ...]
    unavailable: tuple[str, ...]
    omitted: int
