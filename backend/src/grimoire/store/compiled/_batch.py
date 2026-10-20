"""One staged derivation request and its bounded literal SQL templates."""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

from .. import readstats, statcache
from . import (
    _contracts,
    _deletion,
    _encoding,
    _fences,
    _filesystem,
    _maintenance,
    _path_checks,
    _runtime,
    _sqlite_support,
    _storage,
)

_SOURCE_SQL = (
    "SELECT path,"
    "CASE WHEN typeof(mtime_ns)='integer' THEN mtime_ns END,"
    "CASE WHEN typeof(ctime_ns)='integer' THEN ctime_ns END,"
    "CASE WHEN typeof(size)='integer' THEN size END,"
    "CASE WHEN typeof(ino)='text' AND length(ino)<=40 THEN ino END,"
    "CASE WHEN typeof(content_hash)='text' AND length(content_hash)=64 THEN content_hash END,"
    "CASE WHEN typeof(\"check\")='text' AND length(\"check\")=64 THEN \"check\" END "
    " ,CASE WHEN typeof(last_used)='integer' THEN last_used END FROM sources WHERE path IN ("
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?'
    ")"
)


_ARTIFACT_MANY_SQL = (
    "SELECT key_digest,CASE WHEN typeof(payload)='text' "
    "AND length(CAST(payload AS BLOB))<=? THEN payload END,"
    "CASE WHEN typeof(\"check\")='text' AND length(\"check\")=64 THEN \"check\" END "
    " ,CASE WHEN typeof(last_used)='integer' THEN last_used END FROM artifacts WHERE kind=? AND key_digest IN ("
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?'
    ")"
)


_MATERIALIZED_SQL = (
    "SELECT path,CASE WHEN typeof(kind)='text' AND length(kind)<=? THEN kind END,"
    "CASE WHEN typeof(instance)='text' AND length(instance)<=? THEN instance END,"
    "CASE WHEN typeof(built_from)='text' AND length(built_from) IN (0,64) THEN built_from END,"
    "CASE WHEN typeof(last_used)='integer' AND last_used>=0 THEN last_used END,"
    "CASE WHEN typeof(\"check\")='text' AND length(\"check\")=64 THEN \"check\" END,"
    "CASE WHEN typeof(artifact_kind)='text' AND length(artifact_kind)<=? THEN artifact_kind END,"
    "CASE WHEN typeof(artifact_key)='text' AND length(artifact_key)=64 THEN artifact_key END "
    "FROM materialized WHERE path IN ("
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?'
    ")"
)


class Batch:
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

    def _persist_sources(self, kind: _contracts.Kind) -> bool:
        return _contracts.KINDS.get(kind.name) == kind and kind.allow_persisted_sources and self._usable()

    def _sample_clock(self, kind: _contracts.Kind) -> None:
        if not self._source_clock_checked and self._persist_sources(kind):
            self._source_clock_checked = True
            # Capture before ANY source stat. A slow read cannot age its own
            # input into trust, and a failed sample has no wall-time fallback.
            try:
                self._source_clock = _filesystem.filesystem_clock(self.root)
            except _sqlite_support._CACHE_ERRORS:
                _runtime._warn_once("clock")

    def _abort_input(self, error: OSError) -> NoReturn:
        self.disabled = True
        self._rows.clear()
        self._sources.clear()
        self._source_sizes.clear()
        self._materialized.clear()
        self._materialized_sizes.clear()
        self._artifact_touches.clear()
        self._bytes = 0
        raise _contracts.InputUnavailable("compiled input unavailable") from error

    def _input_infos(self, kind: _contracts.Kind, inputs: Sequence[_contracts.Input]) -> dict[Path, _contracts._SourceInfo | None]:
        found: dict[Path, _contracts._SourceInfo | None] = {}
        for item in inputs:
            if item.path not in found:
                try:
                    found[item.path] = _filesystem._source_info(self.root, item.path)
                except FileNotFoundError:
                    found[item.path] = None
                except OSError as error:
                    self._abort_input(error)
            if found[item.path] is None and not (_contracts.KINDS.get(kind.name) == kind
                                                and item.role in kind.absent_ok_roles):
                self._abort_input(FileNotFoundError())
        return found

    def _trusted_sources(self, kind: _contracts.Kind, infos: Sequence[_contracts._SourceInfo]) -> dict[Path, str]:
        if not self._persist_sources(kind):
            return {}
        eligible = [info for info in infos if _filesystem._eligible(info, self._source_clock)]
        if not eligible:
            return {}
        rows = self._source_rows([info.relative for info in eligible if info.relative is not None])
        trusted = {}
        for info in eligible:
            if info.relative is None:
                continue
            row = rows.get(info.relative)
            decoded = _encoding._decode_source(info.relative, row[:7]) if row is not None else None
            if row is not None and decoded is not None and decoded[0][1:] == info.stamp[1:]:
                readstats.bump("compiled.sources_hit")
                trusted[info.path] = decoded[1]
                self._remember_metadata(self._source_known, info.relative, row)
                self._stage_source(info.relative, info.stamp, decoded[1])
        return trusted if self._usable() else {}

    def validate(self, kind: _contracts.Kind, inputs: Sequence[_contracts.Input], *,
                 with_bytes: bool = False) -> tuple[_contracts.ValidatedInput, ...]:
        """Validate listed inputs; cold reads retain exact bytes for compute."""
        # Capture before source bytes even for False-policy derivations.
        # Persistence policy changes trust, never the delete-time boundary.
        if not inputs:
            return ()
        self._usable()
        self._sample_clock(kind)
        infos = self._input_infos(kind, inputs)
        return self._validate_infos(kind, inputs, infos, with_bytes=with_bytes)

    def _validate_infos(self, kind: _contracts.Kind, inputs: Sequence[_contracts.Input],
                        infos: Mapping[Path, _contracts._SourceInfo | None], *,
                        with_bytes: bool) -> tuple[_contracts.ValidatedInput, ...]:
        # File and collection forms share one stat-to-bytes trust rule. A
        # collection already captured its member stats while enumerating;
        # repeating them here could associate bytes with a different stamp.
        trusted = {} if with_bytes else self._trusted_sources(
            kind, [info for info in infos.values() if info is not None])
        values: dict[Path, tuple[str, statcache.Stamp | None, bytes | None]] = {}
        for path, info in infos.items():
            if info is None:
                values[path] = (_contracts.ABSENT, None, None)
                continue
            data = None
            digest = trusted.get(path)
            if digest is None:
                try:
                    data = path.read_bytes()
                except OSError as error:
                    self._abort_input(error)
                digest = hashlib.sha256(data).hexdigest()
                readstats.bump("compiled.sources_miss")
                readstats.bump("compiled.bytes_hashed", len(data))
                if (info.relative is not None and self._persist_sources(kind)
                        and _filesystem._eligible(info, self._source_clock)):
                    self._stage_source(info.relative, info.stamp, digest)
            values[path] = (digest, info.stamp, data)
        history = self.materialized(list(infos))
        return tuple(_contracts.ValidatedInput(item.role, item.path, *values[item.path], history.get(item.path, ()))
                     for item in inputs)

    def _collection_infos(self, kind: _contracts.Kind, collection: _contracts.Collection) -> dict[Path, _contracts._SourceInfo]:
        infos: dict[Path, _contracts._SourceInfo] = {}
        try:
            pending = [collection.path]
            while pending:
                parent = pending.pop()
                with os.scandir(parent) as listing:
                    entries = list(listing)
                for entry in entries:
                    path = Path(entry.path)
                    # Reparse directories/junctions must not become recursive
                    # traversal edges. Linked regular files still supply live
                    # bytes, and _source_info keeps them out of source trust.
                    if entry.is_dir(follow_symlinks=False):
                        if collection.recursive and not _path_checks._linked(entry.stat(follow_symlinks=False)):
                            pending.append(path)
                        continue
                    relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(self.root))).as_posix()
                    if kind.member_filter is not None and not kind.member_filter(relative):
                        continue
                    info = _filesystem._source_info(self.root, path)
                    if info.relative is None:
                        raise OSError('collection member has no contained path')
                    if info.regular:
                        infos[path] = info
        except (OSError, ValueError) as error:
            self._abort_input(OSError(str(error)))
        return infos

    def collection(self, kind: _contracts.Kind, collection: _contracts.Collection, *,
                   with_bytes: bool = False) -> _contracts.CollectionDigest:
        """List membership live; hash sorted relative paths and exact bytes."""
        self._usable()
        self._sample_clock(kind)
        try:
            directory = _filesystem._source_info(self.root, collection.path)
            if directory.relative is None or (collection.recursive and not directory.plain):
                raise OSError('collection has no plain contained directory')
        except (OSError, ValueError) as error:
            self._abort_input(OSError(str(error)))
        infos = self._collection_infos(kind, collection)
        ordered = sorted(infos, key=lambda path: infos[path].relative or "")  # all members have an info
        members = self._validate_infos(kind, [_contracts.Input(collection.role, path) for path in ordered],
                                       infos, with_bytes=with_bytes)
        # Its digest fits a composite key even when the manifest does not.
        readstats.bump("compiled.collection_digest")
        return _contracts.CollectionDigest(collection.role, _encoding._collection_hash(self.root, members), members)

    def validate_one(self, kind: _contracts.Kind, input: _contracts.Input, *,  # noqa: A002 -- Spec03 keyword contract
                     with_bytes: bool = False) -> _contracts.ValidatedInput:
        return self.validate(kind, [input], with_bytes=with_bytes)[0]

    def known_hashes(self, kind: _contracts.Kind, paths: Sequence[Path]) -> dict[Path, str | None]:
        """Stat every supplied path; never read input bytes or record a source."""
        if not paths:
            return {}
        self._sample_clock(kind)
        infos = []
        for path in paths:
            with contextlib.suppress(OSError):
                infos.append(_filesystem._source_info(self.root, path))
        trusted = self._trusted_sources(kind, infos)
        return {path: trusted.get(path) for path in paths}

    def lookup(self, key: _contracts.Key | None) -> _contracts.Lookup:
        answer = self._lookup(key)
        if key is not None:  # make_key's refusal is counted nowhere, as in lookup_many
            readstats.bump(f"compiled.{'hit' if answer.hit else 'miss'}.{key.kind}")
        return answer

    def _lookup(self, key: _contracts.Key | None) -> _contracts.Lookup:
        if key is not None and not self._usable(key):
            readstats.bump("compiled.bypass")
            return _contracts.Lookup(False)
        if key is None or not _runtime._state.lock.acquire(blocking=False):
            return _contracts.Lookup(False)
        session = None
        try:
            if not self._usable(key):
                return _contracts.Lookup(False)
            with _storage._database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return _contracts.Lookup(False)
                # Preserve the existence of a rejected row without loading its
                # payload into Python, so damage is reported like decode failures.
                row = session.conn.execute(
                    "SELECT CASE WHEN typeof(payload)='text' "
                    "AND length(CAST(payload AS BLOB))<=? THEN payload END,"
                    '"check",CASE WHEN typeof(last_used)=\'integer\' THEN last_used END '
                    'FROM artifacts WHERE kind=? AND key_digest=?',
                    (_contracts.MAX_PAYLOAD_BYTES, key.kind, key.digest)).fetchone()
                session.last_used = _runtime._clock()
        except _sqlite_support._CACHE_ERRORS as error:
            if session is not None:
                _storage._recover(self.root, session.path, error)
            _runtime._warn_once("lookup")
            return _contracts.Lookup(False)
        finally:
            _runtime._state.lock.release()
        # JSON construction and checksum work do not hold the SQLite lock.
        answer = _encoding._decode(key, row[:2]) if row is not None else _contracts.Lookup(False)
        self._remember_artifact(key, row, answer)
        return answer if self._usable(key) else _contracts.Lookup(False)


    def lookup_many(self, keys: Sequence[_contracts.Key | None]) -> _contracts.BatchLookup:
        """Decode only caller-supplied unique keys, with per-kind counts.

        A None key (`make_key` refusing) is a miss, as it is for `lookup`."""
        misses = dict.fromkeys(keys, _contracts.Lookup(False))
        values = misses.copy()
        groups: dict[str, list[_contracts.Key]] = {}
        for key in values:
            if key is not None and self._usable(key):
                groups.setdefault(key.kind, []).append(key)
        for kind, supplied in groups.items():
            for offset in range(0, len(supplied), _contracts.SOURCE_CHUNK):
                chunk = supplied[offset:offset + _contracts.SOURCE_CHUNK]
                rows = self._artifact_rows(kind, chunk)
                if rows is None:
                    return _contracts._lookup_result(misses)
                # Each chunk's transfer is bounded. Decode outside SQLite's
                # lock before selecting the next chunk, rather than retaining
                # every requested encoded payload alongside decoded values.
                for key in chunk:
                    row = rows.get(key.digest)
                    if row is not None:
                        values[key] = _encoding._decode(key, row[:2])
                        self._remember_artifact(key, row, values[key])
        if not self._usable():
            return _contracts._lookup_result(misses)
        return _contracts._lookup_result(values if self._usable() else misses)

    def _remember_artifact(self, key: _contracts.Key, row: tuple | None, answer: _contracts.Lookup) -> None:
        try:
            if row is not None and type(row[2]) is int:
                self._artifact_used[key] = row[2]
            if answer.hit and self._usable(key):
                self._touch_artifact(key)
        except _sqlite_support._CACHE_ERRORS:
            # A decoded answer is already valid. A failed bookkeeping insert
            # can follow byte-budget accounting, so discard all partial writes.
            self._discard_staging()
            _runtime._warn_once('lookup_bookkeeping')

    def _touch_artifact(self, key: _contracts.Key) -> None:
        if key in self._rows or int(time.time()) - self._artifact_used.get(key, 0) < _contracts.TOUCH_INTERVAL_S:
            return
        size = len(key.kind) * 6 + 256
        count = len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches)
        if key not in self._artifact_touches:
            if count >= _contracts.MAX_STAGED_ROWS or self._bytes + size > _contracts.MAX_STAGED_BYTES:
                return
            self._bytes += size
        self._artifact_touches[key] = int(time.time())

    def _artifact_rows(self, kind: str, keys: Sequence[_contracts.Key]) -> dict[str, tuple] | None:
        if not self._usable() or not _runtime._state.lock.acquire(blocking=False):
            return None
        session = None
        try:
            if not self._usable():
                return None
            with _storage._database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return None
                supplied = [key.digest for key in keys]
                parameters: list[object] = [_contracts.MAX_PAYLOAD_BYTES, kind, *supplied]
                parameters.extend([None] * (_contracts.SOURCE_CHUNK - len(supplied)))
                rows = {row[0]: row[1:] for row in session.conn.execute(_ARTIFACT_MANY_SQL, parameters)}
                session.last_used = _runtime._clock()
                return rows
        except _sqlite_support._CACHE_ERRORS as error:
            if session is not None:
                _storage._recover(self.root, session.path, error)
            _runtime._warn_once('lookup')
            return None
        finally:
            _runtime._state.lock.release()

    def _source_rows(self, paths: Sequence[str]) -> dict[str, tuple]:
        if not paths or not self._usable() or not _runtime._state.lock.acquire(blocking=False):
            return {}
        session = None
        try:
            if not self._usable():
                return {}
            with _storage._database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return {}
                found = {}
                for offset in range(0, len(paths), _contracts.SOURCE_CHUNK):
                    chunk: list[str | None] = list(paths[offset:offset + _contracts.SOURCE_CHUNK])
                    chunk.extend([None] * (_contracts.SOURCE_CHUNK - len(chunk)))
                    for row in session.conn.execute(_SOURCE_SQL, chunk):
                        found[row[0]] = row
                session.last_used = _runtime._clock()
                return found
        except _sqlite_support._CACHE_ERRORS as error:
            if session is not None:
                _storage._recover(self.root, session.path, error)
            _runtime._warn_once("source")
            return {}
        finally:
            _runtime._state.lock.release()

    def _materialized_rows(self, paths: Sequence[str], *, strict: bool = False) -> list[tuple] | None:
        # Strict callers (spec 05's sync) must tell "no rows" from "could not
        # ask": they wait briefly for the process exclusion, and every
        # non-answer is None rather than an empty, cold-looking list.
        miss: list[tuple] | None = None if strict else []
        if not paths:
            return []
        if not self._usable() or not (_runtime._state.lock.acquire(timeout=_contracts.STRICT_LOCK_SECONDS) if strict
                                      else _runtime._state.lock.acquire(blocking=False)):
            return miss
        session = None
        try:
            if not self._usable():
                return miss
            with _storage._database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return miss
                supplied: list[object] = [_contracts.MAX_KEY_BYTES, _contracts.MAX_KEY_BYTES, _contracts.MAX_KEY_BYTES, *paths]
                supplied.extend([None] * (_contracts.SOURCE_CHUNK - len(paths)))
                rows = _encoding._history_transfer(session.conn.execute(_MATERIALIZED_SQL, supplied))
                session.last_used = _runtime._clock()
                return rows if rows is not None else _contracts._OVER_BUDGET
        except _sqlite_support._CACHE_ERRORS as error:
            if session is not None:
                _storage._recover(self.root, session.path, error)
            _runtime._warn_once('materialized')
            return miss
        finally:
            _runtime._state.lock.release()

    def materialized(self, paths: Sequence[Path]) -> dict[Path, tuple[_contracts.Materialized, ...]]:
        """Advisory work history for explicit paths, including vanished ones."""
        found = self._read_history(paths, strict=False)
        return dict(found) if found is not None else dict.fromkeys(paths, ())

    def materialized_answer(self, paths: Sequence[Path], *,
                            split: bool = False) -> Mapping[Path, tuple[_contracts.Materialized, ...]] | None:
        """`materialized`, or None when the history could not be read at all
        (a busy exclusion after a short wait, an unusable batch or a read
        error). Spec 05's sync must not report a hot path `cold` because the
        question went unanswered.

        A read past the history budget answers every path empty, as
        `materialized` does -- unless `split`, when it answers
        `HISTORY_OVER_BUDGET` instead, so a caller that batched many paths
        into one read can ask again path by path rather than see them all
        cold."""
        return self._read_history(paths, strict=True, split=split)

    def _read_history(self, paths: Sequence[Path], *, strict: bool,
                      split: bool = False) -> Mapping[Path, tuple[_contracts.Materialized, ...]] | None:
        requested: dict[Path, tuple[_contracts.Materialized, ...]] = dict.fromkeys(paths, ())
        unanswered = None if strict else requested
        relatives = {path: relative for path in requested if (relative := _encoding._relative(self.root, path)) is not None}
        supplied = list(dict.fromkeys(relatives.values()))
        decoded: dict[str, list[_contracts.Materialized]] = {}
        count, size = 0, 0
        for offset in range(0, len(supplied), _contracts.SOURCE_CHUNK):
            rows = self._materialized_rows(supplied[offset:offset + _contracts.SOURCE_CHUNK], strict=strict)
            if rows is None:
                return unanswered
            count += len(rows)
            size += sum(_encoding._history_size(row) for row in rows)
            # Past the budget the history is answered as empty in both forms:
            # oversize history stays an optional cold warm-up (S4 contract).
            if rows is _contracts._OVER_BUDGET or count > _contracts.MAX_HISTORY_ROWS or size > _contracts.MAX_HISTORY_BYTES:
                if split:
                    return _contracts.HISTORY_OVER_BUDGET
                _runtime._warn_once('materialized_budget')
                return requested
            for row in rows:
                value = _encoding._decode_materialized(self.root, row)
                if value is not None:
                    decoded.setdefault(row[0], []).append(value)
                    self._remember_metadata(self._history_known, row[:3], row)
        if not self._usable():
            return unanswered
        return {path: tuple(sorted(decoded.get(relatives.get(path, ''), ()),
                                   key=lambda item: (item.kind, item.instance))) for path in requested}

    def _remember_metadata(self, known: dict, key: object, row: tuple) -> None:
        try:
            known[key] = row
        except _sqlite_support._CACHE_ERRORS:
            # The source/hash or history value was already verified. This memo
            # only avoids redundant touches; losing it cannot lose that answer.
            self._discard_staging()
            _runtime._warn_once('metadata_bookkeeping')

    def _stage_materialized(self, row: tuple) -> None:
        key = row[:3]
        previous = self._history_known.get(key)
        if (previous is not None and previous[:4] + previous[5:] == row[:4] + row[5:]
                and row[4] - previous[4] < _contracts.TOUCH_INTERVAL_S):
            return
        size = len(_encoding._canonical(list(row), _contracts.MAX_KEY_BYTES * 2) or '')
        old = self._materialized_sizes.get(key, 0)
        count = len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches)
        if (not self._usable() or (key not in self._materialized and count >= _contracts.MAX_STAGED_ROWS)
                or self._bytes - old + size > _contracts.MAX_STAGED_BYTES):
            self.refused = True
            return
        self._materialized[key] = row
        self._materialized_sizes[key] = size
        self._bytes += size - old

    def mark_materialized(self, path: Path, kind: str, *, instance: object = None,
                          built_from: str = '') -> None:
        try:
            relative, canonical = _encoding._relative(self.root, path), _encoding._instance(instance)
            row = None
            if relative is not None and canonical is not None:
                self.materialized([path])
                row = _encoding._materialized_row(relative, kind, canonical, built_from, None)
            if row is None:
                self.refused = True
            else:
                self._stage_materialized(row)
        except _sqlite_support._CACHE_ERRORS:
            self.refused = True
            self._discard_staging()
            _runtime._warn_once('materialized_admission')

    def copy_materialized(self, old: Path, new: Path, *,
                          instance_map: Callable[[str, str], object] | None = None) -> bool:
        """Stage OLD's rows for NEW. Reads OLD's history strictly: False when
        it went unanswered (a busy exclusion, a read error), so spec 05's sync
        never reports a renamed path cold because nobody could ask; True
        otherwise, including when there was nothing to copy or the copy was
        refused as oversize (an optional cold warm-up)."""
        relative = _encoding._relative(self.root, new)
        if relative is None:
            return True
        answer = self.materialized_answer([old])
        if answer is None:
            return False
        prepared: dict[tuple, tuple] = {}
        for item in answer.get(old, ()):
            # A mapper receives canonical text. Its canonical-string result
            # must not be encoded again; structured JSON results are also
            # canonicalized for the plan's object-valued hook interface.
            mapped = item.instance if instance_map is None else instance_map(item.kind, item.instance)
            canonical = mapped if isinstance(mapped, str) else _encoding._instance(mapped)
            if canonical is None or not _encoding._canonical_instance(canonical):
                _runtime._warn_once('instance')
                return True
            row = _encoding._materialized_row(relative, item.kind, canonical, '', None)
            if row is None:
                _runtime._warn_once('instance')
                return True
            # Two of OLD's rows the mapper sends to one NEW instance (a world
            # record several campaigns read, moved into one campaign) are one
            # row there: identical, so keeping the first loses nothing.
            prepared.setdefault(row[:3], row)
        sizes = {identity: len(_encoding._canonical(list(row), _contracts.MAX_KEY_BYTES * 2) or '')
                 for identity, row in prepared.items()}
        delta = sum(size - self._materialized_sizes.get(identity, 0) for identity, size in sizes.items())
        count = (len(self._rows) + len(self._sources) + len(self._artifact_touches)
                 + len(self._materialized.keys() | prepared.keys()))
        if not self._usable() or count > _contracts.MAX_STAGED_ROWS or self._bytes + delta > _contracts.MAX_STAGED_BYTES:
            return True
        # Admit the complete selector set, rather than silently collapse or
        # truncate units. Oversize history remains an optional cold warm-up.
        self._materialized.update(prepared)
        self._materialized_sizes.update(sizes)
        self._bytes += delta
        return True

    def _family_rows(self, key: _contracts.Key, inputs: Sequence[_contracts.ValidatedInput], instance: object) -> dict | None:
        canonical = _encoding._instance(instance)
        if canonical is None:
            return None
        hashes: dict[str, str] = {}
        for item in inputs:
            relative = _encoding._relative(self.root, item.path)
            if relative is None:
                return None
            digest = '' if item.content_hash == _contracts.ABSENT else item.content_hash
            # Two byte snapshots of one path cannot establish one current
            # built_from. Retain history with an explicitly unknown hash.
            hashes[relative] = digest if relative not in hashes else (
                digest if hashes[relative] == digest else '')
        rows = {}
        for path, digest in hashes.items():
            row = _encoding._materialized_row(path, key.kind, canonical, digest, key)
            if row is None:
                return None
            rows[row[:3]] = row
        return rows

    def _discard_staging(self) -> None:
        self.refused = True     # what was staged is lost: never "nothing to write"
        self._rows.clear()
        self._sources.clear()
        self._source_sizes.clear()
        self._materialized.clear()
        self._materialized_sizes.clear()
        self._artifact_touches.clear()
        self._bytes = 0

    def _store_family(self, key: _contracts.Key | None, value: object, inputs: Sequence[_contracts.ValidatedInput],
                      instance: object) -> bool:
        try:
            return self._admit_family(key, value, inputs, instance)
        except _sqlite_support._CACHE_ERRORS:
            # The caller's answer already exists. Allocation can fail after
            # one map changed, so none of this batch's partial staging may land.
            self._discard_staging()
            _runtime._warn_once('family_admission')
            return False

    def _admit_family(self, key: _contracts.Key | None, value: object, inputs: Sequence[_contracts.ValidatedInput],
                      instance: object) -> bool:
        if key is None or not self._usable(key):
            return False
        payload = _encoding._canonical(value, _contracts.MAX_PAYLOAD_BYTES)
        metadata = self._family_rows(key, inputs, instance)
        if payload is None or metadata is None:
            return False
        sizes = {identity: len(_encoding._canonical(list(row), _contracts.MAX_KEY_BYTES * 2) or '')
                 for identity, row in metadata.items()}
        old_payload = len(self._rows[key][2]) if key in self._rows else 0
        touch_size = len(key.kind) * 6 + 256 if key in self._artifact_touches else 0
        delta = len(payload) - old_payload - touch_size + sum(
            size - self._materialized_sizes.get(identity, 0) for identity, size in sizes.items())
        count = (len(self._rows.keys() | {key}) + len(self._materialized.keys() | metadata.keys())
                 + len(self._artifact_touches.keys() - {key}))
        if count > _contracts.MAX_STAGED_ROWS or self._bytes - sum(self._source_sizes.values()) + delta > _contracts.MAX_STAGED_BYTES:
            return False
        if count + len(self._sources) > _contracts.MAX_STAGED_ROWS or self._bytes + delta > _contracts.MAX_STAGED_BYTES:
            self._bytes -= sum(self._source_sizes.values())
            self._sources.clear()
            self._source_sizes.clear()
        # Prepare the whole family before mutating any staging map. Subsequent
        # flush publishes all rows in one transaction, or none on failure.
        self._artifact_touches.pop(key, None)
        self._rows[key] = (key.kind, key.digest, payload, _encoding._check(key, payload), int(time.time()))
        self._materialized.update(metadata)
        self._materialized_sizes.update(sizes)
        self._bytes += delta
        readstats.bump(f"compiled.store.{key.kind}")
        return True

    def _refresh_family(self, key: _contracts.Key, inputs: Sequence[_contracts.ValidatedInput], instance: object) -> None:
        try:
            self._refresh_family_rows(key, inputs, instance)
        except _sqlite_support._CACHE_ERRORS:
            self._discard_staging()
            _runtime._warn_once('family_refresh')

    def _refresh_family_rows(self, key: _contracts.Key, inputs: Sequence[_contracts.ValidatedInput], instance: object) -> None:
        rows = self._family_rows(key, inputs, instance)
        if rows is None:
            return
        additions = {identity: row for identity, row in rows.items()
                     if identity not in self._history_known
                     or self._history_known[identity][:4] + self._history_known[identity][5:] != row[:4] + row[5:]
                     or row[4] - self._history_known[identity][4] >= _contracts.TOUCH_INTERVAL_S}
        size = sum(len(_encoding._canonical(list(row), _contracts.MAX_KEY_BYTES * 2) or '') for row in additions.values())
        count = len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches)
        if count + len(additions) > _contracts.MAX_STAGED_ROWS or self._bytes + size > _contracts.MAX_STAGED_BYTES:
            return
        for row in additions.values():
            self._stage_materialized(row)

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

    def _stage_source(self, path: str, stamp: statcache.Stamp, digest: str) -> None:
        try:
            self._admit_source(path, stamp, digest)
        except _sqlite_support._CACHE_ERRORS:
            # Bytes and SHA have already succeeded. Neither insert may leave
            # a partial source family or prevent the caller using its snapshot.
            self._discard_staging()
            _runtime._warn_once('source_admission')

    def _admit_source(self, path: str, stamp: statcache.Stamp, digest: str) -> None:
        if not self._usable():
            return
        check = _encoding._source_check(path, stamp, digest)
        row = (path, *stamp[1:4], str(stamp[4]), digest, check, int(time.time()))
        previous = self._source_known.get(path)
        if (previous is not None and type(previous[7]) is int and previous[:7] == row[:7]
                and row[7] - previous[7] < _contracts.TOUCH_INTERVAL_S):
            return
        if check is None:
            return
        size = len(path) * 6 + 256
        old_size = self._source_sizes.get(path, 0)
        if ((path not in self._sources and len(self._sources) + len(self._rows) + len(self._materialized) + len(self._artifact_touches) >= _contracts.MAX_STAGED_ROWS)
                or self._bytes - old_size + size > _contracts.MAX_STAGED_BYTES):
            return
        self._sources[path] = row
        self._source_sizes[path] = size
        self._bytes += size - old_size

    def put(self, key: _contracts.Key | None, value: object) -> None:
        if key is None or not self._usable(key):
            self.refused = self.refused or key is not None
            return
        payload = _encoding._canonical(value, _contracts.MAX_PAYLOAD_BYTES)
        if payload is None:
            self.refused = True
            _runtime._warn_once("payload")
            return
        old = self._rows.get(key)
        old_size = len(old[2]) if old is not None else 0
        # Bound request memory and its single transaction. Excess values
        # remain available to the caller without occupying optional storage.
        if ((key not in self._rows
             and len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches) >= _contracts.MAX_STAGED_ROWS)
                or self._bytes - old_size + len(payload) > _contracts.MAX_STAGED_BYTES):
            self.refused = True
            return
        try:
            check = _encoding._check(key, payload)
            self._rows[key] = (key.kind, key.digest, payload, check, int(time.time()))
            self._bytes += len(payload) - old_size
            readstats.bump(f"compiled.store.{key.kind}")
        except _sqlite_support._CACHE_ERRORS:
            _runtime._warn_once("payload")

    def flush(self, *, keep: Callable[[], bool] | None = None) -> None:
        """Publish staged work only while the caller's optional predicate holds.

        Cancellation is separate from lifecycle invalidation: refuse before a
        transaction, or roll back before commit, without feeding it into the
        post-commit lifecycle purge. Recheck after the writer exclusion wait
        (09-S4 I1); a commit already executing remains an atomic operation.
        """
        rows, self._rows = self._rows, {}
        sources, self._sources = self._sources, {}
        metadata, self._materialized = self._materialized, {}
        touches, self._artifact_touches = self._artifact_touches, {}
        self._source_sizes.clear()
        self._materialized_sizes.clear()
        self._bytes = 0
        if not rows and not sources and not metadata and not touches:
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
            if (keep is not None and not keep()) or not self._usable(strict=True):
                return
            with _storage._database(self.root, expected_generation=self._generation) as session:
                if (session is None or not self._session_current(session, strict=True)
                        or (keep is not None and not keep())):
                    return
                session.conn.execute("BEGIN IMMEDIATE")
                if not self._session_current(session, strict=True):
                    session.conn.rollback()
                    return
                session.conn.executemany(
                    'INSERT INTO artifacts(kind,key_digest,payload,"check",last_used) VALUES (?,?,?,?,?) '
                    'ON CONFLICT(kind,key_digest) DO UPDATE SET payload=excluded.payload,'
                    '"check"=excluded."check",last_used=excluded.last_used', rows.values())
                session.conn.executemany(
                    'INSERT INTO sources(path,mtime_ns,ctime_ns,size,ino,content_hash,"check",last_used) '
                    'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET '
                    'mtime_ns=excluded.mtime_ns,ctime_ns=excluded.ctime_ns,size=excluded.size,'
                    'ino=excluded.ino,content_hash=excluded.content_hash,"check"=excluded."check",'
                    'last_used=excluded.last_used', sources.values())
                # Associations describe an already bounded artifact family.
                # Touches never enumerate paths or change a reader's answer.
                session.conn.executemany(
                    'UPDATE artifacts SET last_used=? WHERE kind=? AND key_digest=? AND last_used<?',
                    ((used, key.kind, key.digest, used - _contracts.TOUCH_INTERVAL_S + 1) for key, used in touches.items()))
                session.conn.executemany(
                    'UPDATE materialized SET last_used=? WHERE artifact_kind=? AND artifact_key=? AND last_used<?',
                    ((used, key.kind, key.digest, used - _contracts.TOUCH_INTERVAL_S + 1) for key, used in touches.items()))
                session.conn.executemany(
                    'INSERT INTO materialized(path,kind,instance,built_from,last_used,"check",artifact_kind,artifact_key) '
                    'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path,kind,instance) DO UPDATE SET '
                    'built_from=excluded.built_from,last_used=excluded.last_used,"check"=excluded."check",'
                    'artifact_kind=excluded.artifact_kind,artifact_key=excluded.artifact_key', metadata.values())
                if (not self._session_current(session, strict=True)
                        or (keep is not None and not keep())):
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
        except _sqlite_support._CACHE_ERRORS as error:
            if session is not None:
                _storage._recover(self.root, session.path, error)
            _runtime._warn_once("flush")
        finally:
            _runtime._state.lock.release()
