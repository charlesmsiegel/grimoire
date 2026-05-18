"""Tests for EmbeddingWorker and reenqueue_missing_embeddings."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from grimoire.event_bus import Event, EventBus
from grimoire.state_store import StateStore
from grimoire.state_store.config import LibrarySectionConfig
from grimoire.state_store.embedding_worker import EmbeddingWorker, reenqueue_missing_embeddings
from grimoire.watcher.watcher import EmbeddingJob, EmbeddingQueue

# ----------------------------------------------------------------------- #
# Fake gateway
# ----------------------------------------------------------------------- #


class _FakeGateway:
    """Minimal embed-only gateway for tests.  Raises on request if ``fail=True``."""

    def __init__(self, dims: int = 3, *, fail: bool = False) -> None:
        self._dims = dims
        self._fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    async def embed(
        self, task: str, texts: list[str], campaign_id: str | None = None
    ) -> list[list[float]]:
        self.calls.append((task, texts))
        if self._fail:
            raise RuntimeError("no provider configured")
        return [[float(i)] * self._dims for i in range(len(texts))]


# ----------------------------------------------------------------------- #
# Helpers
# ----------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_worker(
    store: StateStore,
    queue: EmbeddingQueue,
    bus: EventBus,
    *,
    batch_size: int = 10,
    fail: bool = False,
    dims: int = 3,
) -> EmbeddingWorker:
    config = LibrarySectionConfig(embedding_batch_size=batch_size)
    gateway = _FakeGateway(dims=dims, fail=fail)
    return EmbeddingWorker(store, gateway, queue, bus, config)


async def _seed_library_row(
    store: StateStore,
    *,
    lib_id: str = "worlds/w1/characters/hero",
    kind: str = "library_entity",
    body: str = "A brave hero.",
    name: str = "Hero",
) -> None:
    await store.db.execute(
        """
        INSERT INTO library_index
          (id, world_id, kind, asset_id, name, path, frontmatter, body,
           file_mtime, content_hash, indexed_at, version)
        VALUES (?, 'w1', ?, 'hero', ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            lib_id,
            kind,
            name,
            f"library/{kind}/hero.md",
            json.dumps({"name": name}),
            body,
            _now_iso(),
            "hash1",
            _now_iso(),
        ),
    )


async def _seed_campaign_content_row(
    store: StateStore,
    *,
    row_id: str = "campaigns/c1/emergent/c1_hero",
    campaign_id: str = "c1",
    kind: str = "emergent",
    body: str = "Emergent hero profile.",
) -> None:
    await store.db.execute(
        """
        INSERT INTO campaign_content_index
          (id, campaign_id, kind, entity_subkind, asset_id, path,
           frontmatter, body, file_mtime, content_hash, indexed_at)
        VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            campaign_id,
            kind,
            f"campaigns/{campaign_id}/{kind}/hero.md",
            json.dumps({"title": "Hero"}),
            body,
            _now_iso(),
            "hash2",
            _now_iso(),
        ),
    )


# ----------------------------------------------------------------------- #
# EmbeddingWorker tests
# ----------------------------------------------------------------------- #


async def test_empty_queue_is_noop(store: StateStore) -> None:
    bus = EventBus()
    queue = EmbeddingQueue()
    worker = _make_worker(store, queue, bus)
    written = await worker.drain_once()
    assert written == 0


async def test_drain_once_full_batch(store: StateStore) -> None:
    bus = EventBus()
    queue = EmbeddingQueue()
    worker = _make_worker(store, queue, bus, batch_size=3)

    for i in range(3):
        queue.enqueue(
            EmbeddingJob(
                ref=f"lib/char/{i}",
                scope="library",
                source_kind="library_entity",
                text=f"text {i}",
            )
        )

    written = await worker.drain_once()
    assert written == 3
    assert queue.pending == 0


async def test_drain_once_partial_batch(store: StateStore) -> None:
    bus = EventBus()
    queue = EmbeddingQueue()
    worker = _make_worker(store, queue, bus, batch_size=10)

    for i in range(4):
        queue.enqueue(
            EmbeddingJob(
                ref=f"lib/char/{i}",
                scope="library",
                source_kind="library_entity",
                text=f"text {i}",
            )
        )

    written = await worker.drain_once()
    assert written == 4
    assert queue.pending == 0


async def test_vector_persisted_with_correct_fields(store: StateStore) -> None:
    bus = EventBus()
    queue = EmbeddingQueue()
    worker = _make_worker(store, queue, bus, batch_size=5, dims=3)

    queue.enqueue(
        EmbeddingJob(
            ref="worlds/w1/characters/winifred",
            scope="library",
            source_kind="library_entity",
            text="winifred the character",
        )
    )

    written = await worker.drain_once()
    assert written == 1

    rows = await store.db.fetchall(
        "SELECT ref, scope, source_kind, text FROM embeddings WHERE ref = ?",
        ("worlds/w1/characters/winifred",),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["ref"] == "worlds/w1/characters/winifred"
    assert row["scope"] == "library"
    assert row["source_kind"] == "library_entity"
    assert row["text"] == "winifred the character"


async def test_backoff_on_gateway_failure_jobs_requeued(store: StateStore) -> None:
    bus = EventBus()
    queue = EmbeddingQueue()
    config = LibrarySectionConfig(embedding_batch_size=5)
    gateway = _FakeGateway(fail=True)
    worker = EmbeddingWorker(store, gateway, queue, bus, config)

    queue.enqueue(
        EmbeddingJob(
            ref="lib/char/x",
            scope="library",
            source_kind="library_entity",
            text="some text",
        )
    )

    written = await worker.drain_once()
    assert written == 0
    # Job must be back in the queue.
    assert queue.pending == 1


async def test_embedding_progress_event_emitted(store: StateStore) -> None:
    bus = EventBus()
    queue = EmbeddingQueue()
    worker = _make_worker(store, queue, bus, batch_size=5)

    received: list[Event] = []
    bus.subscribe("embedding_progress", lambda e: received.append(e))

    for i in range(2):
        queue.enqueue(
            EmbeddingJob(
                ref=f"lib/char/{i}",
                scope="library",
                source_kind="library_entity",
                text=f"text {i}",
            )
        )

    await worker.drain_once()
    assert len(received) == 1
    payload = received[0].payload
    assert payload["done_this_batch"] == 2
    assert payload["total_done"] == 2
    assert "pending" in payload


async def test_batch_size_limits_drain_per_call(store: StateStore) -> None:
    bus = EventBus()
    queue = EmbeddingQueue()
    worker = _make_worker(store, queue, bus, batch_size=3)

    for i in range(7):
        queue.enqueue(
            EmbeddingJob(
                ref=f"lib/char/{i}",
                scope="library",
                source_kind="library_entity",
                text=f"text {i}",
            )
        )

    written = await worker.drain_once()
    assert written == 3
    # Remainder stays in queue.
    assert queue.pending == 4


# ----------------------------------------------------------------------- #
# reenqueue_missing_embeddings tests
# ----------------------------------------------------------------------- #


async def test_reenqueue_picks_up_library_rows_without_embeddings(store: StateStore) -> None:
    await _seed_library_row(store, lib_id="worlds/w1/characters/hero", body="A brave hero.")
    queue = EmbeddingQueue()
    count = await reenqueue_missing_embeddings(store, queue)
    assert count == 1
    jobs = queue.drain()
    assert len(jobs) == 1
    assert jobs[0].ref == "worlds/w1/characters/hero"
    assert jobs[0].scope == "library"


async def test_reenqueue_picks_up_campaign_content_rows(store: StateStore) -> None:
    await _seed_campaign_content_row(
        store,
        row_id="campaigns/c1/emergent/hero",
        campaign_id="c1",
        kind="emergent",
        body="Emergent version.",
    )
    queue = EmbeddingQueue()
    count = await reenqueue_missing_embeddings(store, queue)
    assert count == 1
    jobs = queue.drain()
    assert len(jobs) == 1
    assert jobs[0].ref == "campaigns/c1/emergent/hero"
    assert jobs[0].scope == "campaign"
    assert jobs[0].campaign_id == "c1"


async def test_reenqueue_skips_rows_that_already_have_embeddings(store: StateStore) -> None:
    await _seed_library_row(store, lib_id="worlds/w1/characters/hero", body="A brave hero.")
    await store.add_embedding(
        ref="worlds/w1/characters/hero",
        scope="library",
        source_kind="library_entity",
        text="A brave hero.",
        vector=[1.0, 0.0],
        model="test",
        campaign_id=None,
    )
    queue = EmbeddingQueue()
    count = await reenqueue_missing_embeddings(store, queue)
    assert count == 0
    assert queue.pending == 0


async def test_reenqueue_skips_non_embeddable_kinds(store: StateStore) -> None:
    # 'library_world' is not in _EMBEDDABLE_KINDS so should be skipped.
    await store.db.execute(
        """
        INSERT INTO library_index
          (id, world_id, kind, asset_id, name, path, frontmatter, body,
           file_mtime, content_hash, indexed_at, version)
        VALUES ('worlds/w1', 'w1', 'library_world', 'w1', 'World', 'library/world/w1.yaml',
                '{}', 'some body', ?, 'hash3', ?, 1)
        """,
        (_now_iso(), _now_iso()),
    )
    queue = EmbeddingQueue()
    count = await reenqueue_missing_embeddings(store, queue)
    assert count == 0


async def test_reenqueue_skips_empty_body(store: StateStore) -> None:
    await _seed_library_row(store, lib_id="worlds/w1/characters/empty", body="   ")
    queue = EmbeddingQueue()
    count = await reenqueue_missing_embeddings(store, queue)
    assert count == 0


async def test_reenqueue_mixed_library_and_campaign(store: StateStore) -> None:
    await _seed_library_row(store, lib_id="worlds/w1/characters/hero", body="Hero text.")
    await _seed_campaign_content_row(
        store,
        row_id="campaigns/c1/emergent/hero",
        campaign_id="c1",
        kind="emergent",
        body="Emergent text.",
    )
    queue = EmbeddingQueue()
    count = await reenqueue_missing_embeddings(store, queue)
    assert count == 2
    jobs = queue.drain()
    scopes = {j.scope for j in jobs}
    assert scopes == {"library", "campaign"}
