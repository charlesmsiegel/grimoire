"""Tests for the body_compressed auto-summarizer (spec §5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from grimoire.event_bus import Event, EventBus
from grimoire.state_store import StateStore
from grimoire.state_store.config import LibrarySectionConfig
from grimoire.state_store.summarizer import (
    MIN_BODY_CHARS,
    BodySummarizer,
    iter_rows_needing_summary,
    parse_existing_envelope,
    write_envelope,
)
from grimoire.types.llm import CompletionResponse, TokenUsage

# ---------------------------------------------------------------------------
# Fake gateway
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _insert_row(
    store: StateStore,
    *,
    row_id: str,
    body: str,
    body_compressed: str | None = None,
    content_hash: str = "hash-abc",
) -> None:
    await store.db.execute(
        """
        INSERT INTO library_index (
            id, world_id, kind, asset_id, name, path, frontmatter, body,
            body_compressed, tags, keywords, file_mtime, content_hash, indexed_at, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            body = excluded.body,
            body_compressed = excluded.body_compressed,
            content_hash = excluded.content_hash
        """,
        (
            row_id,
            "test-world",
            "character",
            row_id,
            row_id,
            f"worlds/test-world/characters/{row_id}.md",
            "{}",
            body,
            body_compressed,
            None,
            None,
            _now_iso(),
            content_hash,
            _now_iso(),
            1,
        ),
    )


# ---------------------------------------------------------------------------
# parse_existing_envelope
# ---------------------------------------------------------------------------


async def test_parse_envelope_none() -> None:
    summary, h = await parse_existing_envelope(None)
    assert summary is None
    assert h is None


async def test_parse_envelope_invalid_json() -> None:
    summary, h = await parse_existing_envelope("not-json")
    assert summary is None
    assert h is None


async def test_parse_envelope_missing_keys() -> None:
    summary, h = await parse_existing_envelope(json.dumps({"summary": "x"}))
    assert summary is None
    assert h is None


async def test_parse_envelope_valid() -> None:
    env = json.dumps({"summary": "Summarized text.", "hash": "abc123", "model": "p/m"})
    summary, h = await parse_existing_envelope(env)
    assert summary == "Summarized text."
    assert h == "abc123"


# ---------------------------------------------------------------------------
# iter_rows_needing_summary
# ---------------------------------------------------------------------------


async def test_short_body_skipped(store: StateStore) -> None:
    await _insert_row(store, row_id="short", body="Too short.", content_hash="h1")
    rows = await iter_rows_needing_summary(store.db, min_chars=MIN_BODY_CHARS, limit=10)
    assert not any(r["id"] == "short" for r in rows)


async def test_long_body_no_compressed_returned(store: StateStore) -> None:
    long_body = "x" * MIN_BODY_CHARS
    await _insert_row(store, row_id="long", body=long_body, content_hash="h2")
    rows = await iter_rows_needing_summary(store.db, min_chars=MIN_BODY_CHARS, limit=10)
    assert any(r["id"] == "long" for r in rows)


async def test_up_to_date_row_skipped(store: StateStore) -> None:
    long_body = "x" * MIN_BODY_CHARS
    envelope = json.dumps({"summary": "ok", "hash": "h-current", "model": "p/m"})
    await _insert_row(
        store,
        row_id="current",
        body=long_body,
        content_hash="h-current",
        body_compressed=envelope,
    )
    rows = await iter_rows_needing_summary(store.db, min_chars=MIN_BODY_CHARS, limit=10)
    assert not any(r["id"] == "current" for r in rows)


async def test_stale_hash_triggers_resummary(store: StateStore) -> None:
    long_body = "x" * MIN_BODY_CHARS
    old_envelope = json.dumps({"summary": "old", "hash": "old-hash", "model": "p/m"})
    await _insert_row(
        store,
        row_id="stale",
        body=long_body,
        content_hash="new-hash",
        body_compressed=old_envelope,
    )
    rows = await iter_rows_needing_summary(store.db, min_chars=MIN_BODY_CHARS, limit=10)
    assert any(r["id"] == "stale" for r in rows)


# ---------------------------------------------------------------------------
# write_envelope + round-trip
# ---------------------------------------------------------------------------


async def test_write_envelope_round_trips(store: StateStore) -> None:
    long_body = "x" * MIN_BODY_CHARS
    await _insert_row(store, row_id="e1", body=long_body, content_hash="h-e1")
    await write_envelope(
        store.db,
        row_id="e1",
        summary="Summary text.",
        content_hash="h-e1",
        model_id="provider/model",
    )
    row = await store.db.fetchone("SELECT body_compressed FROM library_index WHERE id = ?", ("e1",))
    assert row is not None
    summary, h = await parse_existing_envelope(row["body_compressed"])
    assert summary == "Summary text."
    assert h == "h-e1"

    obj = json.loads(row["body_compressed"])
    assert obj["model"] == "provider/model"


# ---------------------------------------------------------------------------
# BodySummarizer.process_once
# ---------------------------------------------------------------------------


async def test_process_once_summarizes_long_row(store: StateStore) -> None:
    long_body = "y" * MIN_BODY_CHARS
    await _insert_row(store, row_id="p1", body=long_body, content_hash="h-p1")

    gateway = FakeGateway(text="Nice summary.")
    config = LibrarySectionConfig()
    s = BodySummarizer(db=store.db, gateway=gateway, config=config)
    count = await s.process_once()

    assert count == 1
    assert len(gateway.calls) == 1

    row = await store.db.fetchone("SELECT body_compressed FROM library_index WHERE id = ?", ("p1",))
    summary, h = await parse_existing_envelope(row["body_compressed"])
    assert summary == "Nice summary."
    assert h == "h-p1"


async def test_process_once_skips_short_row(store: StateStore) -> None:
    await _insert_row(store, row_id="short2", body="Short.", content_hash="h-s")
    gateway = FakeGateway()
    s = BodySummarizer(db=store.db, gateway=gateway, config=LibrarySectionConfig())
    count = await s.process_once()
    assert count == 0
    assert len(gateway.calls) == 0


async def test_process_once_noop_if_hash_unchanged(store: StateStore) -> None:
    long_body = "z" * MIN_BODY_CHARS
    envelope = json.dumps({"summary": "existing", "hash": "h-noop", "model": "x/y"})
    await _insert_row(
        store,
        row_id="noop",
        body=long_body,
        content_hash="h-noop",
        body_compressed=envelope,
    )
    gateway = FakeGateway()
    s = BodySummarizer(db=store.db, gateway=gateway, config=LibrarySectionConfig())
    count = await s.process_once()
    assert count == 0
    assert len(gateway.calls) == 0


async def test_process_once_resummaries_on_hash_change(store: StateStore) -> None:
    long_body = "a" * MIN_BODY_CHARS
    old_env = json.dumps({"summary": "old", "hash": "hash-old", "model": "x/y"})
    await _insert_row(
        store,
        row_id="rehash",
        body=long_body,
        content_hash="hash-new",
        body_compressed=old_env,
    )
    gateway = FakeGateway(text="Fresh summary.")
    s = BodySummarizer(db=store.db, gateway=gateway, config=LibrarySectionConfig())
    count = await s.process_once()

    assert count == 1
    row = await store.db.fetchone(
        "SELECT body_compressed FROM library_index WHERE id = ?", ("rehash",)
    )
    _, h = await parse_existing_envelope(row["body_compressed"])
    assert h == "hash-new"


async def test_llm_failure_skips_row_but_continues_batch(store: StateStore) -> None:
    long_body = "b" * MIN_BODY_CHARS
    await _insert_row(store, row_id="fail1", body=long_body, content_hash="hf1")
    await _insert_row(store, row_id="ok1", body=long_body, content_hash="ho1")

    call_count = 0

    class MixedGateway:
        async def complete(self, task, request, campaign_id=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first row fails")
            return CompletionResponse(
                text="Good summary.",
                model="p/m",
                finish_reason="stop",
                usage=TokenUsage(),
            )

    s = BodySummarizer(
        db=store.db, gateway=MixedGateway(), config=LibrarySectionConfig(), batch_size=10
    )
    count = await s.process_once()
    assert count == 1  # only the second row succeeded


async def test_library_summary_progress_event_emitted(store: StateStore) -> None:
    long_body = "c" * MIN_BODY_CHARS
    await _insert_row(store, row_id="ev1", body=long_body, content_hash="h-ev1")

    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("library_summary_progress", handler)

    gateway = FakeGateway()
    s = BodySummarizer(db=store.db, gateway=gateway, bus=bus, config=LibrarySectionConfig())
    await s.process_once()

    assert len(received) == 1
    assert received[0].type == "library_summary_progress"
    assert received[0].payload["processed"] == 1
    assert isinstance(received[0].payload["pending"], int)
