"""Mock LLM gateway with per-task queues (spec 17 §Mocked LLM).

Each call to :meth:`complete` / :meth:`stream` / :meth:`embed` pops the
next queued response keyed by ``task``. If the queue is empty the call
fails loudly with :class:`QueueExhaustedError`. This makes tests very
explicit about which model interactions they expect.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from grimoire.types.common import CampaignId, HealthLevel, HealthStatus
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)


class QueueExhaustedError(AssertionError):
    """Raised when no more responses are queued for a task.

    Inheriting from :class:`AssertionError` makes the failure surface
    nicely under pytest: the test message includes the task name and
    the number of calls already served.
    """

    def __init__(self, task: str, served: int) -> None:
        super().__init__(
            f"MockLLMGateway: no queued response for task={task!r} "
            f"(already served {served} call(s)). Did you forget to "
            f"queue_response for this task?"
        )
        self.task = task
        self.served = served


@dataclass(slots=True)
class _LLMCall:
    task: str
    request: CompletionRequest
    campaign_id: CampaignId | None
    streamed: bool


@dataclass(slots=True)
class _EmbedCall:
    task: str
    texts: list[str]
    campaign_id: CampaignId | None


@dataclass(slots=True)
class _Queued:
    """A queued response. Exactly one of ``text``/``chunks``/``error`` is set.

    Structured responses (the Extractor and the drift checker ask for
    JSON) are passed as a Python object, which we serialize with
    ``json.dumps`` so the wire format matches the real gateway.
    """

    text: str | None = None
    chunks: list[str] | None = None
    error: BaseException | None = None
    structured: Any = None
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(input_tokens=1, output_tokens=1))
    finish_reason: str = "stop"


class MockLLMGateway:
    """Drop-in fake for :class:`LLMGatewayService` for tests."""

    def __init__(self) -> None:
        self._completion_queues: dict[str, deque[_Queued]] = defaultdict(deque)
        self._embedding_queues: dict[str, deque[list[list[float]]]] = defaultdict(deque)
        self._routes: dict[str, str] = {}
        self._campaign_routes: dict[CampaignId, dict[str, str]] = defaultdict(dict)
        self.llm_calls: list[_LLMCall] = []
        self.embed_calls: list[_EmbedCall] = []
        self.embedding_dim: int = 4
        self._served_counts: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ #
    # Queue management — call these from tests before invoking the gateway
    # ------------------------------------------------------------------ #

    def queue_response(
        self,
        task: str,
        response: str | dict | list,
        *,
        usage: TokenUsage | None = None,
        finish_reason: str = "stop",
    ) -> None:
        """Queue a response for ``task``.

        Strings are returned verbatim. Anything else is serialized with
        ``json.dumps`` so structured tasks (extractor, drift_check) get
        deterministic JSON that downstream consumers can parse.
        """
        if isinstance(response, str):
            text = response
            structured = None
        else:
            text = json.dumps(response, sort_keys=True)
            structured = response
        self._completion_queues[task].append(
            _Queued(
                text=text,
                structured=structured,
                usage=usage or TokenUsage(input_tokens=1, output_tokens=max(1, len(text) // 4)),
                finish_reason=finish_reason,
            )
        )

    def queue_stream(
        self,
        task: str,
        chunks: list[str],
        *,
        usage: TokenUsage | None = None,
    ) -> None:
        """Queue a streaming response built from ``chunks``."""
        self._completion_queues[task].append(
            _Queued(
                chunks=list(chunks),
                text="".join(chunks),
                usage=usage or TokenUsage(input_tokens=1, output_tokens=max(1, len(chunks))),
            )
        )

    def queue_error(self, task: str, error: BaseException) -> None:
        """Queue an exception so the next call raises it."""
        self._completion_queues[task].append(_Queued(error=error))

    def queue_embeddings(self, task: str, vectors: list[list[float]]) -> None:
        self._embedding_queues[task].append([list(v) for v in vectors])

    def remaining(self, task: str) -> int:
        return len(self._completion_queues.get(task, ()))

    def remaining_embeddings(self, task: str) -> int:
        return len(self._embedding_queues.get(task, ()))

    def assert_all_consumed(self) -> None:
        """Raise if any queue still has unread responses.

        Useful in test teardown to catch over-queueing — the opposite of
        the under-queueing case that ``QueueExhaustedError`` already
        guards.
        """
        leftover: dict[str, int] = {}
        for task, queue in self._completion_queues.items():
            if queue:
                leftover[task] = len(queue)
        for task, queue in self._embedding_queues.items():
            if queue:
                leftover[f"embed:{task}"] = len(queue)
        if leftover:
            raise AssertionError(f"MockLLMGateway: unused queued responses: {leftover}")

    # ------------------------------------------------------------------ #
    # LLMGateway protocol surface
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
    ) -> CompletionResponse:
        queued = self._pop_completion(task)
        self.llm_calls.append(
            _LLMCall(task=task, request=request, campaign_id=campaign_id, streamed=False)
        )
        if queued.error is not None:
            raise queued.error
        text = queued.text if queued.text is not None else ""
        return CompletionResponse(
            text=text,
            model=request.model or "mock",
            finish_reason=queued.finish_reason,
            usage=queued.usage,
            latency_ms=0,
        )

    async def stream(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        queued = self._pop_completion(task)
        self.llm_calls.append(
            _LLMCall(task=task, request=request, campaign_id=campaign_id, streamed=True)
        )
        if queued.error is not None:
            raise queued.error
        chunks = queued.chunks or ([queued.text or ""])
        for chunk in chunks:
            yield CompletionChunk(delta=chunk, is_final=False)
        yield CompletionChunk(delta="", is_final=True, usage=queued.usage)

    async def embed(
        self,
        task: str,
        texts: list[str],
        campaign_id: CampaignId | None = None,
    ) -> list[list[float]]:
        self.embed_calls.append(_EmbedCall(task=task, texts=list(texts), campaign_id=campaign_id))
        if not texts:
            return []
        queue = self._embedding_queues.get(task)
        if queue:
            vectors = queue.popleft()
            if len(vectors) != len(texts):
                raise AssertionError(
                    f"MockLLMGateway: queued embeddings have {len(vectors)} vectors "
                    f"but {len(texts)} texts requested for task={task!r}"
                )
            return vectors
        return [self._fake_vector(text) for text in texts]

    def _fake_vector(self, text: str) -> list[float]:
        """Deterministic embedding stand-in: byte sums mod 100, then padded."""
        base = float(sum(ord(c) for c in text) % 100)
        return [base + float(i) for i in range(self.embedding_dim)]

    def _pop_completion(self, task: str) -> _Queued:
        queue = self._completion_queues.get(task)
        if not queue:
            raise QueueExhaustedError(task, self._served_counts[task])
        self._served_counts[task] += 1
        return queue.popleft()

    # ------------------------------------------------------------------ #
    # Routing / introspection — minimal stubs to satisfy the protocol
    # ------------------------------------------------------------------ #

    async def list_llm_providers(self) -> list[Any]:
        return []

    async def list_embedding_providers(self) -> list[Any]:
        return []

    async def list_routes(self, campaign_id: CampaignId | None = None) -> dict[str, str]:
        if campaign_id is None:
            return dict(self._routes)
        merged = dict(self._routes)
        merged.update(self._campaign_routes.get(campaign_id, {}))
        return merged

    async def set_route(
        self,
        task: str,
        route: str,
        campaign_id: CampaignId | None = None,
    ) -> None:
        if campaign_id is None:
            self._routes[task] = route
        else:
            self._campaign_routes[campaign_id][task] = route

    async def estimate_tokens(self, text: str, provider_id: str | None = None) -> int:
        return max(1, len(text) // 4)

    async def estimate_cost(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
    ) -> float | None:
        return 0.0

    async def health_check(self, provider_id: str) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=provider_id, message="mock")

    async def health_check_all(self) -> dict[str, HealthStatus]:
        return {"mock": HealthStatus(level=HealthLevel.HEALTHY, target_id="mock")}


@dataclass(slots=True)
class MockEmbeddingProvider:
    """Standalone embedding provider for use with conformance tests."""

    id: str = "embed-mock"
    name: str = "mock-embeddings"
    model_id: str = "mock-embed"
    dimensions: int = 4
    seen_inputs: list[list[str]] = field(default_factory=list)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen_inputs.append(list(texts))
        return [
            [float(sum(ord(c) for c in t) % 100) + i for i in range(self.dimensions)] for t in texts
        ]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


__all__ = [
    "MockEmbeddingProvider",
    "MockLLMGateway",
    "QueueExhaustedError",
]
