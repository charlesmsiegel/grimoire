"""Optional platform filesystem clocks and source metadata trust."""

from __future__ import annotations

import os
import platform
import stat
import sys
from pathlib import Path
from types import ModuleType

from .. import file_stats
from . import _contracts, _fences, _path_checks, _runtime, _sqlite_support

# ctypes is optional on packaged hosts; unavailable platform helpers cost
# persisted-source hits, while exact-byte hashing remains usable.
_ctypes: ModuleType | None
try:
    import ctypes as _ctypes_available
except (ImportError, OSError):
    _ctypes = None
else:
    _ctypes = _ctypes_available
_WINDOWS_CLOCK = sys.platform == "win32"
_kernel32 = None
if _WINDOWS_CLOCK and _ctypes is not None:
    try:
        _kernel32 = _ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.GetVolumePathNameW.argtypes = [_ctypes.c_wchar_p, _ctypes.c_wchar_p, _ctypes.c_uint]
        _kernel32.GetVolumePathNameW.restype = _ctypes.c_int
        _kernel32.GetDriveTypeW.argtypes = [_ctypes.c_wchar_p]
        _kernel32.GetDriveTypeW.restype = _ctypes.c_uint
    except (AttributeError, OSError):
        _kernel32 = None

# Linux/Android's generic statfs ABI begins with a native word f_type. Only
# these known ABIs are sampled; unproven hosts keep exact-byte artifact keys.
_LINUX_CLOCK = (sys.platform in ("linux", "android") and platform.machine().lower() in
                {"x86_64", "amd64", "aarch64", "arm64", "i386", "i686", "armv7l", "armv8l"})
_libc = None
if _LINUX_CLOCK and _ctypes is not None:
    try:
        _libc = _ctypes.CDLL(None, use_errno=True)
        _libc.statfs.argtypes = [_ctypes.c_char_p, _ctypes.c_void_p]
        _libc.statfs.restype = _ctypes.c_int
    except (AttributeError, OSError):
        _libc = None
# Local kernel filesystems share the sentinel's clock. Network, FUSE and
# overlay layers are excluded: utime(None) can still transmit CLIENT time
# on CIFS, and a layered mount does not establish its backing clock (S2 gate).
_LOCAL_FILESYSTEMS = frozenset({
    0xEF53, 0x9123683E, 0x58465342, 0x01021994, 0x858458F6,
    0xF2F52010, 0x4D44, 0x2011BAB0,  # ext, btrfs, xfs, tmpfs, ramfs, f2fs, FAT, exFAT
})


def _linux_filesystem_type(path: Path) -> int | None:
    if not _LINUX_CLOCK or _libc is None or _ctypes is None:
        return None
    try:
        final = path.resolve()
        try:
            final.stat()
        except FileNotFoundError:
            # A new sentinel will belong to its guarded containing directory.
            # Existing leaves are classified themselves, including bind mounts.
            final = final.parent
        # Only f_type is interpreted; oversized native-word storage keeps all
        # supported statfs layouts within the allocated, correctly aligned area.
        output = (_ctypes.c_long * 128)()
        if _libc.statfs(os.fsencode(final), output) == 0:
            return int(output[0]) & 0xFFFFFFFF
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _clock_supported(path: Path) -> bool:
    if not _WINDOWS_CLOCK:
        return _linux_filesystem_type(path) in _LOCAL_FILESYSTEMS
    if _kernel32 is None or _ctypes is None:
        return False
    try:
        # Resolve the user-selected boundary only after owned components have
        # been checked. Matching st_dev alone does not prove a remote clock:
        # Windows utime(None) supplies CLIENT time (S2 plan provenance ruling).
        final = str(path.resolve())
        volume = _ctypes.create_unicode_buffer(32768)
        return bool(_kernel32.GetVolumePathNameW(final, volume, len(volume))
                    and _kernel32.GetDriveTypeW(volume.value) in (2, 3, 6)
                    and file_stats.windows_change_supported(path))
    except (OSError, ValueError, AttributeError):
        return False


def _clock_path(root: Path) -> Path | None:
    device = _runtime._device(root)
    location = _fences._location(root, device) if device is not None else None
    return location.parent / f"{device}-clock" if location is not None else None


def filesystem_clock(root: Path) -> _contracts.FilesystemClock | None:
    """Sample proven filesystem time; never fall back to process wall time."""
    if not _runtime._available():
        return None
    try:
        path = _clock_path(root)
        if path is not None and _path_checks._leaf(path) and _clock_supported(path):
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            os.utime(path, None)
            info = path.stat()
            if info.st_dev > 0:
                return _contracts.FilesystemClock(info.st_mtime_ns, info.st_dev)
    except _sqlite_support._CACHE_ERRORS:
        pass
    _runtime._warn_once("clock")
    return None


def _source_info(root: Path, path: Path) -> _contracts._SourceInfo:
    # The timestamp and device must come from ONE live stat, before byte IO.
    info = path.stat()
    changed = file_stats.change_ns(path, info)
    stamp = (str(path), info.st_mtime_ns, changed if changed is not None else info.st_ctime_ns, info.st_size, info.st_ino)
    relative = None
    plain = False
    # Parent traversal can cross a symlink before '..' even when abspath
    # later appears contained. Keep such readable inputs live-only (S2 review).
    if ".." in path.parts:
        return _contracts._SourceInfo(path, stamp, info.st_dev, None, False, stat.S_ISREG(info.st_mode))
    try:
        boundary = Path(os.path.abspath(root))
        relative_path = Path(os.path.abspath(path)).relative_to(boundary)
        relative = relative_path.as_posix()
        current = boundary
        plain = True
        for component in relative_path.parts:
            current = current / component
            if _path_checks._linked(current.lstat()):
                plain = False
                break
    except (OSError, ValueError):
        plain = False
    return _contracts._SourceInfo(path, stamp, info.st_dev, relative, plain, stat.S_ISREG(info.st_mode), changed is not None)


def _eligible(info: _contracts._SourceInfo, clock: _contracts.FilesystemClock | None) -> bool:
    return bool(clock is not None and clock.device > 0 and info.device == clock.device
                and info.relative is not None and info.plain and info.regular and info.metadata_trusted
                and clock.now_ns - max(info.stamp[1:3]) > _contracts.PERSIST_WINDOW_NS)
