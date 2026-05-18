"""Tests for the body_compressed auto-summarizer drainer (spec §5)."""

from __future__ import annotations

from datetime import UTC, datetime

from grimoire.event_bus import Event, EventBus
from grimoire.state_store import StateStore
from grimoire.state_store.summarizer import BodySummarizer, summarize_text
from grimoire.types.llm import CompletionResponse, TokenUsage
from grimoire.watcher.watcher import SummaryJob, SummaryQueue


class FakeGateway:
    def __init__(self, text: str = "A short summary.", model: str = "fake/model") -> None:
        self.text = text
        self.model = model
        self.calls: list[tuple] = []

    async def complete(self, task: str, request, campaign_id=None) -> CompletionResponse:
        self.calls.append((task, request))
        return CompletionResponse(
            text=self.text,
            model=self.model,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        )


class FailingGateway:
    async def complete(self, task, request, campaign_id=None):
        raise RuntimeError("LLM unavailable")


async def _insert_library_row(
    store: StateStore,
    *,
    library_id: str = "worlds/wod/characters/winifred",
    content_hash: str = "h1",
    body: str = "winifred is a vampire.",
) -> None:
    """Insert a minimal library_index row directly via the connection pool."""
    async with store.db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO library_index (
              id, kind, asset_id, name, path, frontmatter, body, body_compressed,
              tags, keywords, file_mtime, content_hash, indexed_at, version
            ) VALUES (?, ?, ?, ?, ?, '{}', ?, NULL, NULL, NULL, ?, ?, ?, 1)
            """,
            (
                library_id,
                "character",
                "winifred",
                "winifred",
                "worlds/wod/characters/winifred.md",
                body,
                datetime.now(UTC).isoformat(),
                content_hash,
                datetime.now(UTC).isoformat(),
            ),
        )


async def _fetch_body_compressed(store: StateStore, library_id: str) -> str | None:
    row = await store.db.fetchone(
        "SELECT body_compressed FROM library_index WHERE id = ?", (library_id,)
    )
    return None if row is None else row["body_compressed"]


# ---------------------------------------------------------------------------
# summarize_text
# ---------------------------------------------------------------------------


async def test_summarize_text_routes_to_library_task() -> None:
    gateway = FakeGateway(text="Compressed prose.")
    out = await summarize_text(gateway, "a long body to be compressed")
    assert out == "Compressed prose."
    task, request = gateway.calls[0]
    assert task == "library.summarize"
    assert request.messages[0].content == "a long body to be compressed"


# ---------------------------------------------------------------------------
# BodySummarizer.process_once
# ---------------------------------------------------------------------------


async def test_process_once_no_jobs(store: StateStore) -> None:
    queue = SummaryQueue()
    summarizer = BodySummarizer(store=store, gateway=FakeGateway(), queue=queue)
    assert await summarizer.process_once() == 0


async def test_process_once_drains_queue_and_persists(store: StateStore) -> None:
    await _insert_library_row(store, library_id="lib/a", content_hash="h-a")
    queue = SummaryQueue()
    queue.enqueue(
        SummaryJob(library_id="lib/a", source_kind="character", text="x" * 1500, content_hash="h-a")
    )
    summarizer = BodySummarizer(
        store=store, gateway=FakeGateway(text="A short summary."), queue=queue
    )

    processed = await summarizer.process_once()

    assert processed == 1
    assert await _fetch_body_compressed(store, "lib/a") == "A short summary."
    assert queue.pending == 0


async def test_process_once_skips_when_content_hash_drifted(store: StateStore) -> None:
    await _insert_library_row(store, library_id="lib/a", content_hash="h-new")
    queue = SummaryQueue()
    queue.enqueue(
        SummaryJob(
            library_id="lib/a", source_kind="character", text="x" * 1500, content_hash="h-stale"
        )
    )
    summarizer = BodySummarizer(store=store, gateway=FakeGateway(), queue=queue)

    processed = await summarizer.process_once()

    # set_body_compressed returns False when the guard rejects; we count
    # only successful writes.
    assert processed == 0
    assert await _fetch_body_compressed(store, "lib/a") is None


async def test_process_once_drops_failing_jobs(store: StateStore) -> None:
    await _insert_library_row(store, library_id="lib/a", content_hash="h-a")
    queue = SummaryQueue()
    queue.enqueue(
        SummaryJob(library_id="lib/a", source_kind="character", text="x" * 1500, content_hash="h-a")
    )
    summarizer = BodySummarizer(store=store, gateway=FailingGateway(), queue=queue)

    processed = await summarizer.process_once()

    assert processed == 0
    assert await _fetch_body_compressed(store, "lib/a") is None
    # Job was popped — failed jobs are dropped, not requeued forever.
    assert queue.pending == 0


async def test_batch_size_limits_drain(store: StateStore) -> None:
    for i in range(5):
        await _insert_library_row(store, library_id=f"lib/{i}", content_hash=f"h-{i}")
    queue = SummaryQueue()
    for i in range(5):
        queue.enqueue(
            SummaryJob(
                library_id=f"lib/{i}",
                source_kind="character",
                text="x" * 1500,
                content_hash=f"h-{i}",
            )
        )
    summarizer = BodySummarizer(
        store=store, gateway=FakeGateway(text="OK"), queue=queue, batch_size=2
    )

    first = await summarizer.process_once()
    assert first == 2
    assert queue.pending == 3
    second = await summarizer.process_once()
    assert second == 2
    assert queue.pending == 1


async def test_library_summary_progress_event_emitted(store: StateStore) -> None:
    await _insert_library_row(store, library_id="lib/a", content_hash="h-a")
    queue = SummaryQueue()
    queue.enqueue(
        SummaryJob(library_id="lib/a", source_kind="character", text="x" * 1500, content_hash="h-a")
    )
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe("library_summary_progress", received.append)
    summarizer = BodySummarizer(store=store, gateway=FakeGateway(), queue=queue, bus=bus)

    await summarizer.process_once()

    assert len(received) == 1
    assert received[0].payload == {"processed": 1, "pending": 0}


async def test_library_summary_progress_not_emitted_when_zero_processed(
    store: StateStore,
) -> None:
    queue = SummaryQueue()
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe("library_summary_progress", received.append)
    summarizer = BodySummarizer(store=store, gateway=FakeGateway(), queue=queue, bus=bus)

    await summarizer.process_once()

    assert received == []
