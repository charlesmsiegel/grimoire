"""Record / replay LLM gateway (spec 17 §LLM record/replay).

Three modes:

* ``"record"`` — delegate the call to a real gateway and write the
  response to a JSON fixture keyed by the hash of
  ``(messages, params, model)``.
* ``"replay"`` — read the response from the fixture file. Missing
  fixtures raise :class:`FixtureMissingError` so tests fail fast
  instead of silently hitting the real API.
* ``"passthrough"`` — just delegate. Useful while iterating without
  re-recording.

Fixtures live under ``<fixture_dir>/llm/by_hash/<hash>.json`` so the
checked-in shape matches the spec.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path
from typing import Any

from grimoire.testing.anonymizer import Anonymizer
from grimoire.types.common import CampaignId
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)
from grimoire.util import now_iso


class ReplayMode(StrEnum):
    RECORD = "record"
    REPLAY = "replay"
    PASSTHROUGH = "passthrough"


class FixtureMissingError(LookupError):
    """Raised in replay mode when no fixture matches the request hash."""

    def __init__(self, fixture_path: Path, request_hash: str) -> None:
        super().__init__(
            f"no LLM fixture for hash {request_hash!r} at {fixture_path}; "
            f"re-record in record mode or check the request inputs."
        )
        self.fixture_path = fixture_path
        self.request_hash = request_hash


def request_hash(request: CompletionRequest) -> str:
    """Stable hash over the request shape that affects the response."""
    payload: dict[str, Any] = {
        "model": request.model,
        "system": request.system,
        "messages": [
            {"role": m.role.value, "content": m.content, "name": m.name} for m in request.messages
        ],
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "stop_sequences": list(request.stop_sequences),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class RecordReplayLLM:
    """A gateway that wraps another gateway in record/replay/passthrough modes."""

    def __init__(
        self,
        fixture_dir: Path,
        mode: ReplayMode = ReplayMode.REPLAY,
        *,
        real_gateway: Any | None = None,
        anonymizer: Anonymizer | None = None,
    ) -> None:
        self.fixture_dir = Path(fixture_dir)
        self.mode = mode
        self._real = real_gateway
        self._by_hash = self.fixture_dir / "llm" / "by_hash"
        self.anonymizer = anonymizer
        needs_real = self.mode is ReplayMode.RECORD or self.mode is ReplayMode.PASSTHROUGH
        if needs_real and real_gateway is None:
            raise ValueError(f"{mode.value!r} mode requires real_gateway")

    # ------------------------------------------------------------------ #
    # Completion
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
        *,
        turn_id: str | None = None,
    ) -> CompletionResponse:
        rh = request_hash(request)
        if self.mode is ReplayMode.REPLAY:
            return self._load_completion(rh)
        if self.mode is ReplayMode.PASSTHROUGH:
            return await self._real.complete(task, request, campaign_id)
        # RECORD
        response = await self._real.complete(task, request, campaign_id)
        self._save_completion(rh, task, request, response)
        return response

    async def stream(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
        *,
        turn_id: str | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        if self.mode is ReplayMode.REPLAY:
            response = self._load_completion(request_hash(request))
            # Replay as a single chunk + terminator so consumers that only
            # observe end-state still work; for token-by-token replay,
            # tests should record `stream_chunks` separately.
            yield CompletionChunk(delta=response.text, is_final=False)
            yield CompletionChunk(delta="", is_final=True, usage=response.usage)
            return
        if self.mode is ReplayMode.PASSTHROUGH:
            async for chunk in self._real.stream(task, request, campaign_id):
                yield chunk
            return
        # RECORD: drain the underlying stream while collecting chunks.
        collected: list[str] = []
        usage: TokenUsage | None = None
        async for chunk in self._real.stream(task, request, campaign_id):
            collected.append(chunk.delta)
            if chunk.usage is not None:
                usage = chunk.usage
            yield chunk
        text = "".join(collected)
        response = CompletionResponse(
            text=text,
            model=request.model,
            finish_reason="stop",
            usage=usage or TokenUsage(input_tokens=1, output_tokens=max(1, len(text) // 4)),
            latency_ms=0,
        )
        self._save_completion(request_hash(request), task, request, response, chunks=collected)

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #

    async def embed(
        self,
        task: str,
        texts: list[str],
        campaign_id: CampaignId | None = None,
        *,
        turn_id: str | None = None,
    ) -> list[list[float]]:
        if self.mode is ReplayMode.REPLAY:
            return [self._load_embedding(text) for text in texts]
        if self.mode is ReplayMode.PASSTHROUGH:
            return await self._real.embed(task, texts, campaign_id)
        vectors = await self._real.embed(task, texts, campaign_id)
        for text, vec in zip(texts, vectors, strict=True):
            self._save_embedding(text, vec)
        return vectors

    # ------------------------------------------------------------------ #
    # Fixture I/O
    # ------------------------------------------------------------------ #

    def _completion_path(self, rh: str) -> Path:
        return self._by_hash / f"{rh}.json"

    def _load_completion(self, rh: str) -> CompletionResponse:
        path = self._completion_path(rh)
        if not path.is_file():
            raise FixtureMissingError(path, rh)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        response = data["response"]
        usage = response.get("usage") or {}
        return CompletionResponse(
            text=response["text"],
            model=response.get("model", data["request"].get("model", "")),
            finish_reason=response.get("finish_reason", "stop"),
            usage=TokenUsage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                total_tokens=int(usage.get("total_tokens", 0)),
            ),
            latency_ms=int(response.get("latency_ms", 0)),
        )

    def _save_completion(
        self,
        rh: str,
        task: str,
        request: CompletionRequest,
        response: CompletionResponse,
        chunks: list[str] | None = None,
    ) -> None:
        path = self._completion_path(rh)
        path.parent.mkdir(parents=True, exist_ok=True)
        anon = self.anonymizer

        def _rewrite(text: str | None) -> str | None:
            if anon is None or text is None:
                return text
            return anon.anonymize_text(text)

        payload = {
            "request": {
                "task": task,
                "model": request.model,
                "messages_hash": rh,
                "messages": [
                    {"role": m.role.value, "content": _rewrite(m.content), "name": m.name}
                    for m in request.messages
                ],
                "params": {
                    "max_tokens": request.max_tokens,
                    "temperature": request.temperature,
                    "stop_sequences": list(request.stop_sequences),
                    "system": _rewrite(request.system),
                },
            },
            "response": {
                "text": _rewrite(response.text),
                "model": response.model,
                "finish_reason": response.finish_reason,
                "usage": response.usage.model_dump(),
                "latency_ms": response.latency_ms,
                "recorded_at": now_iso(),
            },
        }
        if chunks is not None:
            payload["response"]["chunks"] = [_rewrite(c) for c in chunks]
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)

    def _embedding_dir(self) -> Path:
        return self.fixture_dir / "llm" / "embeddings"

    def _embedding_path(self, text: str) -> Path:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self._embedding_dir() / f"{h}.json"

    def _load_embedding(self, text: str) -> list[float]:
        path = self._embedding_path(text)
        if not path.is_file():
            raise FixtureMissingError(path, hashlib.sha256(text.encode("utf-8")).hexdigest())
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data["vector"])

    def _save_embedding(self, text: str, vector: list[float]) -> None:
        path = self._embedding_path(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The hash key is computed from the *original* text so replay
        # still works; only the persisted ``text`` payload (debug echo)
        # is rewritten.
        stored_text: str | None = text
        if self.anonymizer is not None:
            stored_text = self.anonymizer.anonymize_text(text)
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                {"text_sha256": path.stem, "text": stored_text, "vector": list(vector)},
                f,
                ensure_ascii=False,
                indent=2,
            )

    # ------------------------------------------------------------------ #
    # Pass-through helpers — delegate everything else to the real gateway
    # ------------------------------------------------------------------ #

    def __getattr__(self, name: str) -> Any:
        # Delegate non-core methods (list_routes, health_check, etc.)
        # to the wrapped gateway when present; otherwise raise.
        if self._real is None:
            raise AttributeError(name)
        target = getattr(self._real, name)
        if asyncio.iscoroutinefunction(target):
            return target
        return target


__all__ = ["FixtureMissingError", "RecordReplayLLM", "ReplayMode", "request_hash"]
