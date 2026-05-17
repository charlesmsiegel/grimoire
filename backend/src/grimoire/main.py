import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grimoire import __version__
from grimoire.api.campaigns import router as campaigns_router
from grimoire.api.container import ServiceContainer
from grimoire.api.health import router as health_router
from grimoire.api.library import router as library_router
from grimoire.api.setup import router as setup_router
from grimoire.api.stream import StreamManager
from grimoire.api.templates import router as templates_router
from grimoire.api.ws import router as ws_router
from grimoire.characters import CharactersService
from grimoire.config import settings
from grimoire.context.builder import ContextBuilderService
from grimoire.continuity import ContinuityService
from grimoire.event_bus import EventBus
from grimoire.export.service import ExportService
from grimoire.export.sources import DataSources
from grimoire.extractor.service import ExtractorService
from grimoire.imagegen import BackendRegistry, ImageGenService
from grimoire.library import LibraryService
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.observability.health import HealthMonitorService
from grimoire.orchestrator.service import OrchestratorService
from grimoire.plugins import PluginsConfig, PluginsService
from grimoire.scenes import SceneManager
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.time_engine.service import TimeEngineService
from grimoire.watcher.watcher import FileWatcher
from grimoire.world import WorldService

log = logging.getLogger(__name__)

_SEED_ROOT = Path(__file__).resolve().parent / "seed" / "library"


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

        # User-supplied prompt template variants live under {data_root}/templates
        # and take precedence over the bundled defaults so a user can drop in a
        # new variant and select it without rebuilding the package.
        from grimoire.templates import registry as template_registry

        template_registry.register_search_path(data_root / "templates", prepend=True)

        if container.state_store is None:
            container.state_store = StateStore(db=db, data_root=data_root)
        if container.library is None:
            container.library = LibraryService(container.state_store)
        if container.world is None:
            container.world = WorldService(container.library)
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
            container.plugins = PluginsService(PluginsConfig.for_data_root(data_root))
            try:
                await container.plugins.rescan()
            except Exception:
                log.exception("plugins rescan failed at startup")
        if container.characters is None:
            container.characters = CharactersService(container.library, container.mechanics)
        if container.scenes is None:
            container.scenes = SceneManager(data_root, event_bus=container.event_bus)
        if container.continuity is None:
            # In-memory store by default — facts/commitments don't persist
            # across restart. Swap in SqliteContinuityStore when persistence
            # matters.
            container.continuity = ContinuityService()
        if container.imagegen is None:
            # No image-generation backends registered. /images endpoints (read)
            # work against the SQLite index; queue_generation / active_backend
            # will raise NoBackendAvailableError until a backend plugin is
            # installed and registered with the registry. Routes that need a
            # backend should catch that and 503 with a clear message.
            container.imagegen = ImageGenService(
                store=container.state_store,
                registry=BackendRegistry(),
                default_backend_id=None,
                event_bus=container.event_bus,
            )

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
        if container.extras.get("extractor") is None:
            container.extras["extractor"] = ExtractorService(gateway=llm_gateway)
        extractor = container.extras["extractor"]
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

        # Export: scenes is the only required source; others use the bundled
        # services as duck-typed sources. Adapters come from the plugin
        # registry, so installing an export plugin makes it available to
        # /export without further wiring.
        if container.export is None:
            sources = DataSources(
                scenes=container.scenes,
                characters=container.characters,
                world=container.world,
                continuity=container.continuity,
                images=container.imagegen,
                data_root=data_root,
            )
            container.export = ExportService(
                sources=sources,
                adapters=container.plugins.export_adapters(),
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
                ws_push=container.stream.push,
            )

        # Seed default library assets (style guides, etc.) and run one scan
        # so the library_index is populated. We don't start the live
        # watchdog observer here — file changes during runtime won't
        # auto-index, but in-app writes go through StateStore which updates
        # the index directly.
        _seed_defaults(data_root)
        file_watcher = FileWatcher(
            data_root=data_root, store=container.state_store, bus=container.event_bus
        )
        try:
            await file_watcher.scan_now()
        except Exception:
            log.exception("initial library scan failed at startup")

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
        # §3: Stop the gateway health monitor periodic loop if it was started.
        gateway_health_monitor = (
            container.extras.get("gateway_health_monitor") if container.extras else None
        )
        if gateway_health_monitor is not None:
            try:
                await gateway_health_monitor.stop()
            except Exception:
                log.exception("gateway health monitor stop failed during shutdown")
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
    app.include_router(library_router, prefix="/api")
    app.include_router(templates_router, prefix="/api")
    app.include_router(campaigns_router, prefix="/api")
    # WebSocket routes mount under /ws so the Vite dev server's `ws: true`
    # proxy block forwards upgrade requests correctly. The HTTP health probe
    # in the same router lands at /ws/health.
    app.include_router(ws_router, prefix="/ws")
    return app


app = create_app()


__all__ = ["app", "create_app", "settings"]
