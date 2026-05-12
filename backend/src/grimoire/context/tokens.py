"""Token estimation helpers used by the Context Builder.

Production callers should hand in :class:`LLMGatewayService` which forwards
to the active provider's tokenizer (anthropic, llama-cpp). When no gateway
is available — tests, offline mode — we fall back to the spec-05 ``len(s) //
chars_per_token`` approximation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

TokenEstimator = Callable[[str], Awaitable[int]]


def cheap_estimator(chars_per_token: int = 4) -> TokenEstimator:
    """Return an async estimator that approximates token count from string length."""

    async def _estimate(text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // chars_per_token)

    return _estimate


async def estimate_tokens(text: str, estimator: TokenEstimator | None) -> int:
    if estimator is None:
        return max(1, len(text) // 4) if text else 0
    return await estimator(text)


__all__ = ["TokenEstimator", "cheap_estimator", "estimate_tokens"]
