"""Control-snapshot memos: cheap when nothing moved, blind to nothing that did.

The fence, `_synchronize` and `_location` each re-proved their answer from the
control files on every call, which made a warm hit cost far more than the
"stat, one indexed lookup and a decode" spec 03 section 8 prices it at. Each
now reuses an answer while four lstats show nothing has moved and the
snapshot had settled when it was taken. These pin both halves of that.
"""

import os
import time

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind
_key = cache_tests._key


@pytest.fixture(autouse=True)
def _settled(cache, monkeypatch):
    # Every scenario here needs the memos engaged, whichever clock the shared
    # fixture chose.
    real = time.time_ns
    monkeypatch.setattr(cache, "_wall_ns", lambda: real() + 3_600_000_000_000)


def _seed(cache, root, key):
    with cache.batch(root) as request:
        request.put(key, "Realm")


def test_warm_lookups_reuse_the_fence_instead_of_rereading_controls(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    _seed(cache, root, key)
    reads = []
    original = cache._read_marker
    monkeypatch.setattr(cache, "_read_marker", lambda r: reads.append(r) or original(r))

    def marker_reads(lookups):
        reads.clear()
        with cache.batch(root) as request:
            for _ in range(lookups):
                assert request.lookup(key).value == "Realm"
        return len(reads)

    marker_reads(1)  # the first batch after a write re-proves once: its journal moved the directory
    assert marker_reads(40) == marker_reads(1), "control reads grew with the lookups"


def test_a_marker_published_mid_batch_stops_reads(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    _seed(cache, root, key)
    with cache.batch(root) as request:
        assert request.lookup(key).hit
        assert cache._publish_marker(root) is not None   # another process's purge
        assert not request.lookup(key).hit


def test_an_intent_arriving_mid_batch_stops_reads(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    _seed(cache, root, key)
    with cache.batch(root) as request:
        assert request.lookup(key).hit
        intent = cache.prepare_delete(root)
        assert intent is not None
        try:
            assert not request.lookup(key).hit
        finally:
            cache.purge_for_delete(root, intent=intent)


def test_a_purge_published_elsewhere_still_empties_this_file(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    _seed(cache, root, key)
    with cache.batch(root) as request:
        assert request.lookup(key).hit           # synchronize's memo is now set
    assert cache._publish_marker(root) is not None
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    session = cache_tests._open(cache, root)
    assert session.conn.execute("SELECT count(*) FROM artifacts").fetchone() == (0,)


def test_a_link_replacing_the_cache_directory_turns_the_cache_off(cache, root, tmp_path, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    _seed(cache, root, key)
    with cache.batch(root) as request:
        assert request.lookup(key).hit           # _location's memo is now set
    cache.close()
    outside = tmp_path / "outside"
    (root / ".cache" / "compiled").rename(outside)
    try:
        os.symlink(outside, root / ".cache" / "compiled", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    before = sorted(p.name for p in outside.iterdir())
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(_key(cache, _kind(cache, monkeypatch), "mara"), "Mara")
    assert sorted(p.name for p in outside.iterdir()) == before


def test_an_unsettled_snapshot_is_never_remembered(cache, root, monkeypatch):
    # Everything here was written a moment ago; with the real clock no
    # snapshot has settled, so every check keeps taking its full path.
    monkeypatch.setattr(cache, "_wall_ns", time.time_ns)
    key = _key(cache, _kind(cache, monkeypatch))
    _seed(cache, root, key)
    with cache.batch(root) as request:
        assert request.lookup(key).hit
        assert request._fence_snapshot is None
    session = cache_tests._open(cache, root)
    assert session.synced is None
    assert not cache._state.locations
