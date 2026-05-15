"""Automatic1111 stable-diffusion-webui HTTP client backend.

A thin wrapper around a running a1111 server's ``/sdapi/v1`` routes.
``httpx`` is imported lazily so the plugin can be discovered and listed
without the optional dependency being installed.

The backend exposes the same :class:`ImageGenBackend` protocol as the
integrated diffusers backend, which means routing can swap between
the two transparently.
"""

from __future__ import annotations

import asyncio
import base64
import random
import time
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
)
from grimoire.types.llm import ModelInfo

DEFAULT_BASE_URL = "http://127.0.0.1:7860"
DEFAULT_SAMPLER = "DPM++ 2M Karras"

# Static fallback used when the live server hasn't been reached yet — the
# real list comes from `/sdapi/v1/samplers`. Mirrors a1111's defaults.
_FALLBACK_SAMPLERS: tuple[str, ...] = (
    "DPM++ 2M Karras",
    "Euler a",
    "Euler",
    "DDIM",
    "DPM++ SDE Karras",
    "UniPC",
)


class A1111ImageGenBackend:
    """HTTP client for an Automatic1111 server.

    Each ``generate`` call POSTs to ``/sdapi/v1/txt2img`` (or
    ``/sdapi/v1/img2img`` when ``init_image`` is set) and decodes the
    base64-encoded PNG from the response. ``list_models`` /
    ``list_samplers`` fetch the live server's introspection routes;
    ``health_check`` probes ``/sdapi/v1/sd-models`` (an authenticated,
    cheap endpoint that confirms the API surface is reachable).
    """

    id = "imagegen-a1111"
    name = "Automatic1111 webui"
    deterministic_seed = False  # depends on server-side build, treat as non-deterministic

    capabilities = BackendCapabilities(
        text_to_image=True,
        image_to_image=True,
        inpainting=True,
        controlnet=True,
        lora=True,
        img2img_strength_range=(0.0, 1.0),
        max_resolution=(2048, 2048),
        supports_negative_prompt=True,
        supports_seed=True,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._base_url: str = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self._api_key: str | None = cfg.get("api_key") or None
        self._username: str | None = cfg.get("username") or None
        self._password: str | None = cfg.get("password") or None
        self._timeout: float = float(cfg.get("timeout_seconds") or 300)
        self._default_sampler: str = str(cfg.get("default_sampler") or DEFAULT_SAMPLER)
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
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - exercised by integration
                raise RuntimeError("httpx not installed; add `httpx` to the plugin's venv") from exc
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            auth: Any = None
            if self._username and self._password:
                auth = httpx.BasicAuth(self._username, self._password)
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                auth=auth,
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
        seed = (
            request.seed
            if request.seed is not None
            else random.SystemRandom().randint(0, 2**31 - 1)
        )
        payload = self._build_payload(request, seed)
        endpoint = "/sdapi/v1/img2img" if request.init_image else "/sdapi/v1/txt2img"

        t0 = time.perf_counter()
        response = await client.post(endpoint, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"a1111: {endpoint} returned {response.status_code}: {response.text}"
            )
        data = response.json()
        images = data.get("images") or []
        if not images:
            raise RuntimeError("a1111: response contained no images")
        image_bytes = base64.b64decode(images[0])

        info = _parse_info(data.get("info"))
        actual_seed = int(info.get("seed", seed))
        actual_model = str(info.get("sd_model_name") or info.get("model") or request.model or "")

        from grimoire.imagegen.backend import make_thumbnail

        thumbnail_bytes = make_thumbnail(image_bytes)
        return GenerationResult(
            image_bytes=image_bytes,
            thumbnail_bytes=thumbnail_bytes,
            backend=self.id,
            model=actual_model,
            seed=actual_seed,
            actual_params={
                "width": request.width,
                "height": request.height,
                "steps": info.get("steps", request.steps),
                "cfg_scale": info.get("cfg_scale", request.cfg_scale),
                "sampler": info.get("sampler_name", request.sampler or self._default_sampler),
                "negative_prompt": info.get("negative_prompt", request.negative_prompt or ""),
                "endpoint": endpoint,
            },
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def list_models(self) -> list[ModelInfo]:
        try:
            client = await self._ensure_client()
            response = await client.get("/sdapi/v1/sd-models")
            if response.status_code >= 400:
                return []
            rows = response.json() or []
        except Exception:
            return []
        models: list[ModelInfo] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("title") or row.get("model_name") or row.get("hash") or "")
            if not model_id:
                continue
            models.append(ModelInfo(id=model_id, name=str(row.get("model_name") or model_id)))
        return models

    async def list_samplers(self) -> list[str]:
        try:
            client = await self._ensure_client()
            response = await client.get("/sdapi/v1/samplers")
            if response.status_code >= 400:
                return list(_FALLBACK_SAMPLERS)
            rows = response.json() or []
        except Exception:
            return list(_FALLBACK_SAMPLERS)
        names = [str(r.get("name")) for r in rows if isinstance(r, dict) and r.get("name")]
        return names or list(_FALLBACK_SAMPLERS)

    async def health_check(self) -> HealthStatus:
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
            response = await client.get("/sdapi/v1/sd-models")
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"a1111 unreachable at {self._base_url}: {exc!r}",
            )
        if response.status_code >= 500:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"a1111 server error: HTTP {response.status_code}",
            )
        if response.status_code in (401, 403):
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="a1111 rejected credentials (set api_key or username/password)",
            )
        if response.status_code >= 400:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"a1111 returned HTTP {response.status_code}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"a1111 reachable at {self._base_url}",
        )

    # ------------------------------------------------------------------ #
    # Payload assembly
    # ------------------------------------------------------------------ #

    def _build_payload(self, request: GenerationRequest, seed: int) -> dict[str, Any]:
        sampler = request.sampler or self._default_sampler
        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt or "",
            "width": request.width,
            "height": request.height,
            "steps": request.steps,
            "cfg_scale": request.cfg_scale,
            "sampler_name": sampler,
            "seed": seed,
            "batch_size": 1,
            "n_iter": 1,
        }
        if request.model:
            payload["override_worlds"] = {"sd_model_checkpoint": request.model}
            payload["override_worlds_restore_afterwards"] = True
        if request.init_image:
            payload["init_images"] = [base64.b64encode(request.init_image).decode("ascii")]
            if request.init_image_strength is not None:
                payload["denoising_strength"] = float(request.init_image_strength)
        if request.extra:
            payload.update(dict(request.extra))
        return payload


def _parse_info(info: Any) -> dict[str, Any]:
    """a1111 returns `info` as a JSON-encoded string; tolerate both shapes."""
    if isinstance(info, dict):
        return info
    if isinstance(info, str) and info:
        import json

        try:
            parsed = json.loads(info)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


__all__ = ["A1111ImageGenBackend"]
