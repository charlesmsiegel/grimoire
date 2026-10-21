"""Admission of artifacts, source stamps and history rows into a request's bounded staging."""

from __future__ import annotations

import time
from collections.abc import Sequence

from .. import readstats, statcache
from . import (
    _contracts,
    _encoding,
    _runtime,
    _sqlite_support,
)
from ._batch_state import _BatchState


class _Staging(_BatchState):
    """Bounded admission into this request's staging maps; nothing here opens SQLite."""

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
             and self._staged_count() >= _contracts.MAX_STAGED_ROWS)
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

    def _touch_artifact(self, key: _contracts.Key) -> None:
        if key in self._rows or int(time.time()) - self._artifact_used.get(key, 0) < _contracts.TOUCH_INTERVAL_S:
            return
        size = len(key.kind) * 6 + 256
        count = self._staged_count()
        if key not in self._artifact_touches:
            if count >= _contracts.MAX_STAGED_ROWS or self._bytes + size > _contracts.MAX_STAGED_BYTES:
                return
            self._bytes += size
        self._artifact_touches[key] = int(time.time())

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
        if ((path not in self._sources and self._staged_count() >= _contracts.MAX_STAGED_ROWS)
                or self._bytes - old_size + size > _contracts.MAX_STAGED_BYTES):
            return
        self._sources[path] = row
        self._source_sizes[path] = size
        self._bytes += size - old_size

    def _stage_materialized(self, row: tuple) -> None:
        key = row[:3]
        previous = self._history_known.get(key)
        if (previous is not None and previous[:4] + previous[5:] == row[:4] + row[5:]
                and row[4] - previous[4] < _contracts.TOUCH_INTERVAL_S):
            return
        size = len(_encoding._canonical(list(row), _contracts.MAX_KEY_BYTES * 2) or '')
        old = self._materialized_sizes.get(key, 0)
        count = self._staged_count()
        if (not self._usable() or (key not in self._materialized and count >= _contracts.MAX_STAGED_ROWS)
                or self._bytes - old + size > _contracts.MAX_STAGED_BYTES):
            self.refused = True
            return
        self._materialized[key] = row
        self._materialized_sizes[key] = size
        self._bytes += size - old

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
        count = self._staged_count()
        if count + len(additions) > _contracts.MAX_STAGED_ROWS or self._bytes + size > _contracts.MAX_STAGED_BYTES:
            return
        for row in additions.values():
            self._stage_materialized(row)
