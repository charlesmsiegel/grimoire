"""A staged request's lifecycle identity, fence and staging state (`Batch`'s base)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

from . import (
    _contracts,
    _deletion,
    _fences,
    _runtime,
    _sqlite_support,
    _storage,
)


class _BatchState:
    """Request-owned staging; its lifecycle identity precedes caller compute."""

    def __init__(self, root: Path, *, identity: tuple[int, int, bool] | None = None) -> None:
        _runtime._process_state()
        self.root = root
        self.pid, self.epoch, fenced = identity or (os.getpid(), _runtime._state.epoch, bool(_runtime._state.transitions))
        self.disabled = fenced or self.pid != os.getpid() or self.epoch != _runtime._state.epoch
        self._rows: dict[_contracts.Key, tuple[str, str, str, str, int]] = {}
        self._bytes = 0
        self._sources: dict[str, tuple] = {}
        self._source_sizes: dict[str, int] = {}
        self._materialized: dict[tuple[str, str, str], tuple] = {}
        self._materialized_sizes: dict[tuple[str, str, str], int] = {}
        self._artifact_touches: dict[_contracts.Key, int] = {}
        self._artifact_used: dict[_contracts.Key, int] = {}
        self._source_known: dict[str, tuple] = {}
        self._history_known: dict[tuple, tuple] = {}
        self._source_clock_checked = False
        self._source_clock: _contracts.FilesystemClock | None = None
        self._storage_started = False
        self._attempt_started = False
        self._attempt_pending = False
        self._maintenance_checked = False
        self._marker: str | None = None
        self._generation: str | None = None
        self._fence_snapshot: tuple | None = None
        # Whether the last `flush` committed what was staged -- or found
        # nothing to write because every row asked for was already there. A
        # caller that remembers having written (`store/searchdocs`'s
        # materialized rows) reads it, so a refused or failed write is
        # retried, not forgotten. `refused` is set when a put or a row was
        # turned away while staging, which "nothing to write" must not hide.
        self.committed = False
        self.refused = False

    def _usable(self, key: _contracts.Key | None = None, *, strict: bool = False) -> bool:
        self._maintenance()
        self._remember_boundary()
        lifecycle = (not self.disabled and not _runtime._state.transitions and self.pid == os.getpid()
                     and self.epoch == _runtime._state.epoch and _runtime._available()
                     and (key is None or (key.kind in _contracts.KINDS and bool(_contracts._DEVICE_KEY.fullmatch(key.digest)))))
        return lifecycle and self._fence(strict=strict)

    def _maintenance(self) -> None:
        if not self._maintenance_checked:
            self._maintenance_checked = True
            if not self.disabled and self.pid == os.getpid() and self.epoch == _runtime._state.epoch:
                _deletion._pending_maintenance(self.root)

    def begin_compute(self) -> None:
        """Fence caller-supplied snapshot work before its hashing/computation.

        Source validation and store_written do this internally. Callers that
        compute optional history from already supplied bytes need the same
        durable boundary before doing their own work; no SQLite is opened here.
        """
        self._remember_boundary()

    def _remember_boundary(self) -> None:
        # SQL opt-out is temporary (for example while a campaign lock is
        # held), but caller compute has already started. Capture the durable
        # boundary before availability can short-circuit the SQLite fence.
        if not self._attempt_started:
            self._attempt_started = True
            self._marker, clear = _fences._controls(self.root)
            self._attempt_pending = not clear

    def _fence(self, *, strict: bool = False) -> bool:
        """Whether no purge or delete intent has arrived since the boundary.

        Reads may reuse this batch's last passing answer while the control
        snapshot stands (`_control_snapshot`); a commit (`strict`) always
        reads the marker and the intents again."""
        if self._attempt_pending:
            return False
        if not strict and self._fence_snapshot is not None:
            if _fences._control_snapshot(self.root) == self._fence_snapshot:
                return not self.disabled
            self._fence_snapshot = None
        taken = _fences._wall_ns()
        snapshot = _fences._control_snapshot(self.root)
        passed = self._fence_read(strict=strict)
        if passed and snapshot is not None and _fences._settled(snapshot, taken):
            self._fence_snapshot = snapshot
        return passed

    def _fence_read(self, *, strict: bool) -> bool:
        marker, _clear = _fences._controls(self.root, strict=strict)
        # A busy first access still establishes the temporal boundary. A
        # retry may recover the same generation, but cannot adopt a purge that
        # happened while caller compute was already underway (S5 review).
        if marker is None or marker != self._marker:
            return False
        if not self._storage_started:
            self._storage_started = True
            if not _runtime._state.lock.acquire(blocking=False):
                self._storage_started = False
                return False
            try:
                with _storage._database(self.root, expected_generation=self._generation) as session:
                    if session is None:
                        self._storage_started = False
                        return False
                    self._generation = _fences._read_generation(session.conn)
            except _sqlite_support._CACHE_ERRORS:
                self._storage_started = False
                _runtime._warn_once('generation')
            finally:
                _runtime._state.lock.release()
        marker, clear = _fences._controls(self.root, strict=strict)
        return (not self.disabled and self._marker is not None and self._generation is not None
                and marker == self._marker and clear)

    def _session_current(self, session: _runtime._Session, *, strict: bool = False) -> bool:
        return self._usable(strict=strict) and _fences._read_generation(session.conn) == self._generation

    def _abort_input(self, error: OSError) -> NoReturn:
        self.disabled = True
        self._clear_staging()
        raise _contracts.InputUnavailable("compiled input unavailable") from error

    def _discard_staging(self) -> None:
        self.refused = True     # what was staged is lost: never "nothing to write"
        self._clear_staging()

    def _clear_staging(self) -> None:
        self._rows.clear()
        self._sources.clear()
        self._source_sizes.clear()
        self._materialized.clear()
        self._materialized_sizes.clear()
        self._artifact_touches.clear()
        self._bytes = 0

    def _staged_count(self) -> int:
        """Rows staged in every map, against `MAX_STAGED_ROWS`."""
        return len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches)

    def _remember_metadata(self, known: dict, key: object, row: tuple) -> None:
        try:
            known[key] = row
        except _sqlite_support._CACHE_ERRORS:
            # The source/hash or history value was already verified. This memo
            # only avoids redundant touches; losing it cannot lose that answer.
            self._discard_staging()
            _runtime._warn_once('metadata_bookkeeping')
