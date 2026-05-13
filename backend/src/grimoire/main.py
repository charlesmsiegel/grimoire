import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grimoire import __version__
from grimoire.api.campaigns import router as campaigns_router
from grimoire.api.container import ServiceContainer
from grimoire.api.health import router as health_router
from grimoire.api.library import router as library_router
from grimoire.api.stream import StreamManager
from grimoire.api.ws import router as ws_router
from grimoire.characters import CharactersService
from grimoire.config import settings
from grimoire.continuity import ContinuityService
from grimoire.event_bus import EventBus
from grimoire.imagegen import BackendRegistry, ImageGenService
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.plugins import PluginsConfig, PluginsService
from grimoire.scenes import SceneManager
from grimoire.setting import SettingService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.watcher.watcher import FileWatcher

log = logging.getLogger(__name__)

_SEED_ROOT = Path(__file__).resolve().parent / "seed" / "library"


def _seed_defaults(data_root: Path) -> None:
    """Copy bundled default library assets into the user's data root.

    Only fills in files the user doesn't already have — existing files are
    never overwritten, so user edits and additions are preserved. Called once
    per startup before the initial library scan.
    """
    if not _SEED_ROOT.is_dir():
        return
    library_root = data_root / "library"
    for src in _SEED_ROOT.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(_SEED_ROOT)
        dst = library_root / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        log.info("seeded default %s", rel)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(
        settings.resolved_database_path,
        pool_size=settings.db_pool_size,
        enable_wal=settings.enable_wal,
    )
    await db.connect()
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
    for sub in ("library", "mechanics", "plugins", "config/plugins"):
        (data_root / sub).mkdir(parents=True, exist_ok=True)

    if container.state_store is None:
        container.state_store = StateStore(db=db, data_root=data_root)
    if container.library is None:
        container.library = LibraryService(container.state_store)
    if container.setting is None:
        container.setting = SettingService(container.library)
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
        # In-memory store by default — facts/commitments don't persist across
        # restart. Swap in SqliteContinuityStore when persistence matters.
        container.continuity = ContinuityService()
    if container.imagegen is None:
        # No image-generation backends registered. /images endpoints (read) work
        # against the SQLite index; generation requests will fail until a
        # backend plugin is installed and registered with the registry.
        container.imagegen = ImageGenService(
            store=container.state_store,
            registry=BackendRegistry(),
            default_backend_id="none",
            event_bus=container.event_bus,
        )

    # Seed default library assets (style guides, etc.) and run one scan so
    # the library_index is populated. We don't start the live watchdog
    # observer here — file changes during runtime won't auto-index, but
    # in-app writes go through StateStore which updates the index directly.
    _seed_defaults(data_root)
    file_watcher = FileWatcher(
        data_root=data_root, store=container.state_store, bus=container.event_bus
    )
    try:
        await file_watcher.scan_now()
    except Exception:
        log.exception("initial library scan failed at startup")

    app.state.container = container

    try:
        yield
    finally:
        if container.imagegen is not None:
            await container.imagegen.aclose()
        if container.stream is not None:
            await container.stream.aclose()
        await db.close()


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
    app.include_router(library_router, prefix="/api")
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(ws_router, prefix="/api")
    return app


app = create_app()


__all__ = ["app", "create_app", "settings"]
