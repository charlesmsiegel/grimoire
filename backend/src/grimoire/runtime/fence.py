"""An in-process fence: a newer run for the same chat thread makes every older run stale.

A stale run delivers nothing. Claiming a key cancels the cancel tokens older runs registered,
so a stale call stops streaming at once rather than at its next check. Cross-process fencing is
030's (with a durable claim), not this one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from grimoire.providers.cancel import CancelToken


class FenceToken:
    """One run's claim on a key. ``stale`` once any newer claim on the key exists."""

    __slots__ = ("_fence", "key", "number")

    def __init__(self, fence: RunFence, key: str, number: int) -> None:
        self._fence = fence
        self.key = key
        self.number = number

    @property
    def stale(self) -> bool:
        return self._fence._latest(self.key) != self.number

    def on_stale(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Run ``callback`` when this claim goes stale (now, if it already has); returns an unwatch."""
        return self._fence._watch(self, callback)

    def __repr__(self) -> str:
        return f"FenceToken({self.key!r}, {self.number}, stale={self.stale})"


class RunFence:
    """Hands out claims per caller-chosen key (one chat thread); thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = 0
        self._latest_by_key: dict[str, int] = {}
        self._watchers: dict[str, list[tuple[int, Callable[[], None]]]] = {}

    def claim(self, key: str) -> FenceToken:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("a fence key must be nonblank text")
        with self._lock:
            self._counter += 1
            self._latest_by_key[key] = self._counter
            stale = self._watchers.pop(key, [])
            token = FenceToken(self, key, self._counter)
        for _number, callback in stale:  # outside the lock: a callback may wake a blocked read
            callback()
        return token

    def _latest(self, key: str) -> int | None:
        with self._lock:
            return self._latest_by_key.get(key)

    def _watch(self, token: FenceToken, callback: Callable[[], None]) -> Callable[[], None]:
        entry = (token.number, callback)
        with self._lock:
            current = self._latest_by_key.get(token.key) == token.number
            if current:
                self._watchers.setdefault(token.key, []).append(entry)
        if not current:
            callback()

        def unwatch() -> None:
            with self._lock:
                watchers = self._watchers.get(token.key)
                if watchers is not None and entry in watchers:
                    watchers.remove(entry)
                    if not watchers:
                        del self._watchers[token.key]
        return unwatch


def cancel_when_stale(token: FenceToken | None, cancel: CancelToken) -> Callable[[], None]:
    """Cancel ``cancel`` when ``token`` goes stale; returns the unwatch (a no-op without a token)."""
    if token is None:
        return lambda: None
    return token.on_stale(cancel.cancel)
