"""ComfyUI HTTP client backend.

Submits an *API-format* workflow JSON to ``/prompt``, polls ``/history``
for the queued prompt id, then downloads the resulting image via
``/view``. Workflow templates are loaded from a configured directory so
new model architectures (which often ship as ComfyUI workflows before
``diffusers`` catches up) can be supported without code changes.

When no workflows directory is configured the plugin falls back to a
minimal SDXL workflow built in-process; it is sufficient for the
conformance suite and small smoke tests.

``httpx`` is imported lazily so the plugin can be discovered without the
optional dependency installed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
)
from grimoire.types.llm import ModelInfo

DEFAULT_BASE_URL = "http://127.0.0.1:8188"

# ComfyUI's "core" samplers (KSampler `sampler_name` enum). Used as a
# fallback when the server's `/object_info` is unavailable.
_FALLBACK_SAMPLERS: tuple[str, ...] = (
    "euler",
    "euler_ancestral",
    "heun",
    "dpm_2",
    "dpm_2_ancestral",
    "lms",
    "dpm_fast",
    "dpm_adaptive",
    "dpmpp_2s_ancestral",
    "dpmpp_sde",
    "dpmpp_2m",
    "dpmpp_2m_sde",
    "ddim",
    "uni_pc",
)


def _minimal_sdxl_workflow() -> dict[str, Any]:
    """Synthesize a minimal SDXL workflow in ComfyUI's API JSON format.

    Used only when the operator hasn't provided a workflows directory.
    Keep node ids stable — :func:`_fill_workflow` looks them up by id.
    """
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "grimoire", "images": ["8", 0]},
        },
    }


def _fill_workflow(
    workflow: dict[str, Any], request: GenerationRequest, seed: int
) -> dict[str, Any]:
    """Mutate a deep copy of ``workflow`` with values from the request.

    Walks the workflow looking for well-known node class types
    (``KSampler``, ``CLIPTextEncode``, ``EmptyLatentImage``,
    ``CheckpointLoaderSimple``) and overrides their inputs. Unknown
    nodes are left untouched so user-authored workflows keep working.
    """
    filled = copy.deepcopy(workflow)
    positive_set = False
    negative_set = False
    sampler = (request.sampler or "euler").lower()
    for node in filled.values():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type")
        inputs = node.setdefault("inputs", {})
        if cls == "KSampler":
            inputs["seed"] = seed
            inputs["steps"] = request.steps
            inputs["cfg"] = request.cfg_scale
            inputs["sampler_name"] = sampler
            if request.init_image_strength is not None:
                inputs["denoise"] = float(request.init_image_strength)
        elif cls == "EmptyLatentImage":
            inputs["width"] = request.width
            inputs["height"] = request.height
            inputs["batch_size"] = 1
        elif cls == "CLIPTextEncode":
            # Convention: first CLIPTextEncode gets the positive prompt,
            # second gets the negative. Workflows that need a different
            # mapping should rename the nodes (the loader leaves anything
            # other than CLIPTextEncode alone).
            if not positive_set:
                inputs["text"] = request.prompt
                positive_set = True
            elif not negative_set:
                inputs["text"] = request.negative_prompt or ""
                negative_set = True
        elif cls == "CheckpointLoaderSimple" and request.model:
            inputs["ckpt_name"] = request.model
    return filled


class ComfyUIImageGenBackend:
    """HTTP client for a ComfyUI server.

    ``workflows_dir`` (optional) lets operators ship per-architecture
    workflow templates. The template selected for a request is, in
    order: ``<workflows_dir>/<request.model>.json`` → ``<workflows_dir>/
    <default_workflow>.json`` → the synthesized minimal workflow.
    """

    id = "imagegen-comfyui"
    name = "ComfyUI"
    deterministic_seed = False  # server-side; depends on sampler + model

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
        self._workflows_dir: Path | None = (
            Path(cfg["workflows_dir"]) if cfg.get("workflows_dir") else None
        )
        self._default_workflow: str = str(cfg.get("default_workflow") or "default")
        self._client_id: str = str(cfg.get("client_id") or f"grimoire-{uuid.uuid4().hex[:8]}")
        self._timeout: float = float(cfg.get("timeout_seconds") or 600)
        self._poll_interval: float = float(cfg.get("poll_interval_seconds") or 1.0)
        self._api_key: str | None = cfg.get("api_key") or None
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
        seed = (
            request.seed
            if request.seed is not None
            else random.SystemRandom().randint(0, 2**31 - 1)
        )
        workflow = _fill_workflow(self._resolve_workflow(request), request, seed)
        payload = {"prompt": workflow, "client_id": self._client_id}

        t0 = time.perf_counter()
        submit = await client.post("/prompt", json=payload)
        if submit.status_code >= 400:
            raise RuntimeError(f"comfyui: /prompt returned {submit.status_code}: {submit.text}")
        body = submit.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"comfyui: /prompt response missing prompt_id: {body!r}")

        history = await self._wait_for_history(client, str(prompt_id))
        outputs = history.get("outputs") or {}
        image_meta = _first_image_output(outputs)
        if image_meta is None:
            raise RuntimeError(f"comfyui: history {prompt_id} produced no images")

        view_response = await client.get("/view", params=image_meta)
        if view_response.status_code >= 400:
            raise RuntimeError(
                f"comfyui: /view returned {view_response.status_code}: {view_response.text}"
            )
        image_bytes = view_response.content

        from grimoire.imagegen.backend import make_thumbnail

        thumbnail_bytes = make_thumbnail(image_bytes)
        return GenerationResult(
            image_bytes=image_bytes,
            thumbnail_bytes=thumbnail_bytes,
            backend=self.id,
            model=request.model or "",
            seed=seed,
            actual_params={
                "width": request.width,
                "height": request.height,
                "steps": request.steps,
                "cfg_scale": request.cfg_scale,
                "sampler": request.sampler or "euler",
                "negative_prompt": request.negative_prompt or "",
                "prompt_id": prompt_id,
                "workflow": self._selected_workflow_name(request),
            },
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def list_models(self) -> list[ModelInfo]:
        try:
            client = await self._ensure_client()
            response = await client.get("/object_info/CheckpointLoaderSimple")
            if response.status_code >= 400:
                return []
            data = response.json() or {}
        except Exception:
            return []
        info = data.get("CheckpointLoaderSimple") or {}
        required = info.get("input", {}).get("required", {})
        ckpt = required.get("ckpt_name")
        # `ckpt_name` is shaped `[[name1, name2, ...], {tooltip: ...}]`.
        if isinstance(ckpt, list) and ckpt and isinstance(ckpt[0], list):
            return [ModelInfo(id=str(name), name=str(name)) for name in ckpt[0]]
        return []

    async def list_samplers(self) -> list[str]:
        try:
            client = await self._ensure_client()
            response = await client.get("/object_info/KSampler")
            if response.status_code >= 400:
                return list(_FALLBACK_SAMPLERS)
            data = response.json() or {}
        except Exception:
            return list(_FALLBACK_SAMPLERS)
        info = data.get("KSampler") or {}
        required = info.get("input", {}).get("required", {})
        sampler_field = required.get("sampler_name")
        if isinstance(sampler_field, list) and sampler_field and isinstance(sampler_field[0], list):
            return [str(name) for name in sampler_field[0]]
        return list(_FALLBACK_SAMPLERS)

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
            response = await client.get("/system_stats")
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"comfyui unreachable at {self._base_url}: {exc!r}",
            )
        if response.status_code >= 500:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"comfyui server error: HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"comfyui returned HTTP {response.status_code}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"comfyui reachable at {self._base_url}",
        )

    # ------------------------------------------------------------------ #
    # Workflow selection
    # ------------------------------------------------------------------ #

    def _selected_workflow_name(self, request: GenerationRequest) -> str:
        if self._workflows_dir and request.model:
            candidate = self._workflows_dir / f"{Path(request.model).stem}.json"
            if candidate.is_file():
                return candidate.stem
        return self._default_workflow

    def _resolve_workflow(self, request: GenerationRequest) -> dict[str, Any]:
        if self._workflows_dir is None:
            return _minimal_sdxl_workflow()
        if request.model:
            candidate = self._workflows_dir / f"{Path(request.model).stem}.json"
            if candidate.is_file():
                return _load_workflow_file(candidate)
        default = self._workflows_dir / f"{self._default_workflow}.json"
        if default.is_file():
            return _load_workflow_file(default)
        return _minimal_sdxl_workflow()

    def list_workflows(self) -> list[str]:
        """Enumerate `*.json` workflow stems available to this backend."""
        if self._workflows_dir is None or not self._workflows_dir.is_dir():
            return []
        return sorted(p.stem for p in self._workflows_dir.glob("*.json"))

    async def _wait_for_history(self, client: Any, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout
        while True:
            response = await client.get(f"/history/{prompt_id}")
            if response.status_code < 400:
                payload = response.json() or {}
                entry = payload.get(prompt_id)
                if isinstance(entry, dict) and entry.get("outputs"):
                    return entry
            if time.monotonic() > deadline:
                raise TimeoutError(f"comfyui: prompt {prompt_id} did not complete within timeout")
            await asyncio.sleep(self._poll_interval)


def _load_workflow_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"comfyui: workflow {path} is not a JSON object")
    return raw


def _first_image_output(outputs: dict[str, Any]) -> dict[str, Any] | None:
    """Return the `{filename, subfolder, type}` triple for the first image."""
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        images = node_output.get("images") or []
        for image in images:
            if not isinstance(image, dict):
                continue
            filename = image.get("filename")
            if not filename:
                continue
            return {
                "filename": str(filename),
                "subfolder": str(image.get("subfolder") or ""),
                "type": str(image.get("type") or "output"),
            }
    return None


__all__ = ["ComfyUIImageGenBackend"]
