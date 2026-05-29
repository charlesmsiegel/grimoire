"""Bootstrap phase functions for service construction.

Extracts the construction logic from ``lifespan()`` into named phases
so each can be tested independently and the lifespan stays small.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.api.container import ServiceContainer
from grimoire.config import Settings
from grimoire.continuity import (
    ContinuityConfig,
    ContinuityRegistry,
    ContinuityRegistryExportAdapter,
    make_judge_request_factory,
)
from grimoire.lifecycle import QueueBundle

if TYPE_CHECKING:
    from grimoire.storage import Database

log = logging.getLogger(__name__)

_SEED_ROOT = Path(__file__).resolve().parent / "seed" / "library"


class _StopAdapter:
    """Wraps an async callable as a Stoppable for LifecycleManager."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    async def stop(self) -> None:
        await self._fn()


class _ImageGenCoverGenerator:
    """Adapter from :class:`ImageGenService` to the CoverGenerator protocol."""

    def __init__(self, imagegen: Any) -> None:
        self._imagegen = imagegen

    async def generate_cover(self, campaign_id: str, prompt: str) -> bytes | None:
        from grimoire.types.imagegen import GenerationRequest

        try:
            result = await self._imagegen.generate_sync(
                campaign_id,
                GenerationRequest(prompt=prompt, width=1024, height=1536),
            )
        except Exception as exc:
            log.warning("cover generation failed: %r", exc)
            return None
        if result.error:
            return None
        return result.image_bytes or None


def _seed_defaults(data_root: Path) -> None:
    """Copy bundled default library assets into the user's data root."""
    if not _SEED_ROOT.is_dir():
        return
    library_root = data_root / "library"
    for src in _SEED_ROOT.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(_SEED_ROOT)
        dst = library_root / rel
        if dst.exists() and dst.stat().st_size > 0:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.tmp")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        finally:
            if tmp.exists():
                with suppress(OSError):
                    tmp.unlink()
        log.info("seeded default %s", rel)


async def _safe(name: str, coro: Awaitable[Any]) -> None:
    """Await *coro*; swallow and log any exception."""
    try:
        await coro
    except Exception:
        log.exception("%s failed at startup", name)


# ----------------------------------------------------------------------- #
# Phase 1: content services (no LLM gateway required)
# ----------------------------------------------------------------------- #


async def build_content_services(
    settings: Settings,
    container: ServiceContainer,
    db: Database,
) -> None:
    """Wire services that don't depend on an LLM gateway."""
    from grimoire.api.stream import StreamManager
    from grimoire.characters import CharactersService
    from grimoire.event_bus import EventBus
    from grimoire.imagegen import (
        BackendRegistry,
        ImageGenConfig,
        ImageGenHealthProber,
        ImageGenIntegration,
        ImageGenService,
    )
    from grimoire.library import LibraryConfig, LibraryService
    from grimoire.mechanics import MechanicsConfig, MechanicsService
    from grimoire.plugins import PluginsConfig, PluginsService
    from grimoire.scenes import SceneManager
    from grimoire.scenes.indexer import SceneIndexer
    from grimoire.state_store import StateStore, StateStoreConfig
    from grimoire.world import WorldConfig, WorldService

    lifecycle = container.lifecycle

    if container.event_bus is None:
        container.event_bus = EventBus()
    if container.stream is None:
        container.stream = StreamManager(event_bus=container.event_bus)
    lifecycle.register_async("stream", _StopAdapter(container.stream.aclose))

    data_root = settings.data_root
    for sub in ("library", "mechanics", "plugins", "config/plugins", "templates"):
        (data_root / sub).mkdir(parents=True, exist_ok=True)

    state_store_config = StateStoreConfig.from_yaml(
        data_root / "config" / "state_store.yaml",
        data_root=data_root,
        database_path=settings.resolved_database_path,
        enable_wal=settings.enable_wal,
    )
    container.state_store_config = state_store_config

    from grimoire.templates import registry as template_registry

    template_registry.register_search_path(data_root / "templates", prepend=True)

    if container.state_store is None:
        container.state_store = StateStore(
            db=db, data_root=data_root, event_bus=container.event_bus
        )
        await container.state_store.validate_schema()
    if container.transient_state is None:
        from grimoire.transient_state import TransientStateService
        from grimoire.transient_state.config import TransientStateConfig
        from grimoire.transient_state.triggers import attach_triggers

        ts_cfg = TransientStateConfig.from_yaml(data_root / "config" / "transient.yaml")
        container.transient_state = TransientStateService(container.state_store, config=ts_cfg)
        if container.event_bus is not None:
            attach_triggers(container.transient_state, container.event_bus)

    library_cfg = LibraryConfig.from_yaml(data_root / "config" / "library.yaml")
    if container.library is None:
        container.library = LibraryService(container.state_store, config=library_cfg)

    if container.extras_service is None and container.state_store is not None:
        from grimoire.extras import ExtrasService

        container.extras_service = ExtrasService(
            library=container.library,
            store=container.state_store,
        )

    if container.world is None:
        world_cfg = WorldConfig.from_yaml(data_root / "config" / "world.yaml")
        container.world = WorldService(container.library, config=world_cfg)
    if container.calendar is None:
        from grimoire.world.calendar_service import CalendarService

        container.calendar = CalendarService(container.library)

    if container.mechanics is None:
        container.mechanics = MechanicsService(
            MechanicsConfig.for_data_root(data_root),
            state_store=container.state_store,
            event_bus=container.event_bus,
        )
        try:
            await container.mechanics.rescan()
            container.mechanics_rescan_error = None
        except Exception as exc:
            log.exception("mechanics rescan failed at startup")
            container.mechanics_rescan_error = f"{type(exc).__name__}: {exc}"

    if container.plugins is None:
        container.plugins = PluginsService(
            PluginsConfig.for_data_root(data_root),
            event_bus=container.event_bus,
        )
        try:
            await container.plugins.rescan()
            container.plugins_rescan_error = None
        except Exception as exc:
            log.exception("plugins rescan failed at startup")
            container.plugins_rescan_error = f"{type(exc).__name__}: {exc}"
        try:
            await container.plugins.start_periodic_health()
        except Exception:
            log.exception("plugins periodic health loop start failed")
    lifecycle.register_async(
        "plugins_periodic_health", _StopAdapter(container.plugins.stop_periodic_health)
    )

    if container.characters is None:
        container.characters = CharactersService(
            container.library, container.mechanics, event_bus=container.event_bus
        )
    if container.scenes is None:
        container.scenes = SceneManager(
            data_root,
            event_bus=container.event_bus,
            state_store=container.state_store,
        )

    if container.scene_indexer is None and container.state_store is not None:
        scene_indexer = SceneIndexer(
            container.scenes, container.state_store.db, container.event_bus
        )
        scene_indexer.start()
        container.scene_indexer = scene_indexer
    if container.scene_indexer is not None:
        lifecycle.register_async("scene_indexer", container.scene_indexer)

    if container.continuity is None:
        continuity_config = ContinuityConfig()
        container.continuity = ContinuityRegistry(
            db=db,
            config=continuity_config,
            event_bus=container.event_bus,
        )
    if container.scenes is not None and getattr(container.scenes, "_continuity", None) is None:
        container.scenes.set_continuity(container.continuity)

    if container.inventory is None:
        from grimoire.inventory import InventoryService

        container.inventory = InventoryService(
            store=container.state_store,
            event_bus=container.event_bus,
        )

    if container.imagegen is None:
        imagegen_cfg = ImageGenConfig.from_yaml(data_root / "config" / "imagegen.yaml")
        container.imagegen = ImageGenService(
            store=container.state_store,
            registry=BackendRegistry(),
            default_backend_id=None,
            event_bus=container.event_bus,
            config=imagegen_cfg,
        )
    lifecycle.register_async("imagegen", _StopAdapter(container.imagegen.aclose))

    if container.imagegen_integration is None and container.event_bus is not None:
        integration = ImageGenIntegration(container.imagegen, container.event_bus)
        integration.start()
        container.imagegen_integration = integration
    if container.imagegen_integration is not None:
        lifecycle.register_sync("imagegen_integration", container.imagegen_integration)

    if container.imagegen_health_prober is None:
        prober = ImageGenHealthProber(container.imagegen, interval_seconds=30.0)
        prober.start()
        container.imagegen_health_prober = prober
    if container.imagegen_health_prober is not None:
        lifecycle.register_async("imagegen_health_prober", container.imagegen_health_prober)


# ----------------------------------------------------------------------- #
# Phase 2: LLM gateway and services that depend on it
# ----------------------------------------------------------------------- #


async def build_llm_services(
    settings: Settings,
    container: ServiceContainer,
    db: Database,
) -> None:
    """Wire the LLM gateway and everything downstream of it."""
    from grimoire.context.builder import ContextBuilderService
    from grimoire.extractor.llm_strategy import parse_llm_payload
    from grimoire.extractor.schema import output_schema
    from grimoire.extractor.service import ExtractorService
    from grimoire.llm_gateway.gateway import LLMGatewayService
    from grimoire.observability.replayer import TurnReplayerService
    from grimoire.observability.service import ObservabilityService
    from grimoire.scenes.analysis import make_adaptive_scene_analyzer
    from grimoire.scenes.default_summarizers import (
        make_adaptive_summarizer,
        make_default_final_summarizer,
        make_default_running_summarizer,
    )
    from grimoire.scenes.summary_jobs import RunningSummaryWorker
    from grimoire.time_engine.service import TimeEngineService
    from grimoire.time_engine.subscriber import TimeEngineSubscriber

    lifecycle = container.lifecycle

    if container.observability is None:
        obs = ObservabilityService(db=db, event_bus=container.event_bus)
        container.observability = obs
        if container.state_store is not None:
            container.state_store.set_metrics(obs.metrics())
        if container.scenes is not None:
            container.scenes.set_metrics(obs.metrics())
        if container.imagegen is not None:
            container.imagegen.set_metrics(obs.metrics())
    else:
        obs = container.observability
    lifecycle.register_async("observability", _StopAdapter(obs.shutdown))

    if container.hud_config is None:
        from grimoire.hud.config import HudConfigService

        container.hud_config = HudConfigService(data_root=settings.data_root / "campaigns")
    if container.hud is None:
        from grimoire.hud.service import HudService

        container.hud = HudService(config_service=container.hud_config)
    try:
        from grimoire.hud.fetchers import register_default_fetchers

        register_default_fetchers(
            container.hud,
            hud_config=container.hud_config,
            library=container.library,
            extras=container.extras_service,
            continuity=container.continuity,
            scenes=container.scenes,
            world=container.world,
            store=container.state_store,
        )
    except Exception:
        log.exception("hud: failed to register default fetchers")

    if container.imagegen is not None:
        try:
            container.imagegen.register_with_health_monitor(obs.health_monitor)
        except Exception:
            log.exception("imagegen register_with_health_monitor failed")

    if container.llm_gateway is None:
        container.llm_gateway = LLMGatewayService(
            plugins=container.plugins,
            db=db,
            config=settings.llm_gateway.to_gateway_config(),
            data_root=settings.data_root,
            event_bus=container.event_bus,
            health_monitor=obs.health_monitor,
            metrics=obs.metrics(),
        )
        await container.llm_gateway.register_with_health_monitor()
        try:
            await container.llm_gateway.register_provider_defaults()
        except Exception:
            log.exception("register_provider_defaults failed at startup")
    llm_gateway = container.llm_gateway

    if container.imagegen is not None:
        container.imagegen.set_gateway(llm_gateway)
    if obs.replayer is None:
        obs.replayer = TurnReplayerService(
            audit_store=obs.audit_store,
            gateway=llm_gateway,
        )
    await obs.start()
    await obs.health_monitor.start_periodic()
    await obs.retention.start_periodic()

    if container.world is not None and getattr(container.world, "gateway", None) is None:
        container.world.set_gateway(llm_gateway)
    if getattr(container.scenes, "_summarizer", None) is None:
        container.scenes.set_summarizer(
            make_default_running_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        )
    if getattr(container.scenes, "_final_summarizer", None) is None:
        container.scenes.set_final_summarizer(
            make_default_final_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        )
    if getattr(container.scenes, "_adaptive_summarizer", None) is None:
        container.scenes.set_adaptive_summarizer(
            make_adaptive_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        )
    if getattr(container.scenes, "_scene_analyzer", None) is None:
        container.scenes.set_scene_analyzer(
            make_adaptive_scene_analyzer(
                llm_gateway,
                extraction_schema_fn=output_schema,
                payload_parser=parse_llm_payload,
            )
        )

    if container.scene_summary_worker is None:
        worker = RunningSummaryWorker(container.scenes, container.event_bus)
        worker.start()
        container.scene_summary_worker = worker
    lifecycle.register_async("scene_summary_worker", container.scene_summary_worker)

    if container.extractor is None:
        container.extractor = ExtractorService(gateway=llm_gateway, metrics=obs.metrics())

    if isinstance(container.continuity, ContinuityRegistry):
        registry = container.continuity
        registry.set_embedder(llm_gateway)
        registry.set_judge(
            llm_gateway,
            make_judge_request_factory(registry.config.contradiction_check.model_route),
        )

    if container.context_builder is None:
        container.context_builder = ContextBuilderService(
            library=container.library,
            characters=container.characters,
            world=container.world,
            scenes=container.scenes,
            continuity=container.continuity,
            mechanics=container.mechanics,
            gateway=llm_gateway,
            state_store=container.state_store,
            transient_state=container.transient_state,
            metrics=obs.metrics(),
        )

    if container.time_engine is None:
        container.time_engine = TimeEngineService(
            store=container.state_store,
            world=container.world,
            characters=container.characters,
            mechanics=container.mechanics,
            continuity=container.continuity,
            event_bus=container.event_bus,
            metrics=obs.metrics(),
        )
    if container.time_engine_subscriber is None:
        subscriber = TimeEngineSubscriber(
            time_engine=container.time_engine,
            event_bus=container.event_bus,
        )
        subscriber.start()
        container.time_engine_subscriber = subscriber
    lifecycle.register_sync("time_engine_subscriber", container.time_engine_subscriber)


# ----------------------------------------------------------------------- #
# Phase 3: orchestrator, export, and integration subscribers
# ----------------------------------------------------------------------- #


async def build_play_services(
    settings: Settings,
    container: ServiceContainer,
) -> None:
    """Wire the orchestrator, export pipeline, and integration subscribers."""
    from grimoire.characters.integration import CharactersIntegration
    from grimoire.export.epub import EpubAdapter
    from grimoire.export.service import ExportService, ExportServiceConfig
    from grimoire.export.sources import DataSources
    from grimoire.orchestrator.service import OrchestratorService

    lifecycle = container.lifecycle

    if container.export is None:
        cover_generator = _ImageGenCoverGenerator(container.imagegen)
        continuity_export: Any = container.continuity
        if isinstance(continuity_export, ContinuityRegistry):
            continuity_export = ContinuityRegistryExportAdapter(continuity_export)
        sources = DataSources(
            scenes=container.scenes,
            characters=container.characters,
            world=container.world,
            continuity=continuity_export,
            images=container.imagegen,
            cover_generator=cover_generator,
            data_root=settings.data_root,
        )
        export_cfg = settings.export
        epub_adapter = EpubAdapter(
            sources,
            config=export_cfg.adapters.epub,
            filter_defaults=export_cfg.filters,
        )
        container.export = ExportService(
            sources=sources,
            adapters=[epub_adapter, *container.plugins.export_adapters()],
            config=ExportServiceConfig.from_export_config(export_cfg),
            state_store=container.state_store,
        )

    if container.orchestrator is None:
        container.orchestrator = OrchestratorService(
            event_bus=container.event_bus,
            scene_manager=container.scenes,
            llm_gateway=container.llm_gateway,
            context_builder=container.context_builder,
            extractor=container.extractor,
            state_store=container.state_store,
            mechanics=container.mechanics,
            world=container.world,
            continuity=container.continuity,
            transient_state=container.transient_state,
            inventory=container.inventory,
            ws_push=container.stream.push,
            metrics=container.observability.metrics(),
        )

    if container.observability.replayer is not None:
        container.observability.replayer.set_forker(container.orchestrator)

    if (
        container.characters_integration is None
        and container.event_bus is not None
        and container.characters is not None
    ):
        chars_integration = CharactersIntegration(
            container.characters,
            container.scenes,
            container.event_bus,
        )
        chars_integration.start()
        container.characters_integration = chars_integration
    if container.characters_integration is not None:
        lifecycle.register_sync("characters_integration", container.characters_integration)


# ----------------------------------------------------------------------- #
# Phase 4: file watcher, embedding worker, and other background tasks
# ----------------------------------------------------------------------- #


async def start_background_workers(
    settings: Settings,
    container: ServiceContainer,
) -> None:
    """Seed defaults, start the file watcher, and launch background workers."""
    from grimoire.library import LibraryConfig
    from grimoire.mechanics.file_watcher import MechanicsFileWatcher
    from grimoire.state_store import (
        BackupScheduler,
        BodySummarizer,
        EmbeddingWorker,
        RetentionSweeper,
        reenqueue_missing_embeddings,
    )
    from grimoire.watcher.watcher import FileWatcher

    lifecycle = container.lifecycle
    data_root = settings.data_root

    _seed_defaults(data_root)

    queues = QueueBundle()
    library_cfg = LibraryConfig.from_yaml(data_root / "config" / "library.yaml")

    if library_cfg.watch:
        file_watcher = FileWatcher(
            data_root=data_root,
            store=container.state_store,
            bus=container.event_bus,
            scene_manager=container.scenes,
            config=library_cfg,
            embedding_queue=queues.embedding,
            summary_queue=queues.summary,
        )
        container.file_watcher = file_watcher

    embedding_worker = EmbeddingWorker(
        store=container.state_store,
        gateway=container.llm_gateway,
        queue=queues.embedding,
        bus=container.event_bus,
        config=container.state_store_config.library,
    )
    container.embedding_worker = embedding_worker
    lifecycle.register_async("embedding_worker", embedding_worker)

    body_summarizer = BodySummarizer(
        store=container.state_store,
        gateway=container.llm_gateway,
        queue=queues.summary,
        bus=container.event_bus,
    )
    if library_cfg.indexing.summarize_on_index:
        body_summarizer.start()
    else:
        log.info("body summarizer disabled (summarize_on_index=false)")
    container.body_summarizer = body_summarizer
    lifecycle.register_async("body_summarizer", body_summarizer)

    retention_sweeper = RetentionSweeper(
        db=container.db,
        config=container.state_store_config.retention,
        bus=container.event_bus,
    )
    container.retention_sweeper = retention_sweeper
    lifecycle.register_async("retention_sweeper", retention_sweeper)

    backup_scheduler = BackupScheduler(
        data_root=data_root,
        database_path=settings.resolved_database_path,
        config=container.state_store_config.auto_backup,
        bus=container.event_bus,
    )
    backup_scheduler.start()
    container.backup_scheduler = backup_scheduler
    lifecycle.register_sync("backup_scheduler", backup_scheduler)

    mechanics_watcher: MechanicsFileWatcher | None = None
    if container.mechanics.config.reload_on_file_change:
        mechanics_watcher = MechanicsFileWatcher(container.mechanics)
    if mechanics_watcher is not None:
        lifecycle.register_async("mechanics_watcher", mechanics_watcher)

    if container.state_store_config.library.embed_on_index:
        try:
            await reenqueue_missing_embeddings(container.state_store, queues.embedding)
        except Exception:
            log.exception("reenqueue_missing_embeddings failed at startup")
        await embedding_worker.start()
    else:
        log.info("embedding worker disabled (embed_on_index=false)")
    await retention_sweeper.start()

    coros: list[Awaitable[Any]] = []
    if container.scene_indexer is not None:
        coros.append(_safe("scene_backfill", container.scene_indexer.backfill()))
    if container.imagegen is not None:
        coros.append(_safe("imagegen_reload", container.imagegen.reload_pending_jobs()))
    if container.file_watcher is not None and library_cfg.scan_on_startup:
        coros.append(_safe("library_scan", container.file_watcher.scan_now()))
    if mechanics_watcher is not None:
        coros.append(_safe("mechanics_watcher", mechanics_watcher.start()))
    if coros:
        await asyncio.gather(*coros)
