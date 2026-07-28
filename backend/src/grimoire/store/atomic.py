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


@contextmanager
def tempfile_for(path: Path) -> Iterator[Path]:
    """Yield a same-directory temp path that replaces ``path`` on clean exit.

    The temp lives beside its target because ``os.replace`` is only atomic
    within one filesystem. The descriptor ``mkstemp`` opens is closed
    immediately and only a path is yielded, so a caller that opens the path
    itself -- ``thumbs`` hands it to PIL's ``im.save`` -- never contends with a
    handle of ours, and nothing of ours can block the replace on Windows.

    On any exception the temp is removed (best effort: the same scanner that
    blocks a replace can briefly block the unlink) and the exception re-raised,
    leaving the previous record exactly as it was.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name[:_MAX_NAME_HINT]}.", suffix=".tmp")
    try:
        os.close(fd)  # inside the try: a failing close must not leak the temp
        yield Path(tmp_name)
        # Flush the caller's bytes to disk before publishing. Reopened O_RDWR
        # because Windows FlushFileBuffers needs write access.
        fd = os.open(tmp_name, os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _carry_mode(tmp_name, path)
        _replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_text(path: Path, text: str) -> None:
    """Atomic replacement for ``path.write_text(text, encoding="utf-8")``.

    Byte-identical to it, newline translation included -- forcing
    ``newline=""`` would rewrite every file in the user's store from CRLF to LF
    on its next save.
    """
    with tempfile_for(path) as tmp:
        with open(tmp, "w", encoding="utf-8") as f:  # atomic-ok: the temp itself
            f.write(text)


def write_bytes(path: Path, data: bytes) -> None:
    """Atomic replacement for ``path.write_bytes(data)``."""
    with tempfile_for(path) as tmp:
        with open(tmp, "wb") as f:  # atomic-ok: the temp itself
            f.write(data)
