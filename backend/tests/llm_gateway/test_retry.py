"""Tests for the retry helper."""

from __future__ import annotations

import pytest

from grimoire.llm_gateway.config import RetryConfig
from grimoire.llm_gateway.errors import PermanentError, RateLimitError, TransientError
from grimoire.llm_gateway.retry import run_with_retries


async def test_returns_first_attempt_when_successful() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result, retries = await run_with_retries(
        fn,
        policy=RetryConfig(max_retries=3, initial_delay_ms=0),
    )
    assert result == "ok"
    assert retries == 0
    assert calls == 1


async def test_retries_on_transient_error() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TransientError("blip")
        return "ok"

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    result, retries = await run_with_retries(
        fn,
        policy=RetryConfig(max_retries=5, initial_delay_ms=10, backoff_factor=2.0),
        sleep=fake_sleep,
    )
    assert result == "ok"
    assert retries == 2
    # Two sleeps before two retries (one before each retry).
    assert sleeps == pytest.approx([0.01, 0.02])


async def test_raises_after_exhausting_retries() -> None:
    async def fn() -> str:
        raise RateLimitError("nope")

    with pytest.raises(RateLimitError):
        await run_with_retries(
            fn,
            policy=RetryConfig(max_retries=2, initial_delay_ms=0),
        )


async def test_permanent_error_does_not_retry() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise PermanentError("bad request")

    with pytest.raises(PermanentError):
        await run_with_retries(
            fn,
            policy=RetryConfig(max_retries=5, initial_delay_ms=0),
        )
    assert calls == 1
