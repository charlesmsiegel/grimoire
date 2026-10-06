import hashlib
import io
import json

import pytest
from PIL import Image, PngImagePlugin

from grimoire.store import (
    assets,
    image_collections,
    image_hash,
    image_refs,
    image_store,
    world_images,
    worlds,
)
from grimoire.store import image_collection_imports as imports
from tests.collection_fixtures import format1, format1_journal, member_name, place

CID = '0123456789abcdef0123456789abcdef'
SOURCE = 'https://example.test/rotate'


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


# ---- format 2: journals hold image ids --------------------------------------

def pic(color='red'):
    return png(color)[0]


def _never(_):
    raise AssertionError('must not download')


def _on_disk(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _placed(wid, name):
    return image_refs.read(image_collections.image_directory(wid), name).image


def _store_files():
    return sorted(p for p in image_store.store_root().rglob('*') if p.is_file())


def test_a_new_journal_is_format_2_and_accepts_as_format_2(wid):
    stream = iter([png(), png('blue')])
    job = imports.sample(wid, SOURCE, max_requests=2, delay=0, fetch_image=lambda _: next(stream))
    target = imports.job_path(wid, job['job_id'])
    assert _on_disk(target)['format'] == 2
    assert all(image_hash.is_image_id(m) for m in job['members']) and len(job['members']) == 2
    assert 'legacy_members' not in job
    imports.accept(wid, job['job_id'])
    assert image_collections.read(wid, job['collection_id']) == {'format': 2, 'members': job['members']}
    assert world_images.list_images(wid) == []
    assert not image_collections.has_format1(wid)


def test_a_format_1_journal_converts_and_resumes_as_format_2(wid):
    placed = place(wid, pic('red'))
    legacy = pic('green')
    legacy_name = member_name(legacy)
    d = image_collections.image_directory(wid)
    (d / f'{legacy_name}.png').write_bytes(legacy)
    names = [*placed, legacy_name]
    target = format1_journal(wid, SOURCE, CID, names)
    assert image_collections.has_format1(wid)

    job = imports.sample(wid, SOURCE, max_requests=1, delay=0, fetch_image=lambda _: png('blue'))
    stored = _on_disk(target)
    assert stored['format'] == 2 and job['format'] == 2
    assert stored['legacy_members'] == names
    legacy_id = image_store.ingest(legacy, 'png').id
    assert stored['members'][:2] == [_placed(wid, placed[0]), legacy_id]
    assert len(stored['members']) == 3  # resumed: the new sample was appended
    assert stored['totals']['requests'] == 3
    assert not image_collections.has_format1(wid)

    imports.accept(wid, job['job_id'])
    assert image_collections.read(wid, CID) == {'format': 2, 'members': stored['members']}


def test_a_format_1_journal_converts_when_first_read_by_accept(wid):
    names = place(wid, pic('red'), pic('blue'))
    target = format1_journal(wid, SOURCE, CID, names)
    job = imports.accept(wid, hashlib.sha256(SOURCE.encode()).hexdigest())
    ids = [_placed(wid, n) for n in names]
    assert job['members'] == ids and job['legacy_members'] == names
    assert _on_disk(target)['format'] == 2
    assert image_collections.read(wid, CID) == {'format': 2, 'members': ids}


def _png_with(note):
    out = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text('Comment', note)
    Image.new('RGB', (4, 4), 'red').save(out, 'PNG', pnginfo=info)
    return out.getvalue()


def test_two_format_1_names_for_one_picture_convert_to_one_member(wid):
    names = place(wid, _png_with('one'), _png_with('two'))
    assert names[0] != names[1]
    format1_journal(wid, SOURCE, CID, names)
    job = imports.accept(wid, hashlib.sha256(SOURCE.encode()).hexdigest())
    assert job['members'] == [_placed(wid, names[0])]
    assert job['legacy_members'] == names


def test_a_format_1_journal_with_a_mismatched_legacy_file_refuses(wid):
    name = member_name(pic('red'))
    d = image_collections.image_directory(wid)
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{name}.png').write_bytes(pic('green'))  # the name says red
    target = format1_journal(wid, SOURCE, CID, [name])
    before, files = target.read_bytes(), _store_files()
    for attempt in (lambda: imports.sample(wid, SOURCE, delay=0, fetch_image=_never),
                    lambda: imports.accept(wid, hashlib.sha256(SOURCE.encode()).hexdigest())):
        with pytest.raises(image_collections.CollectionInvalidError):
            attempt()
    assert target.read_bytes() == before
    assert _store_files() == files  # the wrong picture was never ingested
    assert not image_collections.manifest_path(wid, CID).exists()
    assert image_collections.has_format1(wid)


def test_an_unmappable_format_1_journal_refuses(wid):
    names = [*place(wid, pic('red')), member_name(pic('blue'))]  # nothing placed for blue
    target = format1_journal(wid, SOURCE, CID, names)
    before = target.read_bytes()
    with pytest.raises(image_collections.CollectionInvalidError):
        imports.sample(wid, SOURCE, delay=0, fetch_image=_never)
    with pytest.raises(image_collections.CollectionInvalidError):
        imports.accept(wid, hashlib.sha256(SOURCE.encode()).hexdigest())
    assert target.read_bytes() == before
    assert not image_collections.manifest_path(wid, CID).exists()
    assert image_collections.has_format1(wid)  # the guard holds until it converts


def test_a_crashed_format_1_publication_reconciles_after_the_upgrade(wid):
    # An older grimoire published the format-1 manifest and crashed before
    # confirming the journal.
    names = format1(wid, CID, pic('red'), pic('blue'))
    manifest = image_collections.manifest_path(wid, CID).read_bytes()
    target = format1_journal(wid, SOURCE, CID, names, accepted=False)
    job = imports.sample(wid, SOURCE, delay=0, fetch_image=_never)
    assert job['accepted'] and job['stop'] == 'accepted'
    stored = _on_disk(target)
    assert stored['format'] == 2 and stored['accepted']
    assert stored['legacy_members'] == names
    assert stored['members'] == [_placed(wid, n) for n in names]
    assert image_collections.manifest_path(wid, CID).read_bytes() == manifest  # never republished
    assert imports.accept(wid, job['job_id'])['accepted']


def test_a_converted_journal_refuses_a_manifest_naming_other_pictures(wid):
    format1(wid, CID, pic('red'))
    others = place(wid, pic('blue'))
    format1_journal(wid, SOURCE, CID, others)
    with pytest.raises(image_collections.CollectionInvalidError, match='differs'):
        imports.sample(wid, SOURCE, delay=0, fetch_image=_never)


def test_an_accepted_format_1_journal_with_a_missing_member_still_reconciles(wid):
    names = format1(wid, CID, pic('red'), pic('blue'))
    target = format1_journal(wid, SOURCE, CID, names, accepted=True)
    d = image_collections.image_directory(wid)
    (d / 'image-refs' / f'{names[1]}.json').unlink()  # the member file is gone
    assert assets.resolve(d, names[1]) is None
    before = target.read_bytes()
    job = imports.sample(wid, SOURCE, delay=0, fetch_image=_never)
    assert job['accepted'] and job['members'] == names
    assert imports.accept(wid, job['job_id'])['members'] == names
    assert target.read_bytes() == before  # not converted: the manifest is authoritative
    assert image_collections.read(wid, CID) == {'format': 1, 'members': names}


def test_an_unparseable_journal_keeps_the_guard_on(wid):
    target = imports.job_path(wid, hashlib.sha256(SOURCE.encode()).hexdigest())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{broken', encoding='utf-8')
    for attempt in (lambda: imports.sample(wid, SOURCE, delay=0, fetch_image=_never),
                    lambda: imports.accept(wid, hashlib.sha256(SOURCE.encode()).hexdigest())):
        with pytest.raises(image_collections.CollectionInvalidError):
            attempt()
    assert target.read_text(encoding='utf-8') == '{broken'
    assert image_collections.has_format1(wid)


def test_job_path_is_under_the_journal_directory(wid):
    assert imports.job_path(wid, 'a' * 64) == image_collections.journal_directory(wid) / f"{'a' * 64}.json"
