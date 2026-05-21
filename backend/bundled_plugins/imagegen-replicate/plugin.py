"""Replicate-hosted image generation backend.

Implements the :class:`ImageGenBackend` protocol by posting to
Replicate's predictions API, polling until the prediction terminates,
then downloading the returned image URL.

Replicate accepts two prediction shapes:

- ``POST /v1/predictions`` with ``{"version": "<sha>", "input": {...}}``
  pins to a specific version.
- ``POST /v1/models/{owner}/{name}/predictions`` with ``{"input": {...}}``
  uses the model's latest official version.

The plugin picks between them based on whether the requested model
reference contains a ``:`` (treated as ``owner/name:version``).

``httpx`` is imported lazily so the plugin can be discovered without
the optional dependency installed.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
)
from grimoire.types.llm import ModelInfo

DEFAULT_BASE_URL = "https://api.replicate.com/v1"
DEFAULT_MODEL = "black-forest-labs/flux-schnell"

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled"})

# A small static catalogue of well-known text-to-image models on
# Replicate. Returned by `list_models` so the UI has something useful to
# show without us shipping a live `/models` browser. Operators can still
# point the backend at any other `owner/name[:version]` they like.
_KNOWN_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(id="black-forest-labs/flux-schnell", name="FLUX schnell"),
    ModelInfo(id="black-forest-labs/flux-dev", name="FLUX dev"),
    ModelInfo(id="black-forest-labs/flux-1.1-pro", name="FLUX 1.1 pro"),
    ModelInfo(id="stability-ai/sdxl", name="Stable Diffusion XL"),
    ModelInfo(id="stability-ai/stable-diffusion-3.5-large", name="SD 3.5 large"),
    ModelInfo(id="bytedance/sdxl-lightning-4step", name="SDXL Lightning (4-step)"),
)


def _split_model(reference: str) -> tuple[str | None, str | None, str | None]:
    """Parse ``owner/name[:version]`` into its components.

    Returns ``(owner, name, version)`` with any missing piece as ``None``.
    """
    if not reference:
        return None, None, None
    head, _, version = reference.partition(":")
    owner, _, name = head.partition("/")
    return (owner or None, name or None, version or None)


def _build_input(request: GenerationRequest) -> dict[str, Any]:
    """Map the request fields onto the union of common Replicate inputs.

    Replicate model schemas vary, but the SDXL/SD3/Flux family models all
    accept some subset of these keys. Extra inputs supplied through
    ``request.extra`` win over the defaults so callers can drive
    model-specific knobs (e.g. ``aspect_ratio``, ``output_format``).
    """
    inputs: dict[str, Any] = {
        "prompt": request.prompt,
        "width": request.width,
        "height": request.height,
        "num_inference_steps": request.steps,
        "guidance_scale": request.cfg_scale,
    }
    if request.negative_prompt:
        inputs["negative_prompt"] = request.negative_prompt
    if request.seed is not None:
        inputs["seed"] = int(request.seed)
    if request.sampler:
        inputs["scheduler"] = request.sampler
    extra = request.extra or {}
    if isinstance(extra, dict):
        inputs.update(extra)
    return inputs


def _first_output_url(output: Any) -> str | None:
    """Pull the first image URL out of Replicate's polymorphic `output`."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        for item in output:
            if isinstance(item, str) and item:
                return item
            if isinstance(item, dict):
                url = item.get("url") or item.get("image")
                if isinstance(url, str) and url:
                    return url
    if isinstance(output, dict):
        url = output.get("url") or output.get("image")
        if isinstance(url, str) and url:
            return url
    return None


class ReplicateImageGenBackend:
    """Client for Replicate's predictions API."""

    id = "imagegen-replicate"
    name = "Replicate"
    deterministic_seed = False  # depends entirely on the upstream model

    capabilities = BackendCapabilities(
        text_to_image=True,
        image_to_image=False,
        inpainting=False,
        controlnet=False,
        lora=False,
        img2img_strength_range=(0.0, 0.0),
        max_resolution=(2048, 2048),
        supports_negative_prompt=True,
        supports_seed=True,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._api_token: str | None = cfg.get("api_token") or None
        self._base_url: str = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self._default_model: str = str(cfg.get("default_model") or DEFAULT_MODEL)
        self._poll_interval: float = float(cfg.get("poll_interval_seconds") or 1.0)
        self._timeout: float = float(cfg.get("timeout_seconds") or 300)
        wait = cfg.get("wait_seconds")
        self._wait_seconds: int = int(wait if wait is not None else 30)
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
            if not self._api_token:
                raise RuntimeError("imagegen-replicate: api_token is not configured")
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - exercised by integration
                raise RuntimeError("httpx not installed; add `httpx` to the plugin's venv") from exc
            headers: dict[str, str] = {
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            }
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=True,
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
        model_ref = request.model or self._default_model
        owner, name, version = _split_model(model_ref)
        if not owner or not name:
            raise RuntimeError(
                f"imagegen-replicate: model {model_ref!r} is not in `owner/name[:version]` form"
            )

        payload: dict[str, Any] = {"input": _build_input(request)}
        if version:
            payload["version"] = version
            submit_path = "/predictions"
        else:
            submit_path = f"/models/{owner}/{name}/predictions"

        headers: dict[str, str] = {}
        if self._wait_seconds > 0:
            headers["Prefer"] = f"wait={self._wait_seconds}"

        t0 = time.perf_counter()
        submit = await client.post(submit_path, json=payload, headers=headers or None)
        if submit.status_code >= 400:
            raise RuntimeError(
                f"imagegen-replicate: {submit_path} returned {submit.status_code}: {submit.text}"
            )
        prediction = submit.json() or {}
        prediction = await self._wait_for_terminal(client, prediction)

        status = str(prediction.get("status") or "")
        if status != "succeeded":
            error = prediction.get("error") or status or "unknown failure"
            raise RuntimeError(f"imagegen-replicate: prediction {status}: {error}")

        url = _first_output_url(prediction.get("output"))
        if not url:
            raise RuntimeError(
                "imagegen-replicate: prediction succeeded but produced no output URL"
            )
        image_response = await client.get(url)
        if image_response.status_code >= 400:
            raise RuntimeError(
                f"imagegen-replicate: image download returned {image_response.status_code}"
            )
        image_bytes = image_response.content

        from grimoire.imagegen.backend import make_thumbnail

        thumbnail_bytes = make_thumbnail(image_bytes)
        used_seed = request.seed if request.seed is not None else 0
        return GenerationResult(
            image_bytes=image_bytes,
            thumbnail_bytes=thumbnail_bytes,
            backend=self.id,
            model=model_ref,
            seed=used_seed,
            actual_params={
                "width": request.width,
                "height": request.height,
                "steps": request.steps,
                "cfg_scale": request.cfg_scale,
                "negative_prompt": request.negative_prompt or "",
                "prediction_id": prediction.get("id"),
                "version": prediction.get("version") or version,
                "output_url": url,
            },
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def list_models(self) -> list[ModelInfo]:
        return list(_KNOWN_MODELS)

    async def list_samplers(self) -> list[str]:
        # Sampler / scheduler enums vary per model on Replicate; the core
        # surface doesn't expose one. Callers pass `request.sampler`
        # through to the model's `scheduler` input when set.
        return []

    async def health_check(self) -> HealthStatus:
        if not self._api_token:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="api_token is not configured",
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
            response = await client.get("/account")
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"replicate unreachable: {exc!r}",
            )
        if response.status_code in (401, 403):
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="replicate rejected api_token",
            )
        if response.status_code >= 500:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"replicate server error: HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"replicate returned HTTP {response.status_code}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message="api_token accepted",
        )

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    async def _wait_for_terminal(self, client: Any, prediction: dict[str, Any]) -> dict[str, Any]:
        status = str(prediction.get("status") or "")
        if status in _TERMINAL_STATUSES:
            return prediction
        get_url = (prediction.get("urls") or {}).get("get")
        prediction_id = prediction.get("id")
        if not get_url and prediction_id:
            get_url = f"{self._base_url}/predictions/{prediction_id}"
        if not get_url:
            raise RuntimeError("imagegen-replicate: prediction has no poll URL")

        deadline = time.monotonic() + self._timeout
        while True:
            response = await client.get(get_url)
            if response.status_code < 400:
                prediction = response.json() or {}
                if str(prediction.get("status") or "") in _TERMINAL_STATUSES:
                    return prediction
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"imagegen-replicate: prediction {prediction_id} did not finish within timeout"
                )
            await asyncio.sleep(self._poll_interval)


__all__ = ["ReplicateImageGenBackend"]
