"""059 save/reopen, canonical-source isolation and hostile file/operation scenarios."""
import json
from dataclasses import replace

import pytest

from grimoire.core import DIRECTOR, AuthorityError
from grimoire.core.identity import InstanceRef
from grimoire.store import LibraryStore, RecordEnvelope, StaleWrite, WriteDenied
from grimoire.store.layout import address_parts
from grimoire.testing.fileops import snapshot
from grimoire.topology import Path, Place, TopologyError
from grimoire.topologystore import TopologyLibrary, overlay_ref
from store_helpers import owner_handle
from topology_fixtures import GRAPH, TOWN, GATE, WOOD, PORT, OTHER, topology, seed

D = {'requested_by': DIRECTOR}
BLOCK = replace(topology().paths[0], blocked=True)


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as opened:
        seed(opened)
        TopologyLibrary(opened, owner_handle()).publish(topology(), **D).commit()
        yield opened


def library(store):
    return TopologyLibrary(store, owner_handle())


def change(lib, path=BLOCK, op='block', expected=None, campaign='a'):
    return lib.change_path(GRAPH, campaign, path, operation_id=op, provenance='human:owner',
                           expected=expected, **D)


def edit(store, ref, mutate):
    file = store.root.joinpath(*address_parts(ref))
    data = json.loads(file.read_bytes())
    mutate(data)
    file.write_text(json.dumps(data))


def test_persistence_campaign_block_and_immutable_source(store):
    lib = library(store)
    before = {k: v for k, v in snapshot(store.root).items() if k.startswith('sources/')}
    planned = change(lib)
    assert len(planned.preview()) == 1
    receipt = planned.commit()
    assert receipt.address == overlay_ref(GRAPH, 'a')
    assert lib.view(GRAPH, campaign_id='a', **D).index.route(TOWN, PORT, **D) is None
    for campaign in ('b', None):
        assert lib.view(GRAPH, campaign_id=campaign, **D).index.route(TOWN, PORT, **D).paths == ('road', 'bridge')
    assert before == {k: v for k, v in snapshot(store.root).items() if k.startswith('sources/')}
    root = store.root
    store.close()
    with LibraryStore.open(root) as reopened:
        view = library(reopened).view(GRAPH, campaign_id='a', **D)
        assert view.index.route(TOWN, PORT, conditions=('ticket',), **D).paths == ('trail', 'ferry')
        assert view.index.ancestors(GATE, **D) == (TOWN,)
        assert view.overlay_etag == receipt.etag


def test_retry_after_later_change_does_not_resurrect_block(store):
    lib = library(store)
    first = change(lib).commit()
    change(lib, topology().paths[0], 'restore', first.etag).commit()
    before = snapshot(store.root)
    replay = change(lib)
    assert replay.preview() == {}
    assert replay.commit().replayed
    assert lib.view(GRAPH, campaign_id='a', **D).index.route(TOWN, PORT, **D).paths == ('road', 'bridge')
    assert snapshot(store.root) == before
    with pytest.raises(TopologyError, match='operation_conflict'):
        change(lib, topology().paths[0])


def test_path_override_endpoints_and_conditions(store):
    lib = library(store)
    change(lib, Path('road', TOWN, PORT, bidirectional=True, requires=('key',))).commit()
    view = lib.view(GRAPH, campaign_id='a', **D)
    assert view.index.route(PORT, TOWN, **D) is None
    assert view.index.route(PORT, TOWN, conditions=('key',), **D).paths == ('road',)


def test_stale_overlay_and_source_during_preview(store):
    lib = library(store)
    pending = change(lib)
    change(lib, op='other').commit()
    with pytest.raises(StaleWrite):
        pending.commit()
    pending = change(lib, campaign='b')
    edit(store, GRAPH, lambda data: data['payload'].update(provenance='edited'))
    with pytest.raises(StaleWrite):
        pending.commit()
    with pytest.raises(TopologyError, match='source_changed'):
        lib.view(GRAPH, campaign_id='a', **D)


def test_unauthorized_and_actor_query_refused(store):
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        change(TopologyLibrary(store, owner_handle('sources'))).commit()
    with pytest.raises(AuthorityError):
        library(store).view(GRAPH, requested_by='actor')
    with pytest.raises(AuthorityError):
        library(store).publish(topology(), requested_by='actor')
    assert snapshot(store.root) == before


def test_imported_town_is_not_a_fallback(store):
    graph = replace(topology(), ref=replace(GRAPH, entity_id='imported-roads'),
                    places=topology().places + (Place(OTHER, ('Town',)),))
    lib = library(store)
    lib.publish(graph, **D).commit()
    assert set(lib.view(graph.ref, **D).index.matches('Saltmarch', **D)) == {TOWN, OTHER}
    store.root.joinpath(*address_parts(TOWN)).unlink()
    view = lib.view(graph.ref, **D)
    assert view.index.missing == (TOWN,)
    with pytest.raises(TopologyError, match='missing_place'):
        view.index.route(TOWN, PORT, **D)
    assert view.index.route(OTHER, PORT, **D) is None


@pytest.mark.parametrize('mutation', [
    lambda d: d['payload'].update(schema='future/1'),
    lambda d: d['payload'].update(unrecognized=True),
    lambda d: d['payload'].update(graph=str(GRAPH.at('r2'))),
    lambda d: d.update(base=str(GRAPH.at('r2'))),
    lambda d: d['payload'].update(operations=None),
])
def test_corrupt_overlay_never_falls_back_or_rewrites(store, mutation):
    lib = library(store)
    change(lib).commit()
    edit(store, overlay_ref(GRAPH, 'a'), mutation)
    before = snapshot(store.root)
    with pytest.raises(TopologyError):
        lib.view(GRAPH, campaign_id='a', **D)
    with pytest.raises(TopologyError):
        change(lib)
    assert snapshot(store.root) == before


def test_unknown_envelope_metadata_preserved(store):
    lib = library(store)
    change(lib).commit()
    address = overlay_ref(GRAPH, 'a')
    edit(store, address, lambda d: d.update(custom={'keep': [1, 2]}))
    etag = store.read(address).etag
    change(lib, topology().paths[0], 'restore', etag).commit()
    assert store.read(address).envelope.extra['custom']['keep'] == (1, 2)


def test_deterministic_overlay_identity_and_cross_kind_collision(store):
    assert overlay_ref(GRAPH, 'a') != overlay_ref(GRAPH, 'b')
    assert overlay_ref(GRAPH, 'a') != overlay_ref(GRAPH.at('r2'), 'a')
    ref = overlay_ref(GRAPH, 'a')
    transaction = store.transaction()
    transaction.put(RecordEnvelope(InstanceRef('a', 'character', ref.instance_id), {}), None)
    transaction.commit(owner_handle())
    with pytest.raises(TopologyError, match='instance_id_conflict'):
        change(library(store))


def test_malformed_source_and_missing_campaign_refused(store):
    lib = library(store)
    with pytest.raises(TopologyError, match='missing_campaign'):
        change(lib, campaign='absent')
    edit(store, GRAPH, lambda d: d['payload'].update(schema='future'))
    with pytest.raises(TopologyError, match='unsupported_schema'):
        lib.view(GRAPH, **D)


def test_retry_survives_reopen_and_repeated_change_object(store):
    lib = library(store)
    planned = change(lib)
    planned.commit()
    assert planned.commit().replayed
    root = store.root
    store.close()
    with LibraryStore.open(root) as reopened:
        before = snapshot(root)
        assert change(library(reopened)).commit().replayed
        assert snapshot(root) == before


def test_hand_edited_cross_kind_collision_blocks_existing_overlay(store):
    lib = library(store)
    change(lib).commit()
    address = overlay_ref(GRAPH, 'a')
    other = InstanceRef('a', 'character', address.instance_id)
    target = store.root.joinpath(*address_parts(other))
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(store.root.joinpath(*address_parts(address)).read_bytes())
    data['address'] = str(other)
    target.write_text(json.dumps(data))
    with pytest.raises(TopologyError, match='instance_id_conflict'):
        lib.view(GRAPH, campaign_id='a', **D)


def test_source_missing_does_not_make_campaign_block_disappear(store):
    lib = library(store)
    change(lib).commit()
    store.root.joinpath(*address_parts(GRAPH)).unlink()
    with pytest.raises(TopologyError, match='unavailable_topology'):
        lib.view(GRAPH, campaign_id='a', **D)


def test_place_edit_or_campaign_delete_after_preview_refuses(store):
    lib = library(store)
    pending = change(lib)
    edit(store, TOWN, lambda d: d.update(name='Changed'))
    with pytest.raises(StaleWrite):
        pending.commit()
    pending = change(lib)
    store.root.joinpath('campaigns/a/scope.json').unlink()
    with pytest.raises(TopologyError, match='missing_campaign'):
        pending.commit()


def test_new_revision_requires_existing_manifest_head(store):
    lib = library(store)
    second = replace(topology(), ref=GRAPH.at('r2'))
    with pytest.raises(Exception) as error:
        lib.publish(second, **D).commit()
    assert 'head' in str(error.value).lower()
    source = store.read_source(GRAPH.source)
    tx = store.transaction()
    tx.put(replace(source.manifest, heads={'topology/roads': 'r1'}), source.etag)
    tx.commit(owner_handle())
    lib.publish(second, **D).commit()
    assert lib.view(GRAPH, **D).index.route(TOWN, PORT, **D).paths == ('road', 'bridge')
    assert lib.view(second.ref, **D).index.route(TOWN, PORT, **D).paths == ('road', 'bridge')


def test_shadowed_invalid_historical_path_is_not_accepted_as_retry(store):
    lib = library(store)
    first = change(lib).commit()
    change(lib, topology().paths[0], 'restore', first.etag).commit()
    address = overlay_ref(GRAPH, 'a')
    edit(store, address, lambda d: d['payload']['operations'][0]['path'].update(target=str(OTHER)))
    before = snapshot(store.root)
    with pytest.raises(TopologyError, match='unknown_endpoint'):
        lib.view(GRAPH, campaign_id='a', **D)
    with pytest.raises(TopologyError, match='unknown_endpoint'):
        change(lib)
    assert snapshot(store.root) == before
