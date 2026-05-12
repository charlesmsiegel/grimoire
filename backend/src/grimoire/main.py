from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grimoire import __version__
from grimoire.api.health import router as health_router
from grimoire.config import settings
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
    try:
        yield
    finally:
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
    return app


app = create_app()


__all__ = ["app", "create_app", "settings"]
