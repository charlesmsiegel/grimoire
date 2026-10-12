"""Metadata-change time, including Windows where stat ctime is birth time.

No bytes are opened or read. Unsupported metadata opts out of stamp trust.
FILE_BASIC_INFO.ChangeTime is distinct from CreationTime (Microsoft Win32).
"""

from __future__ import annotations

import os
import struct
from types import ModuleType
from typing import ClassVar

_ctypes: ModuleType | None
try:
    import ctypes as _ctypes_available
except (ImportError, OSError):
    _ctypes = None
else:
    _ctypes = _ctypes_available


if _ctypes is not None:
    class _BasicInfo(_ctypes_available.Structure):
        _fields_: ClassVar = [('creation', _ctypes_available.c_int64), ('access', _ctypes_available.c_int64),
                    ('write', _ctypes_available.c_int64), ('change', _ctypes_available.c_int64), ('attributes', _ctypes_available.c_uint32)]


    class _StatByName(_ctypes_available.Structure):
        __slots__ = ()
        # FILE_STAT_BASIC_INFORMATION: same ABI used by CPython's Windows
        # stat fast path. One result includes ChangeTime and 128-bit identity.
        _fields_: ClassVar = [('file_id', _ctypes_available.c_uint64),
            *[(name, _ctypes_available.c_int64) for name in
              ('creation', 'access', 'write', 'change', 'allocation', 'size')],
            *[(name, _ctypes_available.c_uint32) for name in
              ('attributes', 'reparse', 'links', 'device_type', 'characteristics', 'reserved')],
            ('volume', _ctypes_available.c_uint64), ('identity', _ctypes_available.c_ubyte * 16)]


def _api():
    if os.name != 'nt' or _ctypes is None:
        return None
    try:
        api = getattr(_ctypes, 'WinDLL')('kernel32', use_last_error=True)  # noqa: B009 -- absent from POSIX stubs
        api.CreateFileW.argtypes = [_ctypes.c_wchar_p, _ctypes.c_uint32, _ctypes.c_uint32, _ctypes.c_void_p,
                                   _ctypes.c_uint32, _ctypes.c_uint32, _ctypes.c_void_p]
        api.CreateFileW.restype = _ctypes.c_void_p
        api.GetFileInformationByHandleEx.argtypes = [_ctypes.c_void_p, _ctypes.c_int, _ctypes.c_void_p, _ctypes.c_uint32]
        api.GetFileInformationByHandleEx.restype = _ctypes.c_int
        api.CloseHandle.argtypes = [_ctypes.c_void_p]
        api.CloseHandle.restype = _ctypes.c_int
        api.GetVolumePathNameW.argtypes = [_ctypes.c_wchar_p, _ctypes.c_wchar_p, _ctypes.c_uint32]
        api.GetVolumePathNameW.restype = _ctypes.c_int
        api.GetDriveTypeW.argtypes = [_ctypes.c_wchar_p]
        api.GetDriveTypeW.restype = _ctypes.c_uint32
        api.GetVolumeInformationW.argtypes = [_ctypes.c_wchar_p, _ctypes.c_wchar_p, _ctypes.c_uint32,
                                            _ctypes.c_void_p, _ctypes.c_void_p, _ctypes.c_void_p,
                                            _ctypes.c_wchar_p, _ctypes.c_uint32]
        api.GetVolumeInformationW.restype = _ctypes.c_int
        return api
    except (AttributeError, OSError):
        return None


def _name_api():
    if _ctypes is None or os.name != 'nt':
        return None
    try:
        api = getattr(_ctypes, 'WinDLL')('api-ms-win-core-file-l2-1-4')  # noqa: B009 -- optional platform export
        function = api.GetFileInformationByName
        function.argtypes = [_ctypes.c_wchar_p, _ctypes.c_int, _ctypes.c_void_p, _ctypes.c_uint32]
        function.restype = _ctypes.c_int
        return function
    except (AttributeError, OSError):
        return None


_WINDOWS = _api()
_BY_NAME = _name_api()
_FILETIME_EPOCH = 116444736000000000
# The documented104-byte little-endian ABI. Decode the native buffer once
# in C rather than boxing its ctypes fields repeatedly on every warm read.
_NATIVE_FIELDS = struct.Struct('<Q6q6IQ16s')


def windows_change_supported(path: str | os.PathLike[str], device: int | None = None) -> bool:
    """Current local NTFS/ReFS provenance; never cache filesystem support.

    Mount paths and serials can both be reused after reformatting/remounting.
    The device argument identifies the caller's stamp, not filesystem trust.
    """
    if _WINDOWS is None or _ctypes is None:
        return False
    volume = _ctypes.create_unicode_buffer(32768)
    if not (_WINDOWS.GetVolumePathNameW(os.path.abspath(path), volume, len(volume))
            and _WINDOWS.GetDriveTypeW(volume.value) in (2, 3, 6)):
        return False
    filesystem = _ctypes.create_unicode_buffer(64)
    return bool(_WINDOWS.GetVolumeInformationW(volume.value, None, 0, None, None, None,
                                                   filesystem, len(filesystem))
                     and filesystem.value in ('NTFS', 'ReFS'))


def native_stamp(path: str | os.PathLike[str]) -> tuple[int, int, int, int, int] | None:
    """Coherent live metadata for lifetime-limited memory memos.

    This does not establish filesystem provenance for persisted source trust.
    Reject unavailable, remote/reparse/unknown-device and inconsistent time
    results. CopyFile can preserve ChangeTime while assigning a new creation
    time, so the invalidation stamp includes the later of the two. A new file
    cannot appear aged; a future creation time keeps it out of the racy window.
    Every call queries the current file's metadata.
    """
    if _BY_NAME is None or _ctypes is None or _WINDOWS is None:
        return None
    basic = _StatByName()
    if not _BY_NAME(os.fspath(path), 3, _ctypes.byref(basic), _NATIVE_FIELDS.size):
        return None
    fields = _NATIVE_FIELDS.unpack_from(basic)
    created, modified, changed, size = fields[1], fields[3], fields[4], fields[6]
    if (fields[7] & 0x400 or fields[11] & 0x10 or fields[10] not in (7, 8)
            or min(created, changed) <= _FILETIME_EPOCH or size < 0):
        return None
    return ((modified - _FILETIME_EPOCH) * 100, (max(changed, created) - _FILETIME_EPOCH) * 100,
            size, int.from_bytes(fields[14], 'little'), fields[13])


def fast_stamp(path: str | os.PathLike[str]) -> tuple[int, int, int, int, int] | None:
    """Live metadata with current-mount proof for persisted source trust.

    A serial alone is not a volume identity: clones can share it. Resolve the
    current mount and filesystem anew before every persisted-source check.
    """
    observed = native_stamp(path)
    return observed if observed is not None and windows_change_supported(path, observed[4]) else None


def change_ns(path: str | os.PathLike[str], info: os.stat_result) -> int | None:
    """Use native metadata change time; never substitute Windows creation."""
    if os.name != 'nt':
        return info.st_ctime_ns
    if _BY_NAME is not None:
        stamp = fast_stamp(path)
        # A stat and native query that crossed a replacement cannot vouch
        # for one file version. Keep its bytes live without persisting trust.
        if stamp is None or (stamp[0], stamp[2], stamp[3], stamp[4]) != (
                info.st_mtime_ns, info.st_size, info.st_ino, info.st_dev):
            return None
        return stamp[1]
    if _ctypes is None or not windows_change_supported(path, info.st_dev):
        return None
    handle = _WINDOWS.CreateFileW(os.path.abspath(path), 0x80, 7, None, 3, 0x02000000, None)
    if handle == _ctypes.c_void_p(-1).value or handle is None:
        return None
    try:
        basic = _BasicInfo()
        if not _WINDOWS.GetFileInformationByHandleEx(handle, 0, _ctypes.byref(basic), _ctypes.sizeof(basic)):
            return None
        return ((max(basic.change, basic.creation) - _FILETIME_EPOCH) * 100
                if min(basic.change, basic.creation) > _FILETIME_EPOCH else None)
    finally:
        _WINDOWS.CloseHandle(handle)
