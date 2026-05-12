"""Async retry helper with exponential backoff.

The gateway uses one of these per call. Each attempt's exception is
inspected: transient errors trigger a backed-off retry up to
`max_retries`; permanent errors raise immediately.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from grimoire.llm_gateway.config import RetryConfig
from grimoire.llm_gateway.errors import PermanentError, RateLimitError, TransientError

RETRIABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TransientError,
    RateLimitError,
    TimeoutError,
    asyncio.TimeoutError,
)


async def run_with_retries[T](
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryConfig,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[T, int]:
    """Run `fn`, retrying transient errors. Returns `(result, retries_used)`.

    `retries_used` is the number of *retries* (so 0 means the call
    succeeded on the first attempt).
    """
    last_error: BaseException | None = None
    delay = max(0, policy.initial_delay_ms) / 1000.0
    backoff = max(1.0, policy.backoff_factor)
    retries = 0
    while retries <= policy.max_retries:
        try:
            return await fn(), retries
        except PermanentError:
            raise
        except RETRIABLE_EXCEPTIONS as exc:
            last_error = exc
            if retries == policy.max_retries:
                break
            if delay > 0:
                await sleep(delay)
            delay *= backoff
            retries += 1
    assert last_error is not None
    raise last_error
