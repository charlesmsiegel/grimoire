"""Periodic backend health prober (§2 of imagegen remaining-design).

Walks the BackendRegistry on a fixed interval, calls
``ImageGenService.health_check(backend_id)``, which emits
``imagegen_backend_health_changed`` on level transitions. Errors are
swallowed + logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ImageGenHealthProber:
    """Periodic ``health_check`` driver for all registered backends."""

    def __init__(self, service: Any, *, interval_seconds: float = 30.0) -> None:
        self._svc = service
        self._interval = max(float(interval_seconds), 0.01)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="imagegen-health-prober")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                backends = await self._svc.list_backends()
            except Exception:
                logger.exception("health prober: list_backends failed")
                backends = []
            for info in backends:
                try:
                    await self._svc.health_check(info.id)
                except Exception:
                    logger.exception("health prober: health_check(%s) failed", info.id)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
