# ImageGen — Remaining Work

> Everything from the original `specs/12-imagegen.md` (now superseded) that
> did **not** land in the shipped design (`2026-05-12-imagegen-design.md`).
> Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-imagegen-design.md`
**Module:** `backend/src/grimoire/imagegen/`

## 1. Orchestrator trigger fan-out

Spec 12 §When to generate calls for the Orchestrator to ask ImageGen
`should_illustrate(scene_id, post_id)` after every turn and queue when the
per-campaign `TriggerConfig` says yes. The pure decision function
`should_illustrate(...)` is shipped (`service.py:84`), but no caller
invokes it: `grep imagegen` in `backend/src/grimoire/orchestrator/` returns
nothing.

This is the ImageGen-facing side of the Orchestrator remaining doc §1
("background-work fan-out after `turn_complete`"). Concrete work here:

- Decide where the trigger boolean signals come from (scene open / new
  location / new character appearance / in-combat). Scene Manager already
  exposes `start_scene`-shaped events; the others need a producer.
- Wire either the Orchestrator's post-turn fan-out **or** an event-bus
  subscriber inside `ImageGenService` to read `turn_complete` events,
  load the `TriggerConfig` for the campaign (today there is no per-
  campaign storage of the trigger config — see §6), and call
  `queue_generation(campaign_id, scene_id, post_id)` when triggered.

## 2. Health monitoring loop and fallback

Spec 12 §Health and fallback: backends are periodically health-checked,
and an unhealthy primary falls back to a configured alternate (typically
the integrated `diffusers` backend). Today `health_check` exists on every
backend and `ImageGenService.health_check(backend_id)` emits
`imagegen_backend_health_changed` on level changes — but nothing calls it
on a schedule, no `active_backend(...)` selection ever consults health, and
there is no "configured alternate" stored anywhere.

Needs:
- A periodic health prober (likely an `asyncio.Task` launched from
  `ImageGenService.__init__` or `main.py`'s lifespan, with interval
  configurable per backend).
- A `campaign.imagegen.fallback_backend` setting (per-campaign override of
  the global default).
- `queue_generation` / `active_backend` consult the last-known
  `HealthLevel` and route to the fallback when `UNHEALTHY`. If no
  fallback is available, leave the job queued and emit a warning event
  (today's behavior queues against the dead backend).

## 3. Progress events from backends

Spec 12 §Events emitted lists `imagegen_progress` for "periodic progress
updates (for backends that report)". The service has no progress channel
today — `_run_job` awaits `backend.generate(request)` as a single
coroutine and emits only `imagegen_job_started` / `image_ready`.

Design needed:
- Extend `ImageGenBackend` with an optional progress callback (e.g.
  `generate(request, *, progress=None)` where `progress` is `Callable[[
  ProgressEvent], Awaitable[None]] | None`). Backends that don't support
  progress simply ignore the kwarg.
- `_run_job` passes a callback that emits
  `imagegen_progress({job_id, campaign_id, step, total_steps, eta_ms})`.
- Wire the diffusers pipeline's `callback_on_step_end` and a1111's
  `/sdapi/v1/progress` polling loop into this surface (comfyui already
  has WebSocket progress; dalle has none).

## 4. Cooperative cancellation of running jobs

Today `cancel_job` only short-circuits *queued* jobs: a `RUNNING` job's
`backend.generate(...)` call runs to completion, the result is dropped on
the floor (`service.py:737`), and the user waits anyway. Spec 12 doesn't
explicitly demand this, but a sensible UI requires "Cancel" to actually
stop the GPU. The integrated diffusers + a1111 backends both support
mid-generation cancellation (diffusers via `callback_on_step_end` returning
a stop sentinel; a1111 via `/sdapi/v1/interrupt`).

Approach: add an optional `cancel_token: asyncio.Event` arg on
`ImageGenBackend.generate`, pass one per job from `_run_job`, set it from
`cancel_job` when the job is `RUNNING`. Wire backends that support
interrupt; the others continue to ignore the token.

## 5. Manual prompt editing and "save as new"

Spec 12 §Re-roll and variation: "User can edit the prompt of a saved
image, regenerate, save as new (the old image persists with its own
metadata)." `reroll` + `variation` are shipped, but there's no method
that takes an `image_id` plus an edited prompt / negative / params and
queues a fresh job with the merged request.

Surface to add:
```python
async def edit_and_regenerate(
    image_id: str,
    *,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    params: dict | None = None,
    keep_seed: bool = False,
) -> str: ...   # new job id
```

Implementation is a thin wrapper around `_request_from_metadata` +
`queue_generation` — the storage layer already supports a new row per
image, so "save as new" is automatic.

## 6. Per-campaign trigger / imagegen config storage

`TriggerConfig.from_config(...)` knows how to parse the spec-12 YAML block,
but nothing actually reads it from disk or from the per-campaign settings
store. The `campaigns` SQL table has `image_preset_id` but no other
imagegen knobs. Decision needed:

- Where does the per-campaign block live? Options: (a) a new
  `imagegen_config` JSON column on `campaigns`; (b) a sidecar
  `campaigns/<id>/imagegen.yaml`; (c) reuse the `composition.yaml`
  surface.
- `ImageGenService` needs a `get_trigger_config(campaign_id) ->
  TriggerConfig` so §1 can consult it.

Same surface should also store the active backend id (today held in the
in-memory `_campaign_backend` dict — lost on restart) and the fallback
backend id for §2.

## 7. Top-level `imagegen:` YAML config parsing

The spec's §Configuration block covers `default_backend`, `diffusers.*`,
`queue.*`, `caching.*`, `thumbnails.*`, `triggers.*`, `storage.*`. Today:

- `default_backend` → constructor arg, hard-wired to `None` in `main.py`.
- `diffusers.*` → handled per-plugin in
  `bundled_plugins/imagegen-diffusers/manifest.yaml`'s config schema, not
  by the core.
- `queue.max_concurrent_per_backend` → fixed at 1 (one worker task per
  handle).
- `queue.persist_pending` → not implemented (see §8).
- `caching.enabled` / `caching.cache_dir` → in-memory only; no on-disk
  cache, no enable/disable flag.
- `thumbnails.size` / `format` / `quality` → hard-coded `(256, 256)` JPEG
  q=85 in `make_thumbnail(...)`.
- `triggers.*` → see §6.
- `storage.image_format` → hard-coded PNG; `save_metadata_sidecar` not
  configurable.

Pick which of these are worth exposing in v1 and add a `Config.from_yaml`
loader that flows through `ImageGenService.__init__`.

## 8. Persistent job queue

`queue.persist_pending: true` in the spec config. Today `_jobs` and the
per-backend `asyncio.Queue` are in-process only — restart drops every
queued job on the floor with no way to recover.

Approach: persist `QUEUED` / `RUNNING` jobs to a small SQLite table
(`imagegen_jobs`), reload on startup, re-enqueue queued ones, and mark
running-at-shutdown jobs as failed with a clear reason so the user can
re-queue manually.

## 9. First-launch model download prompt

Spec 12 §Integrated diffusers backend specifies a user-facing prompt on
first launch: "Download SDXL Base 1.0 (~6 GB) to enable image generation?"
with `download_on_first_use: prompt | auto | never` driving the behavior.

Today `IntegratedDiffusersBackend._build_pipeline` calls
`StableDiffusionXLPipeline.from_pretrained(...)` which silently downloads
the weights inside the lazy `_ensure_pipeline` call — no prompt, no
progress reporting, no "never download" option.

Work:
- A `download_on_first_use` config knob honoured by `_build_pipeline`
  (raise an `UNCONFIGURED` health status with "download disabled by
  config" when `"never"`).
- An async download-progress hook surfaced on the event bus so the
  frontend can render a progress bar.
- A REST route or WebSocket message for the "prompt" mode so the UI can
  confirm before the download starts.

## 10. Image tag editing

The `images` SQL row has a `tags TEXT` column and `ImageMetadata.tags`
exists, but the only writer is `_persist_result` which always writes
`[]`. There's no `set_tags(image_id, tags)` / `add_tag` / `remove_tag`
method. Spec 12 sidecar example lists `tags: [scene-establishing, action]`
as a feature.

Tiny addition: an `async def set_tags(image_id, tags)` on the service
that writes both the SQL row and the YAML sidecar, plus a REST verb.

## 11. Per-character canonical seed

Spec 12 §Per-character canonical seed: "Each character's library card has
an optional `canonical_seed`. When this character is in an image, the
prompt + seed produces a consistent face/look across scenes."

`PromptComposer.compose(...)` pulls each present character's
`image.base_prompt` / `image.negative_prompt` but never reads
`character.image.canonical_seed`. The seed always comes from the
`GenerationRequest` (or a random one when omitted).

Design needed: when no explicit seed is passed and exactly one present
character has a `canonical_seed`, use it. When multiple do, the spec is
silent — pick a deterministic combiner (e.g. XOR of seeds modulo
2**31-1) and document it. Per-image overrides already work because the
caller can pass `seed=...` directly.

## 12. Pre-warm the integrated backend

Spec 12 §Open questions resolution: "Lazily by default; user can
pre-warm in worlds." The lazy default is implemented (the pipeline is
built on first `generate(...)`). The user-facing pre-warm action isn't:
no REST route, no `prewarm(backend_id)` service method. Wire one — it's
literally `await backend._ensure_pipeline()`, just needs an entry point.

## 13. Missing REST surface

`api/campaigns.py` exposes `POST /images/generate` and `GET /images`
only. The full `ImageGenProtocol` surface (`types/protocols.py:957`)
includes 13 methods. Routes missing:

- `GET /imagegen/backends` (`list_backends`)
- `GET /campaigns/{id}/imagegen/active` (`active_backend`)
- `PUT /campaigns/{id}/imagegen/active` (`set_active_backend`)
- `GET /campaigns/{id}/images/jobs` (`list_jobs`)
- `DELETE /campaigns/{id}/images/jobs/{job_id}` (`cancel_job`)
- `PATCH /campaigns/{id}/images/jobs/{job_id}` (`prioritize_job`)
- `POST /campaigns/{id}/images/{image_id}/reroll` (`reroll`)
- `POST /campaigns/{id}/images/{image_id}/variation` (`variation`)
- `GET /campaigns/{id}/images/{image_id}` (`get_image`)
- `PUT /campaigns/{id}/images/{image_id}/star` (`star_image`)
- `DELETE /campaigns/{id}/images/{image_id}` (`delete_image`)
- `GET /imagegen/backends/{id}/health` (`health_check`)

Plus a WebSocket bridge so `imagegen_job_*` / `image_ready` /
`imagegen_progress` (§3) reach the Frontend.

## 14. Replicate bundled plugin (v2; deferred)

Spec 12 §Plugin backends lists `imagegen-replicate` ("Adapter for
Replicate-hosted models") as a bundled plugin. The directory does not
exist under `bundled_plugins/`. Record here so it doesn't drop off — the
DALL-E plugin is structurally similar enough to be a useful template.

## 15. Pluggable visual-element extraction

`extract_visual_elements(...)` in `prompt.py:111` is a keyword heuristic
("see", "stood", "dress", "rain"…). Spec 12 §Prompt composition implies
the LLM-based Extractor can later supply richer hints (the sketch's
`self._extract_visual_elements(post_id)` reads like an LLM call).

Wire `PromptComposer` to optionally call into the Extractor module for a
"visual elements" extraction pass, falling back to the heuristic when the
Extractor isn't configured or returns nothing. Treat as a quality-of-life
upgrade — the heuristic is good enough for v1.

## 16. Deferred from spec 12 §Open questions

These are explicitly v2-or-later in the original spec; capture here so
they don't get re-litigated when scoping the next pass:

- **Controlnet for character pose consistency** (v2; deferred)
- **Inpainting via UI** (v2; deferred)
- **LoRA management — first-class browser/installer** (v2; deferred).
  The data model already carries `loras: list[LoraSpec]` and the
  bundled diffusers plugin advertises `lora=True` in its capabilities;
  the runtime never loads any LoRAs.
- **Character consistency models — IP-Adapter, Textual Inversion, custom
  training** (v2; deferred)
- **Animation / video** (rejected; "out of scope" in the spec)
- **NSFW handling — user-configurable safety gates** (v2; deferred)
- **Bulk regeneration ("Regenerate all images in this scene with the
  new preset")** (v2; deferred)

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. **§6 + §7** — get the per-campaign config + top-level YAML loader in
   place. Most other work depends on these.
2. **§1** — orchestrator fan-out + trigger consultation. Now ImageGen
   actually runs on its own.
3. **§13** — flesh out the REST + WebSocket surface so the Frontend can
   drive the queue / star / delete / reroll without dropping to SQL.
4. **§3 + §4** — progress events + cooperative cancellation, share a
   per-job control surface on `ImageGenBackend.generate`.
5. **§2** — health loop + fallback routing.
6. **§5 + §10 + §11 + §12** — small, independent UX wins (edit-prompt,
   tags, canonical seed, pre-warm); pick up whichever the user notices
   missing first.
7. **§8** — persistent queue once §1 makes restart-survival actually
   matter.
8. **§9** — first-launch download UX; coordinate with the Frontend plan.
9. **§14 + §15** — replicate plugin + LLM-driven visual extraction;
   genuinely optional.
