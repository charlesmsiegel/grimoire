import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grimoire import __version__
from grimoire.api.alternates import router as alternates_router
from grimoire.api.auxiliary import router as auxiliary_router
from grimoire.api.calendars import router as calendars_router
from grimoire.api.campaigns import router as campaigns_router
from grimoire.api.config import router as config_router
from grimoire.api.container import ServiceContainer
from grimoire.api.context import router as context_router
from grimoire.api.expressions import router as expressions_router
from grimoire.api.extras import router as extras_router
from grimoire.api.health import router as health_router
from grimoire.api.hud import router as hud_router
from grimoire.api.imagegen import router as imagegen_router
from grimoire.api.imports import router as imports_router
from grimoire.api.library import router as library_router
from grimoire.api.observability import router as observability_router
from grimoire.api.setup import router as setup_router
from grimoire.api.templates import router as templates_router
from grimoire.api.transient_state import router as transient_state_router
from grimoire.api.ws import router as ws_router
from grimoire.bootstrap import (
    background_reconcile,
    build_content_services,
    build_llm_services,
    build_play_services,
    start_background_workers,
)
from grimoire.config import settings
from grimoire.library import LibraryConfig
from grimoire.storage import Database, apply_migrations

log = logging.getLogger(__name__)


async def _teardown(container: ServiceContainer | None, db: Database, *, close_db: bool) -> None:
    """Stop registered workers/subscribers in reverse order, then close the DB.

    The container's :class:`LifecycleManager` owns the stop sequence; the DB is
    closed last (and only if we opened it) so no worker hits a dead connection.
    """
    if container is not None and container.lifecycle is not None:
        await container.lifecycle.stop_all()
    if close_db:
        try:
            await db.close()
        except Exception:
            log.exception("db close failed during shutdown")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Construct services in dependency order, then tear them down in reverse.

    The wiring lives in :mod:`grimoire.bootstrap` as ordered phases; this driver
    only owns the database lifecycle, the cold-start vs. pre-wired decision, and
    orderly teardown via the container's ``LifecycleManager``.
    """
    container: ServiceContainer | None = getattr(app.state, "container", None)
    prewired = container is not None
    # Reuse a pre-wired container's db (and its connection pool) so any already
    # attached services keep pointing at the same connection; otherwise open one
    # of our own and remember that we must close it.
    if container is not None and container.state_store is not None:
        db = container.state_store.db
        owned_db = False
    else:
        db = Database(
            settings.resolved_database_path,
            pool_size=settings.db_pool_size,
            enable_wal=settings.enable_wal,
        )
        await db.connect()
        owned_db = True

    bg_task: asyncio.Task[None] | None = None
    try:
        await apply_migrations(db)
        app.state.db = db
        if container is None:
            container = ServiceContainer(db=db)
        container.db = db

        await build_content_services(settings, container, db)
        await build_llm_services(settings, container, db)
        await build_play_services(settings, container)
        embedding_queue = await start_background_workers(settings, container)
        app.state.container = container

        # A pre-wired container (tests) reconciles inline so state is ready before
        # the first request; a cold start defers it to a background task so the
        # API begins serving immediately while indexes backfill.
        scan_on_startup = LibraryConfig.from_yaml(
            settings.data_root / "config" / "library.yaml"
        ).scan_on_startup
        if prewired:
            await background_reconcile(
                container, embedding_queue, scan_library=scan_on_startup, delay=0.0
            )
        else:
            bg_task = asyncio.create_task(
                background_reconcile(container, embedding_queue, scan_library=scan_on_startup),
                name="background-reconcile",
            )
    except Exception:
        # Tear down whatever got constructed so a partial startup can't leak the
        # connection pool or any background workers we managed to start.
        await _teardown(container, db, close_db=owned_db)
        raise

    try:
        yield
    finally:
        if bg_task is not None and not bg_task.done():
            bg_task.cancel()
            with suppress(asyncio.CancelledError):
                await bg_task
        await _teardown(container, db, close_db=owned_db)


_DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def _resolve_cors_origins() -> list[str]:
    """Build the CORS allowlist from the env vars run.sh already exposes.

    GRIMOIRE_FRONTEND_ORIGINS wins outright (comma-separated). Otherwise we
    keep the defaults plus, if the user overrode the frontend host/port,
    the matching ``http://<host>:<port>`` so a non-default Vite dev server
    isn't blocked at the browser without any signal.
    """
    explicit = os.environ.get("GRIMOIRE_FRONTEND_ORIGINS")
    if explicit:
        origins = [o.strip() for o in explicit.split(",") if o.strip()]
        if origins:
            return origins
    origins = list(_DEFAULT_CORS_ORIGINS)
    host = os.environ.get("GRIMOIRE_FRONTEND_HOST")
    port = os.environ.get("GRIMOIRE_FRONTEND_PORT")
    if host or port:
        host = host or "127.0.0.1"
        port = port or "5173"
        extra = f"http://{host}:{port}"
        if extra not in origins:
            origins.append(extra)
    return origins


def create_app() -> FastAPI:
    app = FastAPI(title="Grimoire", version=__version__, lifespan=lifespan)

    # CORS allowlist: built from the same env vars that run.sh exposes so
    # pointing Vite at a non-default host or port (or via tunnel) doesn't
    # silently fall through to the default origins and 403 every request
    # without a hint as to why. Set GRIMOIRE_FRONTEND_ORIGINS to override
    # entirely (comma-separated).
    cors_origins = _resolve_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(setup_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(library_router, prefix="/api")
    app.include_router(calendars_router, prefix="/api")
    app.include_router(imports_router, prefix="/api")
    app.include_router(templates_router, prefix="/api")
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(alternates_router, prefix="/api")
    app.include_router(auxiliary_router, prefix="/api")
    app.include_router(imagegen_router, prefix="/api")
    app.include_router(hud_router, prefix="/api")
    app.include_router(expressions_router, prefix="/api")
    app.include_router(observability_router, prefix="/api")
    app.include_router(transient_state_router, prefix="/api")
    app.include_router(context_router, prefix="/api")
    app.include_router(extras_router, prefix="/api")
    # WebSocket routes mount under /ws so the Vite dev server's `ws: true`
    # proxy block forwards upgrade requests correctly. The HTTP health probe
    # in the same router lands at /ws/health.
    app.include_router(ws_router, prefix="/ws")
    return app


app = create_app()


__all__ = ["app", "create_app", "settings"]
