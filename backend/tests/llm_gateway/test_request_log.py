"""Tests for the LLM request log writer."""

from __future__ import annotations

from grimoire.llm_gateway.request_log import LLMRequestLog, request_hash
from grimoire.types.llm import CompletionRequest, Message, MessageRole, TokenUsage


def _request() -> CompletionRequest:
    return CompletionRequest(
        model="opus-4-7",
        system="be helpful",
        messages=[Message(role=MessageRole.USER, content="hi")],
        max_tokens=256,
        temperature=0.7,
    )


def test_request_hash_is_stable() -> None:
    a = request_hash(_request())
    b = request_hash(_request())
    assert a == b
    assert len(a) == 64


def test_request_hash_changes_with_content() -> None:
    base = _request()
    other = base.model_copy(update={"messages": [Message(role=MessageRole.USER, content="bye")]})
    assert request_hash(base) != request_hash(other)


async def test_record_writes_row(db) -> None:
    log = LLMRequestLog(db)
    await log.record(
        task="main",
        provider_id="anthropic",
        model="opus-4-7",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        cost_usd=0.0001,
        latency_ms=42,
        retries=1,
        fallback_used=False,
        campaign_id="camp-1",
        turn_id="turn-1",
    )
    row = await db.fetchone(
        "SELECT task, provider, model, prompt_tokens, completion_tokens, "
        "total_tokens, cost_usd, latency_ms, retries, fallback_used, campaign_id, turn_id "
        "FROM llm_requests"
    )
    assert row["task"] == "main"
    assert row["provider"] == "anthropic"
    assert row["model"] == "opus-4-7"
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5
    assert row["total_tokens"] == 15
    assert row["retries"] == 1
    assert row["fallback_used"] == 0
    assert row["campaign_id"] == "camp-1"


async def test_record_excerpt_disabled_by_default(db) -> None:
    log = LLMRequestLog(db)
    await log.record(
        task="main",
        provider_id="p",
        model="m",
        response_text="secret response body",
    )
    row = await db.fetchone("SELECT response_excerpt FROM llm_requests")
    assert row["response_excerpt"] is None


async def test_record_excerpt_when_enabled(db) -> None:
    log = LLMRequestLog(db, log_response_text=True, response_excerpt_chars=10)
    await log.record(
        task="main",
        provider_id="p",
        model="m",
        response_text="01234567890123456789",
    )
    row = await db.fetchone("SELECT response_excerpt FROM llm_requests")
    assert row["response_excerpt"] == "0123456789"


async def test_record_failure_writes_error(db) -> None:
    log = LLMRequestLog(db)
    await log.record(
        task="extractor",
        provider_id="p",
        model="m",
        retries=3,
        error="TransientError: timeout",
    )
    row = await db.fetchone("SELECT error, retries FROM llm_requests")
    assert row["error"] == "TransientError: timeout"
    assert row["retries"] == 3
