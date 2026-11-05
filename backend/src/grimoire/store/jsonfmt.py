"""Strict JSON reading and deterministic writing for human-editable records.

Reading rejects anything a careless or hostile editor could make ambiguous:
a BOM, invalid UTF-8, duplicate keys, non-finite numbers, oversized integers,
lone surrogates and runaway nesting. Every such failure is a ``JsonError``, so
one bad file can be reported instead of taking a whole load down. Writing is
sorted, indented and refuses values it could not read back.

A number is read as a Python ``float`` (or exact ``int``), so a decimal with more digits than a
double holds, one that underflows to zero, or a huge one is served rounded. Reads accept it; a
*rewrite* that would put the rounded value back where the text held another one is refused by the
store (``ImpreciseNumber``, reason ``imprecise_number``) through `reemitted_imprecise`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from decimal import Decimal, InvalidOperation
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


def is_imprecise(text: str) -> bool:
    """Whether the JSON number ``text`` (a fraction or exponent) changes value as a ``float``:
    ``Decimal(text) != Decimal(repr(float(text)))``, including underflow to zero. ``1.50``,
    ``1e2`` and ``0.1`` are precise (``repr`` is the shortest text naming the same double)."""
    try:
        value = float(text)
        return math.isfinite(value) and Decimal(text) != Decimal(repr(value))
    except (ValueError, InvalidOperation):
        return False


class _Imprecise(float):
    """A float read from text it does not represent exactly (the text is kept)."""

    text: str


def _mark_imprecise(text: str) -> float:
    value = _parse_float(text)
    if not is_imprecise(text):
        return value
    marked = _Imprecise(value)
    marked.text = text
    return marked


def _imprecise_at(value: object, path: tuple[object, ...] = ()) -> Iterator[tuple[tuple[object, ...], float]]:
    stack: list[tuple[object, tuple[object, ...]]] = [(value, path)]
    while stack:
        item, where = stack.pop()
        if isinstance(item, _Imprecise):
            yield where, float(item)
        elif isinstance(item, dict):
            stack.extend((child, (*where, key)) for key, child in item.items())
        elif isinstance(item, list):
            stack.extend((child, (*where, index)) for index, child in enumerate(item))


def _lookup(value: object, path: tuple[object, ...]) -> object:
    for step in path:
        if isinstance(value, dict) and isinstance(step, str) and step in value:
            value = value[step]
        elif isinstance(value, list) and isinstance(step, int) and step < len(value):
            value = value[step]
        else:
            return _ABSENT
    return value


_ABSENT = object()


def reemitted_imprecise(old: bytes, new: bytes) -> tuple[str, ...]:
    """Where ``new`` re-emits, as its rounded float, a number ``old``'s text held more exactly.

    Locations are JSON pointers. A location the new document drops or sets to another value is a
    deliberate change and is not listed; identical bytes change nothing. Comparison is location by
    location, so a number moved to another location (an item inserted before it in a list) is not
    caught. Only readable JSON objects are compared: anything else is the reader's to refuse.
    """
    if old == new:
        return ()
    try:
        before = json.loads(bytes(old).decode("utf-8"), parse_float=_mark_imprecise, parse_int=_parse_int,
                            parse_constant=_reject_constant)
        found = list(_imprecise_at(before))
        if not found:
            return ()
        after = json.loads(bytes(new).decode("utf-8"), parse_int=_parse_int, parse_constant=_reject_constant)
    except (ValueError, RecursionError, TypeError, MemoryError):  # JsonError is a ValueError
        return ()
    hits = []
    for path, rounded in found:
        current = _lookup(after, path)
        if type(current) in (int, float) and current == rounded:
            hits.append("/" + "/".join(str(step).replace("~", "~0").replace("/", "~1") for step in path))
    return tuple(sorted(hits))


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
    data = (text + "\n").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise InvalidEnvelope(f"the document is larger than {MAX_FILE_BYTES} bytes, more than a reader accepts")
    return data
