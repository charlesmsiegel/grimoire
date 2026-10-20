"""Bounded canonical derivation keys and checked row encoding."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from .. import statcache
from . import _contracts, _runtime, _sqlite_support


def _scalar(value: object, limit: int) -> bool:
    if value is None or type(value) is bool:
        return True
    if isinstance(value, str):
        return type(value) is str and len(value) <= limit
    if isinstance(value, int):
        return type(value) is int and value.bit_length() <= limit * 3
    return type(value) is float and isinstance(value, float) and math.isfinite(value)


def _json_value(value: object, limit: int) -> bool:
    # Iterator frames avoid copying a million-item list onto a work stack.
    # Depth also rejects cycles. A single encoder string chunk is bounded by
    # 12 * limit (non-BMP escapes); the complete output has the tighter limit.
    stack: list[tuple[Iterator, int]] = [(iter((value,)), 0)]
    count = 0
    while stack:
        iterator, depth = stack[-1]
        item = next(iterator, stack)
        if item is stack:
            stack.pop()
            continue
        count += 1
        if count > _contracts.MAX_JSON_ITEMS:
            return False
        if type(item) not in (dict, list):
            if not _scalar(item, limit):
                return False
            continue
        if depth >= _contracts.MAX_JSON_DEPTH:
            return False
        if isinstance(item, dict):
            if (len(item) > _contracts.MAX_JSON_ITEMS
                    or not all(type(key) is str and len(key) <= limit for key in item)):
                return False
            stack.append((iter(item.values()), depth + 1))
        elif isinstance(item, list):
            stack.append((iter(item), depth + 1))
    return True


def _canonical(value: object, limit: int) -> str | None:
    try:
        if not _json_value(value, limit):
            return None
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False)
        chunks, size = [], 0
        for chunk in encoder.iterencode(value):
            size += len(chunk)  # ensure_ascii makes characters equal bytes
            if size > limit:
                return None
            chunks.append(chunk)
        return "".join(chunks)
    except _sqlite_support._CACHE_ERRORS:
        return None


# Strings, then everything but brackets, are removed in the regex engine, so
# Python walks only the nesting. Visiting each string token in Python made a
# payload of many short strings cost more to check than to decode (S6 cost
# probe). json.loads still validates the complete syntax.
_JSON_STRING = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"')
_JSON_NOT_STRUCTURE = re.compile(r'[^][{}"]+')


def _json_depth(text: str) -> bool:
    structure = _JSON_NOT_STRUCTURE.sub('', _JSON_STRING.sub('', text))
    if '"' in structure:
        return False  # a string that never closes
    depth = 0
    for token in structure:
        if token in '[{':
            depth += 1
            if depth > _contracts.MAX_JSON_DEPTH:
                return False
        else:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _key_inputs(kind: _contracts.Kind, inputs: Sequence[tuple[str, str]]) -> list | None:
    ordered = []
    for role, digest in inputs:
        if (not isinstance(role, str) or not isinstance(digest, str)
                or len(role) > _contracts.MAX_KEY_BYTES
                or not (_contracts._DEVICE_KEY.fullmatch(digest)
                        or (digest == _contracts.ABSENT and role in kind.absent_ok_roles))):
            return None
        ordered.append([role, digest])
    return ordered


def _registered(kind: _contracts.Kind, params: Mapping[str, object], width: int) -> bool:
    return (isinstance(kind, _contracts.Kind) and _contracts.KINDS.get(kind.name) == kind
            and type(kind.version) is int and kind.version >= 1
            and set(params) == set(kind.params)
            and set(kind.path_inputs) <= set(kind.params)
            and width <= _contracts.MAX_KEY_BYTES // 64)


def _composite(kind: _contracts.Kind, params: Mapping[str, object], files: list, groups: list,
               build: str | None) -> str | None:
    # A file can literally contain a collection's canonical manifest.
    # Separate categories and boundaries rather than hashing a prefix that
    # could itself be supplied as file bytes (Spec03 S4 Codex review).
    payload = _canonical([kind.name, kind.version, build, dict(params),
                          ['files', files], ['collections', groups]], _contracts.MAX_KEY_BYTES)
    return hashlib.sha256(payload.encode("ascii")).hexdigest() if payload else None


def make_key(kind: _contracts.Kind, inputs: Sequence[tuple[str, str]], *,
             params: Mapping[str, object], collections: Sequence[tuple[str, str]] = ()) -> _contracts.Key | None:
    """Select registered derivations, including their input categories."""
    try:
        if not _registered(kind, params, len(inputs) + len(collections)):
            return None
        files = _key_inputs(kind, inputs)
        groups = _key_inputs(kind, collections)
        if files is None or groups is None or not _runtime._available():
            return None
        digest = _composite(kind, params, files, groups, _runtime._build())
        return _contracts.Key(kind.name, digest) if digest else None
    except _sqlite_support._CACHE_ERRORS:
        return None


def content_digest(kind: _contracts.Kind, *, params: Mapping[str, object]) -> str | None:
    """The digest `make_key` names for a kind keyed by its params alone (no
    file or collection input), computed whether or not the cache is usable:
    for a derived artifact whose own identity IS that composite key
    (`store/searchdocs`'s document key), so the identity and the cache row
    agree, and the identity exists with the cache off. Silent, like
    `build_fingerprint`. None when it cannot be computed."""
    try:
        if not _registered(kind, params, 0):
            return None
        return _composite(kind, params, [], [], _runtime.build_fingerprint())
    except _sqlite_support._CACHE_ERRORS:
        return None


def _check(key: _contracts.Key, payload: str) -> str:
    # Payload is already bounded. Quoting its canonical text at most doubles
    # its escapes; checksums never serialize an arbitrary caller object.
    body = json.dumps([key.kind, key.digest, payload], separators=(",", ":"),
                      sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(body.encode("ascii")).hexdigest()


def _decode(key: _contracts.Key, row: tuple) -> _contracts.Lookup:
    try:
        payload, check = row
        if (isinstance(payload, str) and isinstance(check, str)
                and _check(key, payload) == check and _json_depth(payload)):
            value = json.loads(payload)
            if _json_value(value, _contracts.MAX_PAYLOAD_BYTES):
                return _contracts.Lookup(True, value)
    except _sqlite_support._CACHE_ERRORS:
        pass
    # A damaged row remains a miss, but is degradation rather than an
    # ordinary cold lookup. One fixed warning cannot expose its contents.
    _runtime._warn_once("artifact")
    return _contracts.Lookup(False)


def _source_check(path: str, stamp: statcache.Stamp, digest: str) -> str | None:
    if (type(path) is not str or len(path) > _contracts.MAX_KEY_BYTES or not _contracts._DEVICE_KEY.fullmatch(digest)
            or any(type(n) is not int or not -(2**63) <= n < 2**63 for n in stamp[1:4])
            or stamp[3] < 0 or type(stamp[4]) is not int or stamp[4] < 0
            or len(str(stamp[4])) > 40):
        return None
    body = _canonical([path, list(stamp[1:]), digest], _contracts.MAX_KEY_BYTES)
    return hashlib.sha256(body.encode("ascii")).hexdigest() if body else None


def _decode_source(path: str, row: tuple) -> tuple[statcache.Stamp, str] | None:
    try:
        stored, mtime, ctime, size, inode, digest, check = row[:7]
        if (stored == path and isinstance(inode, str) and inode.isascii() and inode.isdecimal()
                and len(inode) <= 40 and str(int(inode)) == inode
                and isinstance(digest, str) and isinstance(check, str)):
            stamp = (path, mtime, ctime, size, int(inode))
            expected = _source_check(path, stamp, digest)
            if expected is not None and expected == check:
                return stamp, digest
    except _sqlite_support._CACHE_ERRORS:
        pass
    _runtime._warn_once("source")
    return None


def space_digest(space: str) -> str:
    """One spelling of vector space identity, including its exact NULs."""
    return hashlib.sha256(space.encode('utf-8')).hexdigest()[:16]


def _relative(root: Path, path: Path) -> str | None:
    if '..' in path.parts:
        return None
    try:
        return Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root))).as_posix()
    except (OSError, ValueError):
        return None


def _instance(instance: object) -> str | None:
    return '' if instance is None else _canonical(instance, _contracts.MAX_KEY_BYTES)


def _canonical_instance(text: str) -> bool:
    try:
        return text == '' or _canonical(json.loads(text), _contracts.MAX_KEY_BYTES) == text
    except _sqlite_support._CACHE_ERRORS:
        return False


def _materialized_check(path: str, kind: str, instance: str, built_from: str,
                        artifact_kind: str | None, artifact_key: str | None) -> str | None:
    body = _canonical([path, kind, instance, built_from, artifact_kind, artifact_key], _contracts.MAX_KEY_BYTES)
    return hashlib.sha256(body.encode('ascii')).hexdigest() if body else None


def _materialized_row(path: str, kind: str, instance: str, built_from: str,
                      key: _contracts.Key | None) -> tuple | None:
    if (not kind or not _canonical_instance(instance)
            or (built_from != '' and not _contracts._DEVICE_KEY.fullmatch(built_from))):
        return None
    artifact_kind, artifact_key = (key.kind, key.digest) if key else (None, None)
    check = _materialized_check(path, kind, instance, built_from, artifact_kind, artifact_key)
    return (path, kind, instance, built_from, int(time.time()), check, artifact_kind, artifact_key) if check else None


def _decode_materialized(root: Path, row: tuple) -> _contracts.Materialized | None:
    try:
        path, kind, instance, built_from, used, check, artifact_kind, artifact_key = row
        if (not all(isinstance(item, str) for item in (path, kind, instance, built_from, check))
                or not kind or type(used) is not int or used < 0 or not _canonical_instance(instance)
                or (built_from != '' and not _contracts._DEVICE_KEY.fullmatch(built_from))
                or (artifact_kind is None) != (artifact_key is None)):
            raise ValueError('invalid materialized field')
        if artifact_key is not None and not _contracts._DEVICE_KEY.fullmatch(artifact_key):
            raise ValueError('invalid materialized field')
        expected = _materialized_check(path, kind, instance, built_from, artifact_kind, artifact_key)
        if expected is not None and check == expected:
            return _contracts.Materialized(root / path, kind, instance, built_from, used)
    except _sqlite_support._CACHE_ERRORS:
        pass
    _runtime._warn_once('materialized')
    return None


def _collection_hash(root: Path, members: Sequence[_contracts.ValidatedInput]) -> str:
    manifest = [[_relative(root, item.path), item.content_hash] for item in members]
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(separators=(',', ':'), ensure_ascii=True).iterencode(manifest):
        digest.update(chunk.encode('ascii'))
    return digest.hexdigest()


def _history_size(row: tuple) -> int:
    return sum(len(item.encode('utf-8')) if isinstance(item, str) else 8 for item in row)


def _history_transfer(cursor: Iterator[tuple]) -> list[tuple] | None:
    # Path count does not bound selectors per path. Reject the complete
    # optional answer before retaining an unbounded family; never truncate
    # independent selectors into an apparently complete rename history.
    rows: list[tuple] = []
    size = 0
    for row in cursor:
        size += _history_size(row)
        if len(rows) >= _contracts.MAX_HISTORY_ROWS or size > _contracts.MAX_HISTORY_BYTES:
            _runtime._warn_once('materialized_budget')
            return None
        rows.append(row)
    return rows
