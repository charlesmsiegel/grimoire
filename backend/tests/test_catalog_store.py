"""Catalog publication reuses canonical transactions and authority; fixtures are synthetic."""
from dataclasses import replace

import pytest

from grimoire.catalogs import Catalog, CatalogError, compose_catalogs, publish_catalog
from grimoire.core.identity import SourceRef
from grimoire.store import LibraryStore, RecordEnvelope, StaleWrite, StoreError
from grimoire.testing.fileops import snapshot as files_of
from portrayal_fixtures import manifest
from store_helpers import owner_handle

SOURCE = 'catalog:shared'
REF = SourceRef(SOURCE, 'catalog', 'cast', 'r1')
MARA = SourceRef(SOURCE, 'character', 'mara', 'r1')


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as opened:
        tx = opened.transaction()
        tx.put(replace(manifest(SOURCE), extra={'x-note': 'retained'}), None)
        tx.put(RecordEnvelope(MARA, {'description': 'Synthetic'}, name='Mara'), None)
        tx.commit(owner_handle())
        yield opened


def test_publish_reload_and_strict_response_loss_replay(store):
    expected = store.read_source(SOURCE).etag
    catalog = Catalog(REF, (MARA,))
    publish_catalog(store, owner_handle(), catalog, expected=expected, name='Cast')
    after = files_of(store.root)
    with pytest.raises(StaleWrite):
        publish_catalog(store, owner_handle(), catalog, expected=expected)
    assert files_of(store.root) == after
    updated = store.read_source(SOURCE)
    assert updated.manifest.extra == {'x-note': 'retained'}
    assert updated.manifest.heads['catalog/cast'] == 'r1'
    assert compose_catalogs(store.load(), (REF,)).entries[0].ref == MARA
    with pytest.raises(StoreError):
        publish_catalog(store, owner_handle(), catalog, expected=updated.etag)
    assert files_of(store.root) == after
    with pytest.raises(StoreError):
        publish_catalog(store, owner_handle(), Catalog(REF), expected=updated.etag)
    assert files_of(store.root) == after


def test_existing_revision_cannot_move_new_head_backwards(store):
    for ref in (REF, REF.at('r2')):
        publish_catalog(store, owner_handle(), Catalog(ref, (MARA,)), expected=store.read_source(SOURCE).etag)
    before = files_of(store.root)
    with pytest.raises(StoreError):
        publish_catalog(store, owner_handle(), Catalog(REF, (MARA,)), expected=store.read_source(SOURCE).etag)
    assert store.read_source(SOURCE).manifest.heads['catalog/cast'] == 'r2'
    assert files_of(store.root) == before


def test_bad_dependency_or_authority_never_partially_writes(store):
    before = files_of(store.root)
    expected = store.read_source(SOURCE).etag
    with pytest.raises(CatalogError, match='missing_member'):
        publish_catalog(store, owner_handle(), Catalog(REF, (MARA.at('missing'),)), expected=expected)
    assert files_of(store.root) == before
    with pytest.raises(StoreError):
        publish_catalog(store, owner_handle('campaigns'), Catalog(REF, (MARA,)), expected=expected)
    assert files_of(store.root) == before


def test_publish_rejects_self_dependency(store):
    before = files_of(store.root)
    with pytest.raises(CatalogError, match='cycle'):
        publish_catalog(store, owner_handle(), Catalog(REF, dependencies=(REF,)),
                        expected=store.read_source(SOURCE).etag)
    assert files_of(store.root) == before
