"""store/recycle_bin.py -- where a real bin exists, and sending to it.

Windows cannot run here, so every Windows branch is driven through the `_win`
seam (and `_platform`); the real ctypes block is only imported, never called.
The autouse `fake_bin` fixture in conftest replaces `check`, `_platform` and
`_send2trash` for the rest of the suite, so each test here restores the real
`check` / `_platform` it needs.
"""

from __future__ import annotations

import os
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
    """The volume's GUID resolves and its BitBucket key is absent: Windows'
    defaults apply (the bin on, its default capacity)."""
    windows(registry={})
    assert recycle_bin.check(tmp_path) == Availability(True)


def test_windows_unresolved_volume_guid_refuses(windows, tmp_path):
    """No GUID means the per-volume key cannot even be named, so NukeOnDelete
    and MaxCapacity are unread -- never the same as absent, since a fixed
    volume set to delete immediately would then pass."""
    windows(registry={("HKCU", VOLUME_KEY, "NukeOnDelete"): 1}, guid=None)
    assert recycle_bin.check(tmp_path) == Availability(
        False, "bin_settings_unreadable", MESSAGES["bin_settings_unreadable"])
    windows(registry={}, guid=None)
    assert recycle_bin.check(tmp_path).code == "bin_settings_unreadable"


def test_an_unresolved_volume_guid_refuses_the_batch(windows, monkeypatch, tmp_path):
    _, paths, log = _windows_batch(windows, monkeypatch, tmp_path, [1, 1],
                                   registry={}, guid=None)
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: pytest.fail("before ran"))
    assert result.sent == [] and log == []
    assert result.refused == paths[0] and result.code == "bin_settings_unreadable"
    assert all(p.exists() for p in paths)


# --- a registry value that cannot be read is not an unset one -----------------
#
# Access denied (or any read error but "not there") on a policy flag or on
# `MaxCapacity` used to read as absent -- i.e. "the bin is on" -- which is the
# one answer that can turn a send into a permanent delete. It refuses now.

UNREADABLE_SETTINGS = [
    ("HKCU", POLICY, "NoRecycleFiles"),
    ("HKLM", POLICY, "NoRecycleFiles"),
    ("HKCU", VOLUME_KEY, "NukeOnDelete"),
    ("HKCU", VOLUME_KEY, "MaxCapacity"),
]


@pytest.mark.parametrize("setting", UNREADABLE_SETTINGS)
def test_an_unreadable_bin_setting_refuses_the_check(windows, tmp_path, setting):
    windows(registry={setting: recycle_bin.UNREADABLE})
    assert recycle_bin.check(tmp_path) == Availability(
        False, "bin_settings_unreadable", MESSAGES["bin_settings_unreadable"])


@pytest.mark.parametrize("setting", UNREADABLE_SETTINGS)
def test_an_unreadable_bin_setting_refuses_the_batch(windows, monkeypatch, tmp_path, setting):
    _, paths, log = _windows_batch(windows, monkeypatch, tmp_path, [1, 1],
                                   registry={setting: recycle_bin.UNREADABLE})
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: pytest.fail("before ran"))
    assert result.sent == [] and log == []
    assert result.refused == paths[0] and result.code == "bin_settings_unreadable"
    assert all(p.exists() for p in paths)


def test_the_unreadable_sentence():
    assert MESSAGES["bin_settings_unreadable"] == (
        "Windows' Recycle Bin settings for this drive couldn't be read, "
        "so pictures won't be sent to it.")


def test_a_set_policy_still_refuses_beside_an_unreadable_one(windows, tmp_path):
    windows(registry={("HKCU", POLICY, "NoRecycleFiles"): recycle_bin.UNREADABLE,
                      ("HKLM", POLICY, "NoRecycleFiles"): 1})
    assert not recycle_bin.check(tmp_path).ok


POLICY_FLAGS = [
    ("HKCU", POLICY, "NoRecycleFiles"),
    ("HKLM", POLICY, "NoRecycleFiles"),
    ("HKCU", VOLUME_KEY, "NukeOnDelete"),
]


@pytest.mark.parametrize("setting", POLICY_FLAGS)
@pytest.mark.parametrize("value", [1, 2, 0xFFFFFFFF])
def test_any_nonzero_policy_flag_refuses(windows, monkeypatch, tmp_path, setting, value):
    """Windows reads these flags as on when nonzero, and an unexpected value
    is safest read as on: it refuses in the check and in every batch."""
    _, paths, log = _windows_batch(windows, monkeypatch, tmp_path, [1],
                                   registry={setting: value})
    assert recycle_bin.check(tmp_path) == Availability(
        False, "bin_deletes_immediately", MESSAGES["bin_deletes_immediately"])
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: pytest.fail("before ran"))
    assert result.sent == [] and log == [] and result.code == "bin_deletes_immediately"


@pytest.mark.parametrize("setting", POLICY_FLAGS)
def test_a_zero_policy_flag_is_allowed(windows, tmp_path, setting):
    windows(registry={setting: 0})
    assert recycle_bin.check(tmp_path) == Availability(True)


def test_policies_set_to_zero_are_ok(windows, tmp_path):
    windows(registry={("HKCU", POLICY, "NoRecycleFiles"): 0,
                      ("HKLM", POLICY, "NoRecycleFiles"): 0,
                      ("HKCU", VOLUME_KEY, "NukeOnDelete"): 0,
                      ("HKCU", VOLUME_KEY, "MaxCapacity"): 100})
    assert recycle_bin.check(tmp_path) == Availability(True)


def test_a_batch_reads_max_capacity_once(windows, monkeypatch, tmp_path):
    """The capacity a batch judges sizes by is the one its availability check
    read: a second read could disagree with the first (or fail)."""
    _, paths, _ = _windows_batch(
        windows, monkeypatch, tmp_path, [1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(0, 5), (1024, 6)])
    asked = []
    reg = recycle_bin._win.reg_dword

    def reg_dword(hive, path, name):
        asked.append(name)
        return reg(hive, path, name)

    monkeypatch.setattr(recycle_bin._win, "reg_dword", reg_dword)
    assert recycle_bin.send_batch(tmp_path, paths, lambda p: None).sent == paths
    assert asked.count("MaxCapacity") == 1


def _registry_read(answer):
    """The open-and-query `_reg_value` drives: `answer` is raised when it is
    an exception, else returned as the (value, kind) pair."""
    def read():
        if isinstance(answer, BaseException):
            raise answer
        return answer
    return read


@pytest.mark.parametrize("answer, expected", [
    ((1, recycle_bin.REG_DWORD), 1),
    ((0, recycle_bin.REG_DWORD), 0),
    ((5000, recycle_bin.REG_DWORD), 5000),
    (FileNotFoundError(2, "The system cannot find the file specified"), None),
    (PermissionError(13, "Access is denied"), recycle_bin.UNREADABLE),
    (OSError(1018, "Illegal operation attempted on a registry key"), recycle_bin.UNREADABLE),
    (("1", 1), recycle_bin.UNREADABLE),             # REG_SZ: not the DWORD Windows reads
    ((b"\x01\x00\x00\x00", 3), recycle_bin.UNREADABLE),   # REG_BINARY
])
def test_reg_value_tells_absent_from_unreadable(answer, expected):
    got = recycle_bin._reg_value(_registry_read(answer))
    assert got is expected if expected is None or expected is recycle_bin.UNREADABLE \
        else got == expected


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


# --- a failing bin query never lets a send go unremarked ----------------------

def test_pre_batch_query_failure_sends_nothing(windows, monkeypatch, tmp_path):
    _, paths, log = _windows_batch(
        windows, monkeypatch, tmp_path, [1, 1],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100}, bin_states=[None])
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: pytest.fail("before ran"))
    assert result.sent == [] and log == []
    assert result.refused == paths[0] and result.code == "volume_has_no_bin"
    assert all(p.exists() for p in paths)


def test_post_batch_query_failure_is_unverified(windows, monkeypatch, tmp_path):
    _, paths, _ = _windows_batch(
        windows, monkeypatch, tmp_path, [1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100}, bin_states=[(0, 5), None])
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None)
    assert result.sent == paths and result.unverified == paths


# --- every batch asks the whole Windows question again ------------------------
#
# `check` answering ok is not enough: a drive, a policy or a per-volume setting
# can change between the scan (or the route's check) and a batch, and on
# Windows any of them turns "send to the bin" into a permanent delete that
# reports success. So each batch runs the full availability check before it
# hands a single file over.

def _flip_drive(w):
    w.drive = 4


def _flip_policy_hkcu(w):
    w.registry[("HKCU", POLICY, "NoRecycleFiles")] = 1


def _flip_policy_hklm(w):
    w.registry[("HKLM", POLICY, "NoRecycleFiles")] = 1


def _flip_nuke(w):
    w.registry[("HKCU", VOLUME_KEY, "NukeOnDelete")] = 1


def _flip_query(w):
    w.bin_states = [None]


def _flip_volume(w):
    w.volume = None


def _flip_guid(w):
    w.guid = None


def _unreadable(*setting):
    def flip(w):
        w.registry[setting] = recycle_bin.UNREADABLE
    return flip


@pytest.mark.parametrize("flip, code", [
    (_flip_drive, "volume_has_no_bin"),
    (_flip_policy_hkcu, "bin_deletes_immediately"),
    (_flip_policy_hklm, "bin_deletes_immediately"),
    (_flip_nuke, "bin_deletes_immediately"),
    (_flip_query, "volume_has_no_bin"),
    (_flip_volume, "volume_has_no_bin"),
    (_flip_guid, "bin_settings_unreadable"),
    (_unreadable("HKCU", POLICY, "NoRecycleFiles"), "bin_settings_unreadable"),
    (_unreadable("HKLM", POLICY, "NoRecycleFiles"), "bin_settings_unreadable"),
    (_unreadable("HKCU", VOLUME_KEY, "NukeOnDelete"), "bin_settings_unreadable"),
    (_unreadable("HKCU", VOLUME_KEY, "MaxCapacity"), "bin_settings_unreadable"),
])
def test_a_guard_that_flips_after_check_refuses_the_batch(windows, monkeypatch, tmp_path,
                                                          flip, code):
    w, paths, log = _windows_batch(windows, monkeypatch, tmp_path, [1, 1], registry={})
    assert recycle_bin.check(tmp_path).ok
    flip(w)
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: pytest.fail("before ran"))
    assert result.sent == [] and log == []
    assert result.refused == paths[0] and result.code == code
    assert all(p.exists() for p in paths)


def test_a_batch_reads_the_bin_once_before_sending(windows, monkeypatch, tmp_path):
    """The availability check's own bin query is the batch's "before" reading,
    so the backstop compares against the state the guards were judged on."""
    w, paths, _ = _windows_batch(
        windows, monkeypatch, tmp_path, [1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(0, 5), (1024, 6)])
    assert recycle_bin.send_batch(tmp_path, paths, lambda p: None).unverified == []
    assert w.queries == 2


# --- a file that cannot be sized is never a reason to lose the batch ----------

def test_a_file_gone_from_the_outbox_is_skipped_not_refused(windows, monkeypatch, tmp_path):
    _, paths, log = _windows_batch(
        windows, monkeypatch, tmp_path, [1024, 1024, 1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(0, 5), (0, 5)])
    paths[1].unlink()                      # gone after the outbox was listed
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None)
    assert result.sent == [paths[0], paths[2]] and log == ["f0.bin", "f2.bin"]
    assert result.refused is None and result.code is None
    # The backstop still ran over what was sent: the count did not rise.
    assert result.unverified == [paths[0], paths[2]]


def test_a_file_that_cannot_be_sized_is_refused_and_the_backstop_runs(
        windows, monkeypatch, tmp_path):
    _, paths, log = _windows_batch(
        windows, monkeypatch, tmp_path, [1024, 1024, 1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(0, 5), (0, 5)])
    real_stat = Path.stat

    def stat(self, *a, **kw):
        if self.name == "f1.bin":
            raise PermissionError("denied")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", stat)
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None)
    assert result.sent == [paths[0]] and log == ["f0.bin"]
    assert result.refused == paths[1]
    assert result.code == "refused" and result.error == "PermissionError"
    assert result.unverified == [paths[0]]
    assert os.path.exists(paths[1]) and os.path.exists(paths[2])   # not Path.stat


# --- a cancel stops the batch between files -----------------------------------

def test_a_cancel_stops_before_the_next_file(monkeypatch, tmp_path):
    a, b, c = _files(tmp_path, 1, 1, 1)
    order = []
    monkeypatch.setattr(recycle_bin, "_send2trash", _fake_send(order))
    result = recycle_bin.send_batch(tmp_path, [a, b, c], lambda p: None,
                                    cancel=lambda: len(order) >= 1)
    assert result.sent == [a] and order == ["f0.bin"]
    assert result.cancelled and result.refused is None
    assert b.exists() and c.exists()


def test_a_cancel_already_asked_sends_nothing(monkeypatch, tmp_path):
    a, b = _files(tmp_path, 1, 1)
    order = []
    monkeypatch.setattr(recycle_bin, "_send2trash", _fake_send(order))
    result = recycle_bin.send_batch(tmp_path, [a, b], lambda p: pytest.fail("before ran"),
                                    cancel=lambda: True)
    assert result.sent == [] and order == [] and result.cancelled


def test_a_cancelled_windows_batch_still_runs_the_backstop(windows, monkeypatch, tmp_path):
    _, paths, log = _windows_batch(
        windows, monkeypatch, tmp_path, [1024, 1024],
        registry={("HKCU", VOLUME_KEY, "MaxCapacity"): 100},
        bin_states=[(0, 5), (0, 5)])
    result = recycle_bin.send_batch(tmp_path, paths, lambda p: None,
                                    cancel=lambda: len(log) >= 1)
    assert result.sent == [paths[0]] and result.cancelled
    assert result.unverified == [paths[0]]
