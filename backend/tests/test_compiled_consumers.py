"""Measured supplied-text counts and ctime-aware outer reader parity."""

import os
import threading
import time

import pytest

from grimoire.store import locks, search, statcache, tokens
from tests import frozen_copy
from tests import test_compiled as cache_tests
from tests.test_token_counting import _Enc

cache = cache_tests.cache
root = cache_tests.root


@pytest.fixture
def encoder(monkeypatch):
    value = _Enc()
    monkeypatch.setattr(tokens, '_encoder', lambda: value)
    monkeypatch.setattr(tokens, 'PERSIST_MIN_CHARS', 0)
    return value


def test_supplied_body_is_not_vouched_for_by_unchanged_path(root, monkeypatch, encoder):
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: path.stat().st_ctime_ns + 2000000000)
    assert tokens.record_tokens(path, 'Mara') == 1
    assert tokens.record_tokens(path, 'Mara Seraphine Winifred') == 3


def test_encoder_recovery_cannot_reuse_heuristic(root, monkeypatch):
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: path.stat().st_ctime_ns + 2000000000)
    monkeypatch.setattr(tokens, '_encoder', lambda: None)
    assert tokens.record_tokens(path, 'Seraphine') == 3
    encoder = _Enc()
    monkeypatch.setattr(tokens, '_encoder', lambda: encoder)
    assert tokens.record_tokens(path, 'Seraphine') == 1
    assert tokens.last_counter() == tokens.ENCODING


def test_persistent_encoder_restart_reuses_exact_text_across_paths(cache, root, monkeypatch, encoder):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine') == 2
    cache.close()
    if hasattr(tokens, '_RECORD_POOL'):
        tokens._RECORD_POOL.clear()
    monkeypatch.setattr(encoder, 'encode', lambda text: pytest.fail('persisted encoder count was recomputed'))
    assert tokens.record_tokens(root / 'Saltmarch.md', 'Mara Seraphine') == 2
    assert tokens.last_counter() == tokens.ENCODING


def test_empty_record_count_clears_ambiguous_attribution(cache, root, monkeypatch, encoder):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    tokens.count_tokens('Mara')
    assert tokens.record_tokens(root / 'Realm.md', '') == 0
    assert tokens.last_counter() == ''
    assert not (root / '.cache').exists()


# Search failed the persisted-cost gate (spec ??8/14). Its original signature
# wrapper remains unchanged, including its restored-mtime limitation; full
# change-time validation is asserted by the source/statcache suites and by the
# selected file-derived shelf memos below, rather than claimed for search.


def test_campaign_held_token_call_has_no_sql(cache, root, monkeypatch, encoder):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    monkeypatch.setattr(cache._sqlite, 'connect', lambda *args, **kwargs: pytest.fail('campaign hold reached SQLite'))
    with locks.campaign_lock('realm'):
        assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine') == 2


def test_record_counter_attribution_is_thread_local(root, monkeypatch, encoder):
    tokens.count_tokens('Mara')
    seen = []
    def worker():
        assert tokens.record_tokens(root / 'Realm.md', '') == 0
        seen.append(tokens.last_counter())
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(5)
    assert seen == ['']
    assert tokens.last_counter() == tokens.ENCODING


def test_unavailable_change_metadata_never_authorizes_persisted_source(cache, root, monkeypatch):
    path = root / 'Realm.md'
    path.write_bytes(b'Mara')
    kind = cache_tests._kind(cache, monkeypatch)
    monkeypatch.setattr(statcache.file_stats, 'change_ns', lambda *args: None)
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input('body', path)).data == b'Mara'
        assert not request._sources


@pytest.mark.parametrize('mode', ['offline', 'encode_failure', 'ambiguous'])
def test_failed_or_unattributed_count_never_enters_sql(cache, root, monkeypatch, encoder, mode):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    if mode == 'offline':
        monkeypatch.setattr(tokens, '_encoder', lambda: None)
    elif mode == 'encode_failure':
        def refused(text):
            raise ValueError('encoding refused')
        monkeypatch.setattr(encoder, 'encode', refused)
    else:
        monkeypatch.setattr(tokens, 'count_tokens', lambda text: 7)
    assert tokens.record_tokens(root / 'Realm.md', 'Seraphine') > 0
    session = cache_tests._open(cache, root)
    assert session.conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
    assert tokens.last_counter() != tokens.ENCODING


def test_short_encoder_body_retains_outer_memo_without_creating_sql(cache, root, monkeypatch, encoder):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    monkeypatch.setattr(tokens, 'PERSIST_MIN_CHARS', 262144)
    path = root / 'Realm.md'
    path.write_bytes(b'Mara')
    aged = statcache.time.time_ns() + 2000000000
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: aged)
    assert tokens.record_tokens(path, 'Mara') == 1
    monkeypatch.setattr(encoder, 'encode', lambda text: pytest.fail('outer memo recomputed'))
    assert tokens.record_tokens(path, 'Mara') == 1
    assert not (root / '.cache').exists()


def test_frozen_copy_readers_match_cold_warm_off_restart(cache, tmp_path, monkeypatch):
    from grimoire import store
    from grimoire.store.campaigns import read as campaign_read
    from grimoire.store.worlds import read as world_read
    from tests.fixtures.frozen_campaign import sweep
    original = frozen_copy.digest()
    root = frozen_copy.copy_home(tmp_path / 'Realm')
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    store.migrations.migrate_scene_ids()
    # Token counts are the kind spec 03 wires; every frozen body is admitted
    # so the cold, warm, off and restarted states each read or write SQLite.
    encoder = _Enc()
    monkeypatch.setattr(tokens, '_encoder', lambda: encoder)
    monkeypatch.setattr(tokens, 'PERSIST_MIN_CHARS', 0)
    bodies = [(path, path.read_text(encoding='utf-8', errors='replace'))
              for path in sorted(root.rglob('*.md'))]
    def counts():
        return [tokens.record_tokens(path, body) for path, body in bodies]
    def snapshot():
        return {'sweep': sweep.sweep(root), 'worlds': world_read.list_worlds(),
                'campaigns': campaign_read.list_campaigns(), 'search': list(search.walk()),
                'tokens': counts()}
    def restart():
        cache.close()
        tokens._RECORD_POOL.clear()
        search._POOL.clear()
        statcache._cache.clear()
    expected = snapshot()
    assert any(expected['tokens'])
    assert snapshot() == expected
    monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    restart()
    assert snapshot() == expected
    monkeypatch.delenv('GRIMOIRE_COMPILED_CACHE')
    restart()
    # The sweep counts tokens live itself; only the persisted kind must not.
    with monkeypatch.context() as patched:
        patched.setattr(encoder, 'encode', lambda text: pytest.fail('a persisted count was re-encoded'))
        assert counts() == expected['tokens']
    restart()
    assert snapshot() == expected
    assert frozen_copy.digest() == original


@pytest.mark.parametrize('label', ['world', 'campaign'])
def test_live_shelf_row_memo_detects_restored_mtime_and_declares_dirname(root, monkeypatch, label):
    from grimoire.store.campaigns import read as campaign_read
    from grimoire.store.worlds import read as world_read
    reader = world_read._world_row if label == 'world' else campaign_read._campaign_row
    path = root / (label + '.md')
    path.write_bytes(b'---\nname: Mara\n---\nRealm')
    before = path.stat()
    aged = statcache.time.time_ns() + 2000000000
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: aged)
    assert reader(root, path)['name'] == 'Mara'
    # Native filesystem timestamps can share a tick for two immediate writes.
    time.sleep(0.02)
    path.write_bytes(b'---\nname: MARA\n---\nRealm')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert reader(root, path)['name'] == 'MARA'
    path.write_bytes(b'Realm')
    assert reader(root / 'Realm', path)['name'] == 'Realm'
    assert reader(root / 'Saltmarch', path)['name'] == 'Saltmarch'


def test_supplied_surrogate_keeps_existing_encoder_fallback(root, encoder):
    body = 'Mara ' + chr(0xD800)
    expected = tokens.count_tokens(body)
    assert tokens.record_tokens(root / 'Realm.md', body) == expected
    assert tokens.last_counter() == tokens.ENCODING


def test_body_below_the_cutoff_does_not_pay_restart_bootstrap(cache, root, monkeypatch):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    monkeypatch.setattr(tokens, '_encoder', _Enc)
    # Encoding a body this short costs less than opening the cache for it.
    body = 'Mara ' * ((tokens.PERSIST_MIN_CHARS - 1) // 5)
    assert tokens.record_tokens(root / 'Realm.md', body) == len(body.split())
    assert not (root / '.cache').exists()


def test_warm_supplied_body_does_not_rehash_text(cache, root, monkeypatch, encoder):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    path = root / 'Realm.md'
    path.write_bytes(b'Mara')
    now = statcache.time.time_ns() + 2000000000
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: now)
    assert tokens.record_tokens(path, 'Mara Seraphine') == 2
    monkeypatch.setattr(tokens.hashlib, 'sha256', lambda *args: pytest.fail('warm supplied text was hashed again'))
    assert tokens.record_tokens(path, 'Mara Seraphine') == 2


def test_supplied_digest_memo_is_bounded_and_skips_oversized_text(root, monkeypatch, encoder):
    tokens._large_body_digest.cache_clear()
    for body in ('Mara', 'Seraphine', 'Winifred'):
        tokens.record_tokens(root / 'Realm.md', body)
    assert tokens._large_body_digest.cache_info().currsize == 2
    tokens._large_body_digest.cache_clear()
    monkeypatch.setattr(tokens, 'MAX_DIGEST_MEMO_BYTES', 1)
    tokens.record_tokens(root / 'Realm.md', 'Mara')
    assert tokens._large_body_digest.cache_info().currsize == 0


def test_supplied_text_counter_does_not_consult_file_currency(root, monkeypatch, encoder):
    monkeypatch.setattr(statcache, 'stamp', lambda *args: pytest.fail('pure supplied text consulted file currency'))
    assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine') == 2
    assert tokens.record_tokens(root / 'Realm.md', 'Mara') == 1


def test_optional_digest_allocation_failure_keeps_encoder_fallback(root, monkeypatch, encoder):
    def failed_digest(text):
        raise MemoryError('optional UTF8 hash allocation')
    monkeypatch.setattr(tokens, '_record_digest', failed_digest)
    monkeypatch.setattr(tokens.compiled, 'batch', lambda *args: pytest.fail('failed digest reached SQL'))
    assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine Winifred') == 3
    assert tokens.last_counter() == tokens.ENCODING


def test_warm_text_above_the_cutoff_does_not_repeat_hash(root, monkeypatch):
    monkeypatch.setattr(tokens, '_encoder', _Enc)
    tokens._large_body_digest.cache_clear()
    body = 'Mara ' * (tokens.PERSIST_MIN_CHARS // 5 + 1)
    count = len(body.split())
    assert tokens.record_tokens(root / 'Realm.md', body) == count
    real = tokens.hashlib.sha256

    def sha256(data=b'', *args, **kwargs):
        # Keys are hashed on every lookup; it is the BODY that must not be.
        if len(data) >= tokens.PERSIST_MIN_CHARS:
            pytest.fail('warm body rehashed')
        return real(data, *args, **kwargs)

    monkeypatch.setattr(tokens.hashlib, 'sha256', sha256)
    assert tokens.record_tokens(root / 'Realm.md', body) == count


def test_warm_record_sweep_matches_new_equal_bodies_without_hashing(root, monkeypatch, encoder):
    bodies = ['Mara ' * 60000 + name for name in ('Mara', 'Seraphine', 'Winifred')]
    paths = [root / (str(position) + '.md') for position in range(3)]
    expected = [tokens.record_tokens(path, body) for path, body in zip(paths, bodies, strict=True)]
    tokens._large_body_digest.cache_clear()
    monkeypatch.setattr(tokens, '_record_digest', lambda *args: pytest.fail('equal warm sweep rehashed'))
    for path, body, count in zip(paths, bodies, expected, strict=True):
        reparsed = (' ' + body)[1:]
        assert reparsed == body and reparsed is not body
        assert tokens.record_tokens(path, reparsed) == count


def test_record_body_pool_respects_retained_byte_budget(root, monkeypatch, encoder):
    tokens._RECORD_POOL.clear()
    monkeypatch.setattr(tokens, 'MAX_RECORD_MEMO_BYTES', 200)
    for position in range(10):
        tokens.record_tokens(root / (str(position) + '.md'), 'Mara ' * 10)
    assert sum(tokens.sys.getsizeof(entry[0]) for entry in tokens._RECORD_POOL.values()) <= 200
    assert len(tokens._RECORD_POOL) <= 2


def test_record_body_pool_keeps_a_true_running_byte_total(root, monkeypatch, encoder):
    # The budget is a running total, not a re-sum per insert; it must stay
    # equal to the real sum through replacement, eviction and a bare clear().
    tokens._RECORD_POOL.clear()
    monkeypatch.setattr(tokens, 'MAX_RECORD_MEMO_BYTES', 400)
    monkeypatch.setattr(tokens.statcache, 'MAX_ENTRIES', 3)

    def real():
        return sum(tokens.sys.getsizeof(entry[0]) for entry in tokens._RECORD_POOL.values())

    for position, body in enumerate(['Mara', 'Mara ' * 20, 'Seraphine', 'Winifred ' * 5] * 3):
        tokens.record_tokens(root / (str(position % 5) + '.md'), body)
        assert tokens._RECORD_RETAINED[0] == real() <= 400
        assert len(tokens._RECORD_POOL) <= 3
    tokens._RECORD_POOL.clear()
    tokens.record_tokens(root / 'Realm.md', 'Mara')
    assert tokens._RECORD_RETAINED[0] == real()


def test_fork_child_discards_inherited_token_pool_mutex(root, encoder):
    assert tokens.record_tokens(root / 'Realm.md', 'Mara') == 1
    parent_lock = tokens._RECORD_LOCK
    assert parent_lock.acquire(blocking=False)
    try:
        tokens._after_record_fork()
        assert tokens._RECORD_LOCK.acquire(blocking=False)
        tokens._RECORD_LOCK.release()
        assert not tokens._RECORD_POOL
        assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine') == 2
    finally:
        parent_lock.release()


def test_successful_count_survives_family_checksum_allocation_failure(cache, root, monkeypatch, encoder):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    def failed_checksum(*args):
        raise MemoryError('optional family checksum allocation')
    monkeypatch.setattr(cache, '_check', failed_checksum)
    assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine Winifred') == 3
    assert tokens.last_counter() == tokens.ENCODING
    with cache.batch(root) as request:
        key = cache.make_key(tokens._RECORD_KIND, [], params={'text_hash': tokens.hashlib.sha256(b'Mara Seraphine Winifred').hexdigest(), 'encoding': tokens.ENCODING})
        assert not request.lookup(key).hit


def test_cached_count_survives_optional_history_refresh_allocation_failure(cache, root, monkeypatch, encoder):
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine') == 2
    tokens._RECORD_POOL.clear()
    def failed_metadata(*args):
        raise MemoryError('optional history refresh allocation')
    monkeypatch.setattr(cache.Batch, '_family_rows', failed_metadata)
    monkeypatch.setattr(encoder, 'encode', lambda text: pytest.fail('cached exact count was re-encoded'))
    assert tokens.record_tokens(root / 'Realm.md', 'Mara Seraphine') == 2
    assert tokens.last_counter() == tokens.ENCODING


def test_the_shipped_cutoff_persists_long_bodies_and_never_hashes_short_ones(cache, root, monkeypatch):
    # The real PERSIST_MIN_CHARS, not the fixture's zero: a transcript-sized
    # body survives a restart without re-encoding, and a badge-sized one is
    # counted live without paying for a SHA-256 or opening SQLite.
    encoder = _Enc()
    monkeypatch.setattr(tokens, '_encoder', lambda: encoder)
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    words = ('Realm', 'Saltmarch', 'Seraphine', 'Mara', 'Winifred')
    long_body = ' '.join(words[i % 5] for i in range(tokens.PERSIST_MIN_CHARS // 4))
    assert len(long_body) >= tokens.PERSIST_MIN_CHARS
    short_body = long_body[:tokens.PERSIST_MIN_CHARS - 1].strip()
    hashed = []
    original = tokens._record_digest
    monkeypatch.setattr(tokens, '_record_digest', lambda text: hashed.append(len(text)) or original(text))

    expected = tokens.record_tokens(root / 'Realm.md', short_body)
    assert hashed == [] and not (root / '.cache').exists()

    expected = tokens.record_tokens(root / 'Realm.md', long_body)
    assert hashed == [len(long_body)]
    cache.close()
    tokens._RECORD_POOL.clear()
    monkeypatch.setattr(encoder, 'encode', lambda text: pytest.fail('persisted count was re-encoded'))
    assert tokens.record_tokens(root / 'Saltmarch.md', long_body) == expected


@pytest.mark.parametrize('shape', ['crlf', 'non_utf8', 'two_stems'])
def test_wired_token_kind_is_equal_cold_warm_off_and_restarted(cache, root, monkeypatch, encoder, shape):
    # Spec 03 section 14's equivalence fixtures, through the kind it wires.
    monkeypatch.setenv('GRIMOIRE_HOME', str(root))
    raw = {'crlf': b'Mara\r\nSeraphine\r\n\r\nWinifred',
           'non_utf8': b'Mara \xff\xfe Saltmarch',
           'two_stems': b'Realm and Saltmarch'}[shape]
    names = ('Realm.md', 'Saltmarch.md') if shape == 'two_stems' else ('Realm.md',)
    for name in names:
        (root / name).write_bytes(raw)
    def counts():
        return [tokens.record_tokens(root / name, (root / name).read_bytes().decode('utf-8', 'surrogateescape'))
                for name in names]
    def restart():
        cache.close()
        tokens._RECORD_POOL.clear()
    expected = counts()
    assert counts() == expected
    monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    restart()
    assert counts() == expected
    monkeypatch.delenv('GRIMOIRE_COMPILED_CACHE')
    restart()
    assert counts() == expected
