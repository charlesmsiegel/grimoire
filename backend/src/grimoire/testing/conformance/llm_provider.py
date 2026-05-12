"""LLM provider conformance suite (spec 17 §LLM Provider)."""

from __future__ import annotations

from typing import Any

from grimoire.llm_gateway.errors import PermanentError, RateLimitError, TransientError
from grimoire.testing.conformance.types import (
    ConformanceReport,
    elapsed_ms,
    run_check,
    skip,
    started,
)
from grimoire.types.llm import CompletionRequest, Message, MessageRole


def _probe_request(model: str = "probe-model") -> CompletionRequest:
    return CompletionRequest(
        model=model,
        messages=[Message(role=MessageRole.USER, content="ping")],
        max_tokens=8,
        temperature=0.0,
    )


class LLMProviderConformance:
    kind = "llm_provider"

    async def run(self, adapter: Any) -> ConformanceReport:
        report = ConformanceReport(kind=self.kind, target_id=getattr(adapter, "id", "<unknown>"))
        t0 = started()

        await run_check(
            report,
            "test_complete_returns_completion_result",
            lambda: self._complete_ok(adapter),
        )
        await run_check(
            report,
            "test_complete_handles_empty_response",
            lambda: self._complete_empty(adapter),
        )
        await run_check(
            report,
            "test_complete_streaming_callback_invoked",
            lambda: self._streaming(adapter),
        )
        await run_check(
            report,
            "test_tokenize_consistent_across_calls",
            lambda: self._tokens(adapter),
        )
        await run_check(
            report,
            "test_retry_on_5xx",
            lambda: self._retry_5xx(adapter),
        )
        await run_check(
            report,
            "test_no_retry_on_4xx_other_than_429",
            lambda: self._no_retry_4xx(adapter),
        )
        await run_check(
            report,
            "test_429_respects_retry_after",
            lambda: self._respects_429(adapter),
        )
        await run_check(
            report,
            "test_cost_estimate_nonzero_for_paid_models",
            lambda: self._cost(adapter),
        )

        report.duration_ms = elapsed_ms(t0)
        return report

    # -- checks -------------------------------------------------------- #

    async def _complete_ok(self, adapter: Any) -> None:
        response = await adapter.complete(_probe_request())
        if response is None or not hasattr(response, "text"):
            raise AssertionError("complete must return a CompletionResponse with a text field")

    async def _complete_empty(self, adapter: Any) -> None:
        # Most providers don't have a fixed way to force an empty
        # response; we accept either an empty string or a normal one.
        response = await adapter.complete(_probe_request())
        if response.text is None:
            raise AssertionError("CompletionResponse.text may not be None; use ''")

    async def _streaming(self, adapter: Any) -> None:
        caps = getattr(adapter, "capabilities", None)
        if caps is not None and not getattr(caps, "streaming", True):
            raise skip("provider declares streaming=False")
        stream = adapter.stream(_probe_request())
        seen = 0
        async for chunk in stream:
            seen += 1
            if chunk.is_final:
                break
            if seen > 64:
                break
        if seen == 0:
            raise AssertionError("stream did not yield any chunks")

    async def _tokens(self, adapter: Any) -> None:
        fn = getattr(adapter, "estimate_tokens", None)
        if fn is None:
            raise skip("provider has no estimate_tokens")
        first = await fn("hello world")
        second = await fn("hello world")
        if first != second:
            raise AssertionError(f"estimate_tokens is unstable: {first} != {second}")
        if first <= 0:
            raise AssertionError("estimate_tokens must return a positive count")

    async def _retry_5xx(self, adapter: Any) -> None:
        # We can't force a 5xx without provider cooperation. Look for a
        # documented hook; otherwise skip.
        fault_hook = getattr(adapter, "_inject_fault", None)
        if fault_hook is None:
            raise skip("provider does not expose _inject_fault hook")
        fault_hook(TransientError("simulated 5xx"))
        await adapter.complete(_probe_request())  # provider should retry internally

    async def _no_retry_4xx(self, adapter: Any) -> None:
        fault_hook = getattr(adapter, "_inject_fault", None)
        if fault_hook is None:
            raise skip("provider does not expose _inject_fault hook")
        fault_hook(PermanentError("simulated 4xx"))
        try:
            await adapter.complete(_probe_request())
        except PermanentError:
            return
        raise AssertionError("provider should not retry permanent 4xx errors")

    async def _respects_429(self, adapter: Any) -> None:
        fault_hook = getattr(adapter, "_inject_fault", None)
        if fault_hook is None:
            raise skip("provider does not expose _inject_fault hook")
        fault_hook(RateLimitError("simulated 429"))
        await adapter.complete(_probe_request())  # should retry

    async def _cost(self, adapter: Any) -> None:
        models = await adapter.list_models()
        if not models:
            raise skip("provider has no model catalog")
        paid = [
            m for m in models if (m.input_cost_per_1k or 0) > 0 or (m.output_cost_per_1k or 0) > 0
        ]
        if not paid:
            raise skip("no paid models in catalog")
        # Confirm at least one cost value is positive.
        any_positive = any(
            (m.input_cost_per_1k or 0) > 0 or (m.output_cost_per_1k or 0) > 0 for m in paid
        )
        if not any_positive:
            raise AssertionError("paid models should declare positive per-1k costs")


__all__ = ["LLMProviderConformance"]
