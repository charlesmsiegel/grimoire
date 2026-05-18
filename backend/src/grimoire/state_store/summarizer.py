"""Body-compressed auto-summarizer for ``library_index`` rows.

Drains :class:`grimoire.watcher.SummaryQueue` (filled by ``FileWatcher``)
and writes plain-text summaries back via
:meth:`grimoire.state_store.StateStore.set_body_compressed`. The Context
Builder uses ``body_compressed`` to include an entity at background tier
without consuming the full body budget.

The producer half lives on the watcher: every real content change for a
summarizable library kind whose body exceeds the threshold enqueues a
:class:`SummaryJob`. This worker pops jobs, calls the LLM gateway, and
hands the result to ``set_body_compressed`` together with the
content-hash guard so a stale summary cannot overwrite a fresher one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from grimoire.event_bus import Event, EventBus
from grimoire.types.llm import CompletionRequest, Message, MessageRole

if TYPE_CHECKING:
    from grimoire.state_store.store import StateStore
    from grimoire.watcher.watcher import SummaryQueue

logger = logging.getLogger(__name__)

TARGET_SUMMARY_CHARS: int = 400
TASK_NAME: str = "library.summarize"

_SYSTEM_PROMPT = (
    f"Compress this entity's description into ~{TARGET_SUMMARY_CHARS} characters; "
    "preserve names, relationships, and salient facts; output prose, no lead-in."
)


async def summarize_text(gateway: object, body: str) -> str:
    """Call the LLM gateway and return a compact summary for *body*."""
    request = CompletionRequest(
        model="",  # gateway resolves the model from the task route
        system=_SYSTEM_PROMPT,
        messages=[Message(role=MessageRole.USER, content=body)],
        max_tokens=256,
        temperature=0.2,
    )
    response = await gateway.complete(TASK_NAME, request)  # type: ignore[attr-defined]
    return response.text


class BodySummarizer:
    """Background drainer for :class:`SummaryQueue`.

    Pops :class:`SummaryJob` instances, invokes the gateway with task
    ``library.summarize``, and writes via
    :meth:`StateStore.set_body_compressed` with the job's ``content_hash``
    as a guard so stale jobs are dropped silently.
    """

    def __init__(
        self,
        *,
        store: StateStore,
        gateway: object,
        queue: SummaryQueue,
        bus: EventBus | None = None,
        batch_size: int = 4,
        idle_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._queue = queue
        self._bus = bus
        self._batch_size = batch_size
        self._idle_seconds = idle_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._loop(), name="body-summarizer")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def process_once(self) -> int:
        """Drain up to ``batch_size`` jobs and return how many succeeded."""
        if self._queue.pending == 0:
            return 0
        drained = self._queue.drain()
        jobs = drained[: self._batch_size]
        # ``drain()`` empties the queue; put the overflow back so the next
        # call can pick up where this one left off.
        for leftover in drained[self._batch_size :]:
            self._queue.enqueue(leftover)
        if not jobs:
            return 0

        processed = 0
        for job in jobs:
            try:
                summary = await summarize_text(self._gateway, job.text)
                wrote = await self._store.set_body_compressed(
                    job.library_id,
                    summary,
                    expected_content_hash=job.content_hash,
                )
                if wrote:
                    processed += 1
            except Exception:
                logger.exception(
                    "body_summarizer: failed to summarize library_id=%s; dropping job",
                    job.library_id,
                )

        if self._bus is not None and processed > 0:
            await self._bus.emit(
                Event(
                    type="library_summary_progress",
                    payload={
                        "processed": processed,
                        "pending": self._queue.pending,
                    },
                )
            )

        return processed

    async def _loop(self) -> None:
        while self._running:
            try:
                count = await self.process_once()
                if count == 0:
                    await asyncio.sleep(self._idle_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("body_summarizer: unexpected error in loop; backing off")
                await asyncio.sleep(self._idle_seconds)


__all__ = [
    "TARGET_SUMMARY_CHARS",
    "TASK_NAME",
    "BodySummarizer",
    "summarize_text",
]
