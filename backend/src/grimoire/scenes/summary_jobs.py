"""Background running-summary worker.

Decouples the LLM-driven running-summary update from ``SceneManager.append_post``
so a slow model call doesn't block the next post append (§4 of the Scene
Manager remaining-work spec).

The worker subscribes to ``running_summary_due`` events the manager emits on
its cadence boundary and runs ``update_running_summary`` per-scene FIFO. Bursts
within a single scene coalesce: if N events arrive while the previous summary
is still running, the worker collapses them into one trailing pass on the
latest scene state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from grimoire.scenes.events import RUNNING_SUMMARY_DUE, SceneEvent

logger = logging.getLogger(__name__)


class _SummaryTarget(Protocol):
    async def update_running_summary(self, scene_id: str) -> str: ...


class RunningSummaryWorker:
    """Drain ``running_summary_due`` events into per-scene FIFO jobs.

    Construction creates an idle worker; call :meth:`start` to subscribe to
    the bus and spawn the drain task. :meth:`stop` cancels the drain and
    unsubscribes.

    Coalescing rule: each scene has at most one in-flight job plus at most
    one pending "rerun" flag. Additional ``running_summary_due`` events
    while a job is running collapse onto that flag instead of growing an
    unbounded queue.
    """

    def __init__(self, manager: _SummaryTarget, bus: object) -> None:
        self._manager = manager
        self._bus = bus
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending: set[str] = set()
        self._sub: object | None = None
        self._stopped = False

    def start(self) -> None:
        if self._sub is not None:
            return
        subscribe = getattr(self._bus, "subscribe", None)
        if subscribe is None:
            return
        # Both the in-process scenes bus and the real EventBus expose
        # ``subscribe(event_type, handler)``; we don't depend on the
        # subscription handle type beyond storing it for cleanup.
        self._sub = subscribe(RUNNING_SUMMARY_DUE, self._on_event)

    async def stop(self) -> None:
        self._stopped = True
        sub = self._sub
        self._sub = None
        if sub is not None:
            unsubscribe = getattr(sub, "unsubscribe", None)
            if callable(unsubscribe):
                unsubscribe()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._pending.clear()

    async def _on_event(self, event: SceneEvent) -> None:
        if self._stopped:
            return
        scene_id = getattr(event, "scene_id", None)
        if not scene_id:
            payload = getattr(event, "payload", None) or {}
            scene_id = payload.get("scene_id") if isinstance(payload, dict) else None
        if not scene_id:
            return
        # If a job is already running for this scene, mark a rerun and bail —
        # the running task will pick up the rerun flag when it finishes.
        existing = self._tasks.get(scene_id)
        if existing is not None and not existing.done():
            self._pending.add(scene_id)
            return
        self._spawn(scene_id)

    def _spawn(self, scene_id: str) -> None:
        self._pending.discard(scene_id)
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._run(scene_id))
        self._tasks[scene_id] = task

    async def _run(self, scene_id: str) -> None:
        try:
            await self._manager.update_running_summary(scene_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception("running summary update failed for scene %s", scene_id)
        finally:
            self._tasks.pop(scene_id, None)
            if not self._stopped and scene_id in self._pending:
                # Re-run once on the latest scene state, coalescing any
                # events that piled up while the previous pass was active.
                self._spawn(scene_id)

    async def drain(self) -> None:
        """Wait for all in-flight + pending jobs to settle.

        Tests use this to keep their assertions deterministic without having
        to manually count event handoffs. Returns once no task is running
        and no scene has a pending rerun flag.
        """
        while True:
            tasks = [t for t in self._tasks.values() if not t.done()]
            if not tasks and not self._pending:
                return
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # A rerun may have spawned in the finally-block above; loop.
            await asyncio.sleep(0)


__all__ = ["RunningSummaryWorker"]
