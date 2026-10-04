import io
import uuid

import pytest
from PIL import Image

from grimoire.store import campaigns, export, world_bundle, world_images, worlds
from grimoire.store import image_collections as collections
from grimoire.store.campaigns import read


def png(color='red'):
    b = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(b, 'PNG')
    return b.getvalue()


@pytest.fixture
def collection(client):
    wid = worlds.create_world('Realm')
    cid = uuid.uuid4().hex
    names = [collections.put_member(wid, png(c)) for c in ('red', 'blue')]
    collections.publish(wid, cid, names)
    return wid, cid, names


def test_manifest_and_fallback_are_local_and_ordered(client, collection):
    wid, cid, names = collection
    base = f'/api/worlds/{wid}/image-collections/{cid}'
    r = client.get(base)
    assert r.status_code == 200
    assert r.json()['id'] == cid and r.json()['format'] == 1
    assert r.json()['members'][0].startswith(f'/api/worlds/{wid}/images/{names[0]}?v=')
    assert 'source' not in r.text
    assert client.get(base + '/image').content == png()
    assert client.delete(f'/api/worlds/{wid}/images/{names[0]}').status_code == 409
    assert client.put(f'/api/worlds/{wid}/images/{names[0]}',
                      files={'file': ('blue.png', png('blue'), 'image/png')}).status_code == 409


def test_missing_members_and_bad_manifests_are_explicit(client, collection):
    wid, cid, names = collection
    base = f'/api/worlds/{wid}/image-collections/{cid}'
    world_images.image_path(wid, names[0]).unlink()
    assert client.get(base + '/image').content == png('blue')
    world_images.image_path(wid, names[1]).unlink()
    assert client.get(base).status_code == 404
    path = worlds.world_root(wid) / 'assets/image-collections' / f'{cid}.json'
    path.write_text('{broken')
    assert client.get(base).status_code == 500
    assert client.get(f'/api/worlds/{wid}/image-collections/invalid').status_code == 404


def test_export_packs_a_deterministic_representative(client, collection):
    wid, cid, _names = collection
    campaign = campaigns.create_campaign('Saltmarch', wid)
    registry = export.Images()
    text = f'![Scene]({collections.url_for(wid, cid)})'
    assert export.rewrite_images(text, campaign, registry) == '![Scene](images/img-000.png)'
    assert next(iter(registry.by_path)).read_bytes() == png()


def test_missing_world_export_degrades_to_alt_text(client, collection, monkeypatch):
    wid, cid, _ = collection
    campaign = campaigns.create_campaign('Saltmarch', wid)
    monkeypatch.setattr(read, 'world_root_of', lambda _: worlds.world_root('missing-world'))
    assert export.rewrite_images(f'![Scene]({collections.url_for(wid, cid)})',
                                 campaign, export.Images()) == 'Scene'


def test_world_forks_and_bundles_keep_all_members_and_repoint_links(client, collection, tmp_path):
    wid, cid, names = collection
    root = worlds.world_root(wid)
    lore = root / 'sources/lore/scene.md'
    lore.parent.mkdir(parents=True, exist_ok=True)
    lore.write_text(f'![Scene]({collections.url_for(wid, cid)})')
    forked = worlds.fork_world(wid, 'Other Realm')
    bundle = tmp_path / 'realm.zip'
    world_bundle.write_bundle(wid, bundle)
    imported = world_bundle.import_bundle(bundle)
    for new_id in (forked, imported):
        assert new_id != wid
        assert collections.read(new_id, cid)['members'] == names
        text = (worlds.world_root(new_id) / 'sources/lore/scene.md').read_text()
        assert collections.url_for(new_id, cid) in text
        data = client.get(f'/api/worlds/{new_id}/image-collections/{cid}').json()
        assert len(data['members']) == 2
        assert all(url.startswith(f'/api/worlds/{new_id}/images/') for url in data['members'])
        assert [client.get(url).content for url in data['members']] == [png(), png('blue')]
