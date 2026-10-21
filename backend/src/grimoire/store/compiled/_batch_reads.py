"""A request's bounded literal SQL reads: artifacts, sources and materialized history."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .. import readstats
from . import (
    _contracts,
    _encoding,
    _runtime,
    _sqlite_support,
    _storage,
)
from ._batch_staging import _Staging

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


class _Reads(_Staging):
    """Artifact lookups and advisory work history, each read under the process exclusion."""

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
