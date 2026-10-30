"""Strict JSON reading and deterministic writing for human-editable records.

Reading rejects anything a careless or hostile editor could make ambiguous:
a BOM, invalid UTF-8, duplicate keys, non-finite numbers, oversized integers,
lone surrogates and runaway nesting. Every such failure is a ``JsonError``, so
one bad file can be reported instead of taking a whole load down. Writing is
sorted, indented and refuses values it could not read back.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from types import MappingProxyType

from .errors import InvalidEnvelope

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_INT_DIGITS = 4300
#: Deeper JSON is refused when written and when read, so whatever is written reads back.
MAX_DEPTH = 100
_INT_LIMIT = 10 ** MAX_INT_DIGITS


class JsonError(ValueError):
    """A file is not strict JSON of the kind the store reads."""


def etag(data: bytes) -> str:
    """The content revision of exact file bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def is_etag(value: object) -> bool:
    return (type(value) is str and len(value) == 71 and value.startswith("sha256:")
            and all(char in "0123456789abcdef" for char in value[7:]))


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise JsonError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> object:
    raise JsonError(f"{name} is not allowed")


def _parse_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_INT_DIGITS:
        raise JsonError("integer has too many digits")
    try:
        return int(text)
    except ValueError as error:  # a lower process-wide digit limit
        raise JsonError(str(error)) from None


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise JsonError(f"number {text[:24]!r} is out of range")
    return value


def _walk(value: object):
    """Every value and key below ``value``, iteratively (no recursion limit)."""
    stack = [value]
    while stack:
        item = stack.pop()
        yield item
        if isinstance(item, Mapping):
            for key, child in item.items():
                yield key
                stack.append(child)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def depth_exceeds(value: object, limit: int = MAX_DEPTH) -> bool:
    """Whether containers nest more than ``limit`` deep (iterative: no recursion limit)."""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, Mapping):
            children = item.values()
        elif isinstance(item, (list, tuple)):
            children = item
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_strict(data: bytes) -> dict[str, object]:
    """A top-level JSON object, frozen; raises ``JsonError`` for anything else."""
    if len(data) > MAX_FILE_BYTES:
        raise JsonError(f"file is larger than {MAX_FILE_BYTES} bytes")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as error:
        raise JsonError(f"not UTF-8: {error.reason}") from None
    if text.startswith("﻿"):
        raise JsonError("starts with a byte-order mark")
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant,
                           parse_int=_parse_int, parse_float=_parse_float)
        if not isinstance(value, dict):
            raise JsonError("top level is not an object")
        if depth_exceeds(value):
            raise JsonError(f"nested more than {MAX_DEPTH} levels deep")
        for item in _walk(value):
            if isinstance(item, str) and not _encodable(item):
                raise JsonError("contains a lone surrogate")
        return freeze(value)  # type: ignore[return-value]
    except JsonError:
        raise
    except (ValueError, RecursionError, TypeError, MemoryError) as error:
        raise JsonError(f"invalid JSON: {type(error).__name__}: {str(error)[:120]}") from None


def freeze(value: object) -> object:
    """Read-only JSON: objects become mapping proxies, arrays become tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(child) for child in value)
    return value


def thaw(value: object) -> object:
    """Plain ``dict``/``list`` JSON for the encoder."""
    if isinstance(value, Mapping):
        return {key: thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(child) for child in value]
    return value


def check_writable(value: object) -> None:
    """Refuse anything that would not read back identically."""
    if depth_exceeds(value):  # the same limit reading applies
        raise InvalidEnvelope(f"nested more than {MAX_DEPTH} levels deep")
    for item in _walk(value):
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if abs(item) >= _INT_LIMIT:
                raise InvalidEnvelope("integer has too many digits")
        elif type(item) is float:
            if not math.isfinite(item):
                raise InvalidEnvelope("non-finite numbers cannot be written")
        elif type(item) is str:
            if not _encodable(item):
                raise InvalidEnvelope("text contains a lone surrogate")
        elif isinstance(item, Mapping):
            if not all(type(key) is str for key in item):
                raise InvalidEnvelope("object keys must be text")
        elif not isinstance(item, (list, tuple)):
            raise InvalidEnvelope(f"{type(item).__name__} is not a JSON value")


def render(document: Mapping[str, object]) -> bytes:
    """Deterministic UTF-8 bytes: sorted keys, two-space indent, trailing newline."""
    try:
        check_writable(document)
        text = json.dumps(thaw(document), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    except InvalidEnvelope:
        raise
    except (ValueError, TypeError, RecursionError) as error:
        raise InvalidEnvelope(f"cannot be written as JSON: {error}") from None
    return (text + "\n").encode("utf-8")
