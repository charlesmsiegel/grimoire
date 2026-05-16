# ImageGen — Design (Shipped)

> Captures the ImageGen design as actually built. The matching "remaining"
> spec at `2026-05-16-imagegen-remaining-design.md` covers everything from
> the original `specs/12-imagegen.md` that did **not** land in this work.

**Commit:** `2cf2232` — "Build ImageGen core with integrated diffusers backend (task 23)" (followed by `4cada95`, `4faf5e7`, `e13de4a`, `8d80e72`)
**Module:** `backend/src/grimoire/imagegen/`
**Tests:** `backend/tests/imagegen/test_service.py`, `test_prompt.py`, `test_backend.py`
**Bundled backend plugins:** `backend/bundled_plugins/{imagegen-diffusers, imagegen-a1111, imagegen-comfyui, imagegen-dalle}`

## Purpose

ImageGen turns "produce an illustration" requests into PNG bytes on disk plus
an indexed metadata row. The Orchestrator (or any other caller) hands it a
campaign / scene / post triple; ImageGen composes a prompt from library +
world + character data, dispatches the work to a registered backend, persists
the result, and announces it on the event bus. Backends are pluggable behind
one protocol — the same one is implemented by an in-memory test backend, a
real local `diffusers` pipeline, and HTTP adapters for Automatic1111,
ComfyUI, and OpenAI DALL-E.

## Module surface

```
backend/src/grimoire/imagegen/
├── __init__.py     # public re-exports
├── backend.py      # IntegratedDiffusersBackend, InMemoryDiffusersBackend,
│                   # cache_key_for_request, synthesize_png, make_thumbnail
├── prompt.py       # PromptComposer + compose_* helpers, extract_visual_elements
└── service.py      # ImageGenService, BackendRegistry, TriggerConfig,
                    # should_illustrate, NoBackendAvailableError
```

`ImageGenService` (`service.py:228`) is constructed with:

- `store: StateStore` — used for the `images` SQL index, the
  `data/campaigns/<id>/images/` filesystem layout, and the `campaigns` /
  `posts` rows used during prompt composition
- `registry: BackendRegistry` — holds the integrated backend(s) plus any
  plugin-registered ones, keyed by `backend.id`
- `default_backend_id: str | None` — `None` is legal; routes that need a
  backend raise `NoBackendAvailableError` so the API can translate to 503
- `event_bus: EventBus | None` — optional; emission is silently skipped if
  absent
- `composer: PromptComposer | None` — optional; when omitted callers must
  supply a fully formed `GenerationRequest`
- `plugin_backend_ids: Iterable[str] | None` — backend ids known to be
  plugin-sourced (used only to populate `BackendInfo.is_integrated` and
  `plugin_id`)
- `thumbnail_subdir: str = "thumbnails"`

## Public API

```python
class ImageGenService:
    # Backend management
    async def list_backends() -> list[BackendInfo]
    async def active_backend(campaign_id) -> BackendInfo
    async def set_active_backend(campaign_id, backend_id) -> None

    # Generation
    async def queue_generation(campaign_id, scene_id, post_id,
                               request=None, priority=5) -> str  # job id
    async def generate_sync(campaign_id, request) -> GenerationResult

    # Queue
    async def list_jobs(campaign_id, status=None) -> list[GenerationJob]
    async def cancel_job(job_id) -> None
    async def prioritize_job(job_id, priority) -> None

    # Re-roll / variation
    async def reroll(image_id) -> str                          # new job id
    async def variation(image_id, strength) -> str             # new job id

    # Storage
    async def list_images(campaign_id, scene_id=None, starred_only=False)
    async def get_image(image_id) -> ImageMetadata
    async def star_image(image_id, starred) -> None
    async def delete_image(image_id) -> None

    # Health
    async def health_check(backend_id) -> HealthStatus

    # Lifecycle
    async def aclose() -> None                                 # drains queues
```

Matches `grimoire.types.protocols.ImageGen` (`types/protocols.py:957`).

## Backend protocol

A backend is any duck-typed object exposing:

```python
class ImageGenBackend(Protocol):  # types/protocols.py:254
    id: str
    name: str
    capabilities: BackendCapabilities  # de-facto required; defaults applied if missing

    async def generate(request: GenerationRequest) -> GenerationResult
    async def list_models() -> list[ModelInfo]
    async def list_samplers() -> list[str]
    async def health_check() -> HealthStatus
```

Backends may set `deterministic_seed: bool` as an informational class
attribute (used by the spec-17 conformance suite). `BackendCapabilities`,
`GenerationRequest`, `GenerationResult`, `GenerationJob`, `JobStatus`,
`ImageMetadata`, `LoraSpec`, and `BackendInfo` all live in
`backend/src/grimoire/types/imagegen.py` as pydantic models.

## Backends in this tree

| id                    | Location                                 | Notes |
| --------------------- | ---------------------------------------- | ----- |
| `diffusers-memory`    | `imagegen/backend.py` `InMemoryDiffusersBackend` | Deterministic, stdlib-only PNG synth (`synthesize_png`); used by tests and as a CI-safe fallback. |
| `diffusers`           | `imagegen/backend.py` `IntegratedDiffusersBackend` | Default integrated SDXL pipeline. `torch` + `diffusers` imported lazily; `health_check` returns `UNCONFIGURED` until both are installed. |
| `imagegen-diffusers`  | `bundled_plugins/imagegen-diffusers/plugin.py` `DiffusersImageGenBackend` | Richer plugin variant: curated SDXL/SD3/Flux/SD1.5 catalog, per-`(model, dtype)` pipeline cache, scheduler swap table, attention/VAE/CPU-offload knobs, optional HF token. |
| `imagegen-a1111`      | `bundled_plugins/imagegen-a1111/plugin.py` `A1111ImageGenBackend` | HTTP client for `/sdapi/v1/txt2img` & `/img2img`; introspects `/sd-models` and `/samplers`. `httpx` lazy. |
| `imagegen-comfyui`    | `bundled_plugins/imagegen-comfyui/plugin.py` | ComfyUI workflow loader; the "escape valve" for model architectures `diffusers` doesn't support yet. |
| `imagegen-dalle`      | `bundled_plugins/imagegen-dalle/plugin.py` | OpenAI DALL-E adapter; no seed/sampler support — `list_samplers()` returns `[]`. |

The `imagegen-replicate` backend mentioned in the original spec is not yet
implemented.

`main.py` wires `ImageGenService` with an **empty** `BackendRegistry` and
`default_backend_id=None` (`main.py:144-155`). Backends only become live
once a plugin is activated through the Plugins module and its backend is
registered via `BackendRegistry.register(...)`. The integrated
`InMemoryDiffusersBackend` / `IntegratedDiffusersBackend` classes ship for
tests and for future autoloading, but the runtime container does not pre-
register them today.

## Prompt composition

`PromptComposer.compose(...)` (`prompt.py:184`) follows the spec-12
ordering — preset preamble → location visual description → present
characters' `image.base_prompt`s → scene-specific visual sentences pulled
from the most recent post → mood. Every collaborator (scene manager,
library, world, characters) is optional; missing ones drop their
contribution silently so early integrations work before all modules are
wired up.

- The actual string assembly goes through the `imagegen_positive` and
  `imagegen_negative` Jinja templates (the `grimoire.templates` registry),
  so modders can re-shape the output without forking Python.
- Visual elements are pulled from the post body by
  `extract_visual_elements(...)` — a small sensory-keyword heuristic
  (`see`, `stood`, `dress`, `rain`, …) that returns at most three
  sentences. It's a placeholder for richer Extractor output.
- `ComposedPrompt` carries the rendered `prompt`, `negative_prompt`,
  `params` dict (from the preset's `default_params`), and the ordered
  `parts` fragments for callers that want to inspect/re-render.

`ImageGenService._compose_request(...)` is the bridge between
`queue_generation(request=None, …)` callers and a fully built
`GenerationRequest`: it resolves the campaign's `image_preset_id` from the
`campaigns` table, fetches the post body when given a `post_id`, asks the
composer for a `ComposedPrompt`, and slots the resulting prompt + preset
params into a `GenerationRequest` with sensible defaults (`1024×1024`,
`28 steps`, `cfg_scale=6.5`, sampler `"DPM++ 2M Karras"`).

## Trigger policy

`TriggerConfig` + `should_illustrate(...)` (`service.py:62-114`) live in the
service module as a pure function so the Orchestrator (or any other caller)
can apply the per-campaign policy without instantiating an ImageGen. Modes:
`per_scene`, `per_post`, `every_n_posts`, `manual_only`. Combat suppresses
auto-generation unless `auto_during_combat=True`. The Orchestrator is **not
yet wired** to consult this — see the remaining doc §1.

## Job queue

Each registered backend gets its own `_BackendHandle` (`service.py:131`)
holding an `asyncio.Queue[_QueueEntry]` and a dedicated worker task created
via `asyncio.create_task` on first `_ensure_handle(...)` call. The worker
loop (`_worker`, `service.py:658`) processes entries serially per backend;
different backends run in parallel.

Job lifecycle:

1. `queue_generation(...)` validates `campaign_id` (`_SAFE_CAMPAIGN_ID`
   regex at `service.py:188`), composes a request if needed, creates a
   `GenerationJob(status=QUEUED)`, stashes it in `_jobs`, marks it pending
   on the handle, drops a `_QueueEntry` on the queue, emits
   `imagegen_job_queued`.
2. Worker pulls the entry, skips it if the job was cancelled before run,
   otherwise flips to `RUNNING`, emits `imagegen_job_started`.
3. `_run_job(...)` checks the cache. On a hit with a real (non-`_inline_`)
   image id it emits `image_ready(cached=True)` and returns the cached
   `GenerationResult` without re-rendering. Cancelled mid-cache-check?
   Return without emitting.
4. Otherwise calls `backend.generate(request)`. If the job was cancelled
   while the backend was running, skip persistence + `image_ready` so the
   caller doesn't see a finished image they explicitly cancelled.
5. On success, `_persist_result(...)` writes the PNG, the JPG thumbnail,
   the YAML sidecar, and the SQLite row; the result is stored in the
   cache; `image_ready(cached=False)` is emitted.
6. On exception, `_emit("imagegen_job_failed", reason=str(exc))` unless
   the job has already been flipped to `CANCELLED` by a concurrent
   `cancel_job(...)`.

`cancel_job` flips status to `CANCELLED` and removes the id from
`handle._pending_jobs` so a still-queued entry is skipped at worker pickup.
A `RUNNING` job cannot be hard-aborted — the backend's `generate` call
runs to completion, but the result is dropped on the floor.

`prioritize_job` mutates the existing `GenerationJob` in place; the dict
entry is never swapped because the worker holds a local reference across
its `await _run_job` (`service.py:425-433`).

## Caching

`cache_key_for_request(...)` (`backend.py:43`) produces a stable key from
`prompt`, `negative_prompt`, params (`width, height, steps, cfg_scale,
sampler, init_image_strength, sorted loras`), the `init_image` bytes,
`seed`, and the effective model id. `ImageGenService._cache_key(...)`
prepends `campaign_id|` so a hit in campaign A can never be returned to
campaign B (`service.py:615`).

Random-seed requests (`seed is None`) bypass the cache entirely on both
read and write — there's nothing to dedupe against.

Two caches are tracked side by side: `_cache: dict[cache_key, image_id]`
and `_results: dict[image_id, GenerationResult]`. Synchronous
`generate_sync` calls that hit a request the queue path never persisted
still get their result back via an `_inline_*` synthetic id, which is
deliberately filtered out of `image_ready` cache-hit handling in
`_run_job` so the worker doesn't claim a persisted-image hit for a
result that only ever lived in memory.

## Storage layout

For an image `image_id`:

- `data/campaigns/<campaign_id>/images/<image_id>.png` — the image bytes
- `data/campaigns/<campaign_id>/images/<image_id>.yaml` — the sidecar
  (via `state_store.paths.image_metadata_path`)
- `data/campaigns/<campaign_id>/images/thumbnails/<image_id>.jpg` — the
  thumbnail (256×256 JPEG quality 85 via Pillow, or original bytes if
  Pillow isn't installed)
- `images` row in SQLite — `migrations/006_images.sql`:

```sql
CREATE TABLE images (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, branch_id TEXT NOT NULL,
  scene_id TEXT REFERENCES scenes(id), post_id TEXT REFERENCES posts(id),
  file_path TEXT NOT NULL, thumbnail_path TEXT, prompt TEXT,
  negative_prompt TEXT, params TEXT, backend TEXT, model TEXT,
  seed INTEGER, created_at TEXT, user_starred INTEGER NOT NULL DEFAULT 0,
  tags TEXT
);
```

`_persist_result(...)` re-runs `_validate_campaign_id` and re-checks that
the resolved campaign directory is under `data_root` even though
`queue_generation` already validated — defense-in-depth right next to the
write.

`delete_image(...)` removes the SQL row, the PNG, the thumbnail, the
sidecar, and the `campaign_content_index` entry; each filesystem
operation is best-effort with a `WARNING` log on failure.

## Re-roll and variation

- `reroll(image_id)` → rebuilds a `GenerationRequest` from the stored
  metadata via `_request_from_metadata(..., new_seed=True)` and queues a
  new job in the same campaign / scene / post. The original image stays
  on disk; the re-roll lands as a separate row.
- `variation(image_id, strength)` → same as re-roll, but also loads the
  original PNG bytes into `init_image` and sets `init_image_strength` to
  the supplied value, so the backend runs img2img.

## Events emitted

- `imagegen_job_queued` — `{job_id, campaign_id, backend}`
- `imagegen_job_started` — `{job_id, campaign_id, backend}`
- `image_ready` — `{image_id, campaign_id, scene_id, post_id, cached}`
- `imagegen_job_failed` — `{job_id, campaign_id, reason}` (also emitted on
  cancel with `reason="cancelled"`)
- `imagegen_backend_health_changed` — only when `health_check`'s level
  differs from the previously observed level for that backend

`imagegen_progress` from the original spec is **not** emitted — see the
remaining doc §3.

## Error handling

- Unknown backend → `KeyError`. Service-wide "no backend at all"
  conditions raise `NoBackendAvailableError` (`service.py:117`), a
  `RuntimeError` subclass the API layer can translate to HTTP 503.
- Backend `generate` exceptions → logged at `exception` level, job flipped
  to `FAILED` (unless cancel raced), `imagegen_job_failed` emitted.
- Filesystem cleanup in `delete_image` → `WARNING` on `OSError`; deletion
  still continues.
- Event bus emission failures in `_emit(...)` → swallowed with an
  `exception` log so a bad subscriber can't break image generation.
- Cancellation during `aclose()` is swallowed; workers exit cleanly on
  task cancellation.

## Configuration

The service itself is configured by constructor arguments (no separate
`ImageGenConfig` dataclass yet). Backend-level configuration lives in the
bundled plugin manifests (see e.g. `imagegen-diffusers/manifest.yaml`
config schema for device/dtype/scheduler knobs). The spec-12 YAML schema
under `imagegen:` is **not** parsed by ImageGen itself today — the
service takes its defaults from constructor args and per-request fields.

## API surface (FastAPI)

`backend/src/grimoire/api/campaigns.py` exposes:

- `POST /campaigns/{id}/images/generate` (status 202) — wraps
  `queue_generation`, returns `{job_id}`
- `GET /campaigns/{id}/images` — wraps `list_images`, supports `scene_id`
  and `starred_only` query params

Other surface (cancel, prioritize, star, delete, reroll, variation,
health, backend listing, queue introspection) is not yet exposed via
REST.

## Test wiring

`backend/tests/imagegen/conftest.py` spins up a real `StateStore` over a
SQLite-in-tmp database, applies migrations, registers an
`InMemoryDiffusersBackend`, and seeds one campaign + one scene so the
images FKs hold. Tests cover backend management, trigger evaluation,
synchronous + queued generation, caching (per-campaign + img2img source
bytes), cancellation race conditions, re-roll / variation, star + delete,
health-change events, multi-backend parallelism, path-traversal rejection,
and the prioritize-in-place invariant.
