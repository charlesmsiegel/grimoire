"""Deletes bind their library across local and cross-process pointer moves."""

import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from grimoire import store
from grimoire.routes import campaigns, runs, worlds
from grimoire.store import inference_retired, paths, proclock
from tests import test_compiled as cache_tests

cache = cache_tests.cache


def _record(root, kind):
    folder = root / kind / 'realm'
    folder.mkdir(parents=True)
    (folder / ('world.md' if kind == 'worlds' else 'campaign.md')).write_text(
        '---\nid: realm\nname: Realm\n---\nRealm\n', encoding='utf-8')
    return folder


def _request():
    return SimpleNamespace(scope={}, app=SimpleNamespace(state=SimpleNamespace(runs=runs.RunRegistry())))


def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv('GRIMOIRE_HOME', raising=False)
    pointer = tmp_path / '.grimoire.json'
    monkeypatch.setattr(paths, 'pointer_path', lambda: pointer)
    monkeypatch.setattr(paths, 'DEFAULT_HOME', tmp_path / 'default')
    old, new = tmp_path / 'Realm', tmp_path / 'Saltmarch'
    paths.set_data_dir(old)
    return old, new, pointer


def test_nested_pins_reset_and_isolate_threads(monkeypatch, tmp_path):
    old, new, _pointer = _isolate(monkeypatch, tmp_path)
    seen = []
    with paths.pin_library(new):
        assert paths.home() == new
        worker = threading.Thread(target=lambda: seen.append(paths.home()))
        worker.start()
        worker.join(5)
        with pytest.raises(ValueError), paths.pin_library(old):
            assert paths.home() == old
            raise ValueError('reset')
        assert paths.home() == new
    assert paths.home() == old and seen == [old]


@pytest.mark.parametrize('kind', ['worlds', 'campaigns'])
def test_cooperative_pointer_move_waits_until_delete_then_purge_names_old(cache, monkeypatch, tmp_path, kind):
    old, new, _pointer = _isolate(monkeypatch, tmp_path)
    record = _record(old, kind)
    request = _request()
    prepared, released, moved = threading.Event(), threading.Event(), threading.Event()
    actual = cache.prepare_delete
    def prepare(root):
        prepared.set()
        assert released.wait(5)
        return actual(root)
    monkeypatch.setattr(cache, 'prepare_delete', prepare)
    purged = []
    def purge(root, *, intent=None):
        assert moved.wait(5)
        assert paths.home() == new
        assert request.app.state.runs._maintenance_holds == 0
        assert not store.locks.holds_campaign('realm')
        purged.append(root)
    monkeypatch.setattr(cache, 'purge_for_delete', purge)
    errors = []
    def remove():
        try:
            (worlds.delete_world if kind == 'worlds' else campaigns.delete_campaign)('realm', request)
        except (OSError, HTTPException, AssertionError) as error:
            errors.append(error)
    remover = threading.Thread(target=remove)
    remover.start()
    assert prepared.wait(5)
    mover = threading.Thread(target=lambda: (paths.set_data_dir(new), moved.set()))
    mover.start()
    assert not moved.wait(0.05)
    released.set()
    remover.join(10)
    mover.join(10)
    assert not errors and not remover.is_alive() and not mover.is_alive()
    assert not record.exists() and purged == [old]


@pytest.mark.parametrize('kind', ['worlds', 'campaigns'])
def test_real_other_process_pointer_move_cannot_redirect_delete_or_cleanup(cache, monkeypatch, tmp_path, kind):
    old, new, pointer = _isolate(monkeypatch, tmp_path)
    left, right = _record(old, kind), _record(new, kind)
    note = inference_retired.Note('realm', 'campaign:realm', 'Realm', '', '', 'unrepresentable', 'Realm')
    for root in (old, new):
        monkeypatch.setenv('GRIMOIRE_HOME', str(root))
        inference_retired.record_notes([note])
        with cache.batch(root) as request:
            request.store_written(cache_tests._kind(cache, monkeypatch), [], 'Realm',
                                        params={'stem': 'realm', 'settings': {}})
    monkeypatch.delenv('GRIMOIRE_HOME', raising=False)
    notes_before = (new / inference_retired.FILENAME).read_bytes()
    cache_before = cache_tests._path(cache, new).read_bytes()
    actual = cache.prepare_delete
    def repointed(root):
        intent = actual(root)
        script = ("import sys; from pathlib import Path; from grimoire.store import paths; "
                  "paths.pointer_path=lambda:Path(sys.argv[2]); paths.set_data_dir(Path(sys.argv[1]))")
        env = {**os.environ, 'GRIMOIRE_HOME': ''}
        child = subprocess.run([sys.executable, '-c', script, str(new), str(pointer)],
                               env=env, capture_output=True, text=True, timeout=20, check=False)
        assert child.returncode == 0, child.stderr
        return intent
    monkeypatch.setattr(cache, 'prepare_delete', repointed)
    assert (worlds.delete_world if kind == 'worlds' else campaigns.delete_campaign)('realm', _request()) == {'ok': True}
    assert not left.exists() and right.exists() and paths.home() == new
    assert (new / inference_retired.FILENAME).read_bytes() == notes_before
    assert cache_tests._path(cache, new).read_bytes() == cache_before
    if kind == 'campaigns':
        assert json.loads((old / inference_retired.FILENAME).read_text(encoding='utf-8'))['notes'] == []


@pytest.mark.parametrize('kind', ['worlds', 'campaigns'])
def test_known_missing_delete_prepares_nothing(cache, monkeypatch, tmp_path, kind):
    _isolate(monkeypatch, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('known refusal prepared a cache delete')
    monkeypatch.setattr(cache, 'prepare_delete', forbidden)
    monkeypatch.setattr(cache, 'purge_for_delete', forbidden)
    with pytest.raises(HTTPException) as refusal:
        (worlds.delete_world if kind == 'worlds' else campaigns.delete_campaign)('realm', _request())
    assert refusal.value.status_code == 404


@pytest.mark.parametrize('kind', ['worlds', 'campaigns'])
def test_prepared_mutation_failure_still_attempts_purge(cache, monkeypatch, tmp_path, kind):
    old, _new, _pointer = _isolate(monkeypatch, tmp_path)
    _record(old, kind)
    purged = []
    monkeypatch.setattr(cache, 'purge_for_delete', lambda root, **kwargs: purged.append(root))
    def refused(*args):
        raise OSError('remove refused')
    monkeypatch.setattr(store.worlds if kind == 'worlds' else store.campaigns,
                        'delete_world' if kind == 'worlds' else 'delete_campaign', refused)
    with pytest.raises(OSError):
        (worlds.delete_world if kind == 'worlds' else campaigns.delete_campaign)('realm', _request())
    assert purged == [old]


def test_crash_after_authoritative_removal_recovers_even_after_404_retry(cache, monkeypatch, tmp_path):
    old, _new, _pointer = _isolate(monkeypatch, tmp_path)
    record = _record(old, 'worlds')
    kind = cache_tests._kind(cache, monkeypatch)
    key = cache_tests._key(cache, kind)
    with cache.batch(old) as request:
        request.put(key, 'Realm')
    script = "import os,sys\nfrom pathlib import Path\nfrom types import SimpleNamespace\nfrom grimoire import store\nfrom grimoire.routes import worlds,runs\nfrom grimoire.store import maintenance_reports,proclock\nproclock.lock_dir=lambda:Path(sys.argv[1])\nmaintenance_reports.device_key=lambda root:'a'*64\nactual=store.worlds.delete_world\ndef crash(wid):\n    actual(wid)\n    os._exit(0)\nstore.worlds.delete_world=crash\nrequest=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runs=runs.RunRegistry())))\nworlds.delete_world('realm',request)\nraise RuntimeError('crash injection did not run')\n"
    child = subprocess.run([sys.executable, '-c', script, str(proclock.lock_dir())],
                           env={**os.environ, 'GRIMOIRE_HOME': str(old)},
                           capture_output=True, text=True, timeout=20, check=False)
    assert child.returncode == 0, child.stderr
    assert not record.exists()
    intents = old / '.cache' / 'compiled' / 'delete-intents'
    assert len(list(intents.iterdir())) == 1
    with pytest.raises(HTTPException) as refusal:
        worlds.delete_world('realm', _request())
    assert refusal.value.status_code == 404
    with cache.batch(old) as request:
        assert not request.lookup(key).hit
    assert list(intents.iterdir()) == []


@pytest.mark.parametrize('failed', [False, True])
def test_http_bookkeeping_cannot_stamp_replacement_library(cache, client, monkeypatch, tmp_path, failed):
    old, new, pointer = _isolate(monkeypatch, tmp_path)
    left, right = _record(old, 'campaigns'), _record(new, 'campaigns')
    (left / 'revision.txt').write_text('b' * 32, encoding='ascii')
    (right / 'revision.txt').write_text('a' * 32, encoding='ascii')
    before = (right / 'revision.txt').read_bytes()
    actual = cache.prepare_delete
    def repointed(root):
        intent = actual(root)
        script = ("import sys; from pathlib import Path; from grimoire.store import paths; "
                  "paths.pointer_path=lambda:Path(sys.argv[2]); paths.set_data_dir(Path(sys.argv[1]))")
        child = subprocess.run([sys.executable, '-c', script, str(new), str(pointer)],
                               env={**os.environ, 'GRIMOIRE_HOME': ''}, capture_output=True,
                               text=True, timeout=20, check=False)
        assert child.returncode == 0, child.stderr
        return intent
    monkeypatch.setattr(cache, 'prepare_delete', repointed)
    if failed:
        def refused(cid):
            raise OSError('remove refused')
        monkeypatch.setattr(store.campaigns, 'delete_campaign', refused)
        with pytest.raises(OSError):
            client.delete('/api/campaigns/realm')
    else:
        assert client.delete('/api/campaigns/realm').status_code == 200
    assert (right / 'revision.txt').read_bytes() == before
    assert not (right / 'activity.md').exists()
    if failed:
        assert (left / 'revision.txt').read_text(encoding='ascii') != 'b' * 32
