"""ImageGen service — facade over backends, queue, storage, cache.

Implements spec 12. Holds:

- A :class:`BackendRegistry` (the integrated diffusers backend plus any
  plugin-provided backends).
- A per-backend serial job queue (multiple backends run in parallel).
- An in-memory cache keyed by spec-12 ``(prompt, negative, params, seed,
  model)``; random-seed jobs bypass cache.
- An image filesystem layout under
  ``data/campaigns/<id>/images/<image-id>.{png,yaml}`` plus a JPG
  thumbnail at ``thumbnails/<image-id>.jpg``.
- An SQLite ``images`` index row per generated image.
- Event emission (``imagegen_job_queued`` / ``_started`` / ``image_ready``
  / ``_failed`` / ``imagegen_backend_health_changed``).

The Orchestrator (task 22) creates one :class:`ImageGenService` and wires
it to the rest of the modules; the FastAPI surface (task 31) exposes its
methods over HTTP/WS.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.files import load_yaml, write_yaml
from grimoire.imagegen.backend import cache_key_for_request, make_thumbnail
from grimoire.imagegen.config import ImageGenConfig
from grimoire.imagegen.errors import NoBackendAvailableError
from grimoire.imagegen.prompt import ComposedPrompt, PromptComposer
from grimoire.observability import wire_log
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.state_store import StateStore
from grimoire.state_store.paths import campaigns_root, image_metadata_path
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    BackendInfo,
    GenerationJob,
    GenerationRequest,
    GenerationResult,
    ImageMetadata,
    JobStatus,
)
from grimoire.types.protocols import LLMGateway
from grimoire.util import now_iso

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Trigger policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TriggerConfig:
    # Default off: images are only generated on an explicit user request.
    # A campaign opts into automatic illustration by setting a different
    # mode ('per_scene' | 'per_post' | 'every_n_posts').
    mode: str = "manual_only"  # 'manual_only' | 'per_scene' | 'per_post' | 'every_n_posts'
    every_n: int = 5
    on_scene_open: bool = True
    on_new_location: bool = True
    on_new_character_appearance: bool = True
    auto_during_combat: bool = False

    @classmethod
    def from_config(cls, raw: dict | None) -> TriggerConfig:
        if not raw:
            return cls()

        # Accept both spec-12 YAML keys ("trigger_on_scene_open",
        # "auto_illustrate_during_combat") and the canonical dataclass
        # field names (used when round-tripping via set_trigger_config).
        def _get_bool(*keys: str, default: bool) -> bool:
            for key in keys:
                if key in raw:
                    return bool(raw[key])
            return default

        return cls(
            mode=str(raw.get("trigger_mode") or raw.get("mode") or "manual_only"),
            every_n=int(raw.get("trigger_n") or raw.get("every_n") or 5),
            on_scene_open=_get_bool("trigger_on_scene_open", "on_scene_open", default=True),
            on_new_location=_get_bool("trigger_on_new_location", "on_new_location", default=True),
            on_new_character_appearance=_get_bool(
                "trigger_on_new_character_appearance",
                "on_new_character_appearance",
                default=True,
            ),
            auto_during_combat=_get_bool(
                "auto_illustrate_during_combat", "auto_during_combat", default=False
            ),
        )


def should_illustrate(
    config: TriggerConfig,
    *,
    is_scene_open: bool = False,
    is_new_location: bool = False,
    is_new_character: bool = False,
    is_in_combat: bool = False,
    post_index: int | None = None,
) -> bool:
    """Pure decision: should ImageGen queue a job for this hook?

    The Orchestrator passes the boolean signals; we apply the per-campaign
    trigger policy.
    """
    if config.mode == "manual_only":
        return False
    if is_in_combat and not config.auto_during_combat:
        return False
    if config.mode == "per_post":
        return True
    if config.mode == "per_scene":
        return (
            (config.on_scene_open and is_scene_open)
            or (config.on_new_location and is_new_location)
            or (config.on_new_character_appearance and is_new_character)
        )
    if config.mode == "every_n_posts":
        if post_index is None or config.every_n <= 0:
            return False
        return (post_index % config.every_n) == 0
    return False


__all__ = ["NoBackendAvailableError"]


# --------------------------------------------------------------------------- #
# Backend registry
# --------------------------------------------------------------------------- #


class _BackendHandle:
    """A backend plus its dedicated serial worker."""

    __slots__ = ("_pending_jobs", "backend", "queue", "task")

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.queue: asyncio.Queue[_QueueEntry] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None
        # Track pending job ids so cancellation can short-circuit work
        # before it's pulled off the queue.
        self._pending_jobs: set[str] = set()


@dataclass(frozen=True, slots=True)
class _QueueEntry:
    job_id: str


class BackendRegistry:
    """Holds the integrated + plugin backends, keyed by id.

    Plugin backends can be registered/unregistered at runtime — the
    Plugins module hands them in via :meth:`register`.
    """

    def __init__(self) -> None:
        self._backends: dict[str, Any] = {}

    def register(self, backend: Any) -> None:
        if not hasattr(backend, "id") or not backend.id:
            raise ValueError("backend must expose a non-empty `id`")
        self._backends[backend.id] = backend

    def unregister(self, backend_id: str) -> None:
        self._backends.pop(backend_id, None)

    def get(self, backend_id: str) -> Any | None:
        return self._backends.get(backend_id)

    def all(self) -> list[Any]:
        return list(self._backends.values())

    def ids(self) -> list[str]:
        return list(self._backends.keys())

    def __contains__(self, backend_id: str) -> bool:
        return backend_id in self._backends


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


# `CampaignId` is just `str` at the type layer; enforce a safe filesystem
# shape here so a malicious value can't escape ``data/campaigns/``.
_SAFE_CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")


def _validate_campaign_id(campaign_id: str) -> None:
    if not isinstance(campaign_id, str) or not _SAFE_CAMPAIGN_ID.fullmatch(campaign_id):
        raise ValueError(
            f"unsafe campaign_id {campaign_id!r}: must match [A-Za-z0-9][A-Za-z0-9_.-]{{0,127}}"
        )


def _new_image_id() -> str:
    return f"img_{uuid.uuid4().hex[:12]}"


def _new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def _backend_info(
    backend: Any, *, is_integrated: bool, plugin_id: str | None = None
) -> BackendInfo:
    caps = getattr(backend, "capabilities", None) or BackendCapabilities()
    return BackendInfo(
        id=getattr(backend, "id", ""),
        name=getattr(backend, "name", getattr(backend, "id", "")),
        capabilities=caps,
        is_integrated=is_integrated,
        plugin_id=plugin_id,
    )


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class ImageGenService:
    """Concrete ImageGen implementing :class:`grimoire.types.ImageGenProtocol`."""

    def __init__(
        self,
        *,
        store: StateStore,
        registry: BackendRegistry,
        default_backend_id: str | None = None,
        event_bus: EventBus | None = None,
        composer: PromptComposer | None = None,
        plugin_backend_ids: Iterable[str] | None = None,
        thumbnail_subdir: str = "thumbnails",
        config: ImageGenConfig | None = None,
        gateway: LLMGateway | None = None,
        metrics: MetricsRegistryProtocol = NULL_METRICS,
    ) -> None:
        self.store = store
        self.data_root = store.data_root
        self.registry = registry
        # Resolve default_backend_id: explicit arg wins; otherwise fall back
        # to ImageGenConfig.default_backend.
        self.config = config or ImageGenConfig()
        self.default_backend_id = default_backend_id or self.config.default_backend
        self.event_bus = event_bus
        self.composer = composer
        # Gateway is the single source of truth for per-campaign routing
        # (loaded from campaign.yaml). Optional so existing setups that
        # don't use task-based routing keep working unchanged. Can also
        # be set later via :meth:`set_gateway` because in main.py the
        # gateway is built after ImageGenService.
        self._gateway = gateway
        self._plugin_ids = set(plugin_backend_ids or ())
        self._thumbnail_subdir = thumbnail_subdir
        self._jobs: dict[str, GenerationJob] = {}
        self._results: dict[str, GenerationResult] = {}
        self._image_ids_by_job: dict[str, str] = {}
        # cache_key -> image_id. OrderedDict for LRU eviction; bounded by
        # ``config.caching_max_entries`` so long-running servers don't OOM
        # from retained inline image bytes (see BUGS.md).
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._handles: dict[str, _BackendHandle] = {}
        self._campaign_backend: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._last_health: dict[str, HealthLevel] = {}
        self._cancel_tokens: dict[str, asyncio.Event] = {}
        self._metrics: MetricsRegistryProtocol = metrics

        for backend in self.registry.all():
            self._ensure_handle(backend.id)

        if event_bus is not None:
            event_bus.subscribe("library_entity_changed", self._on_entity_changed)

    def _on_entity_changed(self, event: Event) -> None:
        kind = event.payload.get("kind")
        if kind == "image_preset":
            self._cache.clear()

    def set_gateway(self, gateway: LLMGateway) -> None:
        """Late-bind the gateway used for per-task imagegen routing."""
        self._gateway = gateway

    def set_metrics(self, metrics: MetricsRegistryProtocol) -> None:
        self._metrics = metrics

    def register_with_health_monitor(self, monitor: Any) -> None:
        """§11: register all currently registered backends as health targets.

        ``monitor`` is duck-typed: it must accept ``register_probeable(target, obj)``.
        Backends registered later (plugin loads at runtime) aren't picked up
        automatically; callers can re-invoke after plugins finish loading.
        """
        from grimoire.types.observability import HealthTarget

        for backend in self.registry.all():
            target = HealthTarget(id=backend.id, kind="imagegen_backend")
            monitor.register_probeable(target, backend)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def aclose(self) -> None:
        """Cancel workers and drain queues. Safe to call repeatedly."""
        self._closed = True
        handles = list(self._handles.values())
        for handle in handles:
            if handle.task is not None and not handle.task.done():
                handle.task.cancel()
        for handle in handles:
            if handle.task is None:
                continue
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await handle.task
        self._handles.clear()
        # Drop the in-memory result/cache pair; otherwise repeated
        # construct/aclose cycles in long-lived processes leak image bytes.
        self._cache.clear()
        self._results.clear()

    def _ensure_handle(self, backend_id: str) -> _BackendHandle:
        handle = self._handles.get(backend_id)
        if handle is not None:
            return handle
        backend = self.registry.get(backend_id)
        if backend is None:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        # §9: hand the event bus to backends that want to publish
        # download-progress / health-transition events. Duck-typed so
        # plugins that don't care opt out by simply not defining the hook.
        if self.event_bus is not None:
            setter = getattr(backend, "set_event_bus", None)
            if callable(setter):
                try:
                    setter(self.event_bus)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("imagegen: backend %r set_event_bus raised", backend_id)
        handle = _BackendHandle(backend)
        handle.task = asyncio.create_task(self._worker(backend_id), name=f"imagegen-{backend_id}")
        self._handles[backend_id] = handle
        return handle

    async def unregister_backend(self, backend_id: str) -> None:
        """Drop a backend from the registry AND cancel its worker.

        Calling ``self.registry.unregister(backend_id)`` directly leaves
        the per-backend ``_handles`` entry alive — the worker task keeps
        awaiting a queue tied to a backend the registry no longer knows.
        Always go through this method when removing a backend at runtime.
        """
        self.registry.unregister(backend_id)
        handle = self._handles.pop(backend_id, None)
        if handle is None or handle.task is None:
            return
        if not handle.task.done():
            handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await handle.task

    # ------------------------------------------------------------------ #
    # Backend management
    # ------------------------------------------------------------------ #

    async def list_backends(self) -> list[BackendInfo]:
        out: list[BackendInfo] = []
        for backend in self.registry.all():
            out.append(
                _backend_info(
                    backend,
                    is_integrated=backend.id not in self._plugin_ids,
                    plugin_id=backend.id if backend.id in self._plugin_ids else None,
                )
            )
        return out

    async def active_backend(self, campaign_id: str) -> BackendInfo:
        backend_id = self._campaign_backend.get(campaign_id)
        if backend_id is None:
            raw = await self._load_imagegen_config_row(campaign_id)
            backend_id = raw.get("active_backend") or self.default_backend_id
            if backend_id is not None:
                self._campaign_backend[campaign_id] = backend_id
        backend = self.registry.get(backend_id) if backend_id else None
        if backend is None and self.default_backend_id:
            # The configured backend was removed — fall back to default.
            backend = self.registry.get(self.default_backend_id)
        if backend is None:
            raise NoBackendAvailableError(
                "no image-gen backends registered; install an imagegen plugin "
                "and configure it under Library → Plugins"
            )
        return _backend_info(
            backend,
            is_integrated=backend.id not in self._plugin_ids,
            plugin_id=backend.id if backend.id in self._plugin_ids else None,
        )

    async def set_active_backend(self, campaign_id: str, backend_id: str) -> None:
        if backend_id not in self.registry:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        self._campaign_backend[campaign_id] = backend_id
        self._ensure_handle(backend_id)
        await self._mutate_imagegen_config_row(campaign_id, update={"active_backend": backend_id})

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #

    async def queue_generation(
        self,
        campaign_id: str,
        scene_id: str | None,
        post_id: str | None,
        request: GenerationRequest | None = None,
        priority: int = 5,
        *,
        task: str | None = None,
    ) -> str:
        async with self._metrics.measure("imagegen", "generate"):
            return await self._queue_generation_inner(
                campaign_id, scene_id, post_id, request, priority, task=task
            )

    async def _queue_generation_inner(
        self,
        campaign_id: str,
        scene_id: str | None,
        post_id: str | None,
        request: GenerationRequest | None = None,
        priority: int = 5,
        *,
        task: str | None = None,
    ) -> str:
        _validate_campaign_id(campaign_id)
        if request is None:
            request = await self._compose_request(
                campaign_id=campaign_id, scene_id=scene_id, post_id=post_id
            )
        # Per-task campaign routing: when a task is provided and the
        # campaign has an imagegen_routing entry for it, pick that
        # backend+model. Falls through to active_backend otherwise.
        routed_backend, routed_model = await self._resolve_task_route(campaign_id, task)
        if routed_model is not None:
            request = request.model_copy(update={"model": routed_model})
        backend_id = routed_backend
        if backend_id is None:
            backend_id = self._campaign_backend.get(campaign_id)
        if backend_id is None:
            raw = await self._load_imagegen_config_row(campaign_id)
            backend_id = raw.get("active_backend") or self.default_backend_id
            if backend_id is not None:
                self._campaign_backend[campaign_id] = backend_id

        # Health-aware fallback: if the chosen backend is UNHEALTHY and a
        # fallback is configured, route there instead. If no fallback,
        # leave the job queued and emit a warning event so the UI can
        # surface "your imagegen backend is broken" instead of silently
        # piling up jobs against a dead worker.
        if backend_id and self._last_health.get(backend_id) == HealthLevel.UNHEALTHY:
            fallback = (await self._load_imagegen_config_row(campaign_id)).get("fallback_backend")
            if fallback and fallback in self.registry:
                logger.info(
                    "imagegen: routing %s job to fallback %s (active %s unhealthy)",
                    campaign_id,
                    fallback,
                    backend_id,
                )
                backend_id = fallback
            else:
                await self._emit(
                    events.IMAGEGEN_WARNING,
                    {
                        "campaign_id": campaign_id,
                        "reason": f"active backend {backend_id!r} unhealthy "
                        "and no fallback configured",
                    },
                )

        if backend_id not in self.registry:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        self._ensure_handle(backend_id)
        job_id = _new_job_id()
        job = GenerationJob(
            id=job_id,
            campaign_id=campaign_id,
            backend=backend_id,
            request=request,
            status=JobStatus.QUEUED,
            priority=priority,
            queued_at=datetime.now(UTC),
            scene_id=scene_id,
            post_id=post_id,
        )
        async with self._lock:
            self._jobs[job_id] = job
            handle = self._handles[backend_id]
            handle._pending_jobs.add(job_id)
        if self.config.queue_persist_pending:
            await self.store.db.execute(
                """
                INSERT INTO imagegen_jobs (
                  id, campaign_id, backend, status, priority, request_json,
                  scene_id, post_id, queued_at, started_at, finished_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    job_id,
                    campaign_id,
                    backend_id,
                    JobStatus.QUEUED.value,
                    priority,
                    request.model_dump_json(),
                    scene_id,
                    post_id,
                    now_iso(),
                ),
            )
        await handle.queue.put(_QueueEntry(job_id=job_id))
        await self._emit(
            events.IMAGEGEN_JOB_QUEUED,
            {"job_id": job_id, "campaign_id": campaign_id, "backend": backend_id},
        )
        return job_id

    async def _update_persistent_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        """Best-effort update of the persisted job row. No-op when persistence is off."""
        if not self.config.queue_persist_pending:
            return
        sets: list[str] = []
        values: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            values.append(status)
        if started_at is not None:
            sets.append("started_at = ?")
            values.append(started_at.isoformat())
        if finished_at is not None:
            sets.append("finished_at = ?")
            values.append(finished_at.isoformat())
        if error is not None:
            sets.append("error = ?")
            values.append(error)
        if not sets:
            return
        values.append(job_id)
        await self.store.db.execute(
            f"UPDATE imagegen_jobs SET {', '.join(sets)} WHERE id = ?",
            tuple(values),
        )

    async def reload_pending_jobs(self) -> None:
        """Re-enqueue jobs that were QUEUED at last shutdown (§8).

        RUNNING jobs at shutdown are marked FAILED (we can't recover the
        in-flight state) so the user can re-queue manually.
        """
        if not self.config.queue_persist_pending:
            return
        rows = await self.store.db.fetchall(
            "SELECT * FROM imagegen_jobs WHERE status IN ('queued','running')"
        )
        for row in rows:
            job_id = str(row["id"])
            status = str(row["status"])
            if status == "running":
                await self.store.db.execute(
                    "UPDATE imagegen_jobs SET status = 'failed', error = ?, "
                    "finished_at = ? WHERE id = ?",
                    ("interrupted by shutdown", now_iso(), job_id),
                )
                continue
            request = GenerationRequest.model_validate_json(row["request_json"])
            job = GenerationJob(
                id=job_id,
                campaign_id=str(row["campaign_id"]),
                backend=str(row["backend"]),
                request=request,
                status=JobStatus.QUEUED,
                priority=int(row["priority"]),
                queued_at=datetime.now(UTC),
                scene_id=row["scene_id"],
                post_id=row["post_id"],
            )
            self._jobs[job_id] = job
            backend_id = str(row["backend"])
            if backend_id not in self.registry:
                # The backend that owned this job is gone; mark FAILED.
                job.status = JobStatus.FAILED
                job.error = f"backend {backend_id!r} no longer registered"
                await self._update_persistent_job(
                    job_id,
                    status=JobStatus.FAILED.value,
                    error=job.error,
                    finished_at=datetime.now(UTC),
                )
                continue
            self._ensure_handle(backend_id)
            handle = self._handles[backend_id]
            handle._pending_jobs.add(job_id)
            await handle.queue.put(_QueueEntry(job_id=job_id))

    async def generate_sync(
        self,
        campaign_id: str,
        request: GenerationRequest,
        *,
        task: str | None = None,
    ) -> GenerationResult:
        async with self._metrics.measure("imagegen", "generate"):
            return await self._generate_sync_inner(campaign_id, request, task=task)

    async def _generate_sync_inner(
        self,
        campaign_id: str,
        request: GenerationRequest,
        *,
        task: str | None = None,
    ) -> GenerationResult:
        _validate_campaign_id(campaign_id)
        routed_backend, routed_model = await self._resolve_task_route(campaign_id, task)
        if routed_model is not None:
            request = request.model_copy(update={"model": routed_model})
        backend_id = routed_backend or self._campaign_backend.get(
            campaign_id, self.default_backend_id
        )
        backend = self.registry.get(backend_id)
        if backend is None:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        cached = self._lookup_cache(campaign_id, request, backend=backend)
        if cached is not None:
            return cached
        wire_log.log_request(
            "imagegen",
            payload=request,
            campaign_id=campaign_id,
            backend=backend_id,
            model=routed_model,
            task=task,
        )
        try:
            result = await backend.generate(request)
        except Exception as exc:
            wire_log.log_error(
                "imagegen",
                error=f"{type(exc).__name__}: {exc}",
                campaign_id=campaign_id,
                backend=backend_id,
                model=routed_model,
                task=task,
            )
            raise
        wire_log.log_response(
            "imagegen",
            payload=result,
            campaign_id=campaign_id,
            backend=backend_id,
            task=task,
        )
        self._store_in_cache(campaign_id, request, backend=backend, result=result)
        return result

    async def _resolve_task_route(
        self, campaign_id: str, task: str | None
    ) -> tuple[str | None, str | None]:
        """Look up a per-campaign imagegen route for ``task``.

        Returns ``(backend_id, model)``. Both are ``None`` when no task is
        given, no gateway is wired, or no route exists for the task. A
        route that names an unregistered backend silently falls through
        to the campaign's active_backend so a stale config can't break
        image generation outright.
        """
        if task is None or self._gateway is None:
            return None, None
        try:
            await self._gateway.ensure_campaign_loaded(campaign_id)
        except Exception:
            logger.warning("imagegen: ensure_campaign_loaded failed for campaign=%s", campaign_id)
            return None, None
        route = self._gateway.imagegen_route(task, campaign_id)
        if route is None:
            return None, None
        if route.provider_id not in self.registry:
            logger.warning(
                "imagegen: campaign=%s task=%r routes to backend %r which is not "
                "registered; falling back to active_backend",
                campaign_id,
                task,
                route.provider_id,
            )
            return None, None
        return route.provider_id, route.model

    # ------------------------------------------------------------------ #
    # Queue
    # ------------------------------------------------------------------ #

    async def list_jobs(
        self,
        campaign_id: str,
        status: JobStatus | None = None,
    ) -> list[GenerationJob]:
        async with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.campaign_id == campaign_id and (status is None or job.status == status)
            ]
        jobs.sort(key=lambda j: (-j.priority, j.queued_at or datetime.now(UTC)))
        return jobs

    async def cancel_job(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"no such job {job_id!r}")
            if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                return
            was_running = job.status == JobStatus.RUNNING
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(UTC)
            handle = self._handles.get(job.backend)
            if handle is not None:
                handle._pending_jobs.discard(job_id)
            token = self._cancel_tokens.get(job_id) if was_running else None
        if token is not None:
            token.set()
        await self._update_persistent_job(
            job_id, status=JobStatus.CANCELLED.value, finished_at=datetime.now(UTC)
        )
        await self._emit(events.IMAGEGEN_JOB_FAILED, {"job_id": job_id, "reason": "cancelled"})

    async def prioritize_job(self, job_id: str, priority: int) -> None:
        # Mutate the existing job in place — the worker holds a local
        # reference across its long `await _run_job` and would otherwise
        # update an orphaned object if we swapped the dict entry.
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"no such job {job_id!r}")
            job.priority = priority

    # ------------------------------------------------------------------ #
    # Re-roll / variation
    # ------------------------------------------------------------------ #

    async def reroll(self, image_id: str) -> str:
        meta = await self.get_image(image_id)
        request = self._request_from_metadata(meta, new_seed=True)
        return await self.queue_generation(
            campaign_id=meta.campaign_id,
            scene_id=meta.scene_id,
            post_id=meta.post_id,
            request=request,
        )

    async def edit_and_regenerate(
        self,
        image_id: str,
        *,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        params: dict | None = None,
        keep_seed: bool = False,
    ) -> str:
        """§5 Manual prompt editing + "save as new".

        Loads ``image_id``'s metadata, merges the edits into a fresh
        :class:`GenerationRequest`, and queues a new job. The old image
        and its metadata are untouched — "save as new" is automatic
        because each completed job lands in a new ``images`` row.
        """
        meta = await self.get_image(image_id)
        base = self._request_from_metadata(meta, new_seed=not keep_seed)
        updates: dict[str, Any] = {}
        if prompt is not None:
            updates["prompt"] = prompt
        if negative_prompt is not None:
            updates["negative_prompt"] = negative_prompt
        if params:
            for key in ("width", "height", "steps", "cfg_scale", "sampler"):
                if key in params:
                    updates[key] = params[key]
        new_request = base.model_copy(update=updates) if updates else base
        return await self.queue_generation(
            campaign_id=meta.campaign_id,
            scene_id=meta.scene_id,
            post_id=meta.post_id,
            request=new_request,
        )

    async def variation(self, image_id: str, strength: float) -> str:
        meta = await self.get_image(image_id)
        source_path = Path(meta.file_path)
        if not source_path.is_absolute():
            source_path = self.data_root / source_path
        init_bytes = source_path.read_bytes() if source_path.exists() else None
        request = self._request_from_metadata(meta, new_seed=True)
        request = request.model_copy(
            update={"init_image": init_bytes, "init_image_strength": float(strength)}
        )
        return await self.queue_generation(
            campaign_id=meta.campaign_id,
            scene_id=meta.scene_id,
            post_id=meta.post_id,
            request=request,
        )

    # ------------------------------------------------------------------ #
    # Storage
    # ------------------------------------------------------------------ #

    async def list_images(
        self,
        campaign_id: str,
        scene_id: str | None = None,
        starred_only: bool = False,
    ) -> list[ImageMetadata]:
        query = "SELECT * FROM images WHERE campaign_id = ?"
        params: list[Any] = [campaign_id]
        if scene_id is not None:
            query += " AND scene_id = ?"
            params.append(scene_id)
        if starred_only:
            query += " AND user_starred = 1"
        query += " ORDER BY created_at DESC, id DESC"
        rows = await self.store.db.fetchall(query, tuple(params))
        return [_image_metadata_from_row(row) for row in rows]

    async def get_image(self, image_id: str) -> ImageMetadata:
        row = await self.store.db.fetchone("SELECT * FROM images WHERE id = ?", (image_id,))
        if row is None:
            raise KeyError(f"image {image_id!r} not found")
        return _image_metadata_from_row(row)

    async def star_image(self, image_id: str, starred: bool) -> None:
        # Make sure the image exists first; raises KeyError if not.
        await self.get_image(image_id)
        await self.store.db.execute(
            "UPDATE images SET user_starred = ? WHERE id = ?",
            (1 if starred else 0, image_id),
        )

    async def set_tags(self, image_id: str, tags: list[str]) -> None:
        """§10 Replace the tag list on an image (SQL + YAML sidecar)."""
        meta = await self.get_image(image_id)
        tags_clean = [str(t).strip() for t in tags if str(t).strip()]
        await self.store.db.execute(
            "UPDATE images SET tags = ? WHERE id = ?",
            (json.dumps(tags_clean), image_id),
        )
        sidecar = image_metadata_path(self.data_root, meta.campaign_id, image_id)
        if sidecar.exists():
            try:
                doc = load_yaml(sidecar) or {}
                if isinstance(doc, dict):
                    doc["tags"] = tags_clean
                    write_yaml(sidecar, doc)
            except Exception:
                logger.warning("set_tags: failed to update sidecar", exc_info=True)

    async def delete_image(self, image_id: str) -> None:
        meta = await self.get_image(image_id)
        await self.store.db.execute("DELETE FROM images WHERE id = ?", (image_id,))
        for raw in (meta.file_path, meta.thumbnail_path):
            if not raw:
                continue
            path = Path(raw)
            if not path.is_absolute():
                path = self.data_root / path
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    logger.warning("failed to remove image asset %s", path, exc_info=True)
        sidecar = image_metadata_path(self.data_root, meta.campaign_id, image_id)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                logger.warning("failed to remove image sidecar %s", sidecar, exc_info=True)
        await self.store.db.execute(
            "DELETE FROM campaign_content_index WHERE id = ?",
            (f"campaigns/{meta.campaign_id}/images/{image_id}",),
        )

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    async def prewarm(self, backend_id: str) -> None:
        """§12 Trigger lazy pipeline load for backends that support it.

        Backends with a ``_ensure_pipeline`` (or ``prewarm``) coroutine
        get awaited; backends that don't simply no-op.
        """
        backend = self.registry.get(backend_id)
        if backend is None:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        for name in ("prewarm", "_ensure_pipeline"):
            hook = getattr(backend, name, None)
            if hook is None or not callable(hook):
                continue
            result = hook()
            if hasattr(result, "__await__"):
                await result
            return

    async def health_check(self, backend_id: str) -> HealthStatus:
        backend = self.registry.get(backend_id)
        if backend is None:
            status = HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=backend_id,
                message="backend not registered",
            )
        else:
            status = await backend.health_check()
        prev = self._last_health.get(backend_id)
        if prev != status.level:
            self._last_health[backend_id] = status.level
            await self._emit(
                events.IMAGEGEN_BACKEND_HEALTH_CHANGED,
                {"backend_id": backend_id, "level": status.level.value, "message": status.message},
            )
        return status

    # ------------------------------------------------------------------ #
    # Per-campaign config storage (§6)
    # ------------------------------------------------------------------ #

    async def get_trigger_config(self, campaign_id: str) -> TriggerConfig:
        raw = await self._load_imagegen_config_row(campaign_id)
        return TriggerConfig.from_config(raw.get("trigger") if raw else None)

    async def set_trigger_config(self, campaign_id: str, trigger: TriggerConfig) -> None:
        await self._mutate_imagegen_config_row(
            campaign_id,
            update={
                "trigger": {
                    "mode": trigger.mode,
                    "every_n": trigger.every_n,
                    "on_scene_open": trigger.on_scene_open,
                    "on_new_location": trigger.on_new_location,
                    "on_new_character_appearance": trigger.on_new_character_appearance,
                    "auto_during_combat": trigger.auto_during_combat,
                }
            },
        )

    async def set_fallback_backend(self, campaign_id: str, backend_id: str | None) -> None:
        if backend_id is not None and backend_id not in self.registry:
            raise KeyError(f"no backend registered with id {backend_id!r}")
        await self._mutate_imagegen_config_row(campaign_id, update={"fallback_backend": backend_id})

    async def get_fallback_backend(self, campaign_id: str) -> str | None:
        raw = await self._load_imagegen_config_row(campaign_id)
        return raw.get("fallback_backend")

    async def _load_imagegen_config_row(self, campaign_id: str) -> dict[str, Any]:
        _validate_campaign_id(campaign_id)
        row = await self.store.db.fetchone(
            "SELECT imagegen_config FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if row is None:
            return {}
        raw = row["imagegen_config"]
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    async def _mutate_imagegen_config_row(
        self, campaign_id: str, *, update: dict[str, Any]
    ) -> None:
        _validate_campaign_id(campaign_id)
        existing = await self._load_imagegen_config_row(campaign_id)
        merged = {**existing, **update}
        await self.store.db.execute(
            "UPDATE campaigns SET imagegen_config = ? WHERE id = ?",
            (json.dumps(merged, sort_keys=True), campaign_id),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _compose_request(
        self,
        *,
        campaign_id: str,
        scene_id: str | None,
        post_id: str | None,
    ) -> GenerationRequest:
        composed: ComposedPrompt | None = None
        if self.composer is not None:
            post_body: str | None = None
            if post_id is not None:
                post_body = await self._fetch_post_body(post_id)
            preset_id = await self._campaign_image_preset_id(campaign_id)
            composed = await self.composer.compose(
                campaign_id=campaign_id,
                scene_id=scene_id,
                post_body=post_body,
                image_preset_id=preset_id,
            )
        prompt = composed.prompt if composed else ""
        negative = composed.negative_prompt if composed else None
        params = composed.params if composed else {}
        seed_override = params.get("seed")
        return GenerationRequest(
            prompt=prompt or "a scene",
            negative_prompt=negative,
            width=int(params.get("width", 1024)),
            height=int(params.get("height", 1024)),
            steps=int(params.get("steps", 28)),
            cfg_scale=float(params.get("cfg_scale", 6.5)),
            sampler=str(params.get("sampler", "DPM++ 2M Karras")),
            seed=int(seed_override) if seed_override is not None else None,
        )

    async def _fetch_post_body(self, post_id: str) -> str | None:
        row = await self.store.db.fetchone(
            "SELECT body_excerpt FROM posts WHERE id = ?", (post_id,)
        )
        if row is None:
            return None
        return row["body_excerpt"] or None

    async def _campaign_image_preset_id(self, campaign_id: str) -> str | None:
        row = await self.store.db.fetchone(
            "SELECT image_preset_id FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if row is None:
            return None
        return row["image_preset_id"]

    def _request_from_metadata(self, meta: ImageMetadata, *, new_seed: bool) -> GenerationRequest:
        params = meta.params or {}
        return GenerationRequest(
            prompt=meta.prompt or "",
            negative_prompt=meta.negative_prompt or None,
            width=int(params.get("width", 1024)),
            height=int(params.get("height", 1024)),
            steps=int(params.get("steps", 28)),
            cfg_scale=float(params.get("cfg_scale", 6.5)),
            sampler=str(params.get("sampler", "")),
            seed=None if new_seed else meta.seed,
            model=meta.model or None,
        )

    def _cache_key(self, campaign_id: str, request: GenerationRequest, *, backend: Any) -> str:
        """Cache key scoped to a single campaign.

        The bare :func:`cache_key_for_request` key is namespaced with the
        campaign id so a hit in campaign A never bleeds into campaign B
        (images and their DB rows are campaign-private).
        """
        body = cache_key_for_request(request, model=getattr(backend, "base_model", None))
        return f"{campaign_id}|{body}"

    def _lookup_cache(
        self,
        campaign_id: str,
        request: GenerationRequest,
        *,
        backend: Any,
    ) -> GenerationResult | None:
        if not self.config.caching_enabled:
            return None
        if request.seed is None:
            return None
        key = self._cache_key(campaign_id, request, backend=backend)
        image_id = self._cache.get(key)
        if image_id is None:
            return None
        # Promote this entry to MRU so the LRU eviction below favours dropping
        # genuinely stale entries.
        self._cache.move_to_end(key)
        return self._results.get(image_id)

    def _store_in_cache(
        self,
        campaign_id: str,
        request: GenerationRequest,
        *,
        backend: Any,
        result: GenerationResult,
        image_id: str | None = None,
    ) -> None:
        if not self.config.caching_enabled:
            return
        if request.seed is None:
            return
        key = self._cache_key(campaign_id, request, backend=backend)
        # Use the actually-applied seed (not the requested None) for downstream
        # reuse — but only when the caller provided one.
        ident = image_id or f"_inline_{key}"
        if key in self._cache:
            # Replacing an existing entry: drop the old result if no other
            # cache key still references it.
            old_ident = self._cache[key]
            if old_ident != ident and not any(
                v == old_ident for k, v in self._cache.items() if k != key
            ):
                self._results.pop(old_ident, None)
        self._cache[key] = ident
        self._cache.move_to_end(key)
        self._results[ident] = result
        self._evict_cache_if_full()

    def _evict_cache_if_full(self) -> None:
        """Drop oldest cache entries until size <= ``caching_max_entries``."""
        max_entries = self.config.caching_max_entries
        if max_entries <= 0:
            return
        while len(self._cache) > max_entries:
            _, evicted_ident = self._cache.popitem(last=False)
            # Only drop the matching result if no remaining cache entry maps
            # to it (defensive — image_ids are normally 1:1 with cache keys).
            if not any(v == evicted_ident for v in self._cache.values()):
                self._results.pop(evicted_ident, None)

    async def _worker(self, backend_id: str) -> None:
        handle = self._handles[backend_id]
        backend = handle.backend
        while not self._closed:
            try:
                entry = await handle.queue.get()
            except asyncio.CancelledError:
                break
            job_id = entry.job_id
            async with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.status != JobStatus.QUEUED:
                    handle._pending_jobs.discard(job_id)
                    handle.queue.task_done()
                    continue
                handle._pending_jobs.discard(job_id)
                job.status = JobStatus.RUNNING
                job.started_at = datetime.now(UTC)
            await self._update_persistent_job(
                job_id, status=JobStatus.RUNNING.value, started_at=job.started_at
            )
            await self._emit(
                events.IMAGEGEN_JOB_STARTED,
                {"job_id": job_id, "campaign_id": job.campaign_id, "backend": backend_id},
            )
            try:
                result = await self._run_job(backend, job)
            except asyncio.CancelledError:
                # If the user cancelled in flight, status is already
                # CANCELLED — drop the half-done work and keep the worker
                # alive. If we ourselves were cancelled (service shutdown),
                # break out instead. The trailing `finally` calls
                # task_done() either way.
                if job.status == JobStatus.CANCELLED:
                    continue
                break
            except Exception as exc:
                logger.exception("imagegen job %s failed", job_id)
                async with self._lock:
                    # Preserve CANCELLED if cancel_job raced us — the
                    # caller already saw `imagegen_job_failed`.
                    if job.status != JobStatus.CANCELLED:
                        job.status = JobStatus.FAILED
                        job.finished_at = datetime.now(UTC)
                        job.error = str(exc)
                if job.status == JobStatus.FAILED:
                    await self._update_persistent_job(
                        job_id,
                        status=JobStatus.FAILED.value,
                        finished_at=job.finished_at,
                        error=str(exc),
                    )
                    await self._emit(
                        events.IMAGEGEN_JOB_FAILED,
                        {
                            "job_id": job_id,
                            "campaign_id": job.campaign_id,
                            "reason": str(exc),
                        },
                    )
            else:
                async with self._lock:
                    # cancel_job may have flipped status to CANCELLED
                    # while the backend was still running; respect it.
                    if job.status != JobStatus.CANCELLED:
                        job.status = JobStatus.COMPLETE
                        job.finished_at = datetime.now(UTC)
                        job.result = result
                if job.status == JobStatus.COMPLETE:
                    await self._update_persistent_job(
                        job_id,
                        status=JobStatus.COMPLETE.value,
                        finished_at=job.finished_at,
                    )
            finally:
                handle.queue.task_done()

    async def _run_job(self, backend: Any, job: GenerationJob) -> GenerationResult:
        request = job.request
        cached = self._lookup_cache(job.campaign_id, request, backend=backend)
        if cached is not None:
            key = self._cache_key(job.campaign_id, request, backend=backend)
            existing_image_id = self._cache.get(key)
            if existing_image_id and not existing_image_id.startswith("_inline_"):
                if job.status == JobStatus.CANCELLED:
                    return cached
                self._image_ids_by_job[job.id] = existing_image_id
                await self._emit(
                    events.IMAGE_READY,
                    {
                        "image_id": existing_image_id,
                        "campaign_id": job.campaign_id,
                        "cached": True,
                        "cost_usd": 0.0,
                        "model": "",
                        "backend": "",
                    },
                )
                return cached

        async def _on_progress(info: dict[str, Any]) -> None:
            await self._emit(
                events.IMAGEGEN_PROGRESS,
                {
                    "job_id": job.id,
                    "campaign_id": job.campaign_id,
                    **info,
                },
            )

        wire_log.log_request(
            "imagegen",
            payload=request,
            campaign_id=job.campaign_id,
            backend=getattr(backend, "id", ""),
            job_id=job.id,
            scene_id=job.scene_id,
            post_id=job.post_id,
        )
        token = asyncio.Event()
        self._cancel_tokens[job.id] = token
        try:
            try:
                result = await backend.generate(request, progress=_on_progress, cancel_token=token)
            except TypeError:
                # Older backends that don't accept progress/cancel kwargs.
                result = await backend.generate(request)
        except Exception as exc:
            wire_log.log_error(
                "imagegen",
                error=f"{type(exc).__name__}: {exc}",
                campaign_id=job.campaign_id,
                backend=getattr(backend, "id", ""),
                job_id=job.id,
                scene_id=job.scene_id,
                post_id=job.post_id,
            )
            raise
        finally:
            self._cancel_tokens.pop(job.id, None)
        wire_log.log_response(
            "imagegen",
            payload=result,
            campaign_id=job.campaign_id,
            backend=getattr(backend, "id", ""),
            job_id=job.id,
        )
        # The job may have been cancelled while the backend was running.
        # Skip persistence + `image_ready` so the caller doesn't see a
        # completed image for a job they explicitly cancelled.
        if job.status == JobStatus.CANCELLED:
            return result
        image_id = _new_image_id()
        await self._persist_result(
            image_id=image_id,
            job=job,
            result=result,
        )
        self._store_in_cache(
            job.campaign_id, request, backend=backend, result=result, image_id=image_id
        )
        self._image_ids_by_job[job.id] = image_id
        await self._emit(
            events.IMAGE_READY,
            {
                "image_id": image_id,
                "campaign_id": job.campaign_id,
                "scene_id": job.scene_id,
                "post_id": job.post_id,
                "cached": False,
                "cost_usd": result.cost_usd,
                "model": result.model,
                "backend": result.backend,
            },
        )
        return result

    async def _persist_result(
        self,
        *,
        image_id: str,
        job: GenerationJob,
        result: GenerationResult,
    ) -> None:
        # Re-validate here even though queue/sync entry points already
        # check — `_persist_result` writes to disk, so we want a guard
        # right next to the write in case a future caller skips the
        # earlier validation.
        _validate_campaign_id(job.campaign_id)
        campaign_dir = campaigns_root(self.data_root) / job.campaign_id / "images"
        resolved_dir = campaign_dir.resolve()
        if not resolved_dir.is_relative_to(self.data_root.resolve()):
            raise ValueError(f"campaign_id {job.campaign_id!r} resolves outside data_root")
        campaign_dir.mkdir(parents=True, exist_ok=True)
        png_path = campaign_dir / f"{image_id}.png"
        png_path.write_bytes(result.image_bytes)
        thumb_dir = campaign_dir / self._thumbnail_subdir
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = thumb_dir / f"{image_id}.jpg"
        thumb_bytes = make_thumbnail(
            result.image_bytes,
            size=self.config.thumbnails_size,
            format=self.config.thumbnails_format,
            quality=self.config.thumbnails_quality,
        )
        thumb_path.write_bytes(thumb_bytes)

        now_str = now_iso()
        metadata_payload = {
            "id": image_id,
            "campaign_id": job.campaign_id,
            "scene_id": job.scene_id,
            "post_id": job.post_id,
            "prompt": job.request.prompt,
            "negative_prompt": job.request.negative_prompt or "",
            "seed": result.seed,
            "sampler": job.request.sampler,
            "steps": job.request.steps,
            "cfg_scale": job.request.cfg_scale,
            "width": job.request.width,
            "height": job.request.height,
            "backend": result.backend,
            "model": result.model,
            "created_at": now_str,
            "duration_ms": result.duration_ms,
            "user_starred": False,
            "tags": [],
            "file": png_path.name,
            "thumbnail": f"{self._thumbnail_subdir}/{image_id}.jpg",
        }
        write_yaml(image_metadata_path(self.data_root, job.campaign_id, image_id), metadata_payload)

        params_json = json.dumps(result.actual_params, sort_keys=True, default=str)
        rel_png = str(png_path.relative_to(self.data_root))
        rel_thumb = str(thumb_path.relative_to(self.data_root))
        await self.store.db.execute(
            """
            INSERT OR REPLACE INTO images (
              id, campaign_id, scene_id, post_id, file_path,
              thumbnail_path, prompt, negative_prompt, params, backend, model,
              seed, created_at, user_starred, tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                job.campaign_id,
                job.scene_id,
                job.post_id,
                rel_png,
                rel_thumb,
                job.request.prompt,
                job.request.negative_prompt or "",
                params_json,
                result.backend,
                result.model,
                int(result.seed),
                now_str,
                0,
                json.dumps([]),
            ),
        )

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_bus is None:
            return
        try:
            await self.event_bus.emit(Event(type=event_type, payload=payload))
        except Exception:  # pragma: no cover - defensive
            logger.exception("imagegen event emission failed: %s", event_type)


# --------------------------------------------------------------------------- #
# Row → model adapter
# --------------------------------------------------------------------------- #


def _image_metadata_from_row(row: Any) -> ImageMetadata:
    keys = row.keys() if hasattr(row, "keys") else row
    data = {key: row[key] for key in keys}
    params_raw = data.get("params")
    params: dict[str, Any] = {}
    if isinstance(params_raw, str) and params_raw:
        try:
            params = json.loads(params_raw)
        except json.JSONDecodeError:
            params = {}
    elif isinstance(params_raw, dict):
        params = params_raw
    tags_raw = data.get("tags")
    tags: list[str] = []
    if isinstance(tags_raw, str) and tags_raw:
        try:
            tags = list(json.loads(tags_raw))
        except json.JSONDecodeError:
            tags = []
    elif isinstance(tags_raw, list):
        tags = list(tags_raw)
    created_at_raw = data.get("created_at")
    created_at: datetime | None
    if isinstance(created_at_raw, datetime):
        created_at = created_at_raw
    elif isinstance(created_at_raw, str) and created_at_raw:
        try:
            created_at = datetime.fromisoformat(created_at_raw)
        except ValueError:
            created_at = None
    else:
        created_at = None
    return ImageMetadata(
        id=str(data["id"]),
        campaign_id=str(data["campaign_id"]),
        file_path=str(data.get("file_path") or ""),
        thumbnail_path=data.get("thumbnail_path"),
        prompt=str(data.get("prompt") or ""),
        negative_prompt=str(data.get("negative_prompt") or ""),
        params=params,
        backend=str(data.get("backend") or ""),
        model=str(data.get("model") or ""),
        seed=data.get("seed"),
        scene_id=data.get("scene_id"),
        post_id=data.get("post_id"),
        created_at=created_at,
        user_starred=bool(data.get("user_starred") or 0),
        tags=tags,
    )
