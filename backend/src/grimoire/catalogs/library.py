"""Guarded publication of one immutable catalog revision and its source head (056).

This is a trusted owner's authoring adapter, not an endpoint or model tool. The existing store
handle still authorizes every write. Retries refuse rather than inventing a second revision:
re-read the head after an uncertain response. Dependency checks are a loaded-view preview,
not a transaction-wide hold on referenced bytes (human edits at pinned refs remain possible).
"""
from __future__ import annotations

from dataclasses import replace

from grimoire.store import CommitResult, LibraryStore, StaleWrite, StoredDefinition

from .compose import _compose
from .model import Catalog, catalog_envelope


def publish_catalog(store: LibraryStore, handle: object, catalog: Catalog, *, expected: str,
                    name: str | None = None) -> CommitResult:
    """Create a revision and CAS the existing source manifest; never create a source implicitly."""
    stored = store.read_source(catalog.ref.source)
    if stored.etag != expected:
        raise StaleWrite(((catalog.ref.source, 'catalog source changed; reload before publishing'),))
    envelope = catalog_envelope(catalog, name)
    # The proposed record exists only in this validation view. A cycle or missing dependency
    # refuses before staging anything; the generic transaction supplies atomicity and authority.
    proposed = StoredDefinition(catalog.ref, name or '', envelope, '')
    _compose(store.load(), (catalog.ref,), proposed)
    heads = dict(stored.manifest.heads)
    heads[f'catalog/{catalog.ref.entity_id}'] = catalog.ref.revision
    tx = store.transaction()
    tx.put(envelope, None)
    tx.put(replace(stored.manifest, heads=heads), expected)
    return tx.commit(handle)
