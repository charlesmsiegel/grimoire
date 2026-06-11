"""Snapshot/restore helpers that keep file mutations atomic with SQL.

Shared by the :class:`~grimoire.state_store.store.StateStore` write paths and
:class:`~grimoire.state_store.delta_ops.DeltaOps` so a rolled-back transaction
never leaves a file changed without its index/delta-log record.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path


def restore_file(target: Path, before_bytes: bytes | None) -> None:
    """Undo a disk mutation when the surrounding SQL transaction rolled back.

    If the file did not exist beforehand, unlink the new write; otherwise
    restore the original bytes. Used by library and campaign file writes so
    file state and ``library_index`` / ``campaign_content_index`` never drift
    apart when the index update fails.
    """
    if before_bytes is None:
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
    else:
        target.write_bytes(before_bytes)


@contextlib.contextmanager
def snapshot_file_before(target: Path) -> Iterator[None]:
    """Restore ``target``'s prior bytes (or absence) when the wrapped block fails.

    Every file mutation and its index/delta-log transaction must run inside
    this block: a SQL rollback (or cancellation) would otherwise leave the
    file changed with no delta-log record of it, and that divergence does not
    self-heal — the watcher re-indexes content, not delta history. See BUGS.md
    ("apply_delta path leaves orphan files when SQL rolls back").
    """
    before_bytes = target.read_bytes() if target.exists() else None
    try:
        yield
    except BaseException:
        restore_file(target, before_bytes)
        raise
