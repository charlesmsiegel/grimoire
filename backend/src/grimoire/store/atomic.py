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

**The temp's pathname is never handed out.** Both writers write through the
descriptor ``mkstemp`` returns, so there is no interval in which another
process can unlink the temp and substitute a symlink for our write, our chmod
and our rename to follow. An earlier version exposed the path for PIL's
``im.save``; PIL accepts a file object, so ``thumbs`` now encodes to memory and
calls ``write_bytes``, and the path-yielding context manager is gone rather
than defended (PR review).

Design: docs/superpowers/specs/2026-07-28-atomic-store-writes-design.md
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
import time
from pathlib import Path

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


def _carry_metadata(tmp_name: str, path: Path) -> None:
    """Give the replacement the access metadata the record already had.

    The surviving file is the temp, not the original inode, so anything the old
    file carried is lost unless it is copied across. ``mkstemp`` creates 0600,
    so without this the first atomic write would silently narrow every
    group/world-readable record to owner-only on Linux, macOS and the Android
    build.

    Copied where the platform allows: the permission bits, the owning group
    (uid too, but only a privileged process can change that -- the attempt is
    harmless and simply fails otherwise), and extended attributes.

    **Not** copied, and accepted: POSIX ACLs proper (``getfacl``/``setfacl``)
    are not reachable from the standard library at all, and on Windows a
    target's explicit non-inherited DACL, alternate data streams, and per-file
    compression/encryption flags are lost -- Win32 has ``ReplaceFileW`` exactly
    to preserve those, but Python does not expose it without ``ctypes``.
    Grimoire records are plain files in a user-owned directory that inherit
    their parent's ACL, so a fresh sibling gets the same treatment; a
    genuinely ACL-managed shared store is out of scope. (Raised in PR review.)

    Every step is best effort: failing an entire write over a metadata bit
    would trade a cosmetic problem for a data-loss one.
    """
    try:
        src = os.stat(path)
    except OSError:
        src = None

    mode = stat.S_IMODE(src.st_mode) if src else 0o666 & ~_UMASK
    try:
        os.chmod(tmp_name, mode)
    except OSError:
        pass

    if src is not None and hasattr(os, "chown"):
        try:
            os.chown(tmp_name, src.st_uid, src.st_gid)
        except (OSError, AttributeError):
            # Unprivileged: uid can't change. Retry group alone -- that one
            # usually succeeds and is what shared-group setups depend on.
            try:
                os.chown(tmp_name, -1, src.st_gid)
            except (OSError, AttributeError):
                pass

    if src is not None and hasattr(os, "listxattr"):
        try:
            for attr in os.listxattr(path):
                os.setxattr(tmp_name, attr, os.getxattr(path, attr))
        except OSError:
            pass  # unsupported filesystem, or an attribute we may not copy


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
        _carry_metadata(tmp_name, path)
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


def append_line(path: Path, line: str) -> None:
    """Append one complete line to an append-only log, creating it if absent.

    The temp-and-replace pair above is the wrong shape for a *ledger*: it
    rewrites the whole file to add a row, so an append costs a read plus a full
    copy, and two writers racing each other lose one of the two rows outright
    rather than interleaving them. This is the other crash-safe primitive, for
    the other kind of file — ``store.usage``'s month ledger today.

    **What this guarantees:** the line is published by a single ``write`` on a
    descriptor opened ``O_APPEND``, so the kernel resolves the offset and the
    write as one step. Concurrent appenders — a second grimoire process on a
    synced store, two turns finishing at once — therefore land whole lines in
    some order, never halves of one row spliced into another.

    **What it does not:** durability across power loss (no fsync — a ledger row
    is not worth an fsync on the generating path, and the caller's own docstring
    says a lost row costs a statistic), and atomicity for a line long enough
    that the kernel returns a short write. The remainder is written in the loop
    below, and a torn line is the one thing that can produce; readers of these
    files skip a line they cannot parse for exactly that reason. Rows here are a
    few hundred bytes, orders of magnitude under any platform's atomic-write
    floor.

    ``O_APPEND`` is honoured on Windows too — CPython maps it to
    ``FILE_APPEND_DATA``, which the OS serializes the same way.
    """
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    # The mode matters on first creation only, and mirrors what `_carry_metadata`
    # gives a replaced record: the process umask applied to 0666, rather than
    # mkstemp's owner-only 0600.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666 & ~_UMASK)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)

