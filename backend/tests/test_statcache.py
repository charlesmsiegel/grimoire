"""statcache: stat-keyed memo for derivations of file content, + its hash consumers."""

import hashlib
import os
import time

import pytest

from grimoire.store import characters, entities, statcache


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _age(*paths):
    """Back-date mtimes past the racy window so the cache is allowed to hold them."""
    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    for p in paths:
        os.utime(p, ns=(old, old))


def _age_tree(root):
    _age(*(p for p in root.rglob("*") if p.is_file()))


# ---- the memo itself ----
def test_memo_computes_once_while_file_unchanged(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("hello", encoding="utf-8")
    _age(p)
    calls = []
    for _ in range(3):
        got = statcache.memo("t", statcache.signature(p), lambda: calls.append(1) or "value")
    assert got == "value"
    assert len(calls) == 1


def test_same_size_rapid_rewrite_is_never_served_stale(tmp_path):
    # "Old." -> "New." is a same-size write that can share an mtime tick; the
    # racy window must keep such fresh files out of the cache
    p = tmp_path / "a.md"
    for content in ("Old.", "New."):
        p.write_text(content, encoding="utf-8")
        got = statcache.memo("racy", statcache.signature(p),
                             lambda: p.read_text(encoding="utf-8"))
        assert got == content


def test_memo_recomputes_when_file_changes(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("one", encoding="utf-8")
    first = statcache.memo("t", statcache.signature(p), lambda: p.read_text(encoding="utf-8"))
    p.write_text("two-longer", encoding="utf-8")
    second = statcache.memo("t", statcache.signature(p), lambda: p.read_text(encoding="utf-8"))
    assert (first, second) == ("one", "two-longer")


def test_signature_covers_every_file(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    sig1 = statcache.signature(a, b)
    b.write_text("b-changed", encoding="utf-8")
    assert statcache.signature(a, b) != sig1


def test_signature_none_when_any_file_missing(tmp_path):
    a = tmp_path / "a"
    a.write_text("a", encoding="utf-8")
    assert statcache.signature(a, tmp_path / "missing") is None


def test_memo_stays_bounded(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("x", encoding="utf-8")
    _age(p)
    sig = statcache.signature(p)
    for i in range(statcache.MAX_ENTRIES + 100):
        statcache.memo(f"kind-{i}", sig, lambda: i)
    assert len(statcache._cache) <= statcache.MAX_ENTRIES


# ---- cached consumers keep exact hash semantics ----
def test_entity_hash_tracks_updates(tmp_path):
    eid = entities.create_entity(tmp_path, "lore", "Thing", "body one")
    h1 = entities.entity_hash(tmp_path, "lore", eid)
    assert h1 == _sha((tmp_path / "lore" / f"{eid}.md").read_text(encoding="utf-8"))
    assert entities.entity_hash(tmp_path, "lore", eid) == h1  # cached hit, same value
    entities.update_entity(tmp_path, "lore", eid, body="body two, different length")
    h2 = entities.entity_hash(tmp_path, "lore", eid)
    assert h2 != h1
    assert h2 == _sha((tmp_path / "lore" / f"{eid}.md").read_text(encoding="utf-8"))


def test_entity_hash_none_after_delete(tmp_path):
    eid = entities.create_entity(tmp_path, "lore", "Thing", "body")
    assert entities.entity_hash(tmp_path, "lore", eid) is not None
    entities.delete_entity(tmp_path, "lore", eid)
    assert entities.entity_hash(tmp_path, "lore", eid) is None


def test_card_hash_tracks_updates(tmp_path):
    cid, vid = characters.create_character(tmp_path, "Ada")
    h1 = characters.card_hash(tmp_path, cid, vid)
    assert characters.card_hash(tmp_path, cid, vid) == h1
    card = characters.read_card(tmp_path, cid, vid)
    card["data"]["description"] = "now with a much longer description"
    characters.update_version(tmp_path, cid, vid, card)
    assert characters.card_hash(tmp_path, cid, vid) != h1


def test_dir_hash_tracks_version_add_and_delete(tmp_path):
    cid, vid = characters.create_character(tmp_path, "Ada")
    h1 = characters.dir_hash(tmp_path, cid)
    assert characters.dir_hash(tmp_path, cid) == h1
    v2 = characters.create_version(tmp_path, cid, "alt", characters.blank_card("Ada"))
    h2 = characters.dir_hash(tmp_path, cid)
    assert h2 != h1
    characters.delete_version(tmp_path, cid, v2)
    assert characters.dir_hash(tmp_path, cid) == h1  # same content set again


def test_signature_changes_on_inode_replacement(tmp_path):
    """A rename-replace that preserves mtime and size still invalidates."""
    p = tmp_path / "a.md"
    p.write_text("aaaa", encoding="utf-8")
    _age(p)
    before = statcache.signature(p)
    st = p.stat()
    q = tmp_path / "a.md.tmp"
    q.write_text("bbbb", encoding="utf-8")   # same size, new inode
    os.utime(q, ns=(st.st_mtime_ns, st.st_mtime_ns))
    os.replace(q, p)
    assert statcache.signature(p) != before


def test_signature_absent_ok_yields_cacheable_sentinel(tmp_path):
    """A deliberately-absent companion file is a valid, cacheable state."""
    p = tmp_path / "present.md"
    p.write_text("x", encoding="utf-8")
    _age(p)
    missing = tmp_path / "missing.json"
    sig = statcache.signature(p, missing, absent_ok=True)
    assert sig is not None
    # ...and its creation invalidates.
    missing.write_text("{}", encoding="utf-8")
    _age(missing)
    assert statcache.signature(p, missing, absent_ok=True) != sig
    # Without the flag, a missing path still voids the signature.
    assert statcache.signature(p, tmp_path / "also-missing") is None


def test_memo_pool_budget_override(tmp_path):
    pool: dict = {}
    paths = []
    for i in range(4):
        p = tmp_path / f"f{i}.md"
        p.write_text(str(i), encoding="utf-8")
        paths.append(p)
    _age(*paths)
    for p in paths:
        statcache.memo("k", statcache.signature(p), lambda p=p: p.name,
                       pool=pool, max_entries=2)
    assert len(pool) <= 2


def test_memo_pool_holds_working_set_larger_than_shared_budget(tmp_path):
    """A repeated sweep bigger than MAX_ENTRIES still hits in its own pool."""
    pool: dict = {}
    n = statcache.MAX_ENTRIES + 8
    paths = []
    for i in range(n):
        p = tmp_path / f"s{i}.md"
        p.write_text("x", encoding="utf-8")
        paths.append(p)
    _age(*paths)
    calls = []
    def sweep():
        for p in paths:
            statcache.memo("s", statcache.signature(p),
                           lambda p=p: calls.append(p) or p.name,
                           pool=pool, max_entries=n + 16)
    sweep()
    first = len(calls)
    sweep()
    assert len(calls) == first   # second sweep: all hits


def test_windows_volume_serial_collision_cannot_inherit_support(monkeypatch):
    def by_name(path, kind, result, size):
        native = result._obj
        native.volume = 1234  # Cloned volumes can share this serial number.
        native.device_type = 7
        native.creation = statcache.file_stats._FILETIME_EPOCH + 500
        native.change = statcache.file_stats._FILETIME_EPOCH + 1000
        native.write = statcache.file_stats._FILETIME_EPOCH + 500
        native.size = 4
        return 1
    seam = statcache.file_stats
    monkeypatch.setattr(seam, '_BY_NAME', by_name)
    monkeypatch.setattr(seam, '_WINDOWS', object())
    if hasattr(seam, '_SUPPORTED_DEVICES'):
        monkeypatch.setattr(seam, '_SUPPORTED_DEVICES', {})
    monkeypatch.setattr(seam, 'windows_change_supported', lambda path, device: path == 'Realm')
    assert seam.fast_stamp('Realm') is not None
    assert seam.fast_stamp('Saltmarch') is None


def test_native_memory_metadata_does_not_vouch_for_persisted_sources(monkeypatch):
    seam = statcache.file_stats
    epoch = seam._FILETIME_EPOCH
    creation = [epoch + 500]
    def by_name(path, kind, result, size):
        native = result._obj
        native.volume, native.device_type, native.size = 1234, 7, 4
        native.creation, native.change, native.write = creation[0], epoch + 1000, epoch + 500
        return 1
    monkeypatch.setattr(seam, '_BY_NAME', by_name)
    monkeypatch.setattr(seam, '_WINDOWS', object())
    monkeypatch.setattr(seam, 'windows_change_supported', lambda path, device: False)
    assert seam.native_stamp('Realm') is not None
    assert seam.fast_stamp('Realm') is None
    creation[0] = epoch + 1500
    observed = seam.native_stamp('Realm')
    assert observed[1] == 150000  # CopyFile can preserve an older ChangeTime.
    monkeypatch.setattr(statcache.time, 'time_ns', lambda: observed[1] + statcache.RACY_WINDOW_NS // 2)
    pool = {}
    stamped = ('Realm', *observed[:4])
    assert statcache.memo_stamped('Realm', lambda: ('Mara', (stamped,)), pool=pool, max_entries=4) == 'Mara'
    assert not pool  # Cannot age a newly created file using preserved metadata.


def test_windows_same_mount_serial_replacement_rechecks_filesystem(monkeypatch):
    seam = statcache.file_stats
    filesystem = ['NTFS']
    class NativeVolume:
        @staticmethod
        def GetVolumePathNameW(path, volume, length):  # noqa: N802 -- mirror the Win32 API name
            volume.value = 'Realm'
            return 1
        @staticmethod
        def GetDriveTypeW(volume):  # noqa: N802 -- mirror the Win32 API name
            return 2
        @staticmethod
        def GetVolumeInformationW(volume, name, length, serial, component, flags, target, capacity):  # noqa: N802 -- mirror the Win32 API name
            target.value = filesystem[0]
            return 1
    monkeypatch.setattr(seam, '_WINDOWS', NativeVolume())
    assert seam.windows_change_supported('Realm', 1234)
    filesystem[0] = 'FAT32'
    assert not seam.windows_change_supported('Realm', 1234)


@pytest.mark.skipif(os.name != 'nt', reason='Windows native metadata fallback')
def test_legacy_windows_metadata_keeps_copied_creation_age_guard(monkeypatch):
    seam = statcache.file_stats
    epoch = seam._FILETIME_EPOCH
    closed = []
    class NativeHandle:
        @staticmethod
        def CreateFileW(*args):  # noqa: N802 -- mirror the Win32 API name
            return 1
        @staticmethod
        def GetFileInformationByHandleEx(handle, kind, result, size):  # noqa: N802 -- mirror the Win32 API name
            result._obj.creation, result._obj.change = epoch + 2000, epoch + 1000
            return 1
        @staticmethod
        def CloseHandle(handle):  # noqa: N802 -- mirror the Win32 API name
            closed.append(handle)
    monkeypatch.setattr(seam, '_BY_NAME', None)
    monkeypatch.setattr(seam, '_WINDOWS', NativeHandle())
    monkeypatch.setattr(seam, 'windows_change_supported', lambda *args: True)
    assert seam.change_ns('Realm', os.stat('.')) == 200000
    assert closed == [1]
