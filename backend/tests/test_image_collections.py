import hashlib
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image, PngImagePlugin

from grimoire.store import assets, image_hash, image_refs, image_store, locks, world_images, worlds
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


def test_publish_writes_format_2_with_ids(wid):
    first = collections.put_member(wid, png())
    assert collections.put_member(wid, png()) == first
    second = collections.put_member(wid, png('blue'))
    assert image_hash.is_image_id(first) and image_hash.is_image_id(second)
    cid = uuid.uuid4().hex
    assert collections.publish(wid, cid, [first, second]) == {'format': 2, 'members': [first, second]}
    assert collections.read(wid, cid) == {'format': 2, 'members': [first, second]}
    on_disk = json.loads(collections.manifest_path(wid, cid).read_text(encoding='utf-8'))
    assert on_disk == {'format': 2, 'members': [first, second]}  # source-free
    assert world_images.list_images(wid) == []
    assert [r['image_id'] for r in collections.available(wid, cid)] == [first, second]
    assert collections.publish(wid, cid, [first, second]) == collections.read(wid, cid)
    with pytest.raises(collections.CollectionInvalidError):
        collections.publish(wid, cid, [second, first])


def test_publish_refuses_a_member_that_does_not_resolve(wid):
    present = collections.put_member(wid, png())
    gone = collections.put_member(wid, png('blue'))
    _blob(gone).unlink()
    for members in ([present, gone], [present, _id(7)]):
        cid = uuid.uuid4().hex
        with pytest.raises(collections.CollectionInvalidError):
            collections.publish(wid, cid, members)
        assert not collections.manifest_path(wid, cid).exists()


def test_a_published_manifest_is_immutable_across_formats(wid):
    [name] = format1(wid, CID, png())
    placed = assets.resolve(collections.image_directory(wid), name).image_id
    before = collections.manifest_path(wid, CID).read_bytes()
    # The same picture, by id, is still another manifest: format 1 stays.
    with pytest.raises(collections.CollectionInvalidError, match='immutable'):
        collections.publish(wid, CID, [placed])
    assert collections.manifest_path(wid, CID).read_bytes() == before
    ids = format2(wid, OTHER, png('blue'))
    assert collections.publish(wid, OTHER, ids) == {'format': 2, 'members': ids}
    with pytest.raises(collections.CollectionInvalidError, match='immutable'):
        collections.publish(wid, OTHER, [collections.put_member(wid, png('green'))])
    write_manifest(wid, THIRD, '{broken')
    with pytest.raises(collections.CollectionInvalidError):
        collections.publish(wid, THIRD, ids)


def test_members_are_protected_through_world_image_store(wid):
    [name] = format1(wid, CID, png())
    with pytest.raises(collections.ImageInCollectionError):
        world_images.delete_image(wid, name)
    with pytest.raises(collections.ImageInCollectionError):
        world_images.put_image(wid, name, png('blue'), 'png')
    assert world_images.put_image(wid, name, png(), 'png') == 'png'


@pytest.mark.parametrize('members', [[], ['../avatar'], ['https://example.test/image'], ['collection-image-x'],
                                     ['collection-image-' + '0' * 64]])
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


def test_put_member_returns_an_id_and_places_nothing(wid):
    first = collections.put_member(wid, png_dpi(72))
    assert image_hash.is_image_id(first)
    assert image_store.read(first) is not None
    # One picture is one member however its metadata differs: harvest
    # deduplicates by pixel id, not by the bytes as received.
    assert collections.put_member(wid, png_dpi(144, note='private note')) == first
    d = collections.image_directory(wid)
    assert world_images.list_images(wid) == []
    assert not d.exists() or not any(d.rglob('*'))
    # Sanitised: the stored blob carries no tEXt chunk.
    assert b'private note' not in _blob(first).read_bytes()
    cid = uuid.uuid4().hex
    collections.publish(wid, cid, [first])
    [member] = collections.available(wid, cid)
    assert member['url'].endswith('?v=' + image_store.read(first).blob_sha256)


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
    cid = uuid.uuid4().hex
    write_manifest(wid, cid, {'format': 1, 'members': [name]})
    [member] = collections.available(wid, cid)
    assert member['url'].endswith('?v=' + assets.image_version(d / f'{name}.png'))
    collections.guard_write(wid, name, data)
    with pytest.raises(collections.ImageInCollectionError):
        collections.guard_write(wid, name, png('blue'))
    with pytest.raises(collections.ImageInCollectionError):
        collections.guard_write(wid, name)
    # A legacy file that drifted from its name maps to no image.
    (d / f'{name}.png').write_bytes(png('green'))
    with pytest.raises(collections.CollectionInvalidError, match='has changed'):
        collections.format1_member_id(wid, name)


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
    collections.put_member(wid, png('blue'))
    before = _store_files()
    with pytest.raises(collections.CollectionInvalidError):
        collections.put_member(wid, b'\x89PNG\r\n\x1a\ntruncated')
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


def test_member_index_is_canonical_decimal():
    assert [collections.member_index(n) for n in ('0', '7', '9999', '10000')] == [0, 7, 9999, 10000]
    for n in ('', '00', '01', '-1', '+1', '1.0', ' 1', '1 ', 'abc', '٢', '100000', '9' * 5000, '1\n'):
        assert collections.member_index(n) is None, n


def test_validate_is_the_content_only_manifest_rule():
    ids = [_id(1), _id(2)]
    assert collections.validate({'format': 2, 'members': ids}) == {'format': 2, 'members': ids}
    name = member_name(png())
    assert collections.validate({'format': 1, 'members': [name]}) == {'format': 1, 'members': [name]}
    for raw in ({'format': True, 'members': ids}, {'format': 2, 'members': [name]},
                {'format': 2, 'members': ids, 'extra': 1}, [], {'format': 2, 'members': []}):
        with pytest.raises(collections.CollectionInvalidError):
            collections.validate(raw)
    assert collections._validated is collections.validate  # the name world_bundle calls today
