"""Async retry helper with exponential backoff.

The gateway uses one of these per call. Each attempt's exception is
inspected: transient errors trigger a backed-off retry up to
`max_retries`; permanent errors raise immediately.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from grimoire.llm_gateway.errors import PermanentError, RateLimitError, TransientError
from grimoire.types.llm import RetryPolicy

logger = logging.getLogger(__name__)

# Registry of allowed exception-name → exception class(es).
# Each name maps to one or more concrete exception types so that callers
# can refer to them by the short symbolic names used in YAML config.
_EXCEPTION_REGISTRY: dict[str, tuple[type[BaseException], ...]] = {
    "TimeoutError": (TimeoutError, asyncio.TimeoutError),
    "RateLimitError": (RateLimitError,),
    "TransientError": (TransientError,),
}


def resolve_retry_exceptions(names: list[str]) -> tuple[type[BaseException], ...]:
    """Resolve a list of exception-name strings to a flat tuple of exception types.

    Unknown names log a warning and are skipped (no crash on typos).
    An empty *names* list returns an empty tuple (nothing retried).
    """
    result: list[type[BaseException]] = []
    for name in names:
        classes = _EXCEPTION_REGISTRY.get(name)
        if classes is None:
            logger.warning(
                "llm_gateway: unknown retry_on name %r — skipping (known: %s)",
                name,
                ", ".join(_EXCEPTION_REGISTRY),
            )
            continue
        for cls in classes:
            if cls not in result:
                result.append(cls)
    return tuple(result)


async def run_with_retries[T](
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[T, int]:
    """Run `fn`, retrying transient errors. Returns `(result, retries_used)`.

    `retries_used` is the number of *retries* (so 0 means the call
    succeeded on the first attempt).

    The exception classes that are considered retriable are resolved from
    ``policy.retry_on`` at call time via :func:`resolve_retry_exceptions`.
    """
    retriable = resolve_retry_exceptions(policy.retry_on)
    last_error: BaseException | None = None
    delay = max(0, policy.initial_delay_ms) / 1000.0
    backoff = max(1.0, policy.backoff_factor)
    retries = 0
    while retries <= policy.max_retries:
        try:
            return await fn(), retries
        except PermanentError:
            raise
        except BaseException as exc:
            if not retriable or not isinstance(exc, retriable):
                raise
            last_error = exc
            if retries == policy.max_retries:
                break
            if delay > 0:
                await sleep(delay)
            delay *= backoff
            retries += 1
    assert last_error is not None
    raise last_error
