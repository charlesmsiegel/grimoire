"""The bootstrap pointer is re-read when it changes, not on every `home()`.

Every store path resolves through `paths.home()`, and without `GRIMOIRE_HOME`
-- the desktop default, and Android, which pops the variable -- that meant
opening and parsing `~/.grimoire.json` once per path built: thousands of times
for one overlay-heavy request. The pointer is now memoized on its stat
signature, so these tests pin the two halves of that bargain: a change is
still seen at once, however it lands, and an unchanged pointer is not re-read.
"""

import json
import logging
import os
import time

import pytest

import grimoire.store as store
from grimoire.store import failsoft, paths, statcache


@pytest.fixture(autouse=True)
def _forget_corruption_warnings():
    """`failsoft` dedupes on module state; tests must not inherit each other's."""
    failsoft._warned.clear()


def isolate(monkeypatch, tmp_path):
    """Point the bootstrap pointer at a temp file and clear the env override."""
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    pointer = tmp_path / "pointer" / ".grimoire.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "pointer_path", lambda: pointer)
    monkeypatch.setattr(paths, "DEFAULT_HOME", tmp_path / "default")
    return pointer


def _age(path):
    """Back-date past the racy window, so the memo is allowed to hold the file."""
    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    os.utime(path, ns=(old, old))
    return old


def _count_pointer_reads(monkeypatch, pointer):
    reads = []
    real = failsoft.read_json

    def counting(path, *a, **kw):
        if path == pointer:
            reads.append(path)
        return real(path, *a, **kw)

    monkeypatch.setattr(failsoft, "read_json", counting)
    return reads


def test_set_data_dir_is_seen_by_the_very_next_home(monkeypatch, tmp_path):
    """The Configuration page moves the store and the next request must land in
    the new root. `set_data_dir` writes through `store.atomic` -- a new inode and
    a fresh mtime -- so the memo cannot answer with the previous pointer."""
    isolate(monkeypatch, tmp_path)
    assert store.home() == tmp_path / "default"
    store.set_data_dir(str(tmp_path / "first"))
    assert store.home() == tmp_path / "first"
    store.set_data_dir(str(tmp_path / "second"))
    assert store.home() == tmp_path / "second"
    store.set_data_dir(None)
    assert store.home() == tmp_path / "default"


def test_set_data_dir_is_seen_even_after_the_old_pointer_was_memoized(monkeypatch, tmp_path):
    """The hard case for a memo: the previous pointer is old enough to be held,
    so only the signature moving can invalidate it."""
    pointer = isolate(monkeypatch, tmp_path)
    store.set_data_dir(str(tmp_path / "first"))
    _age(pointer)
    for _ in range(3):
        assert store.home() == tmp_path / "first"
    store.set_data_dir(str(tmp_path / "second"))
    assert store.home() == tmp_path / "second"


def test_an_external_rename_replace_is_observed(monkeypatch, tmp_path):
    """A sync client or a hand edit lands a new file over the pointer. Same
    size and the same restored mtime, so only the inode tells the two apart --
    which is why the signature carries it."""
    pointer = isolate(monkeypatch, tmp_path)
    pointer.write_text(json.dumps({"data_dir": str(tmp_path / "aaaa")}), encoding="utf-8")
    stamp = _age(pointer)
    for _ in range(3):
        assert store.home() == tmp_path / "aaaa"

    staged = pointer.with_name("staged.json")
    staged.write_text(json.dumps({"data_dir": str(tmp_path / "bbbb")}), encoding="utf-8")
    assert staged.stat().st_size == pointer.stat().st_size
    os.utime(staged, ns=(stamp, stamp))
    os.replace(staged, pointer)

    assert store.home() == tmp_path / "bbbb"


def test_a_pointer_created_after_an_absent_one_was_memoized_is_observed(monkeypatch, tmp_path):
    """Absence is cached too (nothing chosen yet is the common first-run state),
    and the file appearing is a different signature, not a stale hit."""
    pointer = isolate(monkeypatch, tmp_path)
    for _ in range(3):
        assert store.home() == tmp_path / "default"
    pointer.write_text(json.dumps({"data_dir": str(tmp_path / "chosen")}), encoding="utf-8")
    _age(pointer)
    assert store.home() == tmp_path / "chosen"


def test_an_unchanged_pointer_is_read_once(monkeypatch, tmp_path):
    pointer = isolate(monkeypatch, tmp_path)
    pointer.write_text(json.dumps({"data_dir": str(tmp_path / "synced")}), encoding="utf-8")
    _age(pointer)
    reads = _count_pointer_reads(monkeypatch, pointer)
    for _ in range(50):
        assert store.home() == tmp_path / "synced"
    assert len(reads) <= 1


def test_a_just_written_pointer_is_re_read_until_it_ages(monkeypatch, tmp_path):
    """Inside statcache's racy window a same-size rewrite can share an mtime
    tick, so a fresh pointer is never held -- correctness before the saving."""
    pointer = isolate(monkeypatch, tmp_path)
    pointer.write_text(json.dumps({"data_dir": str(tmp_path / "synced")}), encoding="utf-8")
    reads = _count_pointer_reads(monkeypatch, pointer)
    for _ in range(3):
        assert store.home() == tmp_path / "synced"
    assert len(reads) == 3


def test_a_caller_mutating_the_pointer_cannot_poison_the_memo(monkeypatch, tmp_path):
    """`set_data_dir` edits the dict it read before writing it back. Handed the
    memo's own object, an edit whose write then failed would still have moved
    every later `home()` -- `data.pop("data_dir")` and a full disk would send
    the library back to the default root with nothing on disk to say why."""
    pointer = isolate(monkeypatch, tmp_path)
    pointer.write_text(json.dumps({"data_dir": str(tmp_path / "synced")}), encoding="utf-8")
    _age(pointer)
    paths._read_pointer().pop("data_dir")
    paths._read_pointer()["extra"] = "noise"
    assert store.home() == tmp_path / "synced"
    assert paths._read_pointer() == {"data_dir": str(tmp_path / "synced")}


def test_a_corrupt_pointer_warns_once_per_signature_and_again_after_a_change(monkeypatch, tmp_path, caplog):
    """The dedup used to be all that kept a corrupt pointer from logging on
    every call. Now an aged corrupt pointer is not even re-read -- and a
    different corruption is a different file, so it is reported."""
    pointer = isolate(monkeypatch, tmp_path)
    pointer.write_text("{ half a", encoding="utf-8")
    _age(pointer)
    with caplog.at_level(logging.WARNING):
        for _ in range(20):
            assert store.home() == tmp_path / "default"
        assert len(caplog.records) == 1
        pointer.write_text("{ half of another", encoding="utf-8")
        _age(pointer)
        for _ in range(20):
            assert store.home() == tmp_path / "default"
    assert len(caplog.records) == 2
