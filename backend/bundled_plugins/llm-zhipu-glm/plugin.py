"""Zhipu GLM LLM Provider.

Zhipu (smart.cn / bigmodel.cn) and its international arm Z.AI expose
GLM-family models through an OpenAI-compatible chat-completions API.
Two account types matter for endpoint selection:

* Pay-as-you-go (the BigModel platform) bills tokens against credits.
* Subscription plans (the "GLM Coding Plan" sold via api.z.ai) bill
  flat-rate per month and use a different host.

Both speak the same wire format, so the plugin keeps a single client
implementation and just rewrites the base URL per plan. Users can also
point `base_url` at any other deployment.
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

DEFAULT_MODEL = "glm-4.6"

PLAN_ENDPOINTS: dict[str, str] = {
    "pay-as-you-go": "https://open.bigmodel.cn/api/paas/v4",
    "coding-plan": "https://api.z.ai/api/coding/paas/v4",
    "glm-coding-plan": "https://api.z.ai/api/coding/paas/v4",
}

# Per-1k-token pricing (USD-equivalent, rough). Only used when the live
# `/models` endpoint does not return pricing; subscription plans report no
# per-token cost so this never fires for them.
_FALLBACK_PRICING: dict[str, tuple[float, float]] = {
    "glm-4.6": (0.60, 2.20),
    "glm-4.5": (0.60, 2.20),
    "glm-4.5-air": (0.20, 1.10),
    "glm-4-plus": (0.50, 1.50),
    "glm-4-air": (0.10, 0.10),
    "glm-4-flash": (0.0, 0.0),
}


def _role(role: Any) -> str:
    return role.value if hasattr(role, "value") else str(role)


class ZhipuGLMLLMProvider:
    id = "zhipu-glm"
    name = "Zhipu GLM"
    capabilities = ProviderCapabilities(
        streaming=True,
        tools=True,
        vision=True,
        max_context=128_000,
        embeddings=False,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.config = cfg
        self._api_key: str | None = cfg.get("api_key") or None
        self._plan: str = str(cfg.get("plan") or "pay-as-you-go")
        self._base_url: str = self._resolve_base_url(cfg.get("base_url"))
        self._default_model: str = str(cfg.get("default_model") or DEFAULT_MODEL)
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
                f"zhipu-glm: request failed ({response.status_code}): {response.text}"
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
            cost_estimate_usd=self._estimate_cost(usage, model_id),
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
                raise RuntimeError(f"zhipu-glm: stream failed ({response.status_code}): {body}")
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
                rows = data.get("data") or []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    mid = str(row.get("id") or "")
                    if not mid:
                        continue
                    pricing = row.get("pricing") or {}
                    models.append(
                        ModelInfo(
                            id=mid,
                            name=str(row.get("name") or mid),
                            context_window=int(row.get("context_length") or 0),
                            input_cost_per_1k=_per_1k(pricing.get("prompt")),
                            output_cost_per_1k=_per_1k(pricing.get("completion")),
                        )
                    )
        except Exception:
            models = []
        if not models:
            # Fall back to the known GLM line-up so the UI has something to
            # render even when the catalog endpoint is not reachable.
            for mid, (in_cost, out_cost) in _FALLBACK_PRICING.items():
                models.append(
                    ModelInfo(
                        id=mid,
                        name=mid,
                        context_window=128_000,
                        input_cost_per_1k=in_cost or None,
                        output_cost_per_1k=out_cost or None,
                    )
                )
        self._models_cache = models
        return list(models)

    async def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4) if text else 0

    async def health_check(self) -> HealthStatus:
        if not self._api_key:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="api_key is not configured",
            )
        try:
            await self._ensure_client()
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
                message=f"could not initialise client: {exc!r}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"plan={self._plan!r}, base_url={self._base_url!r}",
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _resolve_base_url(self, override: Any) -> str:
        if override:
            return str(override).rstrip("/")
        endpoint = PLAN_ENDPOINTS.get(self._plan)
        if endpoint:
            return endpoint
        # An unknown plan with no `base_url` is a config error, but we
        # default to pay-as-you-go to keep the plugin loadable so the UI
        # can surface a validation message.
        return PLAN_ENDPOINTS["pay-as-you-go"]

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if not self._api_key:
                raise RuntimeError("zhipu-glm: api_key is not configured")
            import httpx

            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
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

    def _estimate_cost(self, usage: TokenUsage, model_id: str) -> float | None:
        if self._plan in ("coding-plan", "glm-coding-plan"):
            # Subscription plans don't charge per token; the user pays a
            # flat monthly fee. Reporting 0 makes "cost so far" meaningful
            # without conflating subscription users with free tiers.
            return 0.0
        if not (usage.input_tokens or usage.output_tokens):
            return None
        pricing = _FALLBACK_PRICING.get(model_id)
        if pricing is None:
            return None
        input_rate, output_rate = pricing
        return (usage.input_tokens / 1000.0) * input_rate + (
            usage.output_tokens / 1000.0
        ) * output_rate


def _per_1k(raw: Any) -> float | None:
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


__all__ = ["ZhipuGLMLLMProvider"]
