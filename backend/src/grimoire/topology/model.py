"""Source topology (059), independent of scene staging and actor knowledge."""
from dataclasses import dataclass

from grimoire.core.identity import SourceRef, validate_segment

MAX_PLACES = 256
MAX_PATHS = 1024
MAX_OPERATIONS = 1024


class TopologyError(ValueError):
    """A safe machine-readable refusal; never embeds authored content."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def text(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise TopologyError('invalid_text')


def pinned(ref: object) -> None:
    if type(ref) is not SourceRef or not ref.pinned:
        raise TopologyError('pinned_reference_required')


def tokens(value: object) -> None:
    if type(value) is not tuple or len(value) > 64:
        raise TopologyError('invalid_conditions')
    for token in value:
        try:
            validate_segment(token, 'condition')
        except ValueError:
            raise TopologyError('invalid_conditions') from None
    if len(set(value)) != len(value):
        raise TopologyError('duplicate_condition')


@dataclass(frozen=True)
class Place:
    ref: SourceRef
    aliases: tuple[str, ...] = ()
    parent: SourceRef | None = None

    def __post_init__(self):
        pinned(self.ref)
        if type(self.aliases) is not tuple or len(self.aliases) > 64:
            raise TopologyError('invalid_aliases')
        for alias in self.aliases:
            text(alias)
        if self.parent is not None:
            pinned(self.parent)


@dataclass(frozen=True)
class Path:
    path_id: str
    source: SourceRef
    target: SourceRef
    bidirectional: bool = False
    requires: tuple[str, ...] = ()
    blocked: bool = False

    def __post_init__(self):
        try:
            validate_segment(self.path_id, 'path_id')
        except ValueError:
            raise TopologyError('invalid_path_id') from None
        pinned(self.source)
        pinned(self.target)
        if self.source == self.target:
            raise TopologyError('self_path')
        if type(self.bidirectional) is not bool or type(self.blocked) is not bool:
            raise TopologyError('invalid_path_flag')
        tokens(self.requires)


@dataclass(frozen=True)
class Topology:
    ref: SourceRef
    places: tuple[Place, ...]
    paths: tuple[Path, ...]
    provenance: str

    def __post_init__(self):
        pinned(self.ref)
        if self.ref.kind != 'topology':
            raise TopologyError('invalid_topology_ref')
        text(self.provenance)
        if type(self.places) is not tuple or not 1 <= len(self.places) <= MAX_PLACES \
                or any(type(p) is not Place for p in self.places):
            raise TopologyError('invalid_places')
        if type(self.paths) is not tuple or len(self.paths) > MAX_PATHS \
                or any(type(p) is not Path for p in self.paths):
            raise TopologyError('invalid_paths')
        refs = {p.ref for p in self.places}
        if len({p.ref.entity_key for p in self.places}) != len(self.places):
            raise TopologyError('duplicate_place')
        if self.ref.entity_key in {p.ref.entity_key for p in self.places}:
            raise TopologyError('topology_is_not_place')
        if len({p.path_id for p in self.paths}) != len(self.paths):
            raise TopologyError('duplicate_path')
        for path in self.paths:
            if path.source not in refs or path.target not in refs:
                raise TopologyError('unknown_endpoint')
        parents = {p.ref: p.parent for p in self.places}
        for place in self.places:
            seen = {place.ref}
            parent = place.parent
            while parent is not None:
                if parent not in refs:
                    raise TopologyError('unknown_parent')
                if parent in seen:
                    raise TopologyError('containment_cycle')
                seen.add(parent)
                parent = parents[parent]


@dataclass(frozen=True)
class Operation:
    operation_id: str
    path: Path
    provenance: str

    def __post_init__(self):
        try:
            validate_segment(self.operation_id, 'operation_id')
        except ValueError:
            raise TopologyError('invalid_operation_id') from None
        if type(self.path) is not Path:
            raise TopologyError('invalid_path')
        text(self.provenance)
