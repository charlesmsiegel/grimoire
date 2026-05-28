"""Build the migrated schema once and stamp pristine copies per test.

Replaying every migration for each test that needs a database dominates
unit-suite wall time: the schema is byte-for-byte identical every time, so
running all of ``storage/migrations/`` per test is pure repeated work. This
module runs the migrations **once per process** into a template file and
hands out cheap copies.

Because :func:`grimoire.storage.apply_migrations` early-returns when the
schema is already at HEAD, call sites can keep their existing
``await apply_migrations(db)`` line after stamping — it simply finds nothing
pending. That keeps the optimisation transparent (and self-healing: a newly
added migration is picked up the next time the template is built).

The template is built through the real :func:`apply_migrations` so there is
no second schema definition to drift out of sync.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
from pathlib import Path

from grimoire.storage import Database, apply_migrations

_template_path: Path | None = None
_lock = threading.Lock()


def _build_template() -> Path:
    """Run all migrations into a fresh single-file database, once."""
    directory = Path(tempfile.mkdtemp(prefix="grimoire-schema-"))
    path = directory / "template.sqlite"

    async def _build() -> None:
        # enable_wal=False keeps the whole schema in one self-contained file
        # (no -wal/-shm sidecars), so a plain file copy is a complete snapshot.
        db = Database(path, pool_size=1, enable_wal=False)
        await db.connect()
        try:
            await apply_migrations(db)
        finally:
            await db.close()

    # Callers are typically inside a running event loop (pytest-asyncio),
    # where asyncio.run() would raise. Build on a dedicated thread with its
    # own loop and surface any failure on the calling thread.
    error: list[BaseException] = []

    def _runner() -> None:
        # Capture any failure (including KeyboardInterrupt/SystemExit) and
        # re-raise it on the calling thread below.
        try:
            asyncio.run(_build())
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=_runner, name="grimoire-schema-build")
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return path


def stamp_migrated_db(path: str | Path) -> Path:
    """Copy the once-built, fully-migrated schema to ``path``.

    The first call builds the template (one full migration run); subsequent
    calls are just a file copy. Returns ``path`` as a :class:`~pathlib.Path`.
    The destination's parent directory is created if needed.
    """
    global _template_path
    with _lock:
        if _template_path is None or not _template_path.exists():
            _template_path = _build_template()
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_template_path, dest)
    return dest
