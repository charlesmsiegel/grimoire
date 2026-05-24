"""Background worker that drains ``FileWatcher.embedding_queue`` and persists vectors.

Pulls ``EmbeddingJob`` objects from an :class:`~grimoire.watcher.watcher.EmbeddingQueue`
in batches, calls the active embedding provider via the LLM Gateway, and writes
vectors to the ``embeddings`` table through :meth:`StateStore.add_embedding`.
Emits ``embedding_progress`` events after each batch and backs off exponentially
on failure, re-queuing the failed batch so nothing is silently dropped.

The module-level helper :func:`reenqueue_missing_embeddings` scans
``library_index`` and ``campaign_content_index`` at startup for rows that have
no matching ``embeddings`` row and re-enqueues them, making interrupted runs
resilient to process restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.state_store.config import LibrarySectionConfig

if TYPE_CHECKING:
    from grimoire.state_store.store import StateStore
    from grimoire.watcher.watcher import EmbeddingQueue

logger = logging.getLogger(__name__)

_SLEEP_IDLE: float = 0.5
_BACKOFF_BASE: float = 1.0
_BACKOFF_MAX: float = 60.0


class _GatewayLike(Protocol):
    """Structural sub-type accepted by :class:`EmbeddingWorker`.

    The real ``LLMGatewayService`` satisfies this protocol; tests may supply
    a lightweight fake instead.
    """

    async def embed(
        self,
        task: str,
        texts: list[str],
        campaign_id: str | None = None,
    ) -> list[list[float]]: ...


class EmbeddingWorker:
    """Drains the embedding queue and persists vectors via the State Store.

    Construct once, call :meth:`start` inside an async lifespan, and
    :meth:`stop` on shutdown.  :meth:`drain_once` is public so tests can
    drive it directly without spinning a real asyncio task.
    """

    def __init__(
        self,
        store: StateStore,
        gateway: _GatewayLike,
        queue: EmbeddingQueue,
        bus: EventBus,
        config: LibrarySectionConfig,
        *,
        task_name: str = "library.embed",
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._queue = queue
        self._bus = bus
        self._config = config
        self._task_name = task_name
        self._task: asyncio.Task[None] | None = None
        self._total_done: int = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Start the background drain loop."""
        if self._task is not None:
            return
        self._task = asyncio.get_event_loop().create_task(self._loop(), name="embedding_worker")

    async def stop(self) -> None:
        """Cancel the background drain loop and wait for it to finish."""
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------ #
    # Core work
    # ------------------------------------------------------------------ #

    async def drain_once(self) -> int:
        """Drain up to ``batch_size`` jobs, embed, and persist.

        Returns the number of embeddings written.  On gateway failure the
        batch is re-queued and the method returns 0.
        """
        batch_size = self._config.embedding_batch_size
        all_jobs = self._queue.drain()
        if not all_jobs:
            return 0

        batch = all_jobs[:batch_size]
        remainder = all_jobs[batch_size:]
        for job in remainder:
            self._queue.enqueue(job)

        texts = [job.text for job in batch]
        try:
            vectors = await self._gateway.embed(self._task_name, texts)
        except Exception as exc:
            logger.warning(
                "embedding_worker: embed failed (%s); re-queuing %d jobs", exc, len(batch)
            )
            for job in batch:
                self._queue.enqueue(job)
            return 0

        if len(vectors) != len(batch):
            logger.error(
                "embedding_worker: expected %d vectors, got %d; re-queuing batch",
                len(batch),
                len(vectors),
            )
            for job in batch:
                self._queue.enqueue(job)
            return 0

        model_id = self._resolve_model_id()

        written = 0
        for job, vector in zip(batch, vectors, strict=True):
            try:
                await self._store.add_embedding(
                    ref=job.ref,
                    scope=job.scope,
                    source_kind=job.source_kind,
                    text=job.text,
                    vector=vector,
                    model=model_id,
                    campaign_id=job.campaign_id,
                )
                written += 1
            except Exception:
                logger.exception(
                    "embedding_worker: failed to persist embedding for ref=%s", job.ref
                )

        self._total_done += written
        await self._bus.emit(
            Event(
                type=events.EMBEDDING_PROGRESS,
                payload={
                    "pending": self._queue.pending,
                    "done_this_batch": written,
                    "total_done": self._total_done,
                },
            )
        )
        return written

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _resolve_model_id(self) -> str:
        """Read ``model_id`` from the gateway's embedding provider, if available."""
        try:
            plugins: Any = getattr(self._gateway, "_plugins", None)
            if plugins is None:
                return "unknown"
            router: Any = getattr(self._gateway, "_router", None)
            if router is None:
                return "unknown"
            route = router.resolve(self._task_name, None)
            provider = plugins.get_embedding_provider(route.provider_id)
            if provider is None:
                return "unknown"
            return str(provider.model_id)
        except Exception:
            return "unknown"

    async def _loop(self) -> None:
        backoff = _BACKOFF_BASE
        while True:
            try:
                if self._queue.pending == 0:
                    await asyncio.sleep(_SLEEP_IDLE)
                    backoff = _BACKOFF_BASE
                    continue
                written = await self.drain_once()
                if written == 0 and self._queue.pending > 0:
                    # drain_once re-queued the batch — back off before retrying.
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _BACKOFF_MAX)
                else:
                    backoff = _BACKOFF_BASE
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("embedding_worker: unexpected error in drain loop")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)


# ----------------------------------------------------------------------- #
# Restart-resilience helper
# ----------------------------------------------------------------------- #


def _name_from_frontmatter(raw_fm: str | None) -> str:
    if not raw_fm:
        return ""
    try:
        fm = json.loads(raw_fm)
        if isinstance(fm, dict):
            return str(fm.get("name") or fm.get("title") or "")
    except Exception:
        pass
    return ""


async def reenqueue_missing_embeddings(
    store: StateStore,
    queue: EmbeddingQueue,
) -> int:
    """Re-enqueue index rows that have no matching ``embeddings`` row.

    Scans ``library_index`` and ``campaign_content_index`` for rows whose
    ``ref`` does not appear in ``embeddings.ref``.  Only kinds in
    ``_EMBEDDABLE_KINDS`` are considered.

    Returns the number of jobs enqueued.
    """
    # Deferred import to avoid a circular dependency:
    # state_store.__init__ → embedding_worker → watcher → state_store.
    from grimoire.watcher.watcher import _EMBEDDABLE_KINDS, EmbeddingJob

    kinds_tuple: tuple[str, ...] = tuple(_EMBEDDABLE_KINDS)
    placeholders: str = ",".join("?" * len(kinds_tuple))

    enqueued = 0

    lib_rows = await store.db.fetchall(
        f"""
        SELECT id, kind, name, frontmatter, body
        FROM library_index
        WHERE kind IN ({placeholders})
          AND body IS NOT NULL
          AND id NOT IN (SELECT ref FROM embeddings)
        """,
        kinds_tuple,
    )

    for row in lib_rows:
        body = row["body"] or ""
        if not body.strip():
            continue
        name = row["name"] or _name_from_frontmatter(row["frontmatter"])
        text = f"{name}\n\n{body}" if name else body
        queue.enqueue(
            EmbeddingJob(
                ref=row["id"],
                scope="library",
                source_kind=row["kind"],
                text=text,
                campaign_id=None,
            )
        )
        enqueued += 1

    cc_rows = await store.db.fetchall(
        f"""
        SELECT id, campaign_id, kind, entity_subkind, frontmatter, body
        FROM campaign_content_index
        WHERE kind IN ({placeholders})
          AND body IS NOT NULL
          AND id NOT IN (SELECT ref FROM embeddings)
        """,
        kinds_tuple,
    )

    for row in cc_rows:
        body = row["body"] or ""
        if not body.strip():
            continue
        name = _name_from_frontmatter(row["frontmatter"])
        text = f"{name}\n\n{body}" if name else body
        source_kind = row["entity_subkind"] or row["kind"]
        queue.enqueue(
            EmbeddingJob(
                ref=row["id"],
                scope="campaign",
                source_kind=source_kind,
                text=text,
                campaign_id=row["campaign_id"],
            )
        )
        enqueued += 1

    if enqueued:
        logger.info("reenqueue_missing_embeddings: re-enqueued %d jobs", enqueued)
    return enqueued


__all__ = ["EmbeddingWorker", "reenqueue_missing_embeddings"]
