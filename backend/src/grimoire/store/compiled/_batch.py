"""One staged derivation request: derive, write-through and publication."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

from . import (
    _contracts,
    _deletion,
    _encoding,
    _fences,
    _maintenance,
    _runtime,
    _sqlite_support,
    _storage,
)
from ._batch_inputs import _Inputs

# Re-exported: the package facade names the literal SQL templates here.
from ._batch_reads import _ARTIFACT_MANY_SQL as _ARTIFACT_MANY_SQL
from ._batch_reads import _MATERIALIZED_SQL as _MATERIALIZED_SQL
from ._batch_reads import _SOURCE_SQL as _SOURCE_SQL


class Batch(_Inputs):
    """Request-owned staging; its lifecycle identity precedes caller compute.

    The request is layered by responsibility, each layer in its own module:
    lifecycle and fence state (`_batch_state`), bounded staging admission
    (`_batch_staging`), SQL reads (`_batch_reads`) and input validation
    (`_batch_inputs`). This class adds derivation and publication.
    """

    def _bytes_for(self, kind: _contracts.Kind, values: tuple[_contracts.ValidatedInput, ...], *,
                   required: bool = False) -> tuple[_contracts.ValidatedInput, ...]:
        needed = [_contracts.Input(item.role, item.path) for item in values if item.data is None and item.content_hash != _contracts.ABSENT]
        fetched = iter(self.validate(kind, needed, with_bytes=True))
        result = tuple(next(fetched) if item.data is None and item.content_hash != _contracts.ABSENT else item for item in values)
        # Collection membership was established by the live listing. A member
        # disappearing is a failed required snapshot even when ordinary file
        # inputs with this role deliberately permit ABSENT (S4 fidelity gate).
        if required and any(item.content_hash == _contracts.ABSENT for item in result):
            self._abort_input(FileNotFoundError('collection member disappeared'))
        return result

    def _compute_view(self, files: tuple[_contracts.ValidatedInput, ...],
                      collections: tuple[_contracts.CollectionDigest, ...]) -> _contracts.DerivationInputs:
        return _contracts.DerivationInputs(
            tuple(_contracts.ComputeInput(item.role, item.content_hash, item.data, ()) for item in files),
            tuple(_contracts.ComputeCollection(collection.role, collection.content_hash, tuple(
                _contracts.ComputeInput(item.role, item.content_hash, item.data,
                             tuple(Path(_encoding._relative(self.root, item.path) or '').parts)) for item in collection.members))
                  for collection in collections))

    def derive(self, kind: _contracts.Kind, inputs: Sequence[_contracts.Input], *, params: Mapping[str, object],
               compute: Callable[[_contracts.DerivationInputs, Mapping[str, object]], object],
               collections: Sequence[_contracts.Collection] = (), instance: object = None) -> object:
        self.begin_compute()  # Empty-input keys still represent caller work.
        files = self.validate(kind, inputs)
        groups = tuple(self.collection(kind, collection) for collection in collections)
        pairs = [(item.role, item.content_hash) for item in files]
        key = _encoding.make_key(kind, pairs, params=params, collections=[(group.role, group.content_hash) for group in groups])
        hit = self.lookup(key)
        if hit.hit and key is not None:
            self._refresh_family(key, (*files, *(item for group in groups for item in group.members)), instance)
            return hit.value
        files = self._bytes_for(kind, files)
        groups = tuple(replace(group, members=self._bytes_for(kind, group.members, required=True)) for group in groups)
        groups = tuple(replace(group, content_hash=_encoding._collection_hash(self.root, group.members)) for group in groups)
        key = _encoding.make_key(kind, [(item.role, item.content_hash) for item in files], params=params,
                       collections=[(group.role, group.content_hash) for group in groups])
        value = compute(self._compute_view(files, groups), params)
        if isinstance(value, _contracts.Uncached):
            return value.value
        self._store_family(key, value, (*files, *(item for group in groups for item in group.members)), instance)
        return value

    def store_written(self, kind: _contracts.Kind, inputs: Sequence[_contracts.WrittenInput], value: object, *,
                      params: Mapping[str, object], instance: object = None) -> _contracts.Key | None:
        self._usable()  # capture before hashing the writer's supplied snapshot
        # Known bytes are the writer's snapshot. Never stat/reopen them or
        # infer an immediately trustworthy source stamp from a completed write.
        files = tuple(_contracts.ValidatedInput(item.role, item.path, hashlib.sha256(item.data).hexdigest(), None, item.data)
                      for item in inputs)
        key = _encoding.make_key(kind, [(item.role, item.content_hash) for item in files], params=params)
        return key if self._store_family(key, value, files, instance) else None

    def flush(self, *, keep: Callable[[], bool] | None = None) -> None:
        """Publish staged work only while the caller's optional predicate holds.

        Cancellation is separate from lifecycle invalidation: refuse before a
        transaction, or roll back before commit, without feeding it into the
        post-commit lifecycle purge. Recheck after the writer exclusion wait
        (09-S4 I1); a commit already executing remains an atomic operation.
        """
        staged = self._take_staged()
        if staged.empty:
            # Nothing staged: committed only if nothing was turned away, i.e.
            # every row asked for was already there and current. No fence is
            # read for it: an unusable cache refuses at staging, which set
            # `refused`, and an empty request must create no storage.
            self.committed = not self.refused and (keep is None or keep())
            return
        if not self._usable(strict=True) or not _runtime._state.lock.acquire(timeout=_contracts.WRITE_LOCK_SECONDS):
            return
        session = None
        try:
            if _cancelled(keep) or not self._usable(strict=True):
                return
            with _storage._database(self.root, expected_generation=self._generation) as session:
                if (session is None or not self._session_current(session, strict=True)
                        or _cancelled(keep)):
                    return
                self._publish(session, staged, keep)
        except _sqlite_support._CACHE_ERRORS as error:
            if session is not None:
                _storage._recover(self.root, session.path, error)
            _runtime._warn_once("flush")
        finally:
            _runtime._state.lock.release()

    def _take_staged(self) -> _Staged:
        """Empty this request's staging, returning what it held."""
        staged = _Staged(self._rows, self._sources, self._materialized, self._artifact_touches)
        self._rows, self._sources, self._materialized, self._artifact_touches = {}, {}, {}, {}
        self._source_sizes.clear()
        self._materialized_sizes.clear()
        self._bytes = 0
        return staged

    def _publish(self, session: _runtime._Session, staged: _Staged,
                 keep: Callable[[], bool] | None) -> None:
        """Write `staged` in one transaction, committed only while this
        request's fence and the caller's `keep` still hold."""
        session.conn.execute("BEGIN IMMEDIATE")
        if not self._session_current(session, strict=True):
            session.conn.rollback()
            return
        _write_staged(session.conn, staged)
        if (not self._session_current(session, strict=True)
                or _cancelled(keep)):
            session.conn.rollback()
            return
        session.conn.commit()
        # Publication cannot wait for this SQL exclusion. If it lands
        # after our last pre-commit check, erase the just-committed
        # snapshot before releasing that exclusion (S5 Codex gate).
        if not self._session_current(session, strict=True):
            marker = _fences._read_marker(self.root)
            _deletion._purge_connection(session.conn, marker if marker is not None else self._generation or '')
            return
        # Committed only if nothing asked for was turned away while
        # staging: a partly staged request is a partly written one.
        self.committed = not self.refused
        _maintenance._maintain(session)
        session.last_used = _runtime._clock()


class _Staged(NamedTuple):
    """What one `flush` takes off its request to publish."""

    rows: dict
    sources: dict
    metadata: dict
    touches: dict

    @property
    def empty(self) -> bool:
        return not self.rows and not self.sources and not self.metadata and not self.touches


def _cancelled(keep: Callable[[], bool] | None) -> bool:
    """Whether the caller's optional predicate has stopped holding."""
    return keep is not None and not keep()


def _write_staged(conn, staged: _Staged) -> None:
    conn.executemany(
        'INSERT INTO artifacts(kind,key_digest,payload,"check",last_used) VALUES (?,?,?,?,?) '
        'ON CONFLICT(kind,key_digest) DO UPDATE SET payload=excluded.payload,'
        '"check"=excluded."check",last_used=excluded.last_used', staged.rows.values())
    conn.executemany(
        'INSERT INTO sources(path,mtime_ns,ctime_ns,size,ino,content_hash,"check",last_used) '
        'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET '
        'mtime_ns=excluded.mtime_ns,ctime_ns=excluded.ctime_ns,size=excluded.size,'
        'ino=excluded.ino,content_hash=excluded.content_hash,"check"=excluded."check",'
        'last_used=excluded.last_used', staged.sources.values())
    # Associations describe an already bounded artifact family.
    # Touches never enumerate paths or change a reader's answer.
    conn.executemany(
        'UPDATE artifacts SET last_used=? WHERE kind=? AND key_digest=? AND last_used<?',
        ((used, key.kind, key.digest, used - _contracts.TOUCH_INTERVAL_S + 1) for key, used in staged.touches.items()))
    conn.executemany(
        'UPDATE materialized SET last_used=? WHERE artifact_kind=? AND artifact_key=? AND last_used<?',
        ((used, key.kind, key.digest, used - _contracts.TOUCH_INTERVAL_S + 1) for key, used in staged.touches.items()))
    conn.executemany(
        'INSERT INTO materialized(path,kind,instance,built_from,last_used,"check",artifact_kind,artifact_key) '
        'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path,kind,instance) DO UPDATE SET '
        'built_from=excluded.built_from,last_used=excluded.last_used,"check"=excluded."check",'
        'artifact_kind=excluded.artifact_kind,artifact_key=excluded.artifact_key', staged.metadata.values())
