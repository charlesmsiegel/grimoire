"""Reference-only authored catalogs (056); availability is not actor knowledge."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.core.identity import SourceRef
from grimoire.core.snapshot import SelectedSource, SourceSelection
from grimoire.store import RecordEnvelope, StoredDefinition

SCHEMA = 'grimoire.catalog/1'
MAX_EDGES = 20_000
MAX_ENTRIES = 10_000
MAX_NODES = 128
MAX_DEPTH = 16
MAX_ROOTS = 32
MAX_SELECTION = 32


class CatalogError(ValueError):
    def __init__(self, reason: str, ref: SourceRef | None = None):
        self.reason, self.ref = reason, ref
        super().__init__(reason if ref is None else f'{reason}: {ref}')


def collection(value: object, limit: int, reason: str = 'invalid_catalog') -> tuple:
    if not isinstance(value, (list, tuple)):
        raise CatalogError(reason)
    if len(value) > limit:
        raise CatalogError('limit_exceeded')
    return tuple(value)


def pinned(ref: object) -> SourceRef:
    if not isinstance(ref, SourceRef):
        raise CatalogError('invalid_reference')
    if not ref.pinned:
        raise CatalogError('unpinned_reference', ref)
    return ref


def catalog_ref(ref: object) -> SourceRef:
    ref = pinned(ref)
    if ref.kind != 'catalog':
        raise CatalogError('invalid_catalog_reference', ref)
    return ref


@dataclass(frozen=True)
class Catalog:
    ref: SourceRef
    members: tuple[SourceRef, ...] = ()
    dependencies: tuple[SourceRef, ...] = ()
    exclusions: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        catalog_ref(self.ref)
        for field in ('members', 'dependencies', 'exclusions'):
            values = collection(getattr(self, field), MAX_EDGES)
            seen: dict[tuple[str, str, str], SourceRef] = {}
            for ref in values:
                if not isinstance(ref, SourceRef):
                    raise CatalogError('invalid_reference')
                if field == 'exclusions':
                    if ref.pinned:
                        raise CatalogError('pinned_exclusion', ref)
                    if ref.kind == 'catalog':
                        raise CatalogError('invalid_exclusion', ref)
                else:
                    pinned(ref)
                    if (ref.kind == 'catalog') != (field == 'dependencies'):
                        raise CatalogError('invalid_dependency' if field == 'dependencies' else 'invalid_member', ref)
                if ref.entity_key in seen:
                    reason = 'duplicate_reference' if seen[ref.entity_key] == ref else 'conflicting_revision'
                    raise CatalogError(reason, ref)
                seen[ref.entity_key] = ref
            object.__setattr__(self, field, values)
        if len(self.members) + len(self.dependencies) + len(self.exclusions) > MAX_EDGES:
            raise CatalogError('limit_exceeded', self.ref)


def catalog_envelope(catalog: Catalog, name: str | None = None) -> RecordEnvelope:
    if name is not None and (not isinstance(name, str) or len(name) > 256):
        raise CatalogError('invalid_name', catalog.ref)
    return RecordEnvelope(catalog.ref, {key: [str(ref) for ref in getattr(catalog, key)]
                                       for key in ('members', 'dependencies', 'exclusions')},
                          name=name, extra={'schema': SCHEMA})


def catalog_from_record(stored: StoredDefinition) -> Catalog:
    catalog_ref(stored.ref)
    envelope = stored.envelope
    if envelope.extra.get('schema') != SCHEMA:
        raise CatalogError('unsupported_schema', stored.ref)
    payload = envelope.payload
    keys = ('members', 'dependencies', 'exclusions')
    if not isinstance(payload, Mapping) or set(payload) != set(keys):
        raise CatalogError('invalid_catalog', stored.ref)
    decoded = {}
    for key in keys:
        raw = collection(payload[key], MAX_EDGES)
        try:
            decoded[key] = tuple(SourceRef.parse(value) for value in raw)
        except (ValueError, TypeError):
            raise CatalogError('invalid_reference', stored.ref) from None
    return Catalog(stored.ref, **decoded)


@dataclass(frozen=True)
class CatalogOrigin:
    ref: SourceRef
    etag: str


@dataclass(frozen=True)
class CatalogEntry:
    ref: SourceRef
    name: str
    etag: str
    origins: tuple[CatalogOrigin, ...]


@dataclass(frozen=True)
class CatalogView:
    """Disposable availability and provenance from one load; never a prompt or authorization token."""
    entries: tuple[CatalogEntry, ...]
    basis: tuple[CatalogOrigin, ...]

    def search(self, query: str = '', limit: int = 20) -> tuple[CatalogEntry, ...]:
        if not isinstance(query, str) or len(query) > 256 or type(limit) is not int or not 1 <= limit <= 100:
            raise CatalogError('invalid_search')
        wanted = query.casefold()
        found = []
        for entry in self.entries:
            if wanted in entry.name.casefold() or wanted in str(entry.ref):
                found.append(entry)
                if len(found) == limit:
                    break
        return tuple(found)

    def select(self, sources: tuple[SelectedSource, ...], world: str | None = None) -> SourceSelection:
        sources = collection(sources, MAX_SELECTION, 'invalid_selection')
        available = {entry.ref for entry in self.entries}
        for source in sources:
            if not isinstance(source, SelectedSource):
                raise CatalogError('invalid_selection')
            pinned(source.ref)
            if source.ref not in available:
                raise CatalogError('not_available', source.ref)
        return SourceSelection(world, sources)
