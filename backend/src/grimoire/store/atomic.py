"""Crash-safe record writes (#233).

Every Markdown/JSON record in the store goes through here. A plain
``Path.write_text`` truncates and then writes, so a crash between those two
steps leaves a truncated file -- and a scene transcript cannot be regenerated
from anywhere.

**What this guarantees:** a reader sees the whole previous version or the whole
new version, never a partial one. That covers a process crash, an exception
mid-write, a full disk, and a reader racing a writer.

**What it does not guarantee** (the first draft of the design overclaimed both):

- *Durability across power loss.* CPython's ``os.replace`` uses ``MoveFileExW``
  without ``MOVEFILE_WRITE_THROUGH``, and we do not fsync the parent directory
  on POSIX, so the rename itself may not survive a power cut. The fsync we do
  perform buys the narrower thing: if the rename lands, the bytes behind it are
  complete rather than a page-cache ghost. You may find the old version; you
  will not find a shredded one.
- *Consistency across sync clients.* OneDrive and Dropbox resolve simultaneous
  edits with conflict copies on their own schedule. Out of scope here.

Records that are themselves symlinks or hard links are unsupported: unlike
``write_text``, which follows a leaf symlink and writes through to its target,
``os.replace`` replaces the directory entry. Nothing in grimoire creates linked
records.

Design: docs/superpowers/specs/2026-07-28-atomic-store-writes-design.md
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Transient Windows sharing failures -- a concurrent reader, an antivirus
# scanner, or a sync client holding the target for a moment. Everything else
# (a read-only target, a directory ACL that denies child-create/delete) is
# permanent, and retrying it would only delay the correct error while widening
# the unlocked read-modify-write window callers already race in.
_TRANSIENT_WINERRORS = frozenset((32, 33))  # SHARING_VIOLATION, LOCK_VIOLATION
_RETRY_DELAYS = (0.005, 0.010, 0.015, 0.020)  # ~50 ms total, then give up

# The temp name embeds the target's for debuggability, but truncated: a record
# name near the 255-character component limit would otherwise be writable
# directly yet fail once the prefix and random suffix are added.
_MAX_NAME_HINT = 40

# Read once, at import, while the process is still single-threaded. There is no
# getter for the umask -- you must set it to read it -- and doing that inside a
# request thread would briefly expose a 0 umask to every other thread creating a
# file, making their files world-writable.
_UMASK = os.umask(0)
os.umask(_UMASK)


def _replace(src: str, dst: Path) -> None:
    for delay in _RETRY_DELAYS:
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if getattr(e, "winerror", None) not in _TRANSIENT_WINERRORS:
                raise
            time.sleep(delay)
    os.replace(src, dst)  # last attempt: let a still-locked target raise


def _carry_mode(tmp_name: str, path: Path) -> None:
    """Give the replacement the mode the record already had.

    ``mkstemp`` creates 0600. Without this the first atomic write would
    silently narrow every group/world-readable record to owner-only on Linux,
    macOS and the Android build. A no-op on Windows, where the bits are
    vestigial.
    """
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        mode = 0o666 & ~_UMASK
    try:
        os.chmod(tmp_name, mode)
    except OSError:
        pass  # best effort; a filesystem without mode bits is not a failure


def _assert_target_writable(path: Path) -> None:
    """Refuse a read-only record, the way ``write_text`` did.

    Publishing by rename is governed by the *directory's* permissions, not the
    file's, so a `0444` record (or a Windows read-only attribute) that
    ``Path.write_text`` correctly refused would otherwise be replaced without
    complaint -- silently bypassing a protection the user set deliberately.
    """
    if path.exists() and not os.access(path, os.W_OK):
        raise PermissionError(errno.EACCES, "record is read-only", str(path))


def _mkstemp_beside(path: Path) -> tuple[int, str]:
    return tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name[:_MAX_NAME_HINT]}.", suffix=".tmp")


def _discard(tmp_name: str) -> None:
    """Best effort: the same scanner that can block a replace can briefly block
    the unlink, and failing to remove litter must not mask the real error."""
    try:
        os.unlink(tmp_name)
    except OSError:
        pass


def _write_through_fd(path: Path, mode: str, encoding: str | None, payload) -> None:
    """Write ``payload`` to a temp via mkstemp's own descriptor, then publish.

    The descriptor never leaves this function, so the temp's *pathname* is
    never something another process can substitute between creation and
    publication. Ordering: write -> flush -> fsync -> close -> chmod -> replace.
    The fd is closed exactly once on every path, including failures.
    """
    path = Path(path)
    _assert_target_writable(path)
    fd, tmp_name = _mkstemp_beside(path)
    closed = False
    try:
        with os.fdopen(fd, mode, encoding=encoding, closefd=False) as f:
            f.write(payload)
            f.flush()
        os.fsync(fd)
        os.close(fd)
        closed = True
        _carry_mode(tmp_name, path)
        _replace(tmp_name, path)
    except BaseException:
        if not closed:
            try:
                os.close(fd)
            except OSError:
                pass
        _discard(tmp_name)
        raise


def write_text(path: Path, text: str) -> None:
    """Atomic replacement for ``path.write_text(text, encoding="utf-8")``.

    Byte-identical to it, newline translation included -- forcing
    ``newline=""`` would rewrite every file in the user's store from CRLF to LF
    on its next save.
    """
    _write_through_fd(path, "w", "utf-8", text)


def write_bytes(path: Path, data: bytes) -> None:
    """Atomic replacement for ``path.write_bytes(data)``. See ``write_text``."""
    _write_through_fd(path, "wb", None, data)


@contextmanager
def tempfile_for(path: Path) -> Iterator[Path]:
    """Yield a same-directory temp *path* that replaces ``path`` on clean exit.

    Only for callers that must open the file themselves -- ``thumbs`` hands the
    path to PIL's ``im.save`` and never holds the bytes. Prefer ``write_text``
    / ``write_bytes``, which write through the ``mkstemp`` descriptor and so
    never expose the temp's pathname at all.

    Handing out a pathname reopens a window this module otherwise closes: in a
    store directory writable by another local account, the temp can be unlinked
    and replaced with a symlink between the yield and the reopen, and the write,
    the chmod, and the rename would all follow it. That cannot be *prevented*
    while the contract is "here is a path" -- so it is detected: the identity of
    the file mkstemp created is recorded and re-checked before anything is
    published, and a mismatch aborts without replacing the record.
    """
    path = Path(path)
    _assert_target_writable(path)
    fd, tmp_name = _mkstemp_beside(path)
    created = os.fstat(fd)
    closed = False
    try:
        os.close(fd)  # inside the try: a failing close must not leak the temp
        closed = True
        yield Path(tmp_name)

        # lstat, not stat: a symlink swapped in must be seen AS a symlink
        # rather than silently followed to whatever it points at.
        now = os.lstat(tmp_name)
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (
                created.st_dev, created.st_ino):
            raise OSError(errno.EPERM,
                          "temp file was replaced while being written", tmp_name)
        fd = os.open(tmp_name, os.O_RDWR)
        closed = False
        os.fsync(fd)
        os.close(fd)
        closed = True
        _carry_mode(tmp_name, path)
        _replace(tmp_name, path)
    except BaseException:
        if not closed:
            try:
                os.close(fd)
            except OSError:
                pass
        _discard(tmp_name)
        raise
