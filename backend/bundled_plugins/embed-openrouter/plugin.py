"""OpenRouter Embedding Provider.

Mirrors the llm-openrouter plugin shape: a thin httpx wrapper around the
OpenAI-compatible `/embeddings` endpoint, with `list_models()` driven by
the live `/models` catalog filtered down to embedding-capable rows.

OpenRouter's `/models` catalog mixes chat, vision, audio, and embedding
models in one bucket. We identify embedding models by looking for an
``embedding`` token in either `architecture.output_modalities` or
`architecture.modality`, falling back to a substring match on the model
id. If filtering yields nothing (e.g. the schema changes), we surface a
small hand-curated fallback list so the picker still works.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import ModelInfo

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/text-embedding-3-small"

# Known native dimension counts for popular embedding models exposed via
# OpenRouter. Used to populate the picker before any successful call;
# response payloads are trusted over this table.
KNOWN_DIMENSIONS: dict[str, int] = {
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
    "openai/text-embedding-ada-002": 1536,
    "mistralai/mistral-embed": 1024,
    "cohere/embed-english-v3.0": 1024,
    "cohere/embed-multilingual-v3.0": 1024,
    "cohere/embed-english-light-v3.0": 384,
    "cohere/embed-multilingual-light-v3.0": 384,
    "voyage/voyage-3": 1024,
    "voyage/voyage-3-lite": 512,
}

# Last-resort catalog if /models filtering yields nothing. Kept short on
# purpose — the live catalog is the source of truth.
FALLBACK_MODELS: tuple[str, ...] = (
    "openai/text-embedding-3-small",
    "openai/text-embedding-3-large",
    "mistralai/mistral-embed",
    "cohere/embed-english-v3.0",
    "cohere/embed-multilingual-v3.0",
)


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


class OpenRouterEmbeddingProvider:
    id = "embed-openrouter"
    name = "OpenRouter Embeddings"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.config = cfg
        self._api_key: str | None = cfg.get("api_key") or None
        self._base_url: str = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        active = cfg.get("active_model") or cfg.get("model") or DEFAULT_MODEL
        self.model_id: str = str(active)
        self._configured_dimensions: int | None = (
            int(cfg["dimensions"]) if cfg.get("dimensions") is not None else None
        )
        self._http_referer: str | None = cfg.get("http_referer") or None
        self._app_title: str = str(cfg.get("app_title") or "Grimoire")
        self._timeout: float = float(cfg.get("timeout_seconds") or 60)
        extra = cfg.get("extra_headers") or {}
        self._extra_headers: dict[str, str] = (
            {str(k): str(v) for k, v in extra.items()} if isinstance(extra, dict) else {}
        )
        self.dimensions: int = self._configured_dimensions or KNOWN_DIMENSIONS.get(
            self.model_id, 0
        )
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()
        self._models_cache: list[ModelInfo] | None = None

    # ------------------------------------------------------------------ #
    # EmbeddingProvider protocol
    # ------------------------------------------------------------------ #

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._api_key:
            raise RuntimeError("embed-openrouter: api_key is not configured")
        client = await self._ensure_client()
        payload: dict[str, Any] = {"model": self.model_id, "input": texts}
        if self._configured_dimensions is not None:
            payload["dimensions"] = self._configured_dimensions
        response = await client.post("/embeddings", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"embed-openrouter: request failed ({response.status_code}): {response.text}"
            )
        data = response.json()
        rows = data.get("data") or []
        vectors: list[list[float]] = []
        for entry in rows:
            embedding = entry.get("embedding") or []
            vectors.append([float(v) for v in embedding])
        if vectors and self.dimensions != len(vectors[0]):
            self.dimensions = len(vectors[0])
        return vectors

    async def list_models(self) -> list[ModelInfo]:
        if self._models_cache is not None:
            return list(self._models_cache)
        try:
            data = await self._fetch_models_catalog()
        except Exception as exc:
            logger.warning("embed-openrouter: could not list models: %r", exc)
            data = None
        models: list[ModelInfo] = []
        if isinstance(data, dict):
            for row in data.get("data") or []:
                if not isinstance(row, dict):
                    continue
                if not _is_embedding_model(row):
                    continue
                mid = str(row.get("id") or "")
                if not mid:
                    continue
                pricing = row.get("pricing") or {}
                # OpenRouter exposes embedding pricing as `prompt` (per
                # input token); there's no completion cost.
                input_cost = _per_1k(pricing.get("prompt"))
                models.append(
                    ModelInfo(
                        id=mid,
                        name=str(row.get("name") or mid),
                        input_cost_per_1k=input_cost,
                        dimensions=KNOWN_DIMENSIONS.get(mid),
                    )
                )
        if not models:
            models = [
                ModelInfo(id=mid, name=mid, dimensions=KNOWN_DIMENSIONS.get(mid))
                for mid in FALLBACK_MODELS
            ]
        if self.model_id and not any(m.id == self.model_id for m in models):
            models.append(
                ModelInfo(
                    id=self.model_id,
                    name=self.model_id,
                    dimensions=KNOWN_DIMENSIONS.get(self.model_id),
                )
            )
        models.sort(key=lambda m: m.id)
        self._models_cache = models
        return list(models)

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
            message=f"model {self.model_id!r} reachable",
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _fetch_models_catalog(self) -> Any:
        # OpenRouter's /models endpoint is public, so we can list the
        # catalog before the user has saved an API key.
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
            raise RuntimeError(
                f"openrouter /models returned HTTP {response.status_code}: {response.text}"
            )
        return response.json()

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if not self._api_key:
                raise RuntimeError("embed-openrouter: api_key is not configured")
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


def _is_embedding_model(row: dict[str, Any]) -> bool:
    """Heuristic: does this `/models` row describe an embedding model?

    OpenRouter has changed the architecture shape a few times; rather than
    bet on one field, look for the ``embedding`` token in any of the
    plausible places.
    """
    arch = row.get("architecture")
    if isinstance(arch, dict):
        outputs = arch.get("output_modalities")
        if isinstance(outputs, list) and any(
            isinstance(m, str) and "embedding" in m.lower() for m in outputs
        ):
            return True
        modality = arch.get("modality")
        if isinstance(modality, str) and "embedding" in modality.lower():
            return True
    mid = str(row.get("id") or "")
    return "embed" in mid.lower()


def _per_1k(raw: Any) -> float | None:
    """OpenRouter reports per-token pricing as a string USD value."""
    if raw in (None, "", 0):
        return None
    try:
        return float(raw) * 1000.0
    except (TypeError, ValueError):
        return None


__all__ = ["OpenRouterEmbeddingProvider"]
