"""File watcher that triggers ``MechanicsService.rescan`` on disk changes.

Spec 06 §Discovery and loading: "Reloading: a file watcher on
``data/mechanics/`` can trigger module reload during development."

Implementation notes:

- ``watchdog.observers.Observer`` runs the underlying filesystem listener on
  its own thread; events are forwarded to the asyncio loop via
  ``loop.call_soon_threadsafe`` so the rescan happens cooperatively.
- A 500 ms debounce coalesces editor saves (which fire multiple events for
  each save in a row) into a single rescan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_SECONDS = 0.5


class MechanicsFileWatcher:
    """Watch ``mechanics.config.root`` and call ``mechanics.rescan()`` on change.

    The watcher uses a single asyncio task per pending rescan so a flurry of
    events for one save cycle results in one rescan, not five.
    """

    def __init__(
        self,
        mechanics: Any,
        *,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self._mechanics = mechanics
        self._root = Path(mechanics.config.root)
        self._debounce = debounce_seconds
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Register the observer and start the watchdog worker thread."""
        if self._observer is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._root.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        handler = _Bridge(self)
        observer.schedule(handler, str(self._root), recursive=True)
        observer.start()
        self._observer = observer

    async def stop(self) -> None:
        """Stop the observer and cancel any pending rescan."""
        observer = self._observer
        self._observer = None
        if observer is not None:
            observer.stop()
            await asyncio.get_running_loop().run_in_executor(None, observer.join, 5.0)
        if self._pending_task is not None and not self._pending_task.done():
            self._pending_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pending_task
            self._pending_task = None

    # ------------------------------------------------------------------
    # Internals — called from the watchdog bridge
    # ------------------------------------------------------------------

    def _schedule_rescan(self) -> None:
        """Coalesce repeated events into a single delayed rescan."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._reschedule_on_loop)

    def _reschedule_on_loop(self) -> None:
        # Cancel any in-flight pending rescan and start a new one — the
        # debounce window restarts on every new event.
        task = self._pending_task
        if task is not None and not task.done():
            task.cancel()
        self._pending_task = asyncio.create_task(self._debounced_rescan())

    async def _debounced_rescan(self) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            return
        try:
            await self._mechanics.rescan()
        except Exception as exc:  # pragma: no cover - logged, not propagated
            logger.warning("mechanics rescan failed during watch: %s", exc)


class _Bridge(FileSystemEventHandler):
    """Forward filesystem events from watchdog's worker thread to the watcher."""

    def __init__(self, watcher: MechanicsFileWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        # ``opened`` / ``closed`` events fire constantly on Linux when a
        # process reads a file; only the mutating events should reschedule.
        if event.event_type in {"opened", "closed"}:
            return
        self._watcher._schedule_rescan()


__all__ = ["DEFAULT_DEBOUNCE_SECONDS", "MechanicsFileWatcher"]
