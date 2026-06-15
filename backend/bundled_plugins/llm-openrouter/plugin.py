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
import logging
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

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


def _verify() -> Any:
    """Return an explicit CA bundle path so a broken ``SSL_CERT_FILE`` env
    var doesn't blow up the TLS handshake (httpx prefers the env var over
    its bundled certifi data, and a stale anaconda path is a common cause
    of opaque "file not found" failures inside ``list_models``).
    """
    try:
        import certifi

        return certifi.where()
    except ModuleNotFoundError:  # pragma: no cover - certifi ships with httpx
        return True


def _transport_errors() -> tuple[type[BaseException], ...]:
    """httpx transport-layer exceptions worth retrying.

    ``TransportError`` is the base for connect/read/write/pool failures and
    protocol errors — anything that means "the request never produced a usable
    HTTP response", as opposed to an HTTP error status (which we surface as a
    ``RuntimeError``). Returned as a tuple so callers can use it in ``except``.
    """
    import httpx

    return (httpx.TransportError,)


def _as_transient(exc: BaseException) -> BaseException:
    """Wrap a transport error as the gateway's retriable ``TransientError``.

    The retry/fallback policy only retries ``TransientError``/``RateLimitError``/
    ``TimeoutError``; a raw httpx error is treated as permanent. Falls back to
    the original exception if the gateway errors module can't be imported.
    """
    try:
        from grimoire.llm_gateway.errors import TransientError
    except Exception:  # pragma: no cover - gateway always present at runtime
        return exc
    return TransientError(f"openrouter: transport error: {exc!r}")


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
        self._active_model: str = str(cfg.get("active_model") or DEFAULT_MODEL)
        self._http_referer: str | None = cfg.get("http_referer") or None
        self._app_title: str = str(cfg.get("app_title") or "Grimoire")
        self._timeout: float = float(cfg.get("timeout_seconds") or 120)
        extra = cfg.get("extra_headers") or {}
        self._extra_headers: dict[str, str] = (
            {str(k): str(v) for k, v in extra.items()} if isinstance(extra, dict) else {}
        )
        # OpenRouter usage accounting (`usage: {include: true}`) returns the
        # actual charged cost on every response. On by default; turn off when
        # `base_url` points at a strict OpenAI-compatible gateway that rejects
        # the OpenRouter-only request field.
        self._usage_accounting: bool = bool(cfg.get("usage_accounting", True))
        # Provider routing: send only what the user explicitly configures. With
        # nothing set (or an explicit `provider: {}`), no `provider` field is
        # sent and OpenRouter uses its own/account routing and pricing. Per-model
        # overrides merge on top of the default for that model (#515).
        user_provider = cfg.get("provider")
        self._provider_default: dict[str, Any] = (
            dict(user_provider) if isinstance(user_provider, dict) else {}
        )
        overrides = cfg.get("provider_overrides")
        self._provider_overrides: dict[str, dict[str, Any]] = (
            {str(k): dict(v) for k, v in overrides.items() if isinstance(v, dict)}
            if isinstance(overrides, dict)
            else {}
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
        try:
            response = await client.post("/chat/completions", json=payload)
        except _transport_errors() as exc:
            raise _as_transient(exc) from exc
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
        model_id = str(data.get("model") or request.model or self._active_model)
        _log_usage(model_id, usage, data)
        # Prefer the *actual* charge from usage accounting over the catalog
        # estimate — the gateway preserves a provider-supplied cost, so this is
        # what lands in cost records when a pricier provider served the call.
        actual_cost = _actual_cost(data)
        return CompletionResponse(
            text=text,
            model=model_id,
            finish_reason=finish,
            usage=usage,
            raw=data if isinstance(data, dict) else {},
            cost_estimate_usd=(
                actual_cost
                if actual_cost is not None
                else await self._estimate_cost(usage, model_id)
            ),
            latency_ms=elapsed_ms,
        )

    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        return self._stream_impl(request)

    async def _stream_impl(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        client = await self._ensure_client()
        payload = self._build_payload(request, stream=True)
        usage: TokenUsage | None = None
        model_id = str(request.model or self._active_model)
        meta: dict[str, Any] = {}
        try:
            async with client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"openrouter: stream failed ({response.status_code}): {body}"
                    )
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
                        meta["usage"] = event["usage"]
                    if event.get("provider"):
                        meta.setdefault("provider", event["provider"])
                    if event.get("id"):
                        meta.setdefault("id", event["id"])
                    if event.get("model"):
                        meta.setdefault("model", event["model"])
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield CompletionChunk(delta=str(text), is_final=False)
        except _transport_errors() as exc:
            # Connection/read failures (e.g. ConnectError) are transient. Re-raise
            # as the gateway's TransientError so its retry/fallback policy engages
            # instead of treating the raw httpx error as a permanent failure and
            # aborting the turn on a momentary network blip.
            raise _as_transient(exc) from exc
        if usage is not None:
            _log_usage(str(meta.get("model") or model_id), usage, meta)
        # Carry the actual charge on the final chunk so the gateway's streaming
        # path records it instead of recomputing an estimate from catalog rates.
        yield CompletionChunk(
            delta="", is_final=True, usage=usage, cost_estimate_usd=_actual_cost(meta)
        )

    async def list_models(self) -> list[ModelInfo]:
        if self._models_cache is not None:
            return list(self._models_cache)
        # OpenRouter's /models endpoint is public, so we can list the
        # catalog before the user has saved an API key. Reuse the
        # authenticated client when we have one (pricing is identical on
        # both paths, but the auth call is logged on their dashboard).
        try:
            if self._api_key:
                client = await self._ensure_client()
                response = await client.get("/models")
            else:
                import httpx

                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=self._timeout,
                    verify=_verify(),
                ) as anon:
                    response = await anon.get("/models")
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
            message=f"default model {self._active_model!r}",
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
                verify=_verify(),
            )
            return self._client

    def _build_payload(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            messages.append({"role": _role(m.role), "content": m.content})
        model = request.model or self._active_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if self._usage_accounting:
            # Ask OpenRouter to return the *actual* charged cost so we can log it
            # and detect provider-price variance — catalog pricing reflects the
            # headline rate, not whichever provider actually served the call.
            payload["usage"] = {"include": True}
        provider = self._resolve_provider(model)
        if provider:
            payload["provider"] = provider
        if request.stop_sequences:
            payload["stop"] = list(request.stop_sequences)
        return payload

    def _resolve_provider(self, model: str) -> dict[str, Any]:
        """Provider routing object for `model`: the default base with this
        model's overrides merged on top. Nested objects (e.g. `max_price`)
        merge field-by-field so overriding one limit can't silently drop the
        default's others. Empty when routing is opted out."""
        routing = dict(self._provider_default)
        for key, value in self._provider_overrides.get(model, {}).items():
            base = routing.get(key)
            if isinstance(base, dict) and isinstance(value, dict):
                routing[key] = {**base, **value}
            else:
                routing[key] = value
        return routing

    def _fallback_model_info(self) -> ModelInfo:
        return ModelInfo(id=self._active_model, name=self._active_model, context_window=0)

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


def _actual_cost(data: Any) -> float | None:
    """The actual charge OpenRouter reported for this call in USD, parsed from
    `usage.cost` (present when the request set `usage: {include: true}`).
    None when absent or non-numeric."""
    raw = data.get("usage") if isinstance(data, dict) else None
    cost = raw.get("cost") if isinstance(raw, dict) else None
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        return float(cost)
    return None


def _reasoning_tokens(raw: dict[str, Any]) -> int:
    direct = raw.get("reasoning_tokens")
    if direct:
        return int(direct)
    details = raw.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens"):
        return int(details["reasoning_tokens"])
    return 0


def _log_usage(model_id: str, usage: TokenUsage, data: Any) -> None:
    """Log per-request usage and cost so provider-price variance is visible the
    moment it happens (#515). The blended cost-per-million makes a request that
    landed on a pricier provider stand out against the model's headline rate.
    """
    raw = data.get("usage") if isinstance(data, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    provider = data.get("provider") if isinstance(data, dict) else None
    gen_id = data.get("id") if isinstance(data, dict) else None
    reasoning = _reasoning_tokens(raw)
    cost = _actual_cost(data)
    billed = usage.input_tokens + usage.output_tokens
    cost_per_million: float | None = None
    if cost is not None and billed:
        cost_per_million = round(cost / billed * 1_000_000, 4)
    logger.info(
        "openrouter usage model=%s provider=%s gen_id=%s prompt=%d completion=%d "
        "reasoning=%d total_cost=%s blended_$/M=%s",
        model_id,
        provider,
        gen_id,
        usage.input_tokens,
        usage.output_tokens,
        reasoning,
        cost,
        cost_per_million,
    )


__all__ = ["OpenRouterLLMProvider"]
