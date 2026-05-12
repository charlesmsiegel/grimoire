"""llama.cpp adapter for Grimoire's LLM Gateway.

Wraps `llama-cpp-python` so a local GGUF model can be routed alongside
cloud providers. The library is imported lazily — the plugin can be
discovered, listed, and even instantiated without the dependency present,
and only the first `complete`/`stream`/`estimate_tokens` call loads the
model file.

Inference is synchronous in the underlying C++ binding, so the adapter
runs `complete` on a worker thread and bridges the generator-based stream
through an asyncio queue.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    ModelInfo,
    ProviderCapabilities,
    TokenUsage,
)


def _role(role: Any) -> str:
    return role.value if hasattr(role, "value") else str(role)


class LlamaCppLLMProvider:
    id = "llamacpp"
    name = "llama.cpp"
    capabilities = ProviderCapabilities(
        streaming=True,
        tools=False,
        vision=False,
        max_context=0,
        embeddings=False,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._model_path: str | None = self.config.get("model_path")
        self._n_ctx: int = int(self.config.get("n_ctx") or 8192)
        self._n_threads: int | None = self.config.get("n_threads")
        self._n_gpu_layers: int = int(self.config.get("n_gpu_layers") or 0)
        self._chat_format: str | None = self.config.get("chat_format")
        self._seed: int | None = self.config.get("seed")
        self._model_id: str = self.config.get("model_id") or (
            Path(self._model_path).name if self._model_path else "local-gguf"
        )
        self._llama: Any = None
        self._load_lock = threading.Lock()
        # Reflect the configured context size in the published capabilities so
        # the Gateway's budget planner sees a real number even before the
        # model is loaded.
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=False,
            vision=False,
            max_context=self._n_ctx,
            embeddings=False,
        )

    # ------------------------------------------------------------------ #
    # Lazy model load
    # ------------------------------------------------------------------ #

    def _get_llama(self) -> Any:
        if self._llama is not None:
            return self._llama
        if not self._model_path:
            raise RuntimeError("llamacpp provider: model_path not configured")
        if not Path(self._model_path).exists():
            raise RuntimeError(f"llamacpp provider: model file not found at {self._model_path}")
        try:
            from llama_cpp import Llama
        except ImportError as exc:  # pragma: no cover - exercised by integration
            raise RuntimeError(
                "llama-cpp-python not installed; add it to the plugin's venv"
            ) from exc
        with self._load_lock:
            if self._llama is None:
                kwargs: dict[str, Any] = {
                    "model_path": self._model_path,
                    "n_ctx": self._n_ctx,
                    "n_gpu_layers": self._n_gpu_layers,
                    "verbose": False,
                }
                if self._n_threads is not None:
                    kwargs["n_threads"] = int(self._n_threads)
                if self._chat_format:
                    kwargs["chat_format"] = self._chat_format
                if self._seed is not None:
                    kwargs["seed"] = int(self._seed)
                self._llama = Llama(**kwargs)
        return self._llama

    # ------------------------------------------------------------------ #
    # LLMProvider protocol
    # ------------------------------------------------------------------ #

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        llama = self._get_llama()
        prompt = self._to_prompt(request)
        start = time.monotonic()
        out = await asyncio.to_thread(
            llama,
            prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            stop=list(request.stop_sequences) or None,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        choice = (out.get("choices") or [{}])[0]
        usage_d = out.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(usage_d.get("prompt_tokens", 0)),
            output_tokens=int(usage_d.get("completion_tokens", 0)),
            total_tokens=int(
                usage_d.get(
                    "total_tokens",
                    usage_d.get("prompt_tokens", 0) + usage_d.get("completion_tokens", 0),
                )
            ),
        )
        return CompletionResponse(
            text=str(choice.get("text", "")),
            model=request.model or self._model_id,
            finish_reason=str(choice.get("finish_reason") or "stop"),
            usage=usage,
            raw=dict(out) if isinstance(out, dict) else {},
            cost_estimate_usd=0.0,
            latency_ms=elapsed_ms,
        )

    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        return self._stream_impl(request)

    async def _stream_impl(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        llama = self._get_llama()
        prompt = self._to_prompt(request)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        def _produce() -> None:
            try:
                for piece in llama(
                    prompt,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    stop=list(request.stop_sequences) or None,
                    stream=True,
                ):
                    text = (piece.get("choices") or [{}])[0].get("text", "")
                    if text:
                        loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as exc:  # pragma: no cover - surfaces in tests via integration
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        threading.Thread(target=_produce, daemon=True).start()

        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield CompletionChunk(delta=str(item), is_final=False)
        yield CompletionChunk(delta="", is_final=True)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=self._model_id,
                name=self._model_id,
                context_window=self._n_ctx,
            )
        ]

    async def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._llama is None and not self._model_path:
            return max(1, len(text) // 4)
        try:
            llama = self._get_llama()
        except Exception:
            return max(1, len(text) // 4)
        try:
            tokens = await asyncio.to_thread(llama.tokenize, text.encode("utf-8"))
            return len(tokens)
        except Exception:  # pragma: no cover - defensive
            return max(1, len(text) // 4)

    async def health_check(self) -> HealthStatus:
        if not self._model_path:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="model_path not configured",
            )
        path = Path(self._model_path)
        if not path.exists():
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"model file not found at {self._model_path}",
            )
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message="llama-cpp-python not installed",
            )
        loaded = self._llama is not None
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message="model loaded" if loaded else "model_path resolved (lazy load)",
        )

    # ------------------------------------------------------------------ #
    # Prompt assembly
    # ------------------------------------------------------------------ #

    def _to_prompt(self, request: CompletionRequest) -> str:
        # A minimal chat template that works for most instruct-tuned models;
        # users who need a specific template should set `chat_format` and the
        # llama-cpp-python library handles formatting itself.
        parts: list[str] = []
        if request.system:
            parts.append(f"<|system|>\n{request.system}\n")
        for message in request.messages:
            parts.append(f"<|{_role(message.role)}|>\n{message.content}\n")
        parts.append("<|assistant|>\n")
        return "".join(parts)


__all__ = ["LlamaCppLLMProvider"]
