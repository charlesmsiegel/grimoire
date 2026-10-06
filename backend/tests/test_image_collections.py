import hashlib
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image, PngImagePlugin

from grimoire.store import assets, image_refs, image_store, locks, world_images, worlds
from grimoire.store import image_collection_imports as imports
from grimoire.store import image_collections as collections
from tests.collection_fixtures import format1, format2, member_name, write_manifest

CID = '0123456789abcdef0123456789abcdef'
OTHER = 'fedcba9876543210fedcba9876543210'
THIRD = 'aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb'


def png(color='red'):
    out = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(out, 'PNG')
    return out.getvalue()


@pytest.fixture
def wid(monkeypatch, tmp_path):
    monkeypatch.setenv('GRIMOIRE_HOME', str(tmp_path))
    return worlds.create_world('Realm')


def test_members_deduplicate_and_publication_is_source_free(wid):
    first = collections.put_member(wid, png())
    assert collections.put_member(wid, png()) == first
    second = collections.put_member(wid, png('blue'))
    cid = uuid.uuid4().hex
    collections.publish(wid, cid, [first, second])
    assert collections.read(wid, cid) == {'format': 1, 'members': [first, second]}
    assert len(world_images.list_images(wid)) == 2
    assert collections.publish(wid, cid, [first, second]) == collections.read(wid, cid)
    with pytest.raises(collections.CollectionInvalidError):
        collections.publish(wid, cid, [second, first])


def test_members_are_protected_through_world_image_store(wid):
    [name] = format1(wid, CID, png())
    with pytest.raises(collections.ImageInCollectionError):
        world_images.delete_image(wid, name)
    with pytest.raises(collections.ImageInCollectionError):
        world_images.put_image(wid, name, png('blue'), 'png')
    assert world_images.put_image(wid, name, png(), 'png') == 'png'


@pytest.mark.parametrize('members', [[], ['../avatar'], ['https://example.test/image'], ['collection-image-x']])
def test_invalid_member_lists_are_refused(wid, members):
    with pytest.raises(collections.CollectionInvalidError):
        collections.publish(wid, uuid.uuid4().hex, members)


def test_duplicate_members_and_invalid_ids_are_refused(wid):
    name = collections.put_member(wid, png())
    with pytest.raises(collections.CollectionInvalidError):
        collections.publish(wid, uuid.uuid4().hex, [name, name])
    with pytest.raises(collections.CollectionInvalidError):
        collections.read(wid, '../avatar')
    with pytest.raises(collections.CollectionInvalidError):
        collections.put_member(wid, b'\x89PNG\r\n\x1a\ntruncated')


def test_missing_files_are_omitted_and_corruption_does_not_unlock_members(wid):
    cid = CID
    [name] = format1(wid, cid, png())
    world_images.image_path(wid, name).unlink()
    assert collections.available(wid, cid) == []
    path = worlds.world_root(wid) / 'assets' / 'image-collections' / f'{cid}.json'
    path.write_text('{broken')
    with pytest.raises(collections.CollectionInvalidError):
        collections.referenced(wid, name)


def test_two_publishers_cannot_change_an_accepted_collection(wid):
    names = [collections.put_member(wid, png(c)) for c in ('red', 'blue')]
    cid = uuid.uuid4().hex
    def publish(name):
        try:
            collections.publish(wid, cid, [name])
            return True
        except collections.CollectionInvalidError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(publish, names)) == 1
    assert len(collections.read(wid, cid)['members']) == 1


def test_case_aliases_cannot_mutate_members(wid):
    [name] = format1(wid, CID, png())
    with pytest.raises(collections.ImageInCollectionError):
        world_images.put_image(wid, name.upper(), png('blue'), 'png')
    with pytest.raises(collections.ImageInCollectionError):
        world_images.delete_image(wid, name.upper())
    assert world_images.image_path(wid, name).read_bytes() == png()


def test_windows_world_aliases_share_both_locks(wid):
    if not worlds.world_exists(wid.upper()):
        pytest.skip('filesystem has distinct case identities')
    assert locks.image_collection_lock(wid) is locks.image_collection_lock(wid.upper())
    assert locks.image_collection_job_lock(wid, 'a' * 64) is locks.image_collection_job_lock(wid.upper(), 'a' * 64)


def png_dpi(dpi, color='red', note=None):
    out = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if note:
        info.add_text('Comment', note)  # a tEXt chunk: sanitising strips it
    Image.new('RGB', (4, 4), color).save(out, 'PNG', dpi=(dpi, dpi), pnginfo=info)
    return out.getvalue()


def test_put_member_twice_same_bytes_ok(wid):
    first = collections.put_member(wid, png_dpi(72))
    assert collections.put_member(wid, png_dpi(72)) == first
    d = collections.image_directory(wid)
    ref = image_refs.read(d, first)
    assert ref is not None and ref.image
    assert assets.resolve(d, first).image_id == ref.image
    # The member is named for the bytes as received, not the stored blob's.
    assert first == collections.MEMBER_PREFIX + hashlib.sha256(png_dpi(72)).hexdigest()
    assert len(world_images.list_images(wid)) == 1


def test_put_member_refuses_a_member_whose_placement_names_other_pixels(wid):
    name = collections.put_member(wid, png())
    d = collections.image_directory(wid)
    other = image_store.ingest(png('blue'), 'png')
    assets.link_in(d, name, other.id)
    with pytest.raises(collections.CollectionInvalidError, match='has changed'):
        collections.put_member(wid, png())


def test_publish_with_ref_members(wid):
    # Sanitising strips the tEXt chunk, so the stored blob's bytes differ from
    # the received bytes the name hashes: the byte re-check must not apply.
    received = png_dpi(144, note='private note')
    name = collections.put_member(wid, received)
    d = collections.image_directory(wid)
    assert hashlib.sha256(assets.resolve(d, name).blob_path.read_bytes()).hexdigest() \
        != name[len(collections.MEMBER_PREFIX):]
    cid = uuid.uuid4().hex
    assert collections.publish(wid, cid, [name]) == {'format': 1, 'members': [name]}
    [member] = collections.available(wid, cid)
    assert member['url'].endswith('?v=' + assets.resolve(d, name).blob_sha256)
    # A ref-backed member whose blob has gone cannot be published.
    other = collections.put_member(wid, png('blue'))
    assets.resolve(d, other).blob_path.unlink()
    with pytest.raises(collections.CollectionInvalidError):
        collections.publish(wid, uuid.uuid4().hex, [other])


def test_guard_write_compares_identity_for_ref_members(wid):
    [name] = format1(wid, CID, png_dpi(72))
    collections.guard_write(wid, name, png_dpi(72))
    collections.guard_write(wid, name, png_dpi(144))  # same pixels, other metadata
    with pytest.raises(collections.ImageInCollectionError):
        collections.guard_write(wid, name, png('blue'))
    with pytest.raises(collections.ImageInCollectionError):
        collections.guard_write(wid, name)
    # An accepted same-pixels write through the store leaves the member intact.
    d = collections.image_directory(wid)
    before = assets.resolve(d, name).image_id
    world_images.put_image(wid, name, png_dpi(144), 'png')
    after = assets.resolve(d, name)
    assert after.image_id == before
    assert Image.open(after.blob_path).convert('RGB').getpixel((0, 0)) == (255, 0, 0)


def test_a_member_name_is_not_fileable_over_other_pixels(wid):
    format1(wid, OTHER, png('green'))  # the guard is on only beside format-1 state
    name = collections.MEMBER_PREFIX + hashlib.sha256(png('red')).hexdigest()
    d = collections.image_directory(wid)
    with pytest.raises(collections.CollectionInvalidError):
        world_images.put_image(wid, name, png('blue'), 'png')
    assert assets.resolve(d, name) is None and world_images.image_path(wid, name) is None
    assert world_images.put_image(wid, name, png('red'), 'png') == 'png'
    collections.publish(wid, uuid.uuid4().hex, [name])
    # Same bytes as received under a member's name is the member's own write.
    assert collections.put_member(wid, png('red')) == name


def test_a_member_alias_in_other_case_is_refused_too(wid):
    format1(wid, OTHER, png('green'))
    name = collections.MEMBER_PREFIX + hashlib.sha256(png('red')).hexdigest()
    with pytest.raises(collections.CollectionInvalidError):
        world_images.put_image(wid, name.upper(), png('blue'), 'png')


def _plant_legacy(wid, data):
    name = collections.MEMBER_PREFIX + hashlib.sha256(data).hexdigest()
    d = collections.image_directory(wid)
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{name}.png').write_bytes(data)
    return name, d


def test_legacy_member_rules_unchanged(wid):
    data = png()
    name, d = _plant_legacy(wid, data)
    assert collections.put_member(wid, data) == name
    assert image_refs.read(d, name) is None  # not migrated by a no-op put
    cid = uuid.uuid4().hex
    collections.publish(wid, cid, [name])
    [member] = collections.available(wid, cid)
    assert member['url'].endswith('?v=' + assets.image_version(d / f'{name}.png'))
    collections.guard_write(wid, name, data)
    with pytest.raises(collections.ImageInCollectionError):
        collections.guard_write(wid, name, png('blue'))
    with pytest.raises(collections.ImageInCollectionError):
        collections.guard_write(wid, name)
    # A legacy file that drifted from its name is neither re-adopted nor published.
    (d / f'{name}.png').write_bytes(png('green'))
    with pytest.raises(collections.CollectionInvalidError, match='has changed'):
        collections.put_member(wid, data)
    with pytest.raises(collections.CollectionInvalidError):
        collections.publish(wid, uuid.uuid4().hex, [name])


def _store_files():
    root = image_store.store_root()
    return sorted(p for p in root.rglob('*') if p.is_file())


def test_a_refused_overwrite_stores_nothing(wid):
    [name] = format1(wid, CID, png())
    before = _store_files()
    with pytest.raises(collections.ImageInCollectionError):
        collections.guard_write(wid, name, png('blue'))
    with pytest.raises(collections.ImageInCollectionError):
        world_images.put_image(wid, name, png('green'), 'png')
    assert _store_files() == before


def test_put_member_refusal_stores_nothing(wid):
    # A placement under the member's name that names other pixels, and bytes
    # the store has never seen: refusing them must not leave them stored.
    data = png()
    name = collections.MEMBER_PREFIX + hashlib.sha256(data).hexdigest()
    other = image_store.ingest(png('blue'), 'png').id
    assets.link_in(collections.image_directory(wid), name, other)
    before = _store_files()
    with pytest.raises(collections.CollectionInvalidError):
        collections.put_member(wid, data)
    assert _store_files() == before


# ---- format 2: members named by image id -----------------------------------

def _id(n):
    return f'px1-{n:064x}'


def _blob(image_id):
    return image_refs.resolve_ref(image_refs.Ref(name='', image=image_id, focus=None)).blob_path


@pytest.mark.parametrize('raw', [
    {'format': 3, 'members': [_id(1)]},
    {'format': True, 'members': [_id(1)]},
    {'format': '2', 'members': [_id(1)]},
    {'format': 2, 'members': ['collection-image-' + '0' * 64]},
    {'format': 2, 'members': [_id(1), _id(1)]},
    {'format': 2, 'members': [_id(n) for n in range(10001)]},
], ids=['format-3', 'true', 'string-2', 'not-an-id', 'duplicates', '10001-members'])
def test_read_accepts_both_formats_and_refuses_others(wid, raw):
    names = format1(wid, CID, png())
    assert collections.read(wid, CID) == {'format': 1, 'members': names}
    ids = format2(wid, OTHER, png('blue'), png('green'))
    assert collections.read(wid, OTHER) == {'format': 2, 'members': ids}
    write_manifest(wid, THIRD, {'format': 2, 'members': [_id(n) for n in range(10000)]})
    assert len(collections.read(wid, THIRD)['members']) == 10000
    write_manifest(wid, THIRD, raw)
    with pytest.raises(collections.CollectionInvalidError):
        collections.read(wid, THIRD)


def test_available_keeps_indices_stable_when_a_member_is_missing(wid):
    ids = format2(wid, CID, png('red'), png('blue'), png('green'))
    _blob(ids[1]).unlink()
    rows = collections.available(wid, CID)
    assert [r['index'] for r in rows] == [0, 2]
    assert [r['image_id'] for r in rows] == [ids[0], ids[2]]
    for row, image_id in zip(rows, (ids[0], ids[2]), strict=True):
        resolved = image_refs.resolve_ref(image_refs.Ref(name='', image=image_id, focus=None))
        assert row['path'] == resolved.blob_path
        assert row['url'] == (f'/api/worlds/{wid}/image-collections/{CID}/members/'
                              f"{row['index']}?v={resolved.blob_sha256}")
        assert row['url'] == collections.member_url(wid, CID, row['index'], resolved.blob_sha256)
    assert collections.member_path(wid, CID, 1) is None
    assert collections.member_path(wid, CID, 2) == _blob(ids[2])
    for index in (-1, 3, 10 ** 9):
        assert collections.member_path(wid, CID, index) is None


def test_format_1_rows_keep_library_urls_and_indices(wid):
    names = format1(wid, CID, png('red'), png('blue'))
    world_images.image_path(wid, names[0]).unlink()
    [row] = collections.available(wid, CID)
    d = collections.image_directory(wid)
    assert row['index'] == 1
    assert row['url'] == f'/api/worlds/{wid}/images/{names[1]}?v={assets.resolve(d, names[1]).blob_sha256}'
    assert collections.member_path(wid, CID, 1) == world_images.image_path(wid, names[1])
    assert collections.member_path(wid, CID, 0) is None


def test_format_2_manifests_never_guard_library_names(wid):
    format1(wid, CID, png('red'))  # format-1 state, so the guard is live
    ids = format2(wid, OTHER, png('blue'))
    assert collections.has_format1(wid)
    # A library name spelled like a format-2 member is no member of anything.
    assert not collections.referenced(wid, ids[0])
    assert world_images.put_image(wid, ids[0], png('green'), 'png') == 'png'
    world_images.delete_image(wid, ids[0])
    # Nor does a format-2 manifest make an ordinary library write fail.
    assert world_images.put_image(wid, 'harbour', png('blue'), 'png') == 'png'
    world_images.delete_image(wid, 'harbour')


def test_format_1_members_stay_guarded_beside_format_2(wid):
    [name] = format1(wid, CID, png('red'))
    format2(wid, OTHER, png('blue'))
    assert collections.referenced(wid, name)
    with pytest.raises(collections.ImageInCollectionError):
        world_images.put_image(wid, name, png('blue'), 'png')
    with pytest.raises(collections.ImageInCollectionError):
        world_images.delete_image(wid, name)
    assert world_images.put_image(wid, name, png('red'), 'png') == 'png'


def _journal(wid, raw):
    path = imports.job_path(wid, 'a' * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if isinstance(raw, str) else json.dumps(raw), encoding='utf-8')


def test_has_format1_fails_closed(wid):
    assert not collections.has_format1(wid)
    format2(wid, OTHER, png('blue'))
    assert not collections.has_format1(wid)
    _journal(wid, {'format': 2, 'members': []})
    assert not collections.has_format1(wid)
    for raw in ('{broken', {'format': 1, 'members': []}, {'format': True}, [], {'format': '2'}):
        _journal(wid, raw)
        assert collections.has_format1(wid), raw
    _journal(wid, {'format': 2, 'members': []})
    write_manifest(wid, CID, {'format': 9, 'members': []})
    assert collections.has_format1(wid)
    write_manifest(wid, CID, {'format': 1, 'members': [member_name(png())]})
    assert collections.has_format1(wid)
