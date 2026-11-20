"""Optional, pinned source dependencies declared in a source manifest (079).

A source may declare other sources it uses, under the manifest extension key
``optional_dependencies``: a list of ``{"source": "<id>", "version": "<digest>" | null}``.
Every declared dependency is optional: its absence never stops the declaring source loading, and
the references it leaves are reported as missing by the removal preview. The pin is the
dependency's ``source_version`` when it was pinned: a digest of its declared heads, its links and the
bytes of each entity's head revision, so a moved head, a changed link or bytes edited at a head revision drift it,
while its title, owner, extensions (its own declarations) and manifest history do not. It is
reported, never enforced, and never followed recursively.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace

from grimoire.core.identity import validate_source_id
from grimoire.store import CommitResult, LibraryStore, LibraryView, StaleWrite, StoredSource
from grimoire.store.jsonfmt import is_etag

from .heads import entity_heads
from .model import DEPENDENCIES_KEY, MAX_DEPENDENCIES, Dependency, DependencyStatus, SourceUpgradeError


def _decode(stored: StoredSource) -> tuple[Dependency, ...]:
    manifest = stored.manifest
    raw = manifest.extra.get(DEPENDENCIES_KEY, ())
    if not isinstance(raw, (list, tuple)):
        raise SourceUpgradeError('invalid_dependency', f'{DEPENDENCIES_KEY} must be a list')
    if len(raw) > MAX_DEPENDENCIES:
        raise SourceUpgradeError('limit_exceeded', f'more than {MAX_DEPENDENCIES} dependencies')
    found: list[Dependency] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or not set(entry) <= {'source', 'version'} or 'source' not in entry:
            raise SourceUpgradeError('invalid_dependency', 'an entry is {"source", "version"}')
        source, pin = entry['source'], entry.get('version')
        try:
            validate_source_id(source)
        except (ValueError, TypeError) as error:
            raise SourceUpgradeError('invalid_dependency', str(error)) from None
        if pin is not None and not is_etag(pin):
            raise SourceUpgradeError('invalid_dependency', f'{source}: version is not a sha256 digest')
        if source == manifest.source:
            raise SourceUpgradeError('self_dependency', source)
        if any(item.source == source for item in found):
            raise SourceUpgradeError('invalid_dependency', f'{source} is declared twice')
        found.append(Dependency(source, pin))
    return tuple(found)


def source_version(view: LibraryView, source: str) -> str | None:
    """The pinnable version of a loaded source, or ``None`` where it is not loaded."""
    stored = view.sources.get(source)
    if stored is None:
        return None
    manifest = stored.manifest
    revisions = {str(ref): found.etag for ref, found in entity_heads(view, source).items()}
    document = {
        'heads': dict(manifest.heads),
        'links': sorted([link.subject, link.relation, link.object] for link in manifest.links),
        'revisions': revisions,
    }
    data = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def declared_dependencies(stored: StoredSource) -> tuple[Dependency, ...]:
    """The declarations as written, validated; an absent key declares none."""
    return _decode(stored)


def dependency_report(view: LibraryView, source: str) -> tuple[DependencyStatus, ...]:
    """Each direct dependency of ``source`` against the loaded ``view``; nothing is followed further."""
    stored = view.sources.get(source)
    if stored is None:
        raise SourceUpgradeError('invalid_source', f'{source} is not loaded')
    report = []
    for dependency in _decode(stored):
        current = source_version(view, dependency.source)
        if current is None:
            status = 'missing'
        elif dependency.version is None:
            status = 'following'
        else:
            status = 'pinned' if dependency.version == current else 'drifted'
        report.append(DependencyStatus(dependency, status, current))
    return tuple(report)


def pin_dependency(store: LibraryStore, handle: object, source: str, dependency: str, *,
                   expected: str) -> CommitResult:
    """Declare (or re-pin) ``dependency`` at its current ``source_version`` in ``source``'s manifest.

    One guarded compare-and-swap of the declaring manifest; every other manifest field and
    extension is kept. Re-pinning after reading ``preview_source_update`` is the deliberate upgrade
    of a declaration; it rewrites no reference anywhere. The dependency must load now. A declaration
already malformed refuses (fix it by hand first) rather than being silently replaced.
    """
    stored = store.read_source(source)
    if stored.etag != expected:
        raise StaleWrite(((source, 'source changed; reload before pinning'),))
    if dependency == source:
        raise SourceUpgradeError('self_dependency', source)
    try:
        validate_source_id(dependency)
    except (ValueError, TypeError) as error:
        raise SourceUpgradeError('invalid_dependency', str(error)) from None
    version = source_version(store.load(), dependency)
    if version is None:
        raise SourceUpgradeError('missing_dependency', dependency)
    current = list(_decode(stored))
    entry = Dependency(dependency, version)
    at = next((index for index, item in enumerate(current) if item.source == dependency), None)
    if at is None:
        current.append(entry)
    else:
        current[at] = entry
    if len(current) > MAX_DEPENDENCIES:
        raise SourceUpgradeError('limit_exceeded', f'more than {MAX_DEPENDENCIES} dependencies')
    extra = dict(stored.manifest.extra)
    extra[DEPENDENCIES_KEY] = [{'source': item.source, 'version': item.version} for item in current]
    tx = store.transaction()
    tx.put(replace(stored.manifest, extra=extra), expected)
    return tx.commit(handle)
