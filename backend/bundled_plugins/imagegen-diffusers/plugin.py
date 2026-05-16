"""Local HuggingFace `diffusers` image-gen backend.

Loads a `diffusers` pipeline in-process so no external API server is
required. `torch` and `diffusers` are imported lazily on first use so
the plugin can be discovered, listed, and even health-checked without
the heavy dependencies installed (health is reported as
`UNCONFIGURED` in that case, mirroring `embed-sentence-transformers`).

A single pipeline instance is cached per `(model_id, dtype, device)`
key; switching `active_model` in plugin config rebuilds the
container so cached pipelines are released. When a `GenerationRequest`
pins `model`, we cache that pipeline too — flipping back and forth
between two checkpoints reuses both rather than reloading.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
)
from grimoire.types.llm import ModelInfo

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
DEFAULT_SCHEDULER = "DPM++ 2M Karras"


# Curated catalog of popular checkpoints. The picker shows these as
# suggestions; any other HuggingFace id (or local path) can be typed in.
# `family` is informational — it's surfaced in the model `name` so the UI
# can group SDXL/SD3/Flux without parsing the id.
@dataclass(frozen=True)
class _CuratedModel:
    id: str
    family: str
    label: str
    gated: bool = False


CURATED_MODELS: tuple[_CuratedModel, ...] = (
    # SDXL
    _CuratedModel(
        "stabilityai/stable-diffusion-xl-base-1.0", "SDXL", "SDXL base 1.0 (default)"
    ),
    _CuratedModel(
        "stabilityai/stable-diffusion-xl-refiner-1.0", "SDXL", "SDXL refiner 1.0"
    ),
    _CuratedModel("stabilityai/sdxl-turbo", "SDXL", "SDXL Turbo (1-4 step)"),
    _CuratedModel("Lykon/dreamshaper-xl-1-0", "SDXL", "Dreamshaper XL 1.0"),
    _CuratedModel(
        "playgroundai/playground-v2.5-1024px-aesthetic", "SDXL", "Playground v2.5 1024px"
    ),
    # SD 3 / 3.5 — all gated
    _CuratedModel(
        "stabilityai/stable-diffusion-3-medium-diffusers", "SD3", "SD 3 medium", gated=True
    ),
    _CuratedModel(
        "stabilityai/stable-diffusion-3.5-medium", "SD3", "SD 3.5 medium", gated=True
    ),
    _CuratedModel(
        "stabilityai/stable-diffusion-3.5-large", "SD3", "SD 3.5 large", gated=True
    ),
    # Flux
    _CuratedModel("black-forest-labs/FLUX.1-schnell", "Flux", "Flux.1 Schnell (4-step)"),
    _CuratedModel("black-forest-labs/FLUX.1-dev", "Flux", "Flux.1 dev", gated=True),
    # SD 1.5 / 2.1
    _CuratedModel("runwayml/stable-diffusion-v1-5", "SD1.5", "SD 1.5 base"),
    _CuratedModel("stabilityai/stable-diffusion-2-1", "SD2.1", "SD 2.1 base"),
    _CuratedModel("Lykon/dreamshaper-8", "SD1.5", "Dreamshaper 8"),
)


# Sampler-name → (scheduler class attribute, kwargs) mapping. The
# attribute is resolved against `diffusers.schedulers` at swap time so
# we don't import diffusers until needed.
_SCHEDULER_MAP: dict[str, tuple[str, dict[str, Any]]] = {
    "DPM++ 2M Karras": ("DPMSolverMultistepScheduler", {"use_karras_sigmas": True}),
    "DPM++ 2M": ("DPMSolverMultistepScheduler", {}),
    "DPM++ SDE Karras": (
        "DPMSolverSinglestepScheduler",
        {"use_karras_sigmas": True},
    ),
    "Euler a": ("EulerAncestralDiscreteScheduler", {}),
    "Euler": ("EulerDiscreteScheduler", {}),
    "Heun": ("HeunDiscreteScheduler", {}),
    "LMS": ("LMSDiscreteScheduler", {}),
    "DDIM": ("DDIMScheduler", {}),
    "DDPM": ("DDPMScheduler", {}),
    "UniPC": ("UniPCMultistepScheduler", {}),
    "PNDM": ("PNDMScheduler", {}),
    "DEIS": ("DEISMultistepScheduler", {}),
}


class DiffusersImageGenBackend:
    """In-process diffusers pipeline.

    Pipelines are cached by `(model_id, dtype, device)` and reused
    across calls. Generation runs on a worker thread so the event loop
    stays responsive while the GPU is busy.
    """

    id = "imagegen-diffusers"
    name = "Diffusers (local)"
    # Same seed + same scheduler should produce the same image — the
    # generator is seeded explicitly with `torch.Generator.manual_seed`.
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

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._active_model: str = str(
            cfg.get("active_model") or cfg.get("base_model") or DEFAULT_MODEL
        )
        self._device_pref: str = str(cfg.get("device") or "auto")
        self._dtype_pref: str = str(cfg.get("dtype") or "auto")
        self._variant: str | None = cfg.get("variant") or None
        self._default_scheduler: str = str(cfg.get("default_scheduler") or DEFAULT_SCHEDULER)
        self._attention_slicing: bool = bool(cfg.get("attention_slicing", True))
        self._vae_slicing: bool = bool(cfg.get("vae_slicing", True))
        self._vae_tiling: bool = bool(cfg.get("vae_tiling", False))
        self._cpu_offload: bool = bool(cfg.get("cpu_offload", False))
        self._safety_checker: bool = bool(cfg.get("safety_checker", False))
        self._cache_folder: str | None = cfg.get("cache_folder") or None
        self._hf_token: str | None = cfg.get("hf_token") or None
        self._timeout: float = float(cfg.get("timeout_seconds") or 600)

        # Resolved device — set on first load.
        self._device: str | None = None
        # Cache of text-to-image pipelines, keyed by (model, dtype_str).
        self._txt2img_pipelines: dict[tuple[str, str], Any] = {}
        # Image-to-image pipes share weights with their txt2img sibling,
        # so we keep them in a parallel map keyed identically.
        self._img2img_pipelines: dict[tuple[str, str], Any] = {}
        self._load_lock = asyncio.Lock()
        self._load_error: str | None = None

    # ------------------------------------------------------------------ #
    # ImageGenBackend protocol
    # ------------------------------------------------------------------ #

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        model_id = request.model or self._active_model
        seed = (
            request.seed
            if request.seed is not None
            else random.SystemRandom().randint(0, 2**31 - 1)
        )
        sampler = request.sampler or self._default_scheduler
        is_img2img = request.init_image is not None

        pipe = await self._ensure_pipeline(model_id, img2img=is_img2img)
        return await asyncio.wait_for(
            asyncio.to_thread(
                self._generate_sync,
                pipe,
                request,
                model_id=model_id,
                seed=seed,
                sampler=sampler,
                is_img2img=is_img2img,
            ),
            timeout=self._timeout,
        )

    async def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        for entry in CURATED_MODELS:
            suffix = " (gated)" if entry.gated else ""
            models.append(
                ModelInfo(
                    id=entry.id,
                    name=f"{entry.family} · {entry.label}{suffix}",
                )
            )
        if self._active_model and not any(m.id == self._active_model for m in models):
            models.append(ModelInfo(id=self._active_model, name=self._active_model))
        return models

    async def list_samplers(self) -> list[str]:
        return list(_SCHEDULER_MAP.keys())

    async def health_check(self) -> HealthStatus:
        try:
            import diffusers  # noqa: F401
            import torch  # noqa: F401
        except ImportError as exc:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message=(
                    "diffusers/torch not installed; install plugin requirements "
                    f"(missing: {exc.name})"
                ),
            )
        if self._load_error:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=self._load_error,
            )
        loaded = " (loaded)" if self._txt2img_pipelines else ""
        device = self._device or "auto"
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"diffusers ready on {device}{loaded}; model={self._active_model}",
        )

    # ------------------------------------------------------------------ #
    # Pipeline lifecycle
    # ------------------------------------------------------------------ #

    def _resolve_device(self) -> str:
        """Pick a torch device per `_device_pref`.

        `auto` order: CUDA → MPS → CPU. Explicit choices are passed
        through, including ones that may fail at `.to(device)` time
        (we'd rather surface that error than silently downgrade).
        """
        if self._device_pref != "auto":
            return self._device_pref
        import torch

        if torch.cuda.is_available():
            return "cuda"
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return "mps"
        return "cpu"

    def _resolve_dtype(self, device: str) -> tuple[Any, str]:
        """Return (torch dtype, short string) for the loaded pipeline.

        CPU is always float32 — fp16/bf16 on CPU is much slower and the
        VRAM saving is moot. On GPU we honour `_dtype_pref`, defaulting
        to fp16 (CUDA) or bf16 (MPS) when set to "auto".
        """
        import torch

        if device == "cpu":
            return torch.float32, "float32"
        pref = self._dtype_pref
        if pref == "float16":
            return torch.float16, "float16"
        if pref == "bfloat16":
            return torch.bfloat16, "bfloat16"
        if pref == "float32":
            return torch.float32, "float32"
        if device == "mps":
            return torch.bfloat16, "bfloat16"
        return torch.float16, "float16"

    async def _ensure_pipeline(self, model_id: str, *, img2img: bool) -> Any:
        async with self._load_lock:
            device = self._device or self._resolve_device()
            dtype, dtype_str = self._resolve_dtype(device)
            key = (model_id, dtype_str)
            pipelines = self._img2img_pipelines if img2img else self._txt2img_pipelines
            cached = pipelines.get(key)
            if cached is not None:
                return cached
            try:
                pipe = await asyncio.to_thread(
                    self._build_pipeline,
                    model_id=model_id,
                    device=device,
                    dtype=dtype,
                    dtype_str=dtype_str,
                    img2img=img2img,
                )
            except Exception as exc:
                self._load_error = f"failed to load {model_id!r}: {exc}"
                raise
            self._load_error = None
            self._device = device
            pipelines[key] = pipe
            return pipe

    def _build_pipeline(
        self,
        *,
        model_id: str,
        device: str,
        dtype: Any,
        dtype_str: str,
        img2img: bool,
    ) -> Any:
        from diffusers import AutoPipelineForImage2Image, AutoPipelineForText2Image

        kwargs: dict[str, Any] = {"torch_dtype": dtype}
        if self._variant:
            kwargs["variant"] = self._variant
        if self._cache_folder:
            kwargs["cache_dir"] = self._cache_folder
        if self._hf_token:
            kwargs["token"] = self._hf_token
        if not self._safety_checker:
            # Only honoured by pipelines that ship a safety_checker
            # (SD1.5 family). Passing it to SDXL/SD3/Flux is harmless.
            kwargs["safety_checker"] = None
            kwargs["requires_safety_checker"] = False

        # When an img2img pipe is requested and a txt2img pipe is already
        # loaded for the same model+dtype, rewrap it instead of
        # re-downloading and re-instantiating the components.
        sibling = self._txt2img_pipelines.get((model_id, dtype_str))
        if img2img and sibling is not None:
            pipe = AutoPipelineForImage2Image.from_pipe(sibling)
        elif img2img:
            pipe = AutoPipelineForImage2Image.from_pretrained(model_id, **kwargs)
        else:
            pipe = AutoPipelineForText2Image.from_pretrained(model_id, **kwargs)

        # Apply memory-saving switches that don't need a device move.
        if self._attention_slicing:
            try:
                pipe.enable_attention_slicing()
            except Exception:
                logger.debug("pipeline %r doesn't support attention slicing", model_id)
        if self._vae_slicing:
            try:
                pipe.enable_vae_slicing()
            except Exception:
                logger.debug("pipeline %r doesn't support vae slicing", model_id)
        if self._vae_tiling:
            try:
                pipe.enable_vae_tiling()
            except Exception:
                logger.debug("pipeline %r doesn't support vae tiling", model_id)

        if self._cpu_offload:
            try:
                pipe.enable_sequential_cpu_offload()
            except Exception:
                # Some pipelines need `enable_model_cpu_offload` instead;
                # fall back to that rather than failing the whole load.
                try:
                    pipe.enable_model_cpu_offload()
                except Exception:
                    logger.warning(
                        "pipeline %r doesn't support cpu offload; running on %s",
                        model_id,
                        device,
                    )
                    pipe = pipe.to(device)
        else:
            pipe = pipe.to(device)
        return pipe

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #

    def _generate_sync(
        self,
        pipe: Any,
        request: GenerationRequest,
        *,
        model_id: str,
        seed: int,
        sampler: str,
        is_img2img: bool,
    ) -> GenerationResult:
        import torch

        scheduler_name = self._apply_scheduler(pipe, sampler)
        device = self._device or "cpu"
        # MPS doesn't yet support `torch.Generator(device='mps')` —
        # generators always live on CPU there. CPU generators also work
        # everywhere else, but using the matching device generator on
        # CUDA matches the diffusers docs and is what most reference
        # code does, so prefer that when available.
        gen_device = "cpu" if device == "mps" else device
        generator = torch.Generator(device=gen_device).manual_seed(int(seed))

        call_kwargs: dict[str, Any] = {
            "prompt": request.prompt,
            "num_inference_steps": int(request.steps),
            "guidance_scale": float(request.cfg_scale),
            "generator": generator,
        }
        if request.negative_prompt:
            call_kwargs["negative_prompt"] = request.negative_prompt
        if is_img2img:
            from PIL import Image

            init_img = Image.open(io.BytesIO(request.init_image or b"")).convert("RGB")
            init_img = init_img.resize((int(request.width), int(request.height)))
            call_kwargs["image"] = init_img
            if request.init_image_strength is not None:
                call_kwargs["strength"] = float(request.init_image_strength)
        else:
            call_kwargs["width"] = int(request.width)
            call_kwargs["height"] = int(request.height)
        if request.extra:
            call_kwargs.update(dict(request.extra))

        t0 = time.perf_counter()
        out = pipe(**call_kwargs)
        images = getattr(out, "images", None) or []
        if not images:
            raise RuntimeError("diffusers: pipeline returned no images")
        image = images[0]
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        # Imported lazily to avoid pulling Pillow in via the core when
        # the plugin hasn't been activated yet.
        from grimoire.imagegen.backend import make_thumbnail

        thumbnail_bytes = make_thumbnail(image_bytes)
        return GenerationResult(
            image_bytes=image_bytes,
            thumbnail_bytes=thumbnail_bytes,
            backend=self.id,
            model=model_id,
            seed=seed,
            actual_params={
                "width": request.width,
                "height": request.height,
                "steps": request.steps,
                "cfg_scale": request.cfg_scale,
                "sampler": scheduler_name,
                "device": device,
                "dtype": self._dtype_pref,
                "negative_prompt": request.negative_prompt or "",
                "init_image": is_img2img,
            },
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    def _apply_scheduler(self, pipe: Any, sampler: str) -> str:
        """Swap the pipeline's scheduler to match `sampler`.

        Unknown sampler names fall back to the pipeline's existing
        default rather than raising — image generation is more useful
        with a working scheduler than failing on a typo.
        """
        entry = _SCHEDULER_MAP.get(sampler)
        if entry is None:
            return type(pipe.scheduler).__name__
        scheduler_cls_name, kwargs = entry
        try:
            import diffusers.schedulers as schedulers_mod

            scheduler_cls = getattr(schedulers_mod, scheduler_cls_name, None)
            if scheduler_cls is None:
                return type(pipe.scheduler).__name__
            pipe.scheduler = scheduler_cls.from_config(pipe.scheduler.config, **kwargs)
            return sampler
        except Exception:
            logger.warning("failed to apply scheduler %r; using default", sampler)
            return type(pipe.scheduler).__name__


__all__ = ["CURATED_MODELS", "DEFAULT_MODEL", "DEFAULT_SCHEDULER", "DiffusersImageGenBackend"]
