"""The fail-soft JSON read that reports itself (`store/failsoft.py`)."""

from __future__ import annotations

import logging
import os

import pytest

from grimoire.store import failsoft


@pytest.fixture(autouse=True)
def _quiet_between_tests():
    """The warning cache is module state; a test must not inherit another's."""
    failsoft._warned.clear()
    yield
    failsoft._warned.clear()


def test_missing_file_is_silent(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert failsoft.read_json(tmp_path / "absent.json", dict, "nothing happens") is None
    assert caplog.records == []


def test_missing_parent_directory_is_silent(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert failsoft.read_json(tmp_path / "no" / "such" / "f.json", dict, "x") is None
    assert caplog.records == []


def test_good_file_is_returned_and_silent(tmp_path, caplog):
    p = tmp_path / "f.json"
    p.write_text('{"data_dir": "/srv/store"}', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert failsoft.read_json(p, dict, "x") == {"data_dir": "/srv/store"}
    assert caplog.records == []


def test_malformed_json_warns_with_path_and_consequence(tmp_path, caplog):
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert failsoft.read_json(p, dict, "the library moves") is None
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert str(p) in msg
    assert "the library moves" in msg


def test_wrong_top_level_type_warns(tmp_path, caplog):
    """Valid JSON of the wrong shape reaches the same fallback as a parse
    error, so it has to be as loud."""
    p = tmp_path / "f.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert failsoft.read_json(p, list, "records come back") is None
    assert len(caplog.records) == 1
    assert "records come back" in caplog.records[0].getMessage()


def test_undecodable_bytes_warn(tmp_path, caplog):
    p = tmp_path / "f.json"
    p.write_bytes(b"\xff\xfe not utf-8")
    with caplog.at_level(logging.WARNING):
        assert failsoft.read_json(p, dict, "x") is None
    assert len(caplog.records) == 1


def test_unreadable_file_warns(tmp_path, caplog):
    """A directory where a file is expected: an OSError that is not absence."""
    p = tmp_path / "f.json"
    p.mkdir()
    with caplog.at_level(logging.WARNING):
        assert failsoft.read_json(p, dict, "x") is None
    assert len(caplog.records) == 1


def test_repeat_reads_of_one_corrupt_file_warn_once(tmp_path, caplog):
    """These reads run tens to hundreds of times per request. One warning per
    corruption is diagnosable; hundreds of identical lines are not."""
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            failsoft.read_json(p, dict, "x")
    assert len(caplog.records) == 1


def test_a_changed_file_warns_again(tmp_path, caplog):
    """Deduping must not swallow the report of a *failed repair*."""
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        failsoft.read_json(p, dict, "x")
        p.write_text("[still not an object]", encoding="utf-8")
        failsoft.read_json(p, dict, "x")
    assert len(caplog.records) == 2


def test_a_repaired_file_goes_quiet(tmp_path, caplog):
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        failsoft.read_json(p, dict, "x")
        p.write_text('{"ok": true}', encoding="utf-8")
        assert failsoft.read_json(p, dict, "x") == {"ok": True}
        failsoft.read_json(p, dict, "x")
    assert len(caplog.records) == 1


def test_a_file_that_breaks_again_identically_warns_again(tmp_path, caplog):
    """A successful read forgets the path, so the cache means "corrupt right
    now" rather than "was corrupt once". Without that, a sync client rolling
    a repaired file back to the corrupt version it already holds -- mtime and
    all, which is exactly what a synced store does -- would restore the bug
    silently."""
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    stamp = p.stat()
    with caplog.at_level(logging.WARNING):
        failsoft.read_json(p, dict, "x")
        p.write_text('{"ok": true}', encoding="utf-8")
        failsoft.read_json(p, dict, "x")
        p.write_text("{not json", encoding="utf-8")
        os.utime(p, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        # Without this the test could pass vacuously on a filesystem whose mtime
        # does not round-trip: a *different* signature warns for the wrong reason.
        assert (p.stat().st_mtime_ns, p.stat().st_size) == (stamp.st_mtime_ns, stamp.st_size)
        failsoft.read_json(p, dict, "x")
    assert len(caplog.records) == 2


def test_a_deleted_file_is_forgotten(tmp_path, caplog):
    """A path that goes away leaves no entry behind: the cache tracks files that
    are corrupt now, and a campaign removed after its tombstones went bad must
    not pin a row in it for the life of the process."""
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        failsoft.read_json(p, dict, "x")
        assert p in failsoft._warned
        p.unlink()
        failsoft.read_json(p, dict, "x")
    assert p not in failsoft._warned
    assert len(caplog.records) == 1


def test_the_cache_is_capped(tmp_path, caplog):
    """A path can leave the store without a final read -- delete_campaign
    rmtree's the tree -- so eviction cannot rely on reads alone. The cap is what
    keeps a server that has seen thousands of them from holding every one."""
    for i in range(failsoft._MAX_WARNED + 20):
        p = tmp_path / f"f{i}.json"
        p.write_text("{not json", encoding="utf-8")
        failsoft.read_json(p, dict, "x")
        p.unlink()                        # the campaign is gone; nothing reads it again
    assert len(failsoft._warned) == failsoft._MAX_WARNED


def test_eviction_costs_only_a_repeated_warning(tmp_path, caplog):
    """The evicted entry is the oldest, and losing it is not a correctness
    problem: the file warns again next time it is read."""
    first = tmp_path / "first.json"
    first.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        failsoft.read_json(first, dict, "x")
        for i in range(failsoft._MAX_WARNED):
            p = tmp_path / f"f{i}.json"
            p.write_text("{not json", encoding="utf-8")
            failsoft.read_json(p, dict, "x")
        assert first not in failsoft._warned      # pushed out by the newer ones
        caplog.clear()
        failsoft.read_json(first, dict, "x")
    assert len(caplog.records) == 1


def test_two_corrupt_files_each_warn(tmp_path, caplog):
    """The cache is keyed per path -- one corrupt file must not mask another."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for p in (a, b):
        p.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        failsoft.read_json(a, dict, "x")
        failsoft.read_json(b, dict, "x")
    assert len(caplog.records) == 2
