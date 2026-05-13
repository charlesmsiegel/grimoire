from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grimoire import __version__
from grimoire.api.campaigns import router as campaigns_router
from grimoire.api.container import ServiceContainer
from grimoire.api.health import router as health_router
from grimoire.api.library import router as library_router
from grimoire.api.stream import StreamManager
from grimoire.api.ws import router as ws_router
from grimoire.config import settings
from grimoire.event_bus import EventBus
from grimoire.storage import Database, apply_migrations


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
    app.state.container = container

    try:
        yield
    finally:
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
