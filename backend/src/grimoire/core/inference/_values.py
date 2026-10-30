"""Shared value checks and an immutable mapping for the inference contracts."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import TypeVar

K = TypeVar("K")
V = TypeVar("V")


def count(value: object, label: str, minimum: int = 0) -> int:
    """An int of at least ``minimum``; ``bool`` is not a count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an int >= {minimum}, not {value!r}")
    return value


def identifier(value: object, label: str, error: type[ValueError] = ValueError) -> str:
    """A nonempty, non-blank string id."""
    if not isinstance(value, str) or not value.strip():
        raise error(f"{label} must be a nonempty string, not {value!r}")
    return value


def collection(values: object, label: str, error: type[ValueError] = ValueError) -> tuple:
    """Materialise an iterable of items. A bare string is one id, not a collection
    of one-letter ids, so it is refused rather than silently split."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise error(f"{label} must be a collection, not {values!r}")
    return tuple(values)


class FrozenMap(Mapping[K, V]):
    """A private, hashable copy of a mapping, so frozen values stay frozen."""

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[K, V] | Iterable[tuple[K, V]] = ()) -> None:
        self._items = dict(items)

    def __getitem__(self, key: K) -> V:
        return self._items[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(frozenset(self._items.items()))

    def __repr__(self) -> str:
        return f"FrozenMap({self._items!r})"
