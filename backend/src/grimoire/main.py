import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grimoire import __version__
from grimoire.api.campaigns import router as campaigns_router
from grimoire.api.config import router as config_router
from grimoire.api.container import ServiceContainer
from grimoire.api.health import router as health_router
from grimoire.api.imagegen import router as imagegen_router
from grimoire.api.library import router as library_router
from grimoire.api.setup import router as setup_router
from grimoire.api.stream import StreamManager
from grimoire.api.templates import router as templates_router
from grimoire.api.ws import router as ws_router
from grimoire.characters import CharactersService
from grimoire.characters.integration import CharactersIntegration
from grimoire.config import settings
from grimoire.context.builder import ContextBuilderService
from grimoire.continuity import (
    ContinuityConfig,
    ContinuityRegistry,
    ContinuityRegistryExportAdapter,
    make_judge_request_factory,
)
from grimoire.event_bus import EventBus
from grimoire.export.epub import EpubAdapter
from grimoire.export.service import ExportService, ExportServiceConfig
from grimoire.export.sources import DataSources
from grimoire.extractor.service import ExtractorService
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenConfig,
    ImageGenHealthProber,
    ImageGenIntegration,
    ImageGenService,
)
from grimoire.library import LibraryConfig, LibraryService
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.observability.health import HealthMonitorService
from grimoire.orchestrator.service import OrchestratorService
from grimoire.plugins import PluginsConfig, PluginsService
from grimoire.scenes import SceneManager
from grimoire.scenes.default_summarizers import (
    make_default_final_summarizer,
    make_default_running_summarizer,
)
from grimoire.scenes.indexer import SceneIndexer
from grimoire.scenes.summary_jobs import RunningSummaryWorker
from grimoire.state_store import (
    BackupScheduler,
    BodySummarizer,
    EmbeddingWorker,
    RetentionSweeper,
    StateStore,
    StateStoreConfig,
    reenqueue_missing_embeddings,
)
from grimoire.storage import Database, apply_migrations
from grimoire.time_engine.service import TimeEngineService
from grimoire.time_engine.subscriber import TimeEngineSubscriber
from grimoire.watcher.watcher import FileWatcher
from grimoire.world import WorldConfig, WorldService

log = logging.getLogger(__name__)

_SEED_ROOT = Path(__file__).resolve().parent / "seed" / "library"


class _ImageGenCoverGenerator:
    """Adapter from :class:`ImageGenService` to the CoverGenerator protocol.

    EPUB auto-cover (§6) calls ``generate_sync`` on the active backend for
    the campaign; failures are swallowed and reported back as ``None`` so
    the export falls through to the plain title page.
    """

    def __init__(self, imagegen: ImageGenService) -> None:
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
    """Copy bundled default library assets into the user's data root.

    Skips files the user already has (size > 0) so edits and additions are
    preserved. A zero-byte file at the destination is treated as a leftover
    from a previous crashed copy and overwritten. The copy itself writes to
    a sibling temp file and ``os.replace``s it into place so a crash mid-
    write cannot leave a partial seed file behind. Called once per startup
    before the initial library scan.
    """
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(
        settings.resolved_database_path,
        pool_size=settings.db_pool_size,
        enable_wal=settings.enable_wal,
    )
    await db.connect()
    container: ServiceContainer | None = None
    try:
        # Wrap every step of startup so a failure anywhere (migration error,
        # malformed seed file, service init bug) still closes the connection
        # pool and aclose()'s any services we managed to construct.
        await apply_migrations(db)
        app.state.db = db

        container = getattr(app.state, "container", None)
        if container is None:
            container = ServiceContainer(db=db)
        container.db = db
        if container.event_bus is None:
            container.event_bus = EventBus()
        if container.stream is None:
            container.stream = StreamManager(event_bus=container.event_bus)

        data_root = settings.data_root
        for sub in ("library", "mechanics", "plugins", "config/plugins", "templates"):
            (data_root / sub).mkdir(parents=True, exist_ok=True)

        # State Store config: layered (file overlaid on bootstrap settings).
        # Stashed on the container so the embedding worker / backup scheduler /
        # retention sweep can read it without re-parsing.
        state_store_config = StateStoreConfig.from_yaml(
            data_root / "config" / "state_store.yaml",
            data_root=data_root,
            database_path=settings.resolved_database_path,
            enable_wal=settings.enable_wal,
        )
        container.extras["state_store_config"] = state_store_config

        # User-supplied prompt template variants live under {data_root}/templates
        # and take precedence over the bundled defaults so a user can drop in a
        # new variant and select it without rebuilding the package.
        from grimoire.templates import registry as template_registry

        template_registry.register_search_path(data_root / "templates", prepend=True)

        if container.state_store is None:
            container.state_store = StateStore(db=db, data_root=data_root)
        library_cfg = LibraryConfig.from_yaml(data_root / "config" / "library.yaml")
        if container.library is None:
            container.library = LibraryService(container.state_store, config=library_cfg)
        if container.world is None:
            world_cfg = WorldConfig.from_yaml(data_root / "config" / "world.yaml")
            container.world = WorldService(container.library, config=world_cfg)
        if container.mechanics is None:
            container.mechanics = MechanicsService(
                MechanicsConfig.for_data_root(data_root),
                state_store=container.state_store,
            )
            try:
                await container.mechanics.rescan()
            except Exception:
                log.exception("mechanics rescan failed at startup")
        if container.plugins is None:
            container.plugins = PluginsService(
                PluginsConfig.for_data_root(data_root),
                event_bus=container.event_bus,
            )
            try:
                await container.plugins.rescan()
            except Exception:
                log.exception("plugins rescan failed at startup")
            # Kick off the periodic health loop so plugin_health_changed
            # events flow even when no UI request triggers a probe.
            try:
                await container.plugins.start_periodic_health()
            except Exception:
                log.exception("plugins periodic health loop start failed")
        if container.characters is None:
            container.characters = CharactersService(container.library, container.mechanics)
        if container.scenes is None:
            # Continuity registry is wired below; SceneManager picks it up
            # via the attribute set after that block so the pre-scene
            # briefing can run.
            container.scenes = SceneManager(data_root, event_bus=container.event_bus)
        # Scene indexer keeps the SQLite scenes/posts tables in sync with the
        # markdown + sidecar source-of-truth. Subscribed to manager events
        # post-construction; backfill walks the disk once to catch any
        # direct-edit-while-down deltas.
        if container.extras.get("scene_indexer") is None and container.state_store is not None:
            scene_indexer = SceneIndexer(
                container.scenes, container.state_store.db, container.event_bus
            )
            scene_indexer.start()
            try:
                await scene_indexer.backfill()
            except Exception:
                log.exception("scene indexer backfill failed at startup")
            container.extras["scene_indexer"] = scene_indexer
        if container.continuity is None:
            # The registry hands out one ContinuityService per
            # (campaign_id, branch_id) backed by SqliteContinuityStore so
            # facts survive restart. HybridFactSearchIndex + (when the
            # gateway is available) LLMContradictionJudge are wired in
            # below once the gateway is constructed.
            continuity_config = ContinuityConfig()
            container.continuity = ContinuityRegistry(
                db=db,
                config=continuity_config,
                event_bus=container.event_bus,
            )
        # Hand the (possibly already-constructed) SceneManager the
        # registry so it can attach a pre-scene briefing to
        # ``scene_started`` events. SceneManager itself accepts a
        # ``continuity`` kwarg at construction but the bag is built
        # earlier — patching the attribute here keeps the wiring linear.
        if container.scenes is not None and getattr(container.scenes, "_continuity", None) is None:
            container.scenes._continuity = container.continuity  # noqa: SLF001
        if container.imagegen is None:
            # No image-generation backends registered. /images endpoints (read)
            # work against the SQLite index; queue_generation / active_backend
            # will raise NoBackendAvailableError until a backend plugin is
            # installed and registered with the registry. Routes that need a
            # backend should catch that and 503 with a clear message.
            imagegen_cfg = ImageGenConfig.from_yaml(data_root / "config" / "imagegen.yaml")
            container.imagegen = ImageGenService(
                store=container.state_store,
                registry=BackendRegistry(),
                default_backend_id=None,
                event_bus=container.event_bus,
                config=imagegen_cfg,
            )
        if container.extras.get("imagegen_integration") is None and container.event_bus is not None:
            integration = ImageGenIntegration(container.imagegen, container.event_bus)
            integration.start()
            container.extras["imagegen_integration"] = integration
        if container.extras.get("imagegen_health_prober") is None:
            prober = ImageGenHealthProber(container.imagegen, interval_seconds=30.0)
            prober.start()
            container.extras["imagegen_health_prober"] = prober
        # §8 Reload any jobs that were queued at shutdown.
        try:
            await container.imagegen.reload_pending_jobs()
        except Exception:
            log.exception("imagegen: reload_pending_jobs failed at startup")

        # LLM-adjacent services: gateway + extractor + context builder are
        # the substrate the orchestrator drives. They're wired even when no
        # LLM provider plugin is installed — calls that route through the
        # gateway will raise a clear "no provider" error rather than blowing
        # up at construction time, so the rest of the routes (library,
        # images, worlds, etc.) keep working.
        if container.extras.get("llm_gateway") is None:
            # §3: Construct a standalone HealthMonitorService for the gateway.
            # When ObservabilityService is wired here, pass its health_monitor
            # instance instead and remove this standalone construction.
            gateway_health_monitor = HealthMonitorService(db)
            container.extras["llm_gateway"] = LLMGatewayService(
                plugins=container.plugins,
                db=db,
                config=settings.llm_gateway.to_gateway_config(),
                data_root=settings.data_root,
                event_bus=container.event_bus,
                health_monitor=gateway_health_monitor,
            )
            await container.extras["llm_gateway"].register_with_health_monitor()
            await gateway_health_monitor.start_periodic()
            container.extras["gateway_health_monitor"] = gateway_health_monitor
        llm_gateway = container.extras["llm_gateway"]
        # §3 wire the gateway into WorldService so create_world can
        # auto-generate atmosphere blocks. WorldService is constructed
        # before the gateway exists, so we patch the attribute here.
        if container.world is not None and getattr(container.world, "gateway", None) is None:
            container.world.gateway = llm_gateway
        # §4/§5 default summarizers — only attach if the caller didn't already
        # inject custom ones. The gateway only raises at call-time when no
        # provider plugin is registered, so wiring is safe even on a bare
        # install.
        if getattr(container.scenes, "_summarizer", None) is None:
            container.scenes._summarizer = make_default_running_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        if getattr(container.scenes, "_final_summarizer", None) is None:
            container.scenes._final_summarizer = make_default_final_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        # Background worker drains running_summary_due events so a slow LLM
        # call doesn't block the next append. Coalesces per-scene FIFO.
        if container.extras.get("scene_summary_worker") is None:
            worker = RunningSummaryWorker(container.scenes, container.event_bus)
            worker.start()
            container.extras["scene_summary_worker"] = worker
        if container.extras.get("extractor") is None:
            container.extras["extractor"] = ExtractorService(gateway=llm_gateway)
        extractor = container.extras["extractor"]
        # §3: Now that the gateway exists, wire it through the
        # ContinuityRegistry so per-campaign services get a real LLM
        # judge (instead of the always-uncertain stub) and a vector
        # embedder for HybridFactSearchIndex. Construct in place so the
        # already-instantiated registry — and any cached services it has
        # already handed out — pick up the change on first use; the
        # registry only caches services that have actually been
        # requested, and api/lifespan order means none have yet.
        if isinstance(container.continuity, ContinuityRegistry):
            registry = container.continuity
            registry._embedder = llm_gateway
            registry._judge_gateway = llm_gateway
            registry._judge_request_factory = make_judge_request_factory(
                registry.config.contradiction_check.model_route
            )
        if container.extras.get("context_builder") is None:
            container.extras["context_builder"] = ContextBuilderService(
                library=container.library,
                characters=container.characters,
                world=container.world,
                scenes=container.scenes,
                continuity=container.continuity,
                mechanics=container.mechanics,
                gateway=llm_gateway,
                state_store=container.state_store,
            )
        context_builder = container.extras["context_builder"]

        # Time engine: every dep is already constructed above.
        if container.time_engine is None:
            container.time_engine = TimeEngineService(
                store=container.state_store,
                world=container.world,
                characters=container.characters,
                mechanics=container.mechanics,
                continuity=container.continuity,
                event_bus=container.event_bus,
            )
        # §1 (time-engine remaining): wire the orchestrator's
        # ``turn_complete`` event to drive Time Engine advances. The
        # subscription lives on the container extras so shutdown can
        # disengage it cleanly.
        if container.extras.get("time_engine_subscriber") is None:
            subscriber = TimeEngineSubscriber(
                time_engine=container.time_engine,
                event_bus=container.event_bus,
            )
            subscriber.start()
            container.extras["time_engine_subscriber"] = subscriber

        # Export: scenes is the only required source; others use the bundled
        # services as duck-typed sources. Adapters come from the plugin
        # registry, so installing an export plugin makes it available to
        # /export without further wiring.
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
                data_root=data_root,
            )
            export_cfg = settings.export
            epub_adapter = EpubAdapter(
                sources,
                config=export_cfg.adapters.epub,
                filter_defaults=export_cfg.filters,
            )
            # Spec 13 names EPUB as the v1 priority format; we ship it as a
            # built-in alongside any plugin adapters the registry loaded.
            container.export = ExportService(
                sources=sources,
                adapters=[epub_adapter, *container.plugins.export_adapters()],
                config=ExportServiceConfig.from_export_config(export_cfg),
                state_store=container.state_store,
            )

        # Orchestrator ties the play loop together. ws_push forwards
        # streaming tokens and lifecycle events to subscribed WebSocket
        # clients via StreamManager.
        if container.orchestrator is None:
            container.orchestrator = OrchestratorService(
                event_bus=container.event_bus,
                scene_manager=container.scenes,
                llm_gateway=llm_gateway,
                context_builder=context_builder,
                extractor=extractor,
                state_store=container.state_store,
                mechanics=container.mechanics,
                world=container.world,
                continuity=container.continuity,
                ws_push=container.stream.push,
            )

        # Characters drift fan-out: subscribe to turn_complete and sample
        # drift checks on present characters. The cadence gate inside
        # CharactersService.maybe_check_drift is the source of truth.
        if (
            container.extras.get("characters_integration") is None
            and container.event_bus is not None
            and container.characters is not None
        ):
            chars_integration = CharactersIntegration(
                container.characters,
                container.scenes,
                container.event_bus,
            )
            chars_integration.start()
            container.extras["characters_integration"] = chars_integration

        # Seed default library assets (style guides, etc.) and run one scan
        # so the library_index is populated. We don't start the live
        # watchdog observer here — file changes during runtime won't
        # auto-index, but in-app writes go through StateStore which updates
        # the index directly.
        _seed_defaults(data_root)
        if library_cfg.watch:
            file_watcher = FileWatcher(
                data_root=data_root,
                store=container.state_store,
                bus=container.event_bus,
                scene_manager=container.scenes,
                config=library_cfg,
            )
            container.extras["file_watcher"] = file_watcher
            if library_cfg.scan_on_startup:
                try:
                    await file_watcher.scan_now()
                except Exception:
                    log.exception("initial library scan failed at startup")

        # State Store background workers — embedding drainer (§1), auto-backup
        # (§3), retention sweep (§4), body_compressed summarizer (§5). All
        # honor `state_store_config`; the embedding worker and summarizer
        # quietly back off when no LLM provider is registered yet.
        embedding_worker = EmbeddingWorker(
            store=container.state_store,
            gateway=llm_gateway,
            queue=file_watcher.embedding_queue,
            bus=container.event_bus,
            config=state_store_config.library,
        )
        try:
            await reenqueue_missing_embeddings(container.state_store, file_watcher.embedding_queue)
        except Exception:
            log.exception("reenqueue_missing_embeddings failed at startup")
        await embedding_worker.start()
        container.extras["embedding_worker"] = embedding_worker

        body_summarizer = BodySummarizer(
            store=container.state_store,
            gateway=llm_gateway,
            queue=file_watcher.summary_queue,
            bus=container.event_bus,
        )
        body_summarizer.start()
        container.extras["body_summarizer"] = body_summarizer

        retention_sweeper = RetentionSweeper(
            db=db,
            config=state_store_config.retention,
            bus=container.event_bus,
        )
        await retention_sweeper.start()
        container.extras["retention_sweeper"] = retention_sweeper

        backup_scheduler = BackupScheduler(
            data_root=data_root,
            database_path=settings.resolved_database_path,
            config=state_store_config.auto_backup,
            bus=container.event_bus,
        )
        backup_scheduler.start()
        container.extras["backup_scheduler"] = backup_scheduler

        app.state.container = container
    except Exception:
        # Tear down anything we managed to construct before re-raising,
        # otherwise the connection pool stays open and any partially-built
        # service that holds resources (imagegen workers, stream subscribers)
        # leaks for the life of the process.
        await _shutdown(container, db)
        raise

    try:
        yield
    finally:
        await _shutdown(container, db)


async def _shutdown(container: ServiceContainer | None, db: Database) -> None:
    if container is not None:
        summary_worker = container.extras.get("scene_summary_worker") if container.extras else None
        if summary_worker is not None:
            try:
                await summary_worker.stop()
            except Exception:
                log.exception("scene summary worker stop failed during shutdown")
        scene_indexer = container.extras.get("scene_indexer") if container.extras else None
        if scene_indexer is not None:
            try:
                await scene_indexer.stop()
            except Exception:
                log.exception("scene indexer stop failed during shutdown")
        # Disengage the Time Engine ``turn_complete`` subscriber so the bus
        # doesn't keep forwarding events into a torn-down engine after the
        # lifespan ends.
        time_engine_subscriber = (
            container.extras.get("time_engine_subscriber") if container.extras else None
        )
        if time_engine_subscriber is not None:
            try:
                time_engine_subscriber.stop()
            except Exception:
                log.exception("time engine subscriber stop failed during shutdown")

        # State Store background workers — stop before closing the DB so they
        # don't hit a closed connection mid-loop. Each is independently
        # try/excepted so one failure doesn't strand the others.
        backup_scheduler = container.extras.get("backup_scheduler") if container.extras else None
        if backup_scheduler is not None:
            try:
                backup_scheduler.stop()
            except Exception:
                log.exception("backup_scheduler stop failed during shutdown")
        retention_sweeper = container.extras.get("retention_sweeper") if container.extras else None
        if retention_sweeper is not None:
            try:
                await retention_sweeper.stop()
            except Exception:
                log.exception("retention_sweeper stop failed during shutdown")
        body_summarizer = container.extras.get("body_summarizer") if container.extras else None
        if body_summarizer is not None:
            try:
                await body_summarizer.stop()
            except Exception:
                log.exception("body_summarizer stop failed during shutdown")
        embedding_worker = container.extras.get("embedding_worker") if container.extras else None
        if embedding_worker is not None:
            try:
                await embedding_worker.stop()
            except Exception:
                log.exception("embedding_worker stop failed during shutdown")

        # §3: Stop the gateway health monitor periodic loop if it was started.
        gateway_health_monitor = (
            container.extras.get("gateway_health_monitor") if container.extras else None
        )
        if gateway_health_monitor is not None:
            try:
                await gateway_health_monitor.stop()
            except Exception:
                log.exception("gateway health monitor stop failed during shutdown")
        imagegen_integration = (
            container.extras.get("imagegen_integration") if container.extras else None
        )
        if imagegen_integration is not None:
            try:
                imagegen_integration.stop()
            except Exception:
                log.exception("imagegen integration stop failed during shutdown")
        characters_integration = (
            container.extras.get("characters_integration") if container.extras else None
        )
        if characters_integration is not None:
            try:
                characters_integration.stop()
            except Exception:
                log.exception("characters integration stop failed during shutdown")
        imagegen_health_prober = (
            container.extras.get("imagegen_health_prober") if container.extras else None
        )
        if imagegen_health_prober is not None:
            try:
                await imagegen_health_prober.stop()
            except Exception:
                log.exception("imagegen health prober stop failed during shutdown")
        if container.plugins is not None:
            try:
                await container.plugins.stop_periodic_health()
            except Exception:
                log.exception("plugins periodic health stop failed during shutdown")
        if container.imagegen is not None:
            try:
                await container.imagegen.aclose()
            except Exception:
                log.exception("imagegen aclose failed during shutdown")
        if container.stream is not None:
            try:
                await container.stream.aclose()
            except Exception:
                log.exception("stream aclose failed during shutdown")
    try:
        await db.close()
    except Exception:
        log.exception("db close failed during shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="Grimoire", version=__version__, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(setup_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(library_router, prefix="/api")
    app.include_router(templates_router, prefix="/api")
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(imagegen_router, prefix="/api")
    # WebSocket routes mount under /ws so the Vite dev server's `ws: true`
    # proxy block forwards upgrade requests correctly. The HTTP health probe
    # in the same router lands at /ws/health.
    app.include_router(ws_router, prefix="/ws")
    return app


app = create_app()


__all__ = ["app", "create_app", "settings"]
