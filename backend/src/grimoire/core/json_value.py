"""Immutable JSON-like values for authored fields this version does not interpret.

Unknown fields must survive a round trip verbatim, yet domain values are
frozen and hashable. ``freeze`` turns plain JSON-compatible data into tuples
and key-sorted ``JsonObject`` mappings; ``thaw`` turns them back. Anything
that JSON cannot carry (non-finite floats, non-text keys, sets, bytes, ...) is
refused rather than coerced.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Union

JsonValue = Union[None, bool, int, float, str, tuple["JsonValue", ...], "JsonObject"]


class JsonValueError(ValueError):
    """A value JSON cannot represent exactly."""

    reason = "invalid_field"


@dataclass(frozen=True, slots=True, eq=False)
class JsonObject(Mapping):
    """A frozen, hashable, key-sorted text-keyed mapping of JSON values.

    Equality is JSON-type strict at every depth (``true``, ``1`` and ``1.0``
    differ), so an authored edit is never mistaken for no change.
    """

    pairs: tuple[tuple[str, JsonValue], ...] = ()

    def __post_init__(self) -> None:
        source = self.pairs
        if isinstance(source, (str, bytes)) or not isinstance(source, Iterable):
            raise JsonValueError(f"an object needs a mapping or (key, value) pairs, not {source!r}")
        items = source.pairs if isinstance(source, JsonObject) else (
            source.items() if isinstance(source, Mapping) else source)
        pairs: dict[str, JsonValue] = {}
        for item in items:
            if not isinstance(item, tuple) or len(item) != 2:
                raise JsonValueError(f"object entries must be (key, value) pairs: {item!r}")
            key, value = item
            if not isinstance(key, str):
                raise JsonValueError(f"object keys must be text: {key!r}")
            if key in pairs:
                raise JsonValueError(f"duplicate object key: {key!r}")
            pairs[str.__str__(key)] = freeze(value)
        object.__setattr__(self, "pairs", tuple(sorted(pairs.items())))

    def __getitem__(self, key: str) -> JsonValue:
        for name, value in self.pairs:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self.pairs)

    def __len__(self) -> int:
        return len(self.pairs)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        try:
            other = other if isinstance(other, JsonObject) else JsonObject(other)
        except JsonValueError:
            return False
        return _typed(self) == _typed(other)

    def __hash__(self) -> int:
        return hash(_typed(self))


def _typed(value: JsonValue) -> tuple:
    """A comparison key that keeps JSON types apart (``True``, ``1``, ``1.0``)."""
    if isinstance(value, JsonObject):
        return ("object", tuple((key, _typed(item)) for key, item in value.pairs))
    if isinstance(value, tuple):
        return ("array", tuple(_typed(item) for item in value))
    if isinstance(value, float):  # -0.0 == 0.0, but they are different JSON text
        return ("float", value, math.copysign(1.0, value))
    return (type(value).__name__, value)


def freeze(value: object) -> JsonValue:
    """The immutable form of a JSON-compatible value; already-frozen input is kept."""
    # Subclasses (an IntEnum, a str subclass) are stored as the plain JSON type.
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        return int.__int__(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonValueError(f"JSON has no non-finite numbers: {value!r}")
        return float.__float__(value)
    if isinstance(value, JsonObject):
        return value
    if isinstance(value, Mapping):
        return JsonObject(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    raise JsonValueError(f"not a JSON value: {type(value).__name__}")


def thaw(value: JsonValue) -> object:
    """Plain dict/list/scalar data equal to what ``freeze`` was given."""
    if isinstance(value, JsonObject):
        return {key: thaw(item) for key, item in value.pairs}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def freeze_object(value: object) -> JsonObject:
    """``freeze`` that must yield an object (``extras``, unknown facet data)."""
    frozen = freeze({} if value is None else value)
    if not isinstance(frozen, JsonObject):
        raise JsonValueError(f"expected a JSON object, not {type(value).__name__}")
    return frozen
