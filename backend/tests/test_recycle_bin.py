"""store/recycle_bin.py -- where a real bin exists, and sending to it.

Windows cannot run here, so every Windows branch is driven through the `_win`
seam (and `_platform`); the real ctypes block is only imported, never called.
The autouse `fake_bin` fixture in conftest replaces `check`, `_platform` and
`_send2trash` for the rest of the suite, so each test here restores the real
`check` / `_platform` it needs.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.store import proclock, recycle_bin
from grimoire.store.recycle_bin import MESSAGES, Availability

REAL_CHECK = recycle_bin.check
REAL_PLATFORM = recycle_bin._platform

MIB = 1024 * 1024
POLICY = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
VOLUME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{GUID}"


@pytest.fixture
def real_check(monkeypatch):
    monkeypatch.setattr(recycle_bin, "check", REAL_CHECK)
    monkeypatch.setattr(proclock, "on_android", lambda: False)


def _fake_send(log, fail_on=()):
    def send(p):
        if Path(p).name in fail_on:
            raise OSError("no")
        log.append(Path(p).name)
        Path(p).unlink()
    return send


class Win:
    """A scriptable `_win`: every call is recorded, every answer is a field."""

    def __init__(self, tmp_path, **kw):
        self.asked_volume_of = []
        self.asked_drive_type = []
        self.registry = kw.pop("registry", {})
        self.volume = kw.pop("volume", "C:\\")
        self.drive = kw.pop("drive", 3)
        self.bin_states = kw.pop("bin_states", [(0, 0)])
        self.queries = 0
        self.guid = kw.pop("guid", "{GUID}")
        self.total = kw.pop("total", None)
        assert not kw, kw

    def namespace(self):
        def volume_of(path):
            self.asked_volume_of.append(Path(path))
            return self.volume

        def drive_type(volume):
            self.asked_drive_type.append(volume)
            return self.drive

        def query_bin(volume):
            state = self.bin_states[min(self.queries, len(self.bin_states) - 1)]
            self.queries += 1
            return state

        return SimpleNamespace(
            volume_of=volume_of, drive_type=drive_type, query_bin=query_bin,
            volume_guid=lambda volume: self.guid,
            volume_bytes=lambda volume: self.total,
            reg_dword=lambda hive, path, name: self.registry.get((hive, path, name)),
        )


@pytest.fixture
def windows(monkeypatch, tmp_path, real_check):
    monkeypatch.setattr(recycle_bin, "_platform", lambda: "windows")
    monkeypatch.setattr(recycle_bin, "_send2trash", lambda p: None)

    def install(**kw):
        w = Win(tmp_path, **kw)
        monkeypatch.setattr(recycle_bin, "_win", w.namespace())
        return w
    return install


def _files(tmp_path, *sizes):
    out = []
    for i, size in enumerate(sizes):
        p = tmp_path / f"f{i}.bin"
        p.write_bytes(b"x" * size)
        out.append(p)
    return out


# --- check: platform ---------------------------------------------------------

def test_android_refuses(monkeypatch, tmp_path, real_check):
    monkeypatch.setattr(proclock, "on_android", lambda: True)
    assert recycle_bin.check(tmp_path) == Availability(False, "android", MESSAGES["android"])


def test_not_installed_refuses(monkeypatch, tmp_path, real_check):
    monkeypatch.setattr(recycle_bin, "_send2trash", None)
    assert recycle_bin.check(tmp_path) == Availability(
        False, "not_installed", MESSAGES["not_installed"])


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_linux_and_macos_ok(monkeypatch, tmp_path, real_check, platform):
    monkeypatch.setattr(recycle_bin, "_platform", lambda: platform)
    monkeypatch.setattr(recycle_bin, "_send2trash", lambda p: None)
    assert recycle_bin.check(tmp_path) == Availability(True)


def test_availability_to_dict():
    assert Availability(True).to_dict() == {"ok": True, "code": None, "message": None}
    assert Availability(False, "android", "m").to_dict() == {
        "ok": False, "code": "android", "message": "m"}


def test_the_real_platform_names_this_machine():
    import sys
    assert REAL_PLATFORM() == {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")


# --- check: Windows ----------------------------------------------------------

def test_windows_volume_is_the_outboxs_mount(windows, tmp_path):
    outbox = tmp_path / ".cache" / "image-store" / "recycle"
    outbox.mkdir(parents=True)
    w = windows(volume="C:\\mnt\\usb\\", drive=2)
    assert recycle_bin.check(tmp_path) == Availability(
        False, "volume_has_no_bin", MESSAGES["volume_has_no_bin"])
    assert w.asked_volume_of == [outbox]
    assert w.asked_drive_type == ["C:\\mnt\\usb\\"]


def test_windows_network_drive_refuses(windows, tmp_path):
    windows(drive=4)
    assert recycle_bin.check(tmp_path).code == "volume_has_no_bin"


def test_windows_query_fails_refuses(windows, tmp_path):
    windows(bin_states=[None])
    assert recycle_bin.check(tmp_path).code == "volume_has_no_bin"


@pytest.mark.parametrize("hive", ["HKCU", "HKLM"])
def test_windows_policy_hkcu_and_hklm(windows, tmp_path, hive):
    windows(registry={(hive, POLICY, "NoRecycleFiles"): 1})
    assert recycle_bin.check(tmp_path) == Availability(
        False, "bin_deletes_immediately", MESSAGES["bin_deletes_immediately"])


def test_windows_nuke_on_delete_refuses(windows, tmp_path):
    windows(registry={("HKCU", VOLUME_KEY, "NukeOnDelete"): 1})
    assert recycle_bin.check(tmp_path).code == "bin_deletes_immediately"


def test_windows_absent_volume_key_is_ok(windows, tmp_path):
    windows(registry={}, guid=None)
    assert recycle_bin.check(tmp_path) == Availability(True)
    windows(registry={})
    assert recycle_bin.check(tmp_path) == Availability(True)


def test_windows_fresh_store_uses_an_existing_ancestor(windows, tmp_path):
    w = windows()
    assert recycle_bin.check(tmp_path).ok
    assert w.asked_volume_of == [tmp_path]


def test_windows_unknown_volume_refuses(windows, tmp_path):
    windows(volume=None)
    assert recycle_bin.check(tmp_path).code == "volume_has_no_bin"


# --- send_batch --------------------------------------------------------------

def test_sends_in_order_calling_before_each(monkeypatch, tmp_path, fake_bin):
    a, b = _files(tmp_path, 1, 1)
    order = []
    monkeypatch.setattr(recycle_bin, "_send2trash", _fake_send(order))
    seen = []
    result = recycle_bin.send_batch(tmp_path, [a, b], lambda p: seen.append(p.name))
    assert seen == ["f0.bin", "f1.bin"]
    assert order == ["f0.bin", "f1.bin"]
    assert result.sent == [a, b]
    assert result.refused is None and result.code is None and result.unverified == []


def test_before_raising_stops_and_propagates(monkeypatch, tmp_path):
    a, b = _files(tmp_path, 1, 1)
    order = []
    monkeypatch.setattr(recycle_bin, "_send2trash", _fake_send(order))

    def before(p):
        if p == b:
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        recycle_bin.send_batch(tmp_path, [a, b], before)
    assert order == ["f0.bin"]


def test_stops_at_first_refusal(monkeypatch, tmp_path):
    a, b, c = _files(tmp_path, 1, 1, 1)
    order = []
    monkeypatch.setattr(recycle_bin, "_send2trash", _fake_send(order, fail_on={"f1.bin"}))
    result = recycle_bin.send_batch(tmp_path, [a, b, c], lambda p: None)
    assert result.sent == [a]
    assert result.refused == b
    assert result.code == "refused"
    assert result.error == "OSError"
    assert order == ["f0.bin"]


def test_non_windows_never_checks_capacity(monkeypatch, tmp_path):
    (a,) = _files(tmp_path, 1)
    monkeypatch.setattr(recycle_bin, "_send2trash", _fake_send([]))
    monkeypatch.setattr(recycle_bin, "_platform", lambda: "linux")
    monkeypatch.setattr(recycle_bin, "_win", SimpleNamespace())  # any call would raise
    assert recycle_bin.send_batch(tmp_path, [a], lambda p: None).unverified == []


def _windows_batch(windows, monkeypatch, tmp_path, sizes, **kw):
    w = windows(**kw)
    paths = _files(tmp_path, *sizes)
    log = []
    monkeypatch.setattr(recycle_bin, "_send2trash", _fake_send(log))
    return w, paths, log


def test_too_large_for_bin(windows, monkeypatch, tmp_path):
    _, paths, log = _windows_batch(
        windows, monkeypatch, tmp_path, [1024, 2 * MIB],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 1})
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None)
    assert result.sent == [paths[0]]
    assert result.refused == paths[1]
    assert result.code == "too_large_for_bin"
    assert log == ["f0.bin"]
    assert paths[1].exists()


def test_unverified_when_count_does_not_rise(windows, monkeypatch, tmp_path):
    _, paths, _ = _windows_batch(
        windows, monkeypatch, tmp_path, [1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(0, 5), (0, 5)])
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None)
    assert result.unverified == result.sent == paths


def test_verified_when_count_rises(windows, monkeypatch, tmp_path):
    _, paths, _ = _windows_batch(
        windows, monkeypatch, tmp_path, [1024, 1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(0, 5), (2048, 7)])
    assert recycle_bin.send_batch(tmp_path, paths, lambda p: None).unverified == []


def test_batch_that_may_have_purged_is_not_unverified(windows, monkeypatch, tmp_path):
    pre_size = int(99.9 * MIB)
    _, paths, _ = _windows_batch(
        windows, monkeypatch, tmp_path, [MIB // 2],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(pre_size, 5), (pre_size, 5)])
    assert recycle_bin.send_batch(tmp_path, paths, lambda p: None).unverified == []


@pytest.mark.parametrize("pre_size, expect_unverified", [(110 * MIB, False), (MIB, True)])
def test_unknown_capacity_uses_ten_percent_of_the_volume(
        windows, monkeypatch, tmp_path, pre_size, expect_unverified):
    _, paths, _ = _windows_batch(
        windows, monkeypatch, tmp_path, [MIB], registry={},
        total=1024 * MIB, bin_states=[(pre_size, 5), (pre_size, 5)])
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None)
    assert result.unverified == (paths if expect_unverified else [])


def test_unknown_capacity_never_refuses_a_big_file(windows, monkeypatch, tmp_path):
    _, paths, _ = _windows_batch(windows, monkeypatch, tmp_path, [3 * MIB], registry={},
                                 total=1024 * MIB, bin_states=[(0, 0), (3 * MIB, 1)])
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None)
    assert result.sent == paths and result.code is None


# --- the conftest fake -------------------------------------------------------

def test_fake_bin_moves_and_records(fake_bin, tmp_path):
    (a,) = _files(tmp_path, 3)
    assert recycle_bin.check(tmp_path) == Availability(True)
    result = recycle_bin.send_batch(tmp_path, [a], lambda p: None)
    assert result.sent == [a] and not a.exists()
    assert fake_bin.paths == [a]
