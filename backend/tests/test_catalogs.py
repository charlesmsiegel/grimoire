"""Optional catalog composition: synthetic qualified refs, never private library content."""
from dataclasses import replace

import pytest

from grimoire.catalogs import Catalog, CatalogError, catalog_envelope, compose_catalogs
from grimoire.core.identity import SourceRef
from grimoire.core.index import DefinitionIndex, SourceDescriptor
from grimoire.core.snapshot import SelectedSource
from grimoire.store import LibraryStore, RecordEnvelope, StoredDefinition
from portrayal_fixtures import manifest
from store_helpers import owner_handle

SOURCE = 'catalog:shared'
X = SourceRef(SOURCE, 'character', 'mara', 'r1')
Y = SourceRef('world:realm', 'character', 'mara', 'r1')
A = SourceRef(SOURCE, 'catalog', 'a', 'r1')
B = SourceRef(SOURCE, 'catalog', 'b', 'r1')
C = SourceRef(SOURCE, 'catalog', 'c', 'r1')
D = SourceRef(SOURCE, 'catalog', 'd', 'r1')


def view_of(tmp_path, *catalogs, records=()):
    with LibraryStore.create(tmp_path / 'lib') as store:
        tx = store.transaction()
        values = [RecordEnvelope(X, {}, name='Mara'), RecordEnvelope(Y, {}, name='Mara'), *records]
        for source in sorted({r.address.source for r in values} | {c.ref.source for c in catalogs}):
            heads = {f'{r.address.kind}/{r.address.entity_id}': r.address.revision for r in values
                     if r.address.source == source}
            heads.update({f'catalog/{c.ref.entity_id}': c.ref.revision for c in catalogs if c.ref.source == source})
            tx.put(manifest(source, heads=heads), None)
        for record in values:
            tx.put(record, None)
        for catalog in catalogs:
            tx.put(catalog_envelope(catalog), None)
        tx.commit(owner_handle())
        return store.load()


def assert_reason(reason, call):
    with pytest.raises(CatalogError) as caught:
        call()
    assert caught.value.reason == reason


def test_qualified_diamond_exclusions_do_not_match_names(tmp_path):
    view = view_of(tmp_path, Catalog(A, (X, Y)), Catalog(B, dependencies=(A,), exclusions=(X.unpinned(),)),
                   Catalog(C, dependencies=(A,)), Catalog(D, dependencies=(B, C)))
    result = compose_catalogs(view, (D,))
    assert [e.ref for e in result.entries] == [X, Y]
    assert result.entries[0].origins == (result.basis[0],)
    assert [e.ref for e in compose_catalogs(view, (B,)).search('mara')] == [Y]
    assert result.select((SelectedSource(X, 'character'),)).sources[0].ref == X
    assert result.select(()).world is None
    assert_reason('not_available', lambda: result.select((SelectedSource(X.at('r2'), 'character'),)))
    assert_reason('unpinned_reference', lambda: result.select((SelectedSource(X.unpinned(), 'character'),)))


def test_conflicting_pins_can_be_excluded_only_at_the_node_where_they_meet(tmp_path):
    view = view_of(tmp_path, Catalog(A, (X,)), Catalog(B, (X.at('r2'),)),
                   Catalog(C, dependencies=(A, B)), Catalog(D, dependencies=(A, B), exclusions=(X.unpinned(),)),
                   records=(RecordEnvelope(X.at('r2'), {}, name='Mara'),))
    assert_reason('conflicting_revision', lambda: compose_catalogs(view, (C,)))
    assert compose_catalogs(view, (D,)).entries == ()
    assert_reason('conflicting_revision', lambda: compose_catalogs(view, (A, B)))


def test_parent_exclusion_cannot_mask_missing_member_in_child(tmp_path):
    missing = X.at('missing')
    view = view_of(tmp_path, Catalog(A, (missing,)),
                   Catalog(B, dependencies=(A,), exclusions=(X.unpinned(),)),
                   Catalog(C, (missing,), exclusions=(X.unpinned(),)))
    assert_reason('missing_member', lambda: compose_catalogs(view, (B,)))
    assert compose_catalogs(view, (C,)).entries == ()


def test_cycle_and_missing_dependency_fail_closed(tmp_path):
    view = view_of(tmp_path, Catalog(A, dependencies=(B,)), Catalog(B, dependencies=(A,)),
                   Catalog(C, dependencies=(D,)))
    assert_reason('cycle', lambda: compose_catalogs(view, (A,)))
    assert_reason('missing_catalog', lambda: compose_catalogs(view, (C,)))


@pytest.mark.parametrize('kwargs,reason', [
    ({'members': (X.unpinned(),)}, 'unpinned_reference'),
    ({'members': (A,)}, 'invalid_member'),
    ({'members': (X, X)}, 'duplicate_reference'),
    ({'members': (X, X.at('r2'))}, 'conflicting_revision'),
    ({'dependencies': (X,)}, 'invalid_dependency'),
    ({'exclusions': (X,)}, 'pinned_exclusion'),
    ({'members': 'not-a-list'}, 'invalid_catalog'),
])
def test_invalid_catalog_values_are_refused(kwargs, reason):
    assert_reason(reason, lambda: Catalog(A, **kwargs))


def test_bad_schema_and_unknown_payload_keys_are_not_empty_catalogs(tmp_path):
    view = view_of(tmp_path, records=(RecordEnvelope(A, {'members': [], 'dependencies': [], 'exclusions': [],
                                                       'typo': []}, extra={'schema': 'grimoire.catalog/1'}),))
    assert_reason('invalid_catalog', lambda: compose_catalogs(view, (A,)))
    assert_reason('invalid_catalog_reference', lambda: compose_catalogs(view, (X,)))


@pytest.mark.parametrize('limit', [0, -1, True, 101])
def test_search_limits_are_not_silent_truncation_defaults(tmp_path, limit):
    result = compose_catalogs(view_of(tmp_path, Catalog(A, (X,))), (A,))
    assert_reason('invalid_search', lambda: result.search(limit=limit))
    assert_reason('invalid_search', lambda: result.search('x' * 257))


def test_empty_roots_are_a_valid_world_optional_selection(tmp_path):
    result = compose_catalogs(view_of(tmp_path), ())
    assert result.entries == result.basis == ()
    assert result.select(()).sources == ()


def test_inputs_and_selection_bounds_have_structured_failures(tmp_path):
    view = view_of(tmp_path, Catalog(A, (X,)))
    assert_reason('invalid_reference', lambda: compose_catalogs(view, ([],)))
    assert_reason('duplicate_reference', lambda: compose_catalogs(view, (A, A)))
    assert_reason('limit_exceeded', lambda: compose_catalogs(view, (A,) * 33))
    result = compose_catalogs(view, (A,))
    assert_reason('limit_exceeded', lambda: result.select((SelectedSource(X, 'character'),) * 33))
    assert_reason('invalid_selection', lambda: result.select(('x',)))
    assert_reason('invalid_search', lambda: result.search(None))


def test_depth_and_repeated_diamond_work_are_bounded(tmp_path):
    chain = [SourceRef(SOURCE, 'catalog', f'node-{i}', 'r1') for i in range(17)]
    catalogs = [Catalog(ref, dependencies=(chain[i+1],) if i < 16 else ()) for i, ref in enumerate(chain)]
    view = view_of(tmp_path, *catalogs)
    assert_reason('limit_exceeded', lambda: compose_catalogs(view, (chain[0],)))
    assert compose_catalogs(view, (chain[1],)).entries == ()  # exactly 16 levels


def test_exponential_diamonds_fail_before_unbounded_path_enumeration(tmp_path):
    nodes = [[SourceRef(SOURCE, 'catalog', f'layer-{i}-{j}', 'r1') for j in range(2)] for i in range(8)]
    catalogs = [Catalog(ref, dependencies=tuple(nodes[i+1]) if i < 7 else ())
                for i, layer in enumerate(nodes) for ref in layer]
    view = view_of(tmp_path, *catalogs)
    assert_reason('limit_exceeded', lambda: compose_catalogs(view, tuple(nodes[0])))


def test_total_edge_budget_and_display_name_bounds(tmp_path):
    # Missing entries excluded locally need no reads, but still count as traversal work.
    members = tuple(SourceRef(SOURCE, 'item', f'item-{i}', 'r1') for i in range(5000))
    exclusions = tuple(ref.unpinned() for ref in members)
    view = view_of(tmp_path, Catalog(A, members, exclusions=exclusions),
                   Catalog(B, members, exclusions=exclusions), Catalog(C, dependencies=(A, B)))
    assert compose_catalogs(view, (A, B)).entries == ()  # exactly 20,000 edges
    assert_reason('limit_exceeded', lambda: compose_catalogs(view, (C,)))


def test_schema_and_same_ref_edited_bytes_are_visible_in_provenance(tmp_path):
    view = view_of(tmp_path, Catalog(A, (X,)))
    before = compose_catalogs(view, (A,))
    with LibraryStore.open(tmp_path / 'lib') as store:
        record_path = store.root / 'sources/catalog/shared/_source/records/character/mara/r1.json'
        text = record_path.read_text()
        record_path.write_text(text.replace('Mara', 'Mara updated'))
        after = compose_catalogs(store.load(), (A,))
    assert before.entries[0].ref == after.entries[0].ref
    assert before.entries[0].etag != after.entries[0].etag
    assert after.entries[0].name == 'Mara updated'
    assert before.basis == after.basis


def test_parent_exclusions_apply_before_surviving_result_limit(tmp_path):
    # In-memory native records avoid measuring filesystem overhead in this composition boundary test.
    refs = tuple(SourceRef(SOURCE, 'item', f'gear-{i}', 'r1') for i in range(11000))
    catalogs = (Catalog(A, refs[:6000]), Catalog(B, refs[6000:]),
                Catalog(C, dependencies=(A, B), exclusions=tuple(r.unpinned() for r in refs[:1000])),
                Catalog(D, dependencies=(A, B), exclusions=tuple(r.unpinned() for r in refs[:999])))
    records = [RecordEnvelope(ref, {}, name='Gear') for ref in refs]
    records += [catalog_envelope(c) for c in catalogs]
    index = DefinitionIndex([SourceDescriptor(SOURCE, 'human:owner')],
                            [StoredDefinition(r.address, r.name or '', r, 'synthetic-etag') for r in records])
    view = replace(view_of(tmp_path), index=index)
    assert len(compose_catalogs(view, (C,)).entries) == 10000
    assert_reason('limit_exceeded', lambda: compose_catalogs(view, (D,)))
    assert_reason('limit_exceeded', lambda: compose_catalogs(view, (A, B)))
