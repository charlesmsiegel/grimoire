"""OpenAI-compatible LLM Provider.

The OpenAI chat-completions wire format has become the lingua franca for
LLM inference servers — Ollama, LM Studio, vLLM, llama.cpp server,
llamafile, KoboldCpp, LocalAI, text-generation-webui, Together, Groq,
Fireworks, DeepInfra, Mistral, xAI, Perplexity, and OpenAI itself all
expose `/v1/chat/completions` with the same shape. This plugin is a
single adapter that talks to any of them.

Presets capture the host + auth conventions of the popular options;
`preset: custom` defers to `base_url` and `auth_scheme` for everything
else.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
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

# Preset metadata: (default base URL, default auth scheme, allow-empty-key flag).
# `allow_empty_key` is True for local runtimes that don't require auth at
# all — they should still load even when the user leaves `api_key` blank.
PRESETS: dict[str, tuple[str, str, bool]] = {
    "ollama": ("http://localhost:11434/v1", "none", True),
    "lmstudio": ("http://localhost:1234/v1", "none", True),
    "vllm": ("http://localhost:8000/v1", "bearer", True),
    "llamacpp-server": ("http://localhost:8080/v1", "none", True),
    "llamafile": ("http://localhost:8080/v1", "none", True),
    "koboldcpp": ("http://localhost:5001/v1", "none", True),
    "text-generation-webui": ("http://localhost:5000/v1", "none", True),
    "localai": ("http://localhost:8080/v1", "none", True),
    "openai": ("https://api.openai.com/v1", "bearer", False),
    "groq": ("https://api.groq.com/openai/v1", "bearer", False),
    "together": ("https://api.together.xyz/v1", "bearer", False),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "bearer", False),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "bearer", False),
    "mistral": ("https://api.mistral.ai/v1", "bearer", False),
    "xai": ("https://api.x.ai/v1", "bearer", False),
    "perplexity": ("https://api.perplexity.ai", "bearer", False),
    "custom": ("", "bearer", True),
}


def _role(role: Any) -> str:
    return role.value if hasattr(role, "value") else str(role)


class OpenAICompatibleLLMProvider:
    name = "OpenAI-compatible"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.config = cfg
        self._preset: str = str(cfg.get("preset") or "custom")
        default_base, default_auth, allow_empty = PRESETS.get(self._preset, PRESETS["custom"])
        self._allow_empty_key: bool = allow_empty
        self._base_url: str = str(cfg.get("base_url") or default_base).rstrip("/")
        self._api_key: str | None = cfg.get("api_key") or None
        self._default_model: str | None = cfg.get("default_model") or None
        self._auth_scheme: str = str(cfg.get("auth_scheme") or default_auth)
        self._api_key_header: str = str(cfg.get("api_key_header") or "x-api-key")
        self._context_window: int = int(cfg.get("context_window") or 0)
        self._supports_streaming: bool = bool(
            cfg.get("supports_streaming") if cfg.get("supports_streaming") is not None else True
        )
        self._timeout: float = float(cfg.get("timeout_seconds") or 300)
        extra = cfg.get("extra_headers") or {}
        self._extra_headers: dict[str, str] = (
            {str(k): str(v) for k, v in extra.items()} if isinstance(extra, dict) else {}
        )

        self.id = str(cfg.get("provider_id") or f"openai-compat:{self._preset}")
        if cfg.get("display_name"):
            self.name = str(cfg["display_name"])
        elif self._preset != "custom":
            self.name = f"OpenAI-compatible ({self._preset})"

        self.capabilities = ProviderCapabilities(
            streaming=self._supports_streaming,
            tools=False,
            vision=False,
            max_context=self._context_window,
            embeddings=False,
        )

        self._client: Any = None
        self._client_lock = asyncio.Lock()
        self._models_cache: list[ModelInfo] | None = None

    # ------------------------------------------------------------------ #
    # LLMProvider protocol
    # ------------------------------------------------------------------ #

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = await self._ensure_client()
        payload = self._build_payload(request, stream=False)
        start = time.monotonic()
        response = await client.post("/chat/completions", json=payload)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if response.status_code >= 400:
            raise RuntimeError(
                f"{self.id}: request failed ({response.status_code}): {response.text}"
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = str(message.get("content") or "")
        finish = str(choice.get("finish_reason") or "stop")
        usage = _usage(data.get("usage"))
        model_id = str(data.get("model") or request.model or self._default_model or "unknown")
        return CompletionResponse(
            text=text,
            model=model_id,
            finish_reason=finish,
            usage=usage,
            raw=data if isinstance(data, dict) else {},
            cost_estimate_usd=0.0 if self._is_local() else None,
            latency_ms=elapsed_ms,
        )

    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        return self._stream_impl(request)

    async def _stream_impl(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        client = await self._ensure_client()
        payload = self._build_payload(request, stream=True)
        usage: TokenUsage | None = None
        async with client.stream("POST", "/chat/completions", json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(f"{self.id}: stream failed ({response.status_code}): {body}")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    usage = _usage(event["usage"])
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield CompletionChunk(delta=str(text), is_final=False)
        yield CompletionChunk(delta="", is_final=True, usage=usage)

    async def list_models(self) -> list[ModelInfo]:
        if self._models_cache is not None:
            return list(self._models_cache)
        models: list[ModelInfo] = []
        try:
            client = await self._ensure_client()
            response = await client.get("/models")
            if response.status_code < 400:
                data = response.json()
                rows = data.get("data") or data.get("models") or []
                for row in rows:
                    if isinstance(row, str):
                        models.append(
                            ModelInfo(id=row, name=row, context_window=self._context_window)
                        )
                        continue
                    if not isinstance(row, dict):
                        continue
                    mid = str(row.get("id") or row.get("name") or "")
                    if not mid:
                        continue
                    ctx = int(row.get("context_length") or row.get("context_window") or 0)
                    models.append(
                        ModelInfo(
                            id=mid,
                            name=str(row.get("name") or mid),
                            context_window=ctx or self._context_window,
                        )
                    )
        except Exception:
            models = []
        if not models and self._default_model:
            models = [
                ModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    context_window=self._context_window,
                )
            ]
        self._models_cache = models
        return list(models)

    async def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4) if text else 0

    async def health_check(self) -> HealthStatus:
        if not self._base_url:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="base_url is not set (preset=custom requires one)",
            )
        if not self._api_key and not self._allow_empty_key and self._auth_scheme != "none":
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="api_key is not configured",
            )
        try:
            client = await self._ensure_client()
            response = await client.get("/models")
        except ModuleNotFoundError as exc:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message=f"httpx is not installed ({exc.name})",
            )
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"could not reach {self._base_url}: {exc!r}",
            )
        if response.status_code >= 500:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"server returned HTTP {response.status_code}",
            )
        if response.status_code == 404:
            # Some local servers (notably text-generation-webui at certain
            # versions) don't implement `/models`. Treat as degraded so the
            # UI can still let users send completions.
            return HealthStatus(
                level=HealthLevel.DEGRADED,
                target_id=self.id,
                message="server reachable but `/models` not implemented",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"reachable at {self._base_url}",
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _is_local(self) -> bool:
        host = self._base_url.lower()
        return "localhost" in host or "127.0.0.1" in host or "0.0.0.0" in host or "://[::1]" in host

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if not self._base_url:
                raise RuntimeError(
                    f"{self.id}: base_url is not configured "
                    "(preset=custom requires an explicit base_url)"
                )
            import httpx

            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            if self._auth_scheme == "bearer" and self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            elif self._auth_scheme == "header" and self._api_key:
                headers[self._api_key_header] = self._api_key
            headers.update(self._extra_headers)
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
            return self._client

    def _build_payload(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            messages.append({"role": _role(m.role), "content": m.content})
        model = request.model or self._default_model
        if not model:
            raise RuntimeError(
                f"{self.id}: no model specified on request and no default_model in config"
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.stop_sequences:
            payload["stop"] = list(request.stop_sequences)
        return payload


def _usage(payload: Any) -> TokenUsage:
    if not isinstance(payload, dict):
        return TokenUsage()
    prompt = int(payload.get("prompt_tokens") or 0)
    completion = int(payload.get("completion_tokens") or 0)
    total = int(payload.get("total_tokens") or (prompt + completion))
    # Cached-prompt visibility. These endpoints cache prefixes automatically;
    # the cached count is a SUBSET of ``prompt_tokens`` (already in input_tokens),
    # so we surface it for observability without altering input/total. OpenAI
    # exposes ``prompt_tokens_details.cached_tokens``; DeepSeek exposes
    # ``prompt_cache_hit_tokens``.
    cached = 0
    details = payload.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    if not cached:
        cached = int(payload.get("prompt_cache_hit_tokens") or 0)
    return TokenUsage(
        input_tokens=prompt,
        output_tokens=completion,
        total_tokens=total,
        cache_read_input_tokens=cached,
    )


__all__ = ["OpenAICompatibleLLMProvider"]
