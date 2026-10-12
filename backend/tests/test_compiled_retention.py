"""Device/schema liveness and contained cache retention (03-S5)."""

import os
import shutil
import sqlite3

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_open = cache_tests._open


def _retired(cache, root, session, prefix='b', version=None):
    version = cache.SCHEMA if version is None else version
    path = session.path.parent / (prefix * 32 + f'-v{version}.sqlite')
    shutil.copyfile(session.path, path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE cache_meta SET value=? WHERE name=?', (str(version), 'schema'))
    conn.close()
    os.utime(path, (1, 1))
    return path


def test_daily_lease_on_open_and_while_held(cache, root, monkeypatch):
    monkeypatch.setattr(cache.time, 'time', lambda: 100000)
    session = _open(cache, root)
    lease = session.path.with_suffix('.alive')
    assert lease.is_file() and lease.stat().st_mtime == 100000
    before = lease.stat().st_mtime_ns
    _open(cache, root)
    assert lease.stat().st_mtime_ns == before
    monkeypatch.setattr(cache.time, 'time', lambda: 186401)
    assert _open(cache, root) is session
    assert lease.stat().st_mtime == 186401


def test_fresh_foreign_lease_preserves_old_database(cache, root, monkeypatch):
    monkeypatch.setattr(cache.time, 'time', lambda: 10000000)
    session = _open(cache, root)
    old = _retired(cache, root, session)
    lease = old.with_suffix('.alive')
    lease.write_bytes(b'')
    os.utime(lease, (10000000, 10000000))
    cache._sweep_retention(session)
    assert old.exists()
    os.utime(lease, (1, 1))
    cache._sweep_retention(session)
    assert not old.exists() and not lease.exists()


def test_retention_database_fallback_and_current_schema_exemption(cache, root, monkeypatch):
    monkeypatch.setattr(cache.time, 'time', lambda: 10000000)
    session = _open(cache, root)
    other = _retired(cache, root, session)
    obsolete = _retired(cache, root, session, prefix='a', version=0)
    unknown = session.path.parent / ('c' * 32 + '-v1.sqlite')
    unknown.write_bytes(b'foreign')
    before = unknown.read_bytes()
    os.utime(unknown, (1, 1))
    os.utime(session.path, (1, 1))
    cache._sweep_retention(session)
    assert session.path.exists() and not other.exists() and not obsolete.exists()
    assert unknown.read_bytes() == before


@pytest.mark.parametrize('leaf', ['lease', 'purge', 'clock', 'intents'])
def test_control_link_rejected_without_external_change(cache, root, tmp_path, leaf):
    parent = root / '.cache' / 'compiled'
    parent.mkdir(parents=True)
    external = tmp_path / 'external'
    external.mkdir() if leaf == 'intents' else external.write_bytes(b'Realm')
    target = parent / {'lease': 'a' * 32 + f'-v{cache.SCHEMA}.alive', 'purge': 'purge',
                       'clock': '.clock', 'intents': 'delete-intents'}[leaf]
    try:
        target.symlink_to(external, target_is_directory=leaf == 'intents')
    except OSError:
        pytest.skip('symlink creation unavailable')
    assert _open(cache, root) is None
    if leaf != 'intents':
        assert external.read_bytes() == b'Realm'


@pytest.mark.parametrize('boundary', ['expired', 'fresh_lease', 'busy', 'foreign', 'journal'])
def test_retired_recovery_aside_uses_original_lease_and_exclusion(cache, root, monkeypatch, boundary):
    monkeypatch.setattr(cache.time, 'time', lambda: 10000000)
    session = _open(cache, root)
    old = _retired(cache, root, session)
    lease = old.with_suffix('.alive')
    aside = old.with_name(old.name + '.corrupt')
    old.rename(aside)
    lease.write_bytes(b'')
    os.utime(lease, (1, 1))
    descriptor = None
    if boundary == 'fresh_lease':
        os.utime(lease, (10000000, 10000000))
    elif boundary == 'busy':
        descriptor = cache._recovery_lock(root, old)
        assert descriptor is not None
    elif boundary == 'foreign':
        aside.write_bytes(b'Mara')
    elif boundary == 'journal':
        aside.with_name(aside.name + '-journal').write_bytes(b'Mara')
    before = aside.read_bytes()
    try:
        for _ in range(3):
            cache._sweep_retention(session)
        if boundary == 'expired':
            assert not aside.exists() and not lease.exists()
        else:
            assert aside.read_bytes() == before and lease.exists()
        assert session.path.exists()
    finally:
        if descriptor is not None:
            cache.proclock.release(descriptor)


def test_retired_main_does_not_authorize_foreign_aside_erasure(cache, root, monkeypatch):
    monkeypatch.setattr(cache.time, 'time', lambda: 10000000)
    session = _open(cache, root)
    old = _retired(cache, root, session)
    aside = old.with_name(old.name + '.corrupt')
    aside.write_bytes(b'Mara foreign file')
    before = aside.read_bytes()
    cache._sweep_retention(session)
    assert not old.exists()
    assert aside.read_bytes() == before
