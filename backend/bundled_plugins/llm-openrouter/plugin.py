"""OpenRouter LLM Provider.

OpenRouter exposes a single OpenAI-compatible endpoint that fans out to
many upstream models. We talk to it directly over `httpx` rather than
adding an SDK dependency — the chat-completions wire format is small and
stable, and reusing the same shape lets the GLM and OpenAI-compatible
local-server plugins follow the same pattern.

Models are addressed by their OpenRouter slug (e.g. `openai/gpt-4o`,
`anthropic/claude-opus-4-7`, `meta-llama/llama-3.1-70b-instruct`). The
`/models` catalog is fetched lazily so cost estimates reflect live
pricing where available.
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

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


def _role(role: Any) -> str:
    return role.value if hasattr(role, "value") else str(role)


class OpenRouterLLMProvider:
    id = "openrouter"
    name = "OpenRouter"
    capabilities = ProviderCapabilities(
        streaming=True,
        tools=True,
        vision=True,
        max_context=0,
        embeddings=False,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.config = cfg
        self._api_key: str | None = cfg.get("api_key") or None
        self._base_url: str = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self._default_model: str = str(cfg.get("default_model") or DEFAULT_MODEL)
        self._http_referer: str | None = cfg.get("http_referer") or None
        self._app_title: str = str(cfg.get("app_title") or "Grimoire")
        self._timeout: float = float(cfg.get("timeout_seconds") or 120)
        extra = cfg.get("extra_headers") or {}
        self._extra_headers: dict[str, str] = (
            {str(k): str(v) for k, v in extra.items()} if isinstance(extra, dict) else {}
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
                f"openrouter: request failed ({response.status_code}): {response.text}"
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = str(message.get("content") or "")
        finish = str(choice.get("finish_reason") or "stop")
        usage = _usage(data.get("usage"))
        model_id = str(data.get("model") or request.model or self._default_model)
        return CompletionResponse(
            text=text,
            model=model_id,
            finish_reason=finish,
            usage=usage,
            raw=data if isinstance(data, dict) else {},
            cost_estimate_usd=await self._estimate_cost(usage, model_id),
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
                raise RuntimeError(f"openrouter: stream failed ({response.status_code}): {body}")
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
        try:
            client = await self._ensure_client()
            response = await client.get("/models")
            if response.status_code >= 400:
                return [self._fallback_model_info()]
            data = response.json()
        except Exception:
            return [self._fallback_model_info()]
        rows = data.get("data") or []
        models: list[ModelInfo] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "")
            if not mid:
                continue
            pricing = row.get("pricing") or {}
            input_cost = _per_1k(pricing.get("prompt"))
            output_cost = _per_1k(pricing.get("completion"))
            models.append(
                ModelInfo(
                    id=mid,
                    name=str(row.get("name") or mid),
                    context_window=int(row.get("context_length") or 0),
                    input_cost_per_1k=input_cost,
                    output_cost_per_1k=output_cost,
                )
            )
        if not models:
            models = [self._fallback_model_info()]
        self._models_cache = models
        return list(models)

    async def estimate_tokens(self, text: str) -> int:
        # OpenRouter doesn't expose a count_tokens endpoint; char/4 matches
        # the heuristic used by the other providers.
        return max(1, len(text) // 4) if text else 0

    async def health_check(self) -> HealthStatus:
        if not self._api_key:
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
                message=f"could not reach OpenRouter: {exc!r}",
            )
        if response.status_code >= 400:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"OpenRouter returned HTTP {response.status_code}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"default model {self._default_model!r}",
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if not self._api_key:
                raise RuntimeError("openrouter: api_key is not configured")
            import httpx

            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-Title": self._app_title,
            }
            if self._http_referer:
                headers["HTTP-Referer"] = self._http_referer
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
        payload: dict[str, Any] = {
            "model": request.model or self._default_model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.stop_sequences:
            payload["stop"] = list(request.stop_sequences)
        return payload

    def _fallback_model_info(self) -> ModelInfo:
        return ModelInfo(id=self._default_model, name=self._default_model, context_window=0)

    async def _estimate_cost(self, usage: TokenUsage, model_id: str) -> float | None:
        # Only consult the cached catalog so a `complete()` call never fires
        # an extra `/models` request behind the user's back. Callers that
        # want costs populated can hit `list_models()` once up front.
        if self._models_cache is None or not (usage.input_tokens or usage.output_tokens):
            return None
        for m in self._models_cache:
            if m.id != model_id:
                continue
            if m.input_cost_per_1k is None or m.output_cost_per_1k is None:
                return None
            return (usage.input_tokens / 1000.0) * m.input_cost_per_1k + (
                usage.output_tokens / 1000.0
            ) * m.output_cost_per_1k
        return None


def _per_1k(raw: Any) -> float | None:
    """OpenRouter reports per-token pricing as a string USD value."""
    if raw in (None, "", 0):
        return None
    try:
        return float(raw) * 1000.0
    except (TypeError, ValueError):
        return None


def _usage(payload: Any) -> TokenUsage:
    if not isinstance(payload, dict):
        return TokenUsage()
    prompt = int(payload.get("prompt_tokens") or 0)
    completion = int(payload.get("completion_tokens") or 0)
    total = int(payload.get("total_tokens") or (prompt + completion))
    return TokenUsage(input_tokens=prompt, output_tokens=completion, total_tokens=total)


__all__ = ["OpenRouterLLMProvider"]
