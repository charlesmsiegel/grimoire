"""Tests for the retry helper."""

from __future__ import annotations

import pytest

from grimoire.llm_gateway.errors import PermanentError, RateLimitError, TransientError
from grimoire.llm_gateway.retry import resolve_retry_exceptions, run_with_retries
from grimoire.llm_gateway.settings import GatewaySettings
from grimoire.types.llm import RetryPolicy


async def test_returns_first_attempt_when_successful() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result, retries = await run_with_retries(
        fn,
        policy=RetryPolicy(max_retries=3, initial_delay_ms=0),
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
        policy=RetryPolicy(max_retries=5, initial_delay_ms=10, backoff_factor=2.0),
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
            policy=RetryPolicy(max_retries=2, initial_delay_ms=0),
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
            policy=RetryPolicy(max_retries=5, initial_delay_ms=0),
        )
    assert calls == 1


# §9 new tests ----------------------------------------------------------------


async def test_custom_retry_on_only_retries_specified_exception() -> None:
    """RetryPolicy(retry_on=["TimeoutError"]) retries on TimeoutError but not RateLimitError."""
    timeout_calls = 0
    rate_calls = 0

    async def fn_timeout() -> str:
        nonlocal timeout_calls
        timeout_calls += 1
        if timeout_calls < 3:
            raise TimeoutError("timed out")
        return "ok"

    # Only TimeoutError in retry_on → recovers after 2 retries.
    result, retries = await run_with_retries(
        fn_timeout,
        policy=RetryPolicy(max_retries=5, initial_delay_ms=0, retry_on=["TimeoutError"]),
    )
    assert result == "ok"
    assert retries == 2

    async def fn_rate() -> str:
        nonlocal rate_calls
        rate_calls += 1
        raise RateLimitError("quota exceeded")

    # RateLimitError is NOT in retry_on → propagates immediately without retry.
    with pytest.raises(RateLimitError):
        await run_with_retries(
            fn_rate,
            policy=RetryPolicy(max_retries=5, initial_delay_ms=0, retry_on=["TimeoutError"]),
        )
    # Only one call — no retry attempted.
    assert rate_calls == 1


def test_yaml_retry_on_parsed_through_settings() -> None:
    """GatewaySettings with retry_on parses correctly via to_gateway_config()."""
    s = GatewaySettings(
        retry={
            "max_retries": 2,
            "initial_delay_ms": 100,
            "backoff_factor": 1.5,
            "retry_on": ["TimeoutError"],
        }
    )
    cfg = s.to_gateway_config()

    assert cfg.retry.max_retries == 2
    assert cfg.retry.initial_delay_ms == 100
    assert cfg.retry.backoff_factor == 1.5
    assert cfg.retry.retry_on == ["TimeoutError"]

    # The resolved exception tuple should contain only TimeoutError variants.
    resolved = resolve_retry_exceptions(cfg.retry.retry_on)
    assert TimeoutError in resolved
    assert RateLimitError not in resolved
    assert TransientError not in resolved
