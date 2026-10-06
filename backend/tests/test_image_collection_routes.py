import hashlib
import io
import json

import pytest
from PIL import Image

from grimoire.store import assets, campaigns, export, image_refs, world_bundle, world_images, worlds
from grimoire.store import image_collection_imports as imports
from grimoire.store import image_collections as collections
from grimoire.store.campaigns import read
from tests.collection_fixtures import format1, format2, member_name, write_manifest

CID = '0123456789abcdef0123456789abcdef'
OTHER = 'fedcba9876543210fedcba9876543210'


def png(color='red'):
    b = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(b, 'PNG')
    return b.getvalue()


@pytest.fixture
def collection(client):
    wid = worlds.create_world('Realm')
    names = format1(wid, CID, png('red'), png('blue'))
    return wid, CID, names


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


def png_dpi(dpi, color='red'):
    b = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(b, 'PNG', dpi=(dpi, dpi))
    return b.getvalue()


def test_pixel_duplicate_reupload_does_not_trip_guard(client):
    wid = worlds.create_world('Realm')
    [name] = format1(wid, CID, png_dpi(72))
    url = f'/api/worlds/{wid}/images/{name}'

    def put(data):
        return client.put(url, files={'file': ('x.png', data, 'image/png')})

    assert png_dpi(72) != png_dpi(144)

    def stored_pixel():
        body = client.get(url).content
        return Image.open(io.BytesIO(body)).convert('RGB').getpixel((0, 0))

    assert stored_pixel() == (255, 0, 0)
    assert put(png_dpi(144)).status_code == 200  # same pixels, other pHYs
    assert stored_pixel() == (255, 0, 0)
    r = put(png_dpi(72, 'blue'))
    assert r.status_code == 409 and r.json()['detail'] == 'image_in_collection'
    assert stored_pixel() == (255, 0, 0)


def test_a_member_name_cannot_be_filed_over_other_pixels(client):
    wid = worlds.create_world('Realm')
    format1(wid, OTHER, png('green'))  # the guard is on only beside format-1 state
    name = collections.MEMBER_PREFIX + hashlib.sha256(png('red')).hexdigest()
    url = f'/api/worlds/{wid}/images/{name}'
    r = client.put(url, files={'file': ('x.png', png('blue'), 'image/png')})
    assert r.status_code == 400
    assert world_images.image_path(wid, name) is None
    assert client.put(url, files={'file': ('x.png', png('red'), 'image/png')}).status_code == 200


# ---- format 2, and the member route ----------------------------------------

def _resolved(image_id):
    return image_refs.resolve_ref(image_refs.Ref(name='', image=image_id, focus=None))


def _pixel(body):
    return Image.open(io.BytesIO(body)).convert('RGB').getpixel((0, 0))


def test_format_1_json_keeps_library_urls(client, collection):
    wid, cid, names = collection
    d = collections.image_directory(wid)
    r = client.get(f'/api/worlds/{wid}/image-collections/{cid}')
    assert r.status_code == 200
    assert r.json() == {'format': 1, 'id': cid, 'members': [
        f'/api/worlds/{wid}/images/{n}?v={assets.resolve(d, n).blob_sha256}' for n in names]}


def test_format_2_json_lists_member_index_urls_with_blob_v(client):
    wid = worlds.create_world('Realm')
    ids = format2(wid, CID, png('red'), png('blue'))
    r = client.get(f'/api/worlds/{wid}/image-collections/{CID}')
    assert r.status_code == 200
    assert r.headers['cache-control'] == 'no-cache'
    assert r.json() == {'format': 2, 'id': CID, 'members': [
        f'/api/worlds/{wid}/image-collections/{CID}/members/{i}?v={_resolved(x).blob_sha256}'
        for i, x in enumerate(ids)]}
    assert [_pixel(client.get(u).content) for u in r.json()['members']] == [(255, 0, 0), (0, 0, 255)]
    assert _pixel(client.get(f'/api/worlds/{wid}/image-collections/{CID}/image').content) == (255, 0, 0)


def test_member_route_serves_both_formats(client):
    wid = worlds.create_world('Realm')
    format1(wid, CID, png('red'), png('blue'))
    ids = format2(wid, OTHER, png('green'), png('white'))
    for cid, colours in ((CID, [(255, 0, 0), (0, 0, 255)]), (OTHER, [(0, 128, 0), (255, 255, 255)])):
        for i, colour in enumerate(colours):
            r = client.get(f'/api/worlds/{wid}/image-collections/{cid}/members/{i}')
            assert r.status_code == 200 and _pixel(r.content) == colour
    v = _resolved(ids[0]).blob_sha256
    r = client.get(f'/api/worlds/{wid}/image-collections/{OTHER}/members/0?v={v}')
    assert r.status_code == 200 and 'immutable' in r.headers['cache-control']


@pytest.mark.parametrize('fmt', [1, 2])
def test_member_route_bounds(client, fmt):
    wid = worlds.create_world('Realm')
    make = format1 if fmt == 1 else format2
    members = make(wid, CID, png('red'), png('blue'), png('green'))
    if fmt == 1:
        world_images.image_path(wid, members[1]).unlink()
    else:
        _resolved(members[1]).blob_path.unlink()
    base = f'/api/worlds/{wid}/image-collections/{CID}/members/'
    for n in ('-1', str(10 ** 9), 'abc', '3', '1', '9' * 5000, '\u0662', '1.0', ' 2', '01', '00'):
        assert client.get(base + n).status_code == 404, n
    assert _pixel(client.get(base + '2').content) == (0, 128, 0)
    assert _pixel(client.get(base + '0').content) == (255, 0, 0)
    assert client.get(f'/api/worlds/{wid}/image-collections/{OTHER}/members/0').status_code == 404
    assert client.get(f'/api/worlds/nowhere/image-collections/{CID}/members/0').status_code == 404
    assert client.get(f'/api/worlds/{wid}/image-collections/invalid/members/0').status_code == 404


def _put(client, wid, name, data):
    return client.put(f'/api/worlds/{wid}/images/{name}', files={'file': ('x.png', data, 'image/png')})


def _format1_journal(wid):
    path = imports.job_path(wid, 'a' * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'format': 1, 'job_id': 'a' * 64, 'source_url': 'https://example.test/x',
                                'collection_id': OTHER, 'members': [], 'accepted': False}),
                    encoding='utf-8')


def _corrupt_manifest(wid):
    write_manifest(wid, OTHER, {'format': 1, 'members': 'nope'})


def _unparseable_journal(wid):
    path = imports.job_path(wid, 'a' * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{broken', encoding='utf-8')


@pytest.mark.parametrize('state', [
    lambda wid: format1(wid, OTHER, png('white')),
    _format1_journal,
    _corrupt_manifest,
    _unparseable_journal,
], ids=['format-1-manifest', 'format-1-journal', 'corrupt-manifest', 'unparseable-journal'])
def test_guard_is_inert_only_without_format_1_state(client, state):
    wid = worlds.create_world('Realm')
    format2(wid, CID, png('green'))
    forged = member_name(png('red'))
    assert not collections.has_format1(wid)
    assert _put(client, wid, forged, png('blue')).status_code == 200
    state(wid)
    assert collections.has_format1(wid)
    other = member_name(png('black'))
    r = _put(client, wid, other, png('blue'))
    assert r.status_code == 400, r.text
    assert world_images.image_path(wid, other) is None
