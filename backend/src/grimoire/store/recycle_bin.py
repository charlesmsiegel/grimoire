"""The OS recycle bin: whether this machine has a real one, and sending to it.

Image collection hands unused pictures to the operating system's bin rather
than deleting them, so the one thing this module must never do is turn "move
to the bin" into "delete". Send2Trash (the `desktop` extra) does the moving;
what it cannot promise, on Windows, is that a bin exists to move *to*.

**Why the Windows guards exist.** Send2Trash 2.1 calls the shell without
`FOF_WANTNUKEWARNING`. On a volume with no Recycle Bin (a network share, a
mapped or removable drive) or one set to delete immediately, Windows then
deletes the file permanently and *reports success*. Nothing in the call's
result distinguishes the two, so `check` refuses those configurations before
any file is touched: the outbox's volume must be a fixed drive
(`GetDriveTypeW`), answer `SHQueryRecycleBinW`, and carry neither the
`NoRecycleFiles` policy (HKCU or HKLM) nor `NukeOnDelete`. The volume asked is
the one the *outbox* sits on -- `GetVolumePathNameW` of its deepest existing
ancestor -- not the drive letter of the library, because a removable drive can
be mounted into a folder of `C:`. macOS and Linux raise when a volume has no
trash, and that raise is an ordinary refusal.

**Capacity.** A bin at capacity purges its oldest items to fit a new one, and a
file larger than the whole bin is deleted outright. The capacity is the
volume's `MaxCapacity` (MB) in the registry; a file larger than a *known*
capacity stops the run unsent (`too_large_for_bin`). When the per-volume key is
absent Windows uses its own default (about 10% of the first 40 GB of the
volume plus 5% beyond), which is not stored anywhere we can read, so the
capacity is estimated as 10% of the volume: at or above the default, so an
estimate that is wrong over-estimates capacity, which makes the backstop below
more willing to say "unverified" rather than less.

**The backstop says *unverified*, not *deleted*.** After a batch the bin's
item count is read again. If it rose by less than the number of files sent and
no purge could have happened (the size before plus the bytes sent fits the
capacity), those files are reported in `BatchResult.unverified`. A purge, or
Explorer emptying the bin concurrently, also moves the count, which is why the
claim is only that Windows did not confirm it. It can only notice after the
fact; it is the backstop for a configuration none of the checks recognise.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import proclock

try:
    from send2trash import send2trash as _imported_send2trash
except Exception:  # noqa: BLE001 - an unimportable helper must degrade to `not_installed`, not break the store facade
    _imported_send2trash = None

#: The one entry point to the OS bin, a seam: tests replace it, and None means
#: the helper is not installed.
_send2trash: Callable[[str], None] | None = _imported_send2trash

#: `GetDriveTypeW`'s answer for a local hard disk: the only kind with a bin.
DRIVE_FIXED = 3

MESSAGES: dict[str, str] = {
    "android": "This device has no recycle bin. Run the collection from a computer.",
    "not_installed": ("The recycle-bin helper is not installed. "
                      "Re-run the installer, then try again."),
    "volume_has_no_bin": ("This library's drive has no Recycle Bin (a network, mapped or "
                          "removable drive), so pictures can't be sent to it."),
    "bin_deletes_immediately": ("Windows is set to delete files immediately instead of "
                                "using the Recycle Bin on this drive."),
    "too_large_for_bin": "This picture is larger than the Recycle Bin on its drive can hold.",
}

_POLICY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
_VOLUME_KEYS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
_MIB = 1024 * 1024
#: Estimated bin capacity when the volume has no `MaxCapacity`: a tenth of it.
_ESTIMATE_DIVISOR = 10


@dataclass(frozen=True)
class Availability:
    ok: bool
    code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "code": self.code, "message": self.message}


@dataclass
class BatchResult:
    sent: list[Path]
    refused: Path | None = None
    code: str | None = None
    error: str | None = None
    unverified: list[Path] = field(default_factory=list)


def _platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


# --- Windows -----------------------------------------------------------------
#
# The real implementations are only defined (and only importable) on Windows;
# every other platform gets the same names answering None, so the module
# imports and type-checks everywhere and the logic below runs against fakes.

if sys.platform == "win32":
    import ctypes
    import re
    import winreg
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
    _GUID = re.compile(r"\{[0-9A-Fa-f-]+\}")

    class _SHQUERYRBINFO(ctypes.Structure):
        # shellapi.h wraps this in pshpack1.h: the two int64s follow the DWORD
        # with no padding, and cbSize must say so (20, not 24).
        _pack_ = 1
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("i64Size", ctypes.c_int64),
                    ("i64NumItems", ctypes.c_int64)]

    def _volume_of(path: Path) -> str | None:
        buf = ctypes.create_unicode_buffer(32768)
        if not _kernel32.GetVolumePathNameW(str(path), buf, len(buf)):
            return None
        return buf.value or None

    def _drive_type(volume: str) -> int:
        return int(_kernel32.GetDriveTypeW(volume))

    def _query_bin(volume: str) -> tuple[int, int] | None:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(info)
        if _shell32.SHQueryRecycleBinW(volume, ctypes.byref(info)) != 0:
            return None
        return int(info.i64Size), int(info.i64NumItems)

    def _volume_guid(volume: str) -> str | None:
        buf = ctypes.create_unicode_buffer(64)
        if not _kernel32.GetVolumeNameForVolumeMountPointW(volume, buf, len(buf)):
            return None
        found = _GUID.search(buf.value)
        return found.group(0) if found else None

    def _volume_bytes(volume: str) -> int | None:
        total = ctypes.c_ulonglong(0)
        if not _kernel32.GetDiskFreeSpaceExW(volume, None, ctypes.byref(total), None):
            return None
        return int(total.value)

    def _reg_dword(hive: str, path: str, name: str) -> int | None:
        root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
        try:
            with winreg.OpenKey(root, path) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return int(value) if kind == winreg.REG_DWORD else None
else:
    def _volume_of(path: Path) -> str | None:
        return None

    def _drive_type(volume: str) -> int:
        return 0

    def _query_bin(volume: str) -> tuple[int, int] | None:
        return None

    def _volume_guid(volume: str) -> str | None:
        return None

    def _volume_bytes(volume: str) -> int | None:
        return None

    def _reg_dword(hive: str, path: str, name: str) -> int | None:
        return None

#: The seam tests replace wholesale. Looked up at call time, never bound.
_win: Any = SimpleNamespace(
    volume_of=_volume_of, drive_type=_drive_type, query_bin=_query_bin,
    volume_guid=_volume_guid, volume_bytes=_volume_bytes, reg_dword=_reg_dword,
)


def _outbox_volume(root: Path) -> str | None:
    """The volume the collection outbox lives on: that of the deepest existing
    ancestor of the outbox directory (nothing is created to ask)."""
    probe = root / ".cache" / "image-store" / "recycle"
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    volume: str | None = _win.volume_of(probe)
    return volume


def _volume_key(volume: str) -> str | None:
    guid = _win.volume_guid(volume)
    return f"{_VOLUME_KEYS}\\{guid}" if guid else None


def _refusal(code: str) -> Availability:
    return Availability(False, code, MESSAGES[code])


def check(root: Path) -> Availability:
    """Whether pictures can be sent to a real recycle bin from this store."""
    if proclock.on_android():
        return _refusal("android")
    if _send2trash is None:
        return _refusal("not_installed")
    if _platform() != "windows":
        return Availability(True)
    volume = _outbox_volume(root)
    if volume is None or _win.drive_type(volume) != DRIVE_FIXED:
        return _refusal("volume_has_no_bin")
    if _win.query_bin(volume) is None:
        return _refusal("volume_has_no_bin")
    for hive in ("HKCU", "HKLM"):
        if _win.reg_dword(hive, _POLICY_KEY, "NoRecycleFiles") == 1:
            return _refusal("bin_deletes_immediately")
    key = _volume_key(volume)
    if key is not None and _win.reg_dword("HKCU", key, "NukeOnDelete") == 1:
        return _refusal("bin_deletes_immediately")
    return Availability(True)


def _capacity(volume: str) -> tuple[int | None, int | None]:
    """(known capacity, capacity to judge a purge by), in bytes.

    The first is the volume's `MaxCapacity` and is None when the key is absent.
    The second is the same value, else a tenth of the volume, else None when
    even the volume's size is unreadable (`send_batch` then rules no purge in,
    which errs toward reporting a batch unverified)."""
    key = _volume_key(volume)
    megabytes = _win.reg_dword("HKCU", key, "MaxCapacity") if key else None
    if megabytes is not None:
        return megabytes * _MIB, megabytes * _MIB
    total = _win.volume_bytes(volume)
    return None, (total // _ESTIMATE_DIVISOR if total is not None else None)


def _unverified(volume: str, pre: tuple[int, int] | None, estimate: int | None,
                result: BatchResult, sent_bytes: int) -> list[Path]:
    """The sent files Windows did not confirm (see the module docstring)."""
    if pre is None or not result.sent:
        return []
    post = _win.query_bin(volume)
    if post is None or post[1] - pre[1] >= len(result.sent):
        return []
    # No estimate at all means no purge is assumed: err toward stopping.
    may_have_purged = estimate is not None and pre[0] + sent_bytes > estimate
    return [] if may_have_purged else list(result.sent)


def send_batch(root: Path, paths: list[Path],
               before: Callable[[Path], None]) -> BatchResult:
    """Send `paths` to the bin in order, `before(path)` ahead of each.

    `before` raising stops the run and propagates. The run stops at the first
    refusal (`refused`), or on Windows at a file larger than a known bin
    capacity (`too_large_for_bin`, left unsent). On Windows the files that were
    sent but not confirmed are returned in `unverified` (see the module
    docstring); elsewhere that list is always empty."""
    sender = _send2trash
    if sender is None:
        raise RuntimeError("send_batch called with no recycle-bin helper installed")
    result = BatchResult(sent=[])
    windows = _platform() == "windows"
    volume = _outbox_volume(root) if windows else None
    if windows and volume is None:
        if paths:
            result.refused, result.code = paths[0], "volume_has_no_bin"
        return result
    pre = _win.query_bin(volume) if volume is not None else None
    known, estimate = _capacity(volume) if volume is not None else (None, None)
    sent_bytes = 0
    for path in paths:
        before(path)
        size = path.stat().st_size if windows else 0
        if known is not None and size > known:
            result.refused, result.code = path, "too_large_for_bin"
            break
        try:
            sender(str(path))
        except Exception as exc:  # noqa: BLE001 - any refusal from the OS bin ends the run
            result.refused, result.code, result.error = path, "refused", type(exc).__name__
            break
        result.sent.append(path)
        sent_bytes += size
    if volume is not None:
        result.unverified = _unverified(volume, pre, estimate, result, sent_bytes)
    return result
