"""Body-compressed auto-summarizer for ``library_index`` rows.

Fills ``library_index.body_compressed`` with a short LLM-generated summary
of long body text so the Context Builder can include an entity at background
tier without consuming the full body budget.

Stored value format
-------------------
``body_compressed`` holds a JSON object::

    {"summary": "<prose summary>", "hash": "<source content_hash>", "model": "<provider/model id>"}

A row is considered up-to-date when its stored ``hash`` equals the current
``content_hash``.  A mismatch (or a missing / malformed envelope) means the
row needs (re-)summarization.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING

from grimoire.event_bus import Event, EventBus
from grimoire.state_store.config import LibrarySectionConfig
from grimoire.storage.db import Database
from grimoire.types.llm import CompletionRequest, Message, MessageRole

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

MIN_BODY_CHARS: int = 1200
TARGET_SUMMARY_CHARS: int = 400
TASK_NAME: str = "library.summarize"

_SYSTEM_PROMPT = (
    f"Compress this entity's description into ~{TARGET_SUMMARY_CHARS} characters; "
    "preserve names, relationships, and salient facts; output prose, no lead-in."
)


async def iter_rows_needing_summary(
    db: Database,
    *,
    min_chars: int,
    limit: int,
) -> list[aiosqlite.Row]:
    """Return up to *limit* rows whose body needs a (re-)summary.

    A row qualifies when:
    - ``body`` is non-null and ``len(body) >= min_chars``
    - AND (``body_compressed IS NULL``
           OR the stored envelope's ``hash`` doesn't match ``content_hash``)
    """
    rows = await db.fetchall(
        """
        SELECT id, content_hash, body, body_compressed
        FROM library_index
        WHERE body IS NOT NULL
          AND length(body) >= ?
        LIMIT ?
        """,
        (min_chars, limit * 4),  # over-fetch to account for already-current rows
    )
    result = []
    for row in rows:
        _summary, stored_hash = await parse_existing_envelope(row["body_compressed"])
        if stored_hash == row["content_hash"]:
            continue
        result.append(row)
        if len(result) >= limit:
            break
    return result


async def parse_existing_envelope(value: str | None) -> tuple[str | None, str | None]:
    """Parse a stored body_compressed envelope.

    Returns ``(summary, hash)`` from a valid envelope, or ``(None, None)``
    when the value is absent, not JSON, or missing the expected keys.
    """
    if value is None:
        return None, None
    try:
        obj = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None, None
    if not isinstance(obj, dict):
        return None, None
    summary = obj.get("summary")
    stored_hash = obj.get("hash")
    if not isinstance(summary, str) or not isinstance(stored_hash, str):
        return None, None
    return summary, stored_hash


async def summarize_row(gateway: object, body: str) -> str:
    """Call the LLM gateway and return the summary text for *body*."""
    request = CompletionRequest(
        model="",  # gateway resolves the model from the task route
        system=_SYSTEM_PROMPT,
        messages=[Message(role=MessageRole.USER, content=body)],
        max_tokens=256,
        temperature=0.2,
    )
    response = await gateway.complete(TASK_NAME, request)  # type: ignore[attr-defined]
    return response.text


async def write_envelope(
    db: Database,
    *,
    row_id: str,
    summary: str,
    content_hash: str,
    model_id: str,
) -> None:
    """Persist the JSON envelope to ``library_index.body_compressed``."""
    envelope = json.dumps({"summary": summary, "hash": content_hash, "model": model_id})
    await db.execute(
        "UPDATE library_index SET body_compressed = ? WHERE id = ?",
        (envelope, row_id),
    )


class BodySummarizer:
    """Background worker that fills ``library_index.body_compressed``.

    Pull-based: polls the database for rows needing summarization rather
    than relying on queue events, so it stays decoupled from the watcher.

    Usage::

        summarizer = BodySummarizer(db=store.db, gateway=gateway, bus=bus, config=config)
        summarizer.start()
        # … on shutdown:
        await summarizer.stop()
    """

    def __init__(
        self,
        *,
        db: Database,
        gateway: object,
        bus: EventBus | None = None,
        config: LibrarySectionConfig,
        min_chars: int = MIN_BODY_CHARS,
        batch_size: int = 10,
        idle_seconds: float = 5.0,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._bus = bus
        self._config = config
        self._min_chars = min_chars
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
        """Process one batch and return the count of rows summarized.

        Designed for direct use in tests.
        """
        rows = await iter_rows_needing_summary(
            self._db, min_chars=self._min_chars, limit=self._batch_size
        )
        if not rows:
            return 0

        processed = 0
        for row in rows:
            try:
                summary = await summarize_row(self._gateway, row["body"])
                model_id = _extract_model_id(self._gateway)
                await write_envelope(
                    self._db,
                    row_id=row["id"],
                    summary=summary,
                    content_hash=row["content_hash"],
                    model_id=model_id,
                )
                processed += 1
            except Exception:
                logger.exception(
                    "body_summarizer: failed to summarize row id=%s; will retry next pass",
                    row["id"],
                )

        if self._bus is not None and processed > 0:
            pending = await _count_pending(self._db, self._min_chars)
            await self._bus.emit(
                Event(
                    type="library_summary_progress",
                    payload={"processed": processed, "pending": pending},
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


def _extract_model_id(gateway: object) -> str:
    """Best-effort extraction of the model id from the last response.

    The gateway doesn't expose a direct attribute for the currently-routed
    model, so we fall back to a sentinel string when introspection isn't
    available.  The model id stored in the envelope is informational only.
    """
    return getattr(gateway, "_last_model_id", "unknown")


async def _count_pending(db: Database, min_chars: int) -> int:
    """Count rows that still need summarization (not up-to-date)."""
    rows = await db.fetchall(
        """
        SELECT id, content_hash, body_compressed
        FROM library_index
        WHERE body IS NOT NULL
          AND length(body) >= ?
        """,
        (min_chars,),
    )
    count = 0
    for row in rows:
        _summary, stored_hash = await parse_existing_envelope(row["body_compressed"])
        if stored_hash != row["content_hash"]:
            count += 1
    return count


__all__ = [
    "MIN_BODY_CHARS",
    "TARGET_SUMMARY_CHARS",
    "TASK_NAME",
    "BodySummarizer",
    "iter_rows_needing_summary",
    "parse_existing_envelope",
    "summarize_row",
    "write_envelope",
]
