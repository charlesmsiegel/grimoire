"""Bounded, deterministic dependency composition over one loaded library (056).

Exclusions are local availability choices, not access controls. A parent cannot hide a broken
child. Diamonds retain declaring nodes rather than enumerating exponentially many origin paths.
"""
from __future__ import annotations

from grimoire.core.identity import SourceRef
from grimoire.core.index import MissingDefinition
from grimoire.store import LibraryView, StoredDefinition

from .model import (
    MAX_DEPTH, MAX_EDGES, MAX_ENTRIES, MAX_NODES, MAX_ROOTS,
    CatalogEntry, CatalogError, CatalogOrigin, CatalogView, catalog_from_record, catalog_ref, collection,
)

Members = dict[SourceRef, set[CatalogOrigin]]


def _union(target: Members, incoming: Members) -> None:
    for ref, origins in incoming.items():
        target.setdefault(ref, set()).update(origins)


def _conflicts(members: Members) -> None:
    seen = {}
    for ref in members:
        if ref.entity_key in seen and seen[ref.entity_key] != ref:
            raise CatalogError('conflicting_revision', ref)
        seen[ref.entity_key] = ref


def compose_catalogs(view: LibraryView, roots: tuple[SourceRef, ...]) -> CatalogView:
    """Discover explicit pinned roots; no world is implied and no chat is changed."""
    return _compose(view, roots)


def _compose(view: LibraryView, roots: tuple[SourceRef, ...], proposed: StoredDefinition | None = None) -> CatalogView:
    roots = collection(roots, MAX_ROOTS)
    for root in roots:
        catalog_ref(root)
    if len(set(roots)) != len(roots):
        raise CatalogError('duplicate_reference')
    basis: dict[SourceRef, CatalogOrigin] = {}
    expansions = edges = 0
    records: dict[SourceRef, StoredDefinition] = {}

    def read(ref: SourceRef, reason: str) -> StoredDefinition:
        if proposed is not None and ref == proposed.ref:
            return proposed
        try:
            record = view.index.resolve(ref)
        except MissingDefinition:
            raise CatalogError(reason, ref) from None
        if not isinstance(record, StoredDefinition):
            raise CatalogError('invalid_record', ref)
        return record

    def visit(ref: SourceRef, path: tuple[SourceRef, ...]) -> Members:
        nonlocal expansions, edges
        catalog_ref(ref)
        if ref in path:
            raise CatalogError('cycle', ref)
        expansions += 1
        if expansions > MAX_NODES or len(path) >= MAX_DEPTH:
            raise CatalogError('limit_exceeded', ref)
        stored = read(ref, 'missing_catalog')
        node = catalog_from_record(stored)
        origin = CatalogOrigin(ref, stored.etag)
        basis[ref] = origin
        edges += len(node.members) + len(node.dependencies) + len(node.exclusions)
        if edges > MAX_EDGES:
            raise CatalogError('limit_exceeded', ref)
        members: Members = {}
        for dependency in node.dependencies:
            _union(members, visit(dependency, (*path, ref)))
        for member in node.members:
            members.setdefault(member, set()).add(origin)
        excluded = {item.entity_key for item in node.exclusions}
        members = {member: origins for member, origins in members.items() if member.entity_key not in excluded}
        if len(members) > MAX_ENTRIES:
            raise CatalogError('limit_exceeded', ref)
        _conflicts(members)
        # Validate each completed node. Its parent cannot turn a broken dependency into a sound one.
        for member in members:
            if member not in records:
                records[member] = read(member, 'missing_member')
        return members

    members: Members = {}
    for root in roots:
        _union(members, visit(root, ()))
    if len(members) > MAX_ENTRIES:
        raise CatalogError('limit_exceeded')
    _conflicts(members)
    ordered = lambda origin: str(origin.ref)
    entries = tuple(CatalogEntry(ref, records[ref].name[:256], records[ref].etag,
                                 tuple(sorted(origins, key=ordered)))
                    for ref, origins in sorted(members.items(), key=lambda item: str(item[0])))
    return CatalogView(entries, tuple(sorted(basis.values(), key=ordered)))
