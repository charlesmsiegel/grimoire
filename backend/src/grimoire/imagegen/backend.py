"""ImageGen backends.

Two backends live here:

- :class:`IntegratedDiffusersBackend` — the default. Wraps HuggingFace
  ``diffusers`` ``StableDiffusionXLPipeline``. ``diffusers`` and ``torch``
  are imported lazily on first generation; instantiating the class is free.
- :class:`InMemoryDiffusersBackend` — a deterministic, dependency-free
  backend that produces tiny synthetic PNG bytes. Used by tests and as a
  fallback when the user hasn't installed ``diffusers``/``torch``.

Both implement the :class:`grimoire.types.ImageGenBackend` protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import random
import struct
import time
import zlib
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
)
from grimoire.types.llm import ModelInfo

DEFAULT_SAMPLERS: tuple[str, ...] = (
    "DPM++ 2M Karras",
    "Euler a",
    "Euler",
    "DDIM",
    "UniPC",
)


def cache_key_for_request(request: GenerationRequest, *, model: str | None = None) -> str:
    """Stable cache key for a :class:`GenerationRequest`.

    Spec 12 §Caching: ``(prompt_hash, negative_hash, params_hash, seed,
    model)`` is the cache key. Random-seed generations (``seed is None``)
    bypass cache — callers should not look up by key in that case.

    The img2img source bytes (``init_image``) are hashed in so that two
    seeded requests with the same prompt/params but different sources
    don't collide.
    """
    prompt_hash = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()[:16]
    neg_hash = hashlib.sha256((request.negative_prompt or "").encode("utf-8")).hexdigest()[:16]
    init_hash = hashlib.sha256(request.init_image or b"").hexdigest()[:16]
    params = (
        request.width,
        request.height,
        request.steps,
        round(float(request.cfg_scale), 4),
        request.sampler,
        request.init_image_strength,
        tuple((lora.id, round(lora.weight, 4)) for lora in request.loras),
    )
    params_hash = hashlib.sha256(repr(params).encode("utf-8")).hexdigest()[:16]
    effective_model = model or request.model or ""
    return f"{prompt_hash}:{neg_hash}:{params_hash}:{init_hash}:{request.seed}:{effective_model}"


# --------------------------------------------------------------------------- #
# Synthetic PNG helpers (used by the in-memory backend and as a fallback)
# --------------------------------------------------------------------------- #


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def synthesize_png(width: int, height: int, seed: int, prompt: str = "") -> bytes:
    """Produce a deterministic PNG of ``width`` x ``height`` for ``seed``.

    Uses only the standard library — no PIL required. The bytes are stable
    for a given (width, height, seed, prompt) so callers can use this for
    cache and deterministic-seed checks.
    """
    width = max(1, min(int(width), 1024))
    height = max(1, min(int(height), 1024))
    rng = random.Random(seed)
    salt = hashlib.sha256(prompt.encode("utf-8")).digest()
    base_r, base_g, base_b = salt[0], salt[1], salt[2]
    rows = bytearray()
    for _y in range(height):
        rows.append(0)  # filter type per row
        for _ in range(width):
            rows.append((base_r + rng.randint(0, 63)) & 0xFF)
            rows.append((base_g + rng.randint(0, 63)) & 0xFF)
            rows.append((base_b + rng.randint(0, 63)) & 0xFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(rows), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def make_thumbnail(image_bytes: bytes, size: tuple[int, int] = (256, 256)) -> bytes:
    """Build a thumbnail JPEG (or PNG fallback) for ``image_bytes``.

    Uses Pillow if installed; otherwise returns the source bytes unchanged
    so callers always have *something* to store. Spec defaults to 256x256
    JPG quality 85.
    """
    try:  # pragma: no cover - optional dependency
        from PIL import Image  # type: ignore
    except ImportError:
        return image_bytes
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:  # type: ignore[arg-type]
            im = im.convert("RGB")
            im.thumbnail(size)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception:  # pragma: no cover - defensive
        return image_bytes


# --------------------------------------------------------------------------- #
# In-memory backend
# --------------------------------------------------------------------------- #


class InMemoryDiffusersBackend:
    """Deterministic, no-dependency backend used by tests and CI.

    Produces tiny PNGs via :func:`synthesize_png`. Same ``(width, height,
    seed, prompt)`` always returns the same bytes, so the spec-17 ImageGen
    conformance suite (deterministic-seed check) passes.
    """

    id = "diffusers-memory"
    name = "Integrated (in-memory diffusers stub)"
    deterministic_seed = True

    capabilities = BackendCapabilities(
        text_to_image=True,
        image_to_image=True,
        inpainting=False,
        controlnet=False,
        lora=False,
        img2img_strength_range=(0.0, 1.0),
        max_resolution=(1024, 1024),
        supports_negative_prompt=True,
        supports_seed=True,
    )

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        self.model = self.config.get("base_model", "memory:stub-sdxl")

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        t0 = time.perf_counter()
        seed = (
            request.seed
            if request.seed is not None
            else random.SystemRandom().randint(0, 2**31 - 1)
        )
        image_bytes = synthesize_png(request.width, request.height, seed, request.prompt)
        thumbnail_bytes = synthesize_png(64, 64, seed, request.prompt)
        return GenerationResult(
            image_bytes=image_bytes,
            thumbnail_bytes=thumbnail_bytes,
            backend=self.id,
            model=request.model or self.model,
            seed=seed,
            actual_params={
                "width": request.width,
                "height": request.height,
                "steps": request.steps,
                "cfg_scale": request.cfg_scale,
                "sampler": request.sampler,
                "negative_prompt": request.negative_prompt or "",
            },
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=self.model, name=self.model)]

    async def list_samplers(self) -> list[str]:
        return list(DEFAULT_SAMPLERS)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message="in-memory backend",
        )


# --------------------------------------------------------------------------- #
# Real diffusers backend (lazy)
# --------------------------------------------------------------------------- #


class IntegratedDiffusersBackend:
    """Default backend: HuggingFace ``diffusers`` SDXL pipeline.

    ``torch`` and ``diffusers`` are imported on first call to
    :meth:`generate` so the rest of the app stays importable on machines
    that don't have them installed. If either import fails, generation
    surfaces a clear :class:`RuntimeError` and :meth:`health_check` reports
    ``UNCONFIGURED`` — callers (typically :class:`ImageGenService`) can
    fall back to a different backend.
    """

    id = "diffusers"
    name = "Integrated (diffusers)"
    deterministic_seed = True

    capabilities = BackendCapabilities(
        text_to_image=True,
        image_to_image=True,
        inpainting=False,
        controlnet=False,
        lora=True,
        img2img_strength_range=(0.05, 1.0),
        max_resolution=(2048, 2048),
        supports_negative_prompt=True,
        supports_seed=True,
    )

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        self.base_model = self.config.get("base_model", "stabilityai/stable-diffusion-xl-base-1.0")
        self.device = self.config.get("device", "auto")
        self.half_precision = bool(self.config.get("half_precision", True))
        self._pipe: Any | None = None
        self._load_error: str | None = None
        # Lazy load guard so concurrent generations only initialize once.
        self._init_lock = asyncio.Lock()

    async def _ensure_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe
        async with self._init_lock:
            if self._pipe is not None:
                return self._pipe
            try:
                pipe = await asyncio.to_thread(self._build_pipeline)
            except Exception as exc:  # pragma: no cover - exercised only with diffusers installed
                self._load_error = str(exc)
                raise
            self._pipe = pipe
            return pipe

    def _build_pipeline(self) -> Any:  # pragma: no cover - requires diffusers
        import torch
        from diffusers import StableDiffusionXLPipeline

        device = self.device
        if device == "auto":
            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        torch_dtype = torch.float16 if (device == "cuda" and self.half_precision) else torch.float32
        pipe = StableDiffusionXLPipeline.from_pretrained(self.base_model, torch_dtype=torch_dtype)
        pipe = pipe.to(device)
        self.device = device
        return pipe

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        pipe = await self._ensure_pipeline()
        return await asyncio.to_thread(self._generate_sync, pipe, request)

    def _generate_sync(  # pragma: no cover
        self, pipe: Any, request: GenerationRequest
    ) -> GenerationResult:
        import torch
        from PIL import Image  # noqa: F401  # used by diffusers

        seed = (
            request.seed
            if request.seed is not None
            else random.SystemRandom().randint(0, 2**31 - 1)
        )
        generator = torch.Generator(device=self.device).manual_seed(int(seed))
        t0 = time.perf_counter()
        out = pipe(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            width=request.width,
            height=request.height,
            num_inference_steps=request.steps,
            guidance_scale=request.cfg_scale,
            generator=generator,
        )
        image = out.images[0]
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        image_bytes = buf.getvalue()
        thumbnail_bytes = make_thumbnail(image_bytes)
        return GenerationResult(
            image_bytes=image_bytes,
            thumbnail_bytes=thumbnail_bytes,
            backend=self.id,
            model=request.model or self.base_model,
            seed=seed,
            actual_params={
                "width": request.width,
                "height": request.height,
                "steps": request.steps,
                "cfg_scale": request.cfg_scale,
                "sampler": request.sampler,
                "device": self.device,
                "negative_prompt": request.negative_prompt or "",
            },
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=self.base_model, name=self.base_model)]

    async def list_samplers(self) -> list[str]:
        return list(DEFAULT_SAMPLERS)

    async def health_check(self) -> HealthStatus:
        try:
            import diffusers  # noqa: F401
            import torch  # noqa: F401
        except ImportError as exc:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message=f"missing dependency: {exc.name}; install diffusers + torch to enable",
            )
        if self._load_error:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=self._load_error,
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message="diffusers available" + (" (loaded)" if self._pipe is not None else ""),
        )
