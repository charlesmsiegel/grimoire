import io

import pytest
from PIL import Image

from grimoire.store import image_collection_imports as imports
from grimoire.store import image_collections, worlds


def png(color='red'):
    b = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(b, 'PNG')
    return b.getvalue(), 'png'


@pytest.fixture
def wid(monkeypatch, tmp_path):
    monkeypatch.setenv('GRIMOIRE_HOME', str(tmp_path))
    return worlds.create_world('Realm')


def test_sampling_stops_on_duplicates_and_acceptance_is_offline(wid):
    stream = iter([png(), png('blue'), png(), png('blue')])
    job = imports.sample(wid, 'https://example.test/rotate', duplicate_limit=2,
                         delay=0, fetch_image=lambda _: next(stream))
    assert job['stop'] == 'duplicates'
    assert job['last'] == {'requests': 4, 'valid': 4, 'added': 2, 'duplicates': 2, 'failed': 0}
    imports.accept(wid, job['job_id'])
    manifest = image_collections.read(wid, job['collection_id'])
    assert len(manifest['members']) == 2 and 'source_url' not in manifest
    def must_not_fetch(_):
        raise AssertionError('accepted collections never fetch again')
    assert imports.sample(wid, 'https://example.test/rotate', fetch_image=must_not_fetch)['accepted']
    assert imports.job_path(wid, job['job_id']).exists()  # relinking still needs recovery


def test_limit_is_resumable_and_a_new_invocation_obtains_fresh_evidence(wid):
    first = imports.sample(wid, 'https://example.test/rotate', max_requests=1,
                           delay=0, fetch_image=lambda _: png())
    assert first['stop'] == 'limit' and not first['accepted']
    second = imports.sample(wid, 'https://example.test/rotate', duplicate_limit=2,
                            delay=0, fetch_image=lambda _: png())
    assert second['collection_id'] == first['collection_id']
    assert second['last']['requests'] == 2 and second['last']['added'] == 0
    assert second['totals']['requests'] == 3


def test_invalid_bytes_and_failures_interrupt_completion_evidence(wid):
    stream = iter([png(), png(), None, png(), png()])
    job = imports.sample(wid, 'https://example.test/rotate', duplicate_limit=2,
                         delay=0, fetch_image=lambda _: next(stream))
    assert job['last'] == {'requests': 5, 'valid': 4, 'added': 1, 'duplicates': 3, 'failed': 1}
    failed = imports.sample(wid, 'https://example.test/invalid', delay=0,
                            fetch_image=lambda _: (b'\x89PNG\r\n\x1a\ninvalid', 'png'))
    assert failed['stop'] == 'failures' and failed['last']['failed'] == 3
    with pytest.raises(image_collections.CollectionInvalidError):
        imports.accept(wid, failed['job_id'])


def test_bad_limits_are_refused_without_fetching(wid):
    with pytest.raises(ValueError):
        imports.sample(wid, 'https://example.test/rotate', max_requests=0)
