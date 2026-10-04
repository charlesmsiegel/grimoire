import io
import json

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


def test_publication_survives_a_crash_before_journal_confirmation(wid, monkeypatch):
    source = 'https://example.test/rotate'
    job = imports.sample(wid, source, max_requests=1, delay=0, fetch_image=lambda _: png())
    save = imports._save
    def interrupted(*_):
        raise OSError('interrupted after publication')
    monkeypatch.setattr(imports, '_save', interrupted)
    with pytest.raises(OSError):
        imports.accept(wid, job['job_id'])
    monkeypatch.setattr(imports, '_save', save)
    calls = []
    resumed = imports.sample(wid, source, max_requests=1, delay=0,
                             fetch_image=lambda _: calls.append('download') or png('blue'))
    assert calls == []
    assert resumed['accepted'] and resumed['stop'] == 'accepted'
    assert imports.accept(wid, job['job_id'])['members'] == job['members']


@pytest.mark.parametrize('source', [None, 'file:///avatar.png', '/api/worlds/realm/images/avatar'])
def test_non_http_sources_never_start_a_download(wid, source):
    calls = []
    with pytest.raises(ValueError):
        imports.sample(wid, source, fetch_image=lambda _: calls.append('download'))
    assert calls == []


@pytest.mark.parametrize('corruption', ['unreadable', 'retargeted'])
def test_resume_refuses_corrupt_or_retargeted_journals(wid, corruption):
    source = 'https://example.test/rotate'
    job = imports.sample(wid, source, max_requests=1, delay=0, fetch_image=lambda _: png())
    target = imports.job_path(wid, job['job_id'])
    if corruption == 'unreadable':
        target.write_text('{}', encoding='utf-8')
    else:
        job['source_url'] = 'https://example.test/different'
        target.write_text(json.dumps(job), encoding='utf-8')
    calls = []
    with pytest.raises(image_collections.CollectionInvalidError):
        imports.sample(wid, source, fetch_image=lambda _: calls.append('download'))
    assert calls == []


@pytest.mark.parametrize('corruption', ['missing-manifest', 'different-members'])
def test_accepted_state_corruption_never_resumes_downloads(wid, corruption):
    source = 'https://example.test/rotate'
    job = imports.sample(wid, source, max_requests=1, delay=0, fetch_image=lambda _: png())
    imports.accept(wid, job['job_id'])
    if corruption == 'missing-manifest':
        image_collections.manifest_path(wid, job['collection_id']).unlink()
        expected = FileNotFoundError
    else:
        target = imports.job_path(wid, job['job_id'])
        journal = json.loads(target.read_text(encoding='utf-8'))
        journal['members'] = [image_collections.put_member(wid, png('blue')[0])]
        target.write_text(json.dumps(journal), encoding='utf-8')
        expected = image_collections.CollectionInvalidError
    calls = []
    with pytest.raises(expected):
        imports.sample(wid, source, fetch_image=lambda _: calls.append('download'))
    assert calls == []
