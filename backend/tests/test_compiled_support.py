"""Ctime insertion safety, template leaf and campaign SQL opt-out (03-S6)."""

import importlib.util
import threading

import pytest

from grimoire import prompts
from grimoire.store import locks, statcache
from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root


@pytest.mark.parametrize('ctime', [9000000000, 10000000001])
def test_fresh_ctime_prevents_memo_even_with_old_mtime(monkeypatch, ctime):
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: 10000000000)
    pool = {}
    calls = []
    def compute():
        calls.append(1)
        return len(calls), (('Realm.md', 1, ctime, 5, 1),)
    assert statcache.memo_stamped('Realm', compute, pool=pool, max_entries=8) == 1
    assert not pool
    assert statcache.memo_stamped('Realm', compute, pool=pool, max_entries=8) == 2


def test_slow_compute_cannot_age_ctime_into_cached_state(monkeypatch):
    clock = [10000000000]
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: clock[0])
    pool = {}
    def compute():
        clock[0] += 2000000000
        return 'Realm', (('Realm.md', 1, 9500000000, 5, 1),)
    assert statcache.memo_stamped('Realm', compute, pool=pool, max_entries=8) == 'Realm'
    assert not pool


@pytest.mark.parametrize('mode', ['nested', 'nowait', 'all'])
def test_campaign_hold_tracking_and_thread_isolation(root, monkeypatch, mode):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    assert not locks.campaign_locks_held()
    context = locks.hold_all(['realm', 'saltmarch']) if mode == 'all' else locks.campaign_lock('realm')
    with context:
        assert locks.campaign_locks_held()
        if mode == 'nested':
            with locks.campaign_lock('realm'):
                assert locks.campaign_locks_held()
        elif mode == 'nowait':
            with locks.campaign_lock_nowait('realm') as held:
                assert held and locks.campaign_locks_held()
        seen = []
        worker = threading.Thread(target=lambda: seen.append(locks.campaign_locks_held()))
        worker.start()
        worker.join(5)
        assert seen == [False]
        assert locks.campaign_locks_held()
    assert not locks.campaign_locks_held()
    with locks.config_lock():
        assert not locks.campaign_locks_held()


def test_campaign_locked_live_derivation_performs_no_sql(cache, root, monkeypatch):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    kind = cache_tests._kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    def forbidden(*args, **kwargs):
        pytest.fail('campaign-locked caller opened SQLite')
    monkeypatch.setattr(cache._sqlite, 'connect', forbidden)
    with locks.campaign_lock('realm'), cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('body', path)], params={'stem': 'realm', 'settings': {}},
                              compute=lambda view, params: view.files[0].data.decode()) == 'Realm'
    assert not (root / '.cache' / 'compiled').exists()


def test_shared_template_resolver_is_leaf_and_preserves_public_alias(monkeypatch, tmp_path):
    assert importlib.util.find_spec('grimoire.template_paths') is not None
    monkeypatch.setenv('GRIMOIRE_TEMPLATES', str(tmp_path))
    assert prompts.templates_dir() == tmp_path


def test_campaign_sql_optout_keeps_first_attempt_delete_boundary(cache, root, monkeypatch):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    kind = cache_tests._kind(cache, monkeypatch)
    key = cache_tests._key(cache, kind)
    with cache.batch(root) as request:
        request.put(key, 'Realm')
    with cache.batch(root) as request:
        with locks.campaign_lock('realm'):
            assert not request.lookup(key).hit
        cache.purge_for_delete(root)
        request.put(key, 'Seraphine')
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
