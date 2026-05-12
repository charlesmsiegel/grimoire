"""OpenAI image generation (DALL-E + gpt-image-1) backend.

Implements the :class:`ImageGenBackend` protocol by posting to OpenAI's
``/v1/images/generations`` endpoint and decoding the base64 PNG. The
``httpx`` client is created lazily so the plugin can be discovered and
listed without the optional dependency installed.

OpenAI's image API doesn't expose samplers or honour caller-provided
seeds, so :meth:`list_samplers` returns an empty list and ``seed`` is
ignored (the model's reported seed, when present, is surfaced via
``actual_params``).
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
)
from grimoire.types.llm import ModelInfo

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "dall-e-3"

# Sizes the OpenAI image endpoint accepts, per model. Used to snap the
# caller's `(width, height)` to the nearest supported size.
_MODEL_SIZES: dict[str, tuple[tuple[int, int], ...]] = {
    "dall-e-3": ((1024, 1024), (1792, 1024), (1024, 1792)),
    "dall-e-2": ((256, 256), (512, 512), (1024, 1024)),
    "gpt-image-1": ((1024, 1024), (1536, 1024), (1024, 1536)),
}

_KNOWN_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(id="dall-e-3", name="DALL-E 3"),
    ModelInfo(id="dall-e-2", name="DALL-E 2"),
    ModelInfo(id="gpt-image-1", name="GPT Image 1"),
)


def _snap_size(model: str, width: int, height: int) -> tuple[int, int]:
    """Pick the supported size with the smallest L1 distance to (w, h)."""
    options = _MODEL_SIZES.get(model) or _MODEL_SIZES[DEFAULT_MODEL]
    return min(options, key=lambda opt: abs(opt[0] - width) + abs(opt[1] - height))


class DalleImageGenBackend:
    """Client for OpenAI's image generation endpoint."""

    id = "imagegen-dalle"
    name = "OpenAI DALL-E"
    deterministic_seed = False

    capabilities = BackendCapabilities(
        text_to_image=True,
        image_to_image=False,
        inpainting=False,
        controlnet=False,
        lora=False,
        img2img_strength_range=(0.0, 0.0),
        max_resolution=(1792, 1792),
        supports_negative_prompt=False,
        supports_seed=False,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._api_key: str | None = cfg.get("api_key") or None
        self._base_url: str = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self._default_model: str = str(cfg.get("default_model") or DEFAULT_MODEL)
        self._quality: str = str(cfg.get("quality") or "standard")
        self._style: str = str(cfg.get("style") or "vivid")
        self._organization: str | None = cfg.get("organization") or None
        self._timeout: float = float(cfg.get("timeout_seconds") or 120)
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # HTTP client lifecycle
    # ------------------------------------------------------------------ #

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if not self._api_key:
                raise RuntimeError("imagegen-dalle: api_key is not configured")
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - exercised by integration
                raise RuntimeError("httpx not installed; add `httpx` to the plugin's venv") from exc
            headers: dict[str, str] = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            if self._organization:
                headers["OpenAI-Organization"] = self._organization
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
            return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # ImageGenBackend protocol
    # ------------------------------------------------------------------ #

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        client = await self._ensure_client()
        model = request.model or self._default_model
        width, height = _snap_size(model, request.width, request.height)
        prompt = request.prompt
        if request.negative_prompt:
            prompt = f"{prompt}\n\nAvoid: {request.negative_prompt}"
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": f"{width}x{height}",
            "response_format": "b64_json",
        }
        if model == "dall-e-3":
            payload["quality"] = self._quality
            payload["style"] = self._style

        t0 = time.perf_counter()
        response = await client.post("/images/generations", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"imagegen-dalle: /images/generations returned "
                f"{response.status_code}: {response.text}"
            )
        body = response.json()
        rows = body.get("data") or []
        if not rows or not isinstance(rows[0], dict):
            raise RuntimeError("imagegen-dalle: response had no images")
        b64 = rows[0].get("b64_json")
        if not b64:
            raise RuntimeError("imagegen-dalle: response missing b64_json (response_format)")
        image_bytes = base64.b64decode(b64)
        revised_prompt = rows[0].get("revised_prompt")

        from grimoire.imagegen.backend import make_thumbnail

        thumbnail_bytes = make_thumbnail(image_bytes)
        return GenerationResult(
            image_bytes=image_bytes,
            thumbnail_bytes=thumbnail_bytes,
            backend=self.id,
            model=model,
            seed=request.seed if request.seed is not None else 0,
            actual_params={
                "width": width,
                "height": height,
                "size": payload["size"],
                "quality": payload.get("quality"),
                "style": payload.get("style"),
                "revised_prompt": revised_prompt,
                "negative_prompt": request.negative_prompt or "",
            },
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def list_models(self) -> list[ModelInfo]:
        return list(_KNOWN_MODELS)

    async def list_samplers(self) -> list[str]:
        # OpenAI's image endpoint does not expose samplers.
        return []

    async def health_check(self) -> HealthStatus:
        if not self._api_key:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="api_key is not configured",
            )
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message=f"missing dependency: {exc.name}",
            )
        try:
            client = await self._ensure_client()
            response = await client.get("/models")
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"openai unreachable: {exc!r}",
            )
        if response.status_code in (401, 403):
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="openai rejected api_key",
            )
        if response.status_code >= 500:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"openai server error: HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"openai returned HTTP {response.status_code}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message="api_key accepted",
        )


__all__ = ["DalleImageGenBackend"]
