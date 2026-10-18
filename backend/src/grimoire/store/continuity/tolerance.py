"""Failure isolation for continuity's independent input sources.

A calendar plugin may raise anything, and a ledger may be garbled. A failed
input must cost only what it feeds, never the other pressure items, drivers or
the sweep. Keeping this boundary shared makes that tolerance the same for all
three readers; each call still chooses its own fallback.
"""

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def call(fn: Callable[..., T], fallback: T, *args: Any) -> T:
    """`fn(*args)`, or the caller's exact `fallback` when that input raises."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- user calendar plugins and garbled ledgers may raise anything; one input must cost only what it feeds
        return fallback
