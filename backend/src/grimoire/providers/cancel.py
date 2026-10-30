"""A thread-safe, cooperative cancellation token for one call."""

from __future__ import annotations

import threading
from collections.abc import Callable


class CancelToken:
    """``cancel()`` may come from any thread. Transports register a hook that
    wakes a blocked read; callbacks run outside the lock, at most once each."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next = 0

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            callbacks = list(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            callback()

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; True as soon as the token is cancelled."""
        return self._event.wait(max(0.0, seconds))

    def _on_cancel(self, callback: Callable[[], None]) -> int:
        with self._lock:
            if not self._event.is_set():
                self._next += 1
                self._callbacks[self._next] = callback
                return self._next
        callback()  # already cancelled: run now, outside the lock
        return 0

    def _clear(self, handle: int) -> None:
        with self._lock:
            self._callbacks.pop(handle, None)

    def __repr__(self) -> str:
        return f"CancelToken(cancelled={self.cancelled})"
