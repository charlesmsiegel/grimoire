import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from grimoire.store import image_collections as collections
from grimoire.store import world_images, worlds


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
    name = collections.put_member(wid, png())
    collections.publish(wid, uuid.uuid4().hex, [name])
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
    name = collections.put_member(wid, png())
    cid = uuid.uuid4().hex
    collections.publish(wid, cid, [name])
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
