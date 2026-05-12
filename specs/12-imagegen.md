# 12 — ImageGen

## Purpose

ImageGen generates illustrations for scenes, characters, locations, and items. It runs as a producer: the Orchestrator emits "should illustrate" decisions; ImageGen queues and processes jobs; the Frontend displays results inline or in a gallery.

The default backend is **integrated diffusers** — a core implementation using HuggingFace's `diffusers` library, shipping with the app and working out of the box on first run (downloads SDXL or a similar checkpoint on first use). Alternative backends (Automatic1111, ComfyUI, DALL-E, others) are **plugins** — see `15-plugins.md`.

The same backend protocol applies to the core implementation and to plugins; the only difference is where the code lives.

## Responsibilities

- Decide when to generate (every scene, every N posts, never, manual only — per-campaign)
- Compose prompts from scene context, character cards, location features, image preset
- Maintain a generation queue
- Dispatch to the active backend (integrated diffusers by default; plugin if configured)
- Maintain image metadata files (`.yaml` sidecars per image) and SQLite index
- Re-roll, variation, manual prompt editing, user star/save
- Caching: never regenerate the same (prompt, seed, model) twice
- Streaming progress updates to the Frontend
- Health monitoring of backends

## Non-responsibilities

- Does not decide narrative scene boundaries (Scene Manager does)
- Does not narrate (the LLM does; ImageGen consumes the result)
- Does not store image bytes (the filesystem does, at `data/campaigns/<id>/images/`)
- Does not author image presets (the user does, via the Library)
- Does not implement specific backends as plugins (it implements the integrated one and exposes a protocol that plugins also implement)

## Backend protocol

The integrated `diffusers` backend and all plugin backends implement the same protocol:

```python
class ImageGenBackend(Protocol):
    id: str
    name: str
    capabilities: BackendCapabilities

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
    async def list_models(self) -> list[ModelInfo]: ...
    async def list_samplers(self) -> list[str]: ...
    async def health_check(self) -> HealthStatus: ...

@dataclass
class BackendCapabilities:
    text_to_image: bool
    image_to_image: bool
    inpainting: bool
    controlnet: bool
    lora: bool
    img2img_strength_range: tuple[float, float]
    max_resolution: tuple[int, int]
    supports_negative_prompt: bool
    supports_seed: bool

@dataclass
class GenerationRequest:
    prompt: str
    negative_prompt: Optional[str]
    width: int
    height: int
    steps: int
    cfg_scale: float
    sampler: str
    seed: Optional[int]
    model: Optional[str]
    init_image: Optional[bytes]
    init_image_strength: Optional[float]
    loras: list[LoraSpec]
    extra: dict                            # backend-specific extras

@dataclass
class GenerationResult:
    image_bytes: bytes
    thumbnail_bytes: bytes
    backend: str
    model: str
    seed: int
    actual_params: dict
    duration_ms: int
    error: Optional[str]
```

## Integrated diffusers backend

The default. Lives in the app's core (not in `data/plugins/`). On first launch:

1. If `HF_HUB_CACHE` is empty or missing the configured base model, prompt the user: "Download SDXL Base 1.0 (~6 GB) to enable image generation?"
2. On accept, download to a known cache; report progress
3. Subsequent generations use the cached weights

Implementation sketch:

```python
class IntegratedDiffusersBackend:
    id = "diffusers"
    name = "Integrated (diffusers)"

    def __init__(self, config: dict):
        import torch
        from diffusers import StableDiffusionXLPipeline
        self.device = config.get("device", "cuda" if torch.cuda.is_available() else "cpu")
        self.base_model = config.get("base_model", "stabilityai/stable-diffusion-xl-base-1.0")
        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            self.base_model,
            torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
        ).to(self.device)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        # Run in thread pool to avoid blocking the event loop
        return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request):
        result = self.pipe(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            width=request.width,
            height=request.height,
            num_inference_steps=request.steps,
            guidance_scale=request.cfg_scale,
            generator=torch.Generator(device=self.device).manual_seed(request.seed),
        )
        return GenerationResult(
            image_bytes=...,
            backend=self.id,
            ...
        )
```

GPU acceleration is automatic if available; falls back to CPU (slow) otherwise. The user can configure a different base model if their machine can't handle SDXL.

## Plugin backends (alternatives)

Configurable, drop-in plugins. Bundled:

- `imagegen-a1111` — HTTP client for Automatic1111 webui
- `imagegen-comfyui` — HTTP client for ComfyUI; includes workflow loaders for new model architectures the integrated diffusers backend doesn't yet support
- `imagegen-dalle` — Adapter for OpenAI's DALL-E API
- `imagegen-replicate` — Adapter for Replicate-hosted models

Custom plugins (Stability, Midjourney via unofficial API, etc.) follow the same protocol.

ComfyUI specifically: when a new image model architecture lands and ComfyUI's loader supports it before `diffusers` catches up, the comfyui plugin is the escape valve. Once `diffusers` adds support, the integrated backend can take over.

## Prompt composition

Per-campaign, per-illustration, ImageGen builds a prompt:

```
[image preset style preamble]
[location features]
[present cast, with their per-character image template]
[scene-specific elements from the LLM response]
[mood / atmosphere from setting + scene]
[negative prompt from image preset and per-character templates]
```

Image presets and per-character image templates are library content. Per-scene composition happens at request time.

```python
async def compose_prompt(self, scene_id: str, post_id: Optional[str] = None) -> ComposedPrompt:
    scene = await self.scene_manager.get_scene(scene_id)
    preset = await self.library.get_image_preset(scene.image_preset_id or campaign_default)
    location = await self.setting.resolve(scene.location_ref, scene.campaign_id)
    present = [await self.characters.resolve(ref, scene.campaign_id) for ref in scene.present_character_refs]

    prompt_parts = [preset.style_preamble]
    prompt_parts.append(location.visual_description)
    for char in present:
        prompt_parts.append(char.image.base_prompt)
    if post_id:
        scene_details = await self._extract_visual_elements(post_id)
        prompt_parts.extend(scene_details)
    prompt_parts.append(self._compose_mood(scene))

    return ComposedPrompt(
        prompt=", ".join(prompt_parts),
        negative_prompt=self._compose_negative(preset, present),
        params=preset.default_params,
    )
```

## Job queue

Generations are jobs in a queue:

```python
@dataclass
class GenerationJob:
    id: str
    campaign_id: str
    scene_id: Optional[str]
    post_id: Optional[str]
    request: GenerationRequest
    priority: int                         # higher = sooner
    backend: str
    status: JobStatus                     # 'queued', 'running', 'complete', 'failed', 'cancelled'
    queued_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    result: Optional[GenerationResult]
    error: Optional[str]
```

The queue is processed sequentially per backend (most backends can't handle parallel generations well). Multiple backends can run in parallel.

UI shows the queue; user can cancel pending jobs or re-prioritize.

## When to generate

Per-campaign config:

```yaml
imagegen:
  trigger_mode: per_scene                 # per_scene | per_post | every_n_posts | manual_only
  trigger_n: 5                            # if every_n_posts
  trigger_on_scene_open: true
  trigger_on_new_location: true
  trigger_on_new_character_appearance: true
  auto_illustrate_during_combat: false
```

The Orchestrator emits `should_illustrate(scene_id, post_id)` checks; ImageGen decides based on config and queues a job.

## Image storage

Generated images live at `data/campaigns/<id>/images/<image-id>.png` with a sidecar `<image-id>.yaml`:

```yaml
id: img-0047
scene_id: 0003-chase-through-soho
post_id: post-0028
prompt: "..."
negative_prompt: "..."
seed: 49271
sampler: "DPM++ 2M Karras"
steps: 28
cfg_scale: 6.5
backend: diffusers
model: stabilityai/stable-diffusion-xl-base-1.0
created_at: 2026-05-11T22:14:00
duration_ms: 12450
user_starred: false
tags: [scene-establishing, action]
```

The SQLite `images` table indexes them (see `03-state-store.md`).

Thumbnails generated alongside (256×256, JPG) for gallery use.

## Caching

`(prompt_hash, negative_hash, params_hash, seed, model)` is a cache key. Repeating the exact same generation returns the cached image without re-running the backend. Random-seed generations bypass cache.

## Re-roll and variation

User can re-roll an image:
- Same prompt, new seed → new image
- Tweak prompt → new image
- Strength-adjusted img2img from the current image → variation

User can edit the prompt of a saved image, regenerate, save as new (the old image persists with its own metadata).

## Per-character canonical seed

Each character's library card has an optional `canonical_seed`. When this character is in an image, the prompt + seed produces a consistent face/look across scenes. Users can override per image.

## Health and fallback

Active backends run `health_check()` periodically. If the primary backend is unhealthy (e.g., A1111 server down), ImageGen falls back to the configured alternate (the integrated `diffusers` backend if installed). If no alternate is available, jobs queue with a warning.

## Interface

```python
class ImageGen(Protocol):
    # Backend management
    async def list_backends(self) -> list[BackendInfo]: ...
    async def active_backend(self, campaign_id: str) -> BackendInfo: ...
    async def set_active_backend(self, campaign_id: str, backend_id: str) -> None: ...

    # Generation
    async def queue_generation(
        self,
        campaign_id: str,
        scene_id: Optional[str],
        post_id: Optional[str],
        request: Optional[GenerationRequest] = None,    # if None, compose from scene
        priority: int = 5,
    ) -> str:                                          # job id
        ...

    async def generate_sync(self, campaign_id: str, request: GenerationRequest) -> GenerationResult: ...

    # Queue
    async def list_jobs(self, campaign_id: str, status: Optional[JobStatus] = None) -> list[GenerationJob]: ...
    async def cancel_job(self, job_id: str) -> None: ...
    async def prioritize_job(self, job_id: str, priority: int) -> None: ...

    # Re-roll / variation
    async def reroll(self, image_id: str) -> str: ...   # new job id
    async def variation(self, image_id: str, strength: float) -> str: ...

    # Storage
    async def list_images(
        self,
        campaign_id: str,
        scene_id: Optional[str] = None,
        starred_only: bool = False,
    ) -> list[ImageMetadata]: ...
    async def get_image(self, image_id: str) -> ImageMetadata: ...
    async def star_image(self, image_id: str, starred: bool) -> None: ...
    async def delete_image(self, image_id: str) -> None: ...

    # Health
    async def health_check(self, backend_id: str) -> HealthStatus: ...
```

## Events emitted

- `imagegen_job_queued` — new job added
- `imagegen_job_started` — backend started generating
- `imagegen_progress` — periodic progress updates (for backends that report)
- `image_ready` — image saved, sidecar written, indexed
- `imagegen_job_failed` — error; reason included
- `imagegen_backend_health_changed`

## Configuration

```yaml
imagegen:
  default_backend: diffusers              # 'diffusers' for integrated; or a plugin id

  diffusers:
    device: auto                          # 'auto' | 'cuda' | 'cpu' | 'mps'
    base_model: stabilityai/stable-diffusion-xl-base-1.0
    hf_hub_cache: ~/.cache/huggingface
    download_on_first_use: prompt         # 'prompt' | 'auto' | 'never'
    half_precision: true                  # fp16 on GPU

  queue:
    max_concurrent_per_backend: 1
    persist_pending: true

  caching:
    enabled: true
    cache_dir: ./data/cache/imagegen

  thumbnails:
    size: [256, 256]
    format: jpg
    quality: 85

  triggers:
    default_mode: per_scene
    every_n_posts_default: 5

  storage:
    image_format: png
    save_metadata_sidecar: true
```

## Open questions (deferred)

- **Controlnet for character pose consistency.** Useful but heavy; v2.
- **Inpainting via UI.** Edit a region of an image. v2.
- **LoRA management.** First-class browser/installer for LoRAs. v2.
- **Character consistency models.** IP-Adapter, Textual Inversion, custom training. Beyond v1 scope.
- **Animation / video.** Out of scope.
- **NSFW handling.** Some backends gate-keep; user-configurable safety with appropriate defaults. v2.
- **Bulk regeneration.** "Regenerate all images in this scene with the new preset." Useful, low priority.
- **Pre-warming the integrated backend.** Loading SDXL takes time; do it on app start or lazily?  Lazily by default; user can pre-warm in settings.
