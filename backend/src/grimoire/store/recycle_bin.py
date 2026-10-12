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
any file is touched, and `send_batch` asks the whole question again at the
start of every batch, since any answer can change between the two (a drive
remounted, a policy pushed): the outbox's volume must be a fixed drive
(`GetDriveTypeW`), answer `SHQueryRecycleBinW`, and carry neither the
`NoRecycleFiles` policy (HKCU or HKLM) nor `NukeOnDelete` (any nonzero
value is on). The volume asked is
the one the *outbox* sits on -- `GetVolumePathNameW` of its deepest existing
ancestor -- not the drive letter of the library, because a removable drive can
be mounted into a folder of `C:`. macOS and Linux raise when a volume has no
trash, and that raise is an ordinary refusal.

**A registry value that cannot be read is not an unset one.** An absent key or
value means Windows' defaults (the bin on), but access denied or any other read
error says nothing about the setting, and reading it as absent is the one
answer that can turn a send into a permanent delete. So `_reg_value` keeps the
three apart (a DWORD, None for absent, `UNREADABLE`), and an unreadable
`NoRecycleFiles`, `NukeOnDelete` or `MaxCapacity` refuses with
`bin_settings_unreadable`. So does a volume whose GUID cannot be resolved: its
per-volume key cannot be named, so those two are unread, not absent.

**macOS and Linux need hard links.** There the move into the outbox is a hard
link and an unlink (it never replaces a file), so `check` probes, with a pair
of empty files it removes again, that the outbox's drive can make one, and
refuses `no_hard_links` (or `outbox_not_writable`) before a collection starts
rather than letting it fail at its first object.

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

import contextlib
import os
import sys
import uuid
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
    "bin_settings_unreadable": ("Windows' Recycle Bin settings for this drive couldn't be "
                                "read, so pictures won't be sent to it."),
    "no_hard_links": ("This library's drive can't make hard links (for example exFAT, FAT, "
                      "or some network and cloud drives), so unused pictures can't be safely "
                      "moved to the recycle bin from it. Move the library to a drive that "
                      "supports them to collect from it."),
    "outbox_not_writable": ("This library's folder can't be written to, so unused pictures "
                            "can't be moved to the recycle bin from it."),
}

_POLICY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
_VOLUME_KEYS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
_MIB = 1024 * 1024
#: Estimated bin capacity when the volume has no `MaxCapacity`: a tenth of it.
_ESTIMATE_DIVISOR = 10


class _Unreadable:
    """The type of `UNREADABLE`."""

    def __repr__(self) -> str:
        return "UNREADABLE"


#: A registry value that may exist but could not be read: never None, which
#: means absent.
UNREADABLE = _Unreadable()

#: What one registry read answers: the DWORD, None (absent) or `UNREADABLE`.
RegValue = int | None | _Unreadable

#: `winreg.REG_DWORD`, spelled here so `_reg_value` runs on every platform.
REG_DWORD = 4


def _reg_value(read: Callable[[], tuple[object, int]]) -> RegValue:
    """Classify one registry read. `read` opens the key and queries the value,
    returning (value, kind). FileNotFoundError -- the key or the value is not
    there -- is absent (None): Windows' defaults apply. Any other OSError
    (access denied, a broken hive, ...) is `UNREADABLE`. So is a value that is
    there but is not a DWORD: Windows reads these settings as DWORDs, and
    guessing what another type means could read "delete immediately" as "off",
    so it fails closed like a read error."""
    try:
        value, kind = read()
    except FileNotFoundError:
        return None
    except OSError:
        return UNREADABLE
    if kind != REG_DWORD or not isinstance(value, int):
        return UNREADABLE
    return value


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
    #: `cancel` answered true: the files after the last one sent were not tried.
    cancelled: bool = False


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

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _GUID = re.compile(r"\{[0-9A-Fa-f-]+\}")
    _ulonglong_p = ctypes.POINTER(ctypes.c_ulonglong)

    def _declare(function: Any, argtypes: list[Any], restype: Any) -> None:
        function.argtypes = argtypes
        function.restype = restype

    _declare(_kernel32.GetVolumePathNameW,
             [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_int)
    _declare(_kernel32.GetDriveTypeW, [ctypes.c_wchar_p], ctypes.c_uint)
    _declare(_kernel32.GetVolumeNameForVolumeMountPointW,
             [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_int)
    _declare(_kernel32.GetDiskFreeSpaceExW,
             [ctypes.c_wchar_p, _ulonglong_p, _ulonglong_p, _ulonglong_p], ctypes.c_int)
    # SHQueryRecycleBinW's second argument is a struct pointer, declared once
    # the struct exists (below); the HRESULT comes back as a signed int.

    class _SHQUERYRBINFO(ctypes.Structure):
        # shellapi.h applies 1-byte packing (pshpack1.h) only when _WIN64 is
        # not defined: on 32-bit Windows the int64s follow the DWORD with no
        # padding (cbSize 20), on x64 they are naturally aligned (cbSize 24,
        # at offsets 8 and 16). cbSize is taken from sizeof, so it follows.
        if ctypes.sizeof(ctypes.c_void_p) == 4:
            _pack_ = 1
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("i64Size", ctypes.c_int64),
                    ("i64NumItems", ctypes.c_int64)]

    _declare(_shell32.SHQueryRecycleBinW,
             [ctypes.c_wchar_p, ctypes.POINTER(_SHQUERYRBINFO)], ctypes.c_long)

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

    def _reg_dword(hive: str, path: str, name: str) -> RegValue:
        root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]

        def read() -> tuple[object, int]:
            with winreg.OpenKey(root, path) as key:
                value, kind = winreg.QueryValueEx(key, name)
            return value, kind

        return _reg_value(read)
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

    def _reg_dword(hive: str, path: str, name: str) -> RegValue:
        return None

#: The seam tests replace wholesale. Looked up at call time, never bound.
_win: Any = SimpleNamespace(
    volume_of=_volume_of, drive_type=_drive_type, query_bin=_query_bin,
    volume_guid=_volume_guid, volume_bytes=_volume_bytes, reg_dword=_reg_dword,
)


def _outbox_ancestor(root: Path) -> Path:
    """The deepest existing ancestor of the collection outbox directory (the
    outbox itself when it exists): where to ask about the outbox's drive
    without creating anything."""
    probe = root / ".cache" / "image-store" / "recycle"
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def _outbox_volume(root: Path) -> str | None:
    """The volume the collection outbox lives on: that of the deepest existing
    ancestor of the outbox directory (nothing is created to ask)."""
    volume: str | None = _win.volume_of(_outbox_ancestor(root))
    return volume


_PROBE_PREFIX = ".grimoire-link-probe-"


def _probe_hard_links(directory: Path) -> str | None:
    """Whether a hard link can be made in `directory`: None when it can, else
    the refusal code. Off Windows the move into the outbox is a hard link and
    an unlink (`image_gc._no_clobber_move`), so a drive without them (exFAT,
    FAT, many network and cloud mounts) would stop every collection at its
    first object.

    One uniquely named empty file is created (``O_EXCL``, so nothing is ever
    opened over) and linked to a second unique name; both are removed after,
    whatever happened, and only the names this call made -- a link refused
    because its name exists leaves that file alone. A file that cannot be
    created is `outbox_not_writable` (the folder, not the links, is the
    problem); a link that fails with any OSError is `no_hard_links`."""
    token = uuid.uuid4().hex
    source = Path(directory) / f"{_PROBE_PREFIX}{token}"
    linked = Path(directory) / f"{_PROBE_PREFIX}{token}-link"
    made: list[Path] = []
    try:
        try:
            os.close(os.open(source, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except OSError:
            return "outbox_not_writable"
        made.append(source)
        try:
            os.link(source, linked, follow_symlinks=False)
        except OSError:
            return "no_hard_links"
        made.append(linked)
        return None
    finally:
        for path in made:
            with contextlib.suppress(OSError):
                os.unlink(path)


#: The seam tests replace: the hard-link probe `check` runs off Windows.
#: Looked up at call time, never bound.
_link_probe: Callable[[Path], str | None] = _probe_hard_links


def _volume_key(volume: str) -> str | None:
    guid = _win.volume_guid(volume)
    return f"{_VOLUME_KEYS}\\{guid}" if guid else None


def _refusal(code: str) -> Availability:
    return Availability(False, code, MESSAGES[code])


@dataclass(frozen=True)
class _WindowsReading:
    """What `_windows` answered and read on the way: the outbox's volume, the
    bin's (size, item count) there, and the volume's `MaxCapacity` in MB (None
    when absent; a capacity that could not be read never gets this far)."""
    availability: Availability
    volume: str | None = None
    state: tuple[int, int] | None = None
    max_capacity_mb: int | None = None


def _windows(root: Path) -> _WindowsReading:
    """Windows' half of `check`, and what it read on the way. `send_batch`
    asks this whole question again before every batch, because a drive, a
    policy or a per-volume setting may have changed since `check` answered,
    and keeps the bin reading as the batch's "before" for the backstop and
    the capacity as what it judges sizes by (read once, here, so the two can
    never disagree). A setting that cannot be read refuses
    (`bin_settings_unreadable`): it is never taken as unset."""
    volume = _outbox_volume(root)
    if volume is None or _win.drive_type(volume) != DRIVE_FIXED:
        return _WindowsReading(_refusal("volume_has_no_bin"), volume)
    state = _win.query_bin(volume)
    if state is None:
        return _WindowsReading(_refusal("volume_has_no_bin"), volume)
    key = _volume_key(volume)
    if key is None:
        # No GUID, so the per-volume key cannot even be named: NukeOnDelete
        # and MaxCapacity are unread, which is not the same as absent -- a
        # volume set to delete immediately would pass.
        return _WindowsReading(_refusal("bin_settings_unreadable"), volume, state)
    flags = [_win.reg_dword(hive, _POLICY_KEY, "NoRecycleFiles") for hive in ("HKCU", "HKLM")]
    flags.append(_win.reg_dword("HKCU", key, "NukeOnDelete"))
    # Windows reads each flag as on when it is nonzero, and an unexpected
    # value is safest read as on: any nonzero DWORD refuses, not only 1.
    if any(isinstance(flag, int) and flag != 0 for flag in flags):
        return _WindowsReading(_refusal("bin_deletes_immediately"), volume, state)
    if any(flag is UNREADABLE for flag in flags):
        return _WindowsReading(_refusal("bin_settings_unreadable"), volume, state)
    megabytes = _win.reg_dword("HKCU", key, "MaxCapacity")
    if megabytes is UNREADABLE:
        # The capacity rule (`too_large_for_bin`, the backstop) stands on it.
        return _WindowsReading(_refusal("bin_settings_unreadable"), volume, state)
    return _WindowsReading(Availability(True), volume, state,
                           megabytes if isinstance(megabytes, int) else None)


def check(root: Path) -> Availability:
    """Whether pictures can be sent to a real recycle bin from this store.
    Off Windows this makes (and removes) two empty probe files where the
    outbox goes (`_probe_hard_links`); it never creates the outbox."""
    if proclock.on_android():
        return _refusal("android")
    if _send2trash is None:
        return _refusal("not_installed")
    if _platform() != "windows":
        # Windows moves into the outbox with `os.rename`; elsewhere the move
        # is a hard link, so a drive that cannot make one is refused here
        # rather than failing the run at its first object.
        code = _link_probe(_outbox_ancestor(Path(root)))
        return Availability(True) if code is None else _refusal(code)
    return _windows(root).availability


def _capacity(volume: str, megabytes: int | None) -> tuple[int | None, int | None]:
    """(known capacity, capacity to judge a purge by), in bytes.

    The first is the volume's `MaxCapacity` as `_windows` read it (`megabytes`)
    and is None when the value is absent. The second is the same value, else a
    tenth of the volume, else None when even the volume's size is unreadable
    (`send_batch` then rules no purge in, which errs toward reporting a batch
    unverified)."""
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
    if post is None:
        return list(result.sent)  # nothing confirmed them: a doubt is never "sent"
    if post[1] - pre[1] >= len(result.sent):
        return []
    # No estimate at all means no purge is assumed: err toward stopping.
    may_have_purged = estimate is not None and pre[0] + sent_bytes > estimate
    return [] if may_have_purged else list(result.sent)


def _never() -> bool:
    return False


def send_batch(root: Path, paths: list[Path], before: Callable[[Path], None], *,
               cancel: Callable[[], bool] = _never) -> BatchResult:
    """Send `paths` to the bin in order, `before(path)` ahead of each.

    On Windows the whole availability check (`_windows`) runs first, every
    batch: a refusal there refuses `paths[0]` with its code and sends nothing.
    `cancel()` is asked before each file; once it answers true nothing more is
    sent and `cancelled` is set. `before` raising stops the run and propagates.
    The run stops at the first refusal (`refused`), or on Windows at a file
    larger than a known bin capacity (`too_large_for_bin`, left unsent) or one
    that cannot be sized (`refused`, with the error's class). A file gone
    before it is sized is skipped: it is neither sent nor refused. On Windows
    the files that were sent but not confirmed are returned in `unverified`
    (see the module docstring), however the batch ended short of a raise;
    elsewhere that list is always empty."""
    sender = _send2trash
    if sender is None:
        raise RuntimeError("send_batch called with no recycle-bin helper installed")
    result = BatchResult(sent=[])
    windows = _platform() == "windows"
    volume: str | None = None
    pre: tuple[int, int] | None = None
    known, estimate = None, None
    if windows:
        reading = _windows(root)
        available, volume, pre = reading.availability, reading.volume, reading.state
        if not available.ok or volume is None:
            # With no bin to trust, nothing is sent: on Windows that is how a
            # file gets deleted outright.
            if paths:
                result.refused, result.code = paths[0], available.code or "volume_has_no_bin"
            return result
        known, estimate = _capacity(volume, reading.max_capacity_mb)
    sent_bytes = 0
    for path in paths:
        if cancel():
            result.cancelled = True
            break
        before(path)
        size = _send_one(path, sender, result, windows=windows, known=known)
        if size is None:
            break
        sent_bytes += size
    if volume is not None:
        result.unverified = _unverified(volume, pre, estimate, result, sent_bytes)
    return result


def _send_one(path: Path, sender: Callable[[str], None], result: BatchResult, *,
              windows: bool, known: int | None) -> int | None:
    """Hand one file to the bin: the bytes it took (0 for a file gone before
    it could be sized, which is skipped), or None when the batch stops here,
    with the refusal recorded on `result`."""
    size = 0
    if windows:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return 0                     # gone from the outbox: nothing to send
        except OSError as exc:
            result.refused, result.code, result.error = path, "refused", type(exc).__name__
            return None
    if known is not None and size > known:
        result.refused, result.code = path, "too_large_for_bin"
        return None
    try:
        sender(str(path))
    except Exception as exc:  # noqa: BLE001 - any refusal from the OS bin ends the run
        result.refused, result.code, result.error = path, "refused", type(exc).__name__
        return None
    result.sent.append(path)
    return size
