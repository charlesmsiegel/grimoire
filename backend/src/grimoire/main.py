"""FastAPI app assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import router
from .store import migrations

DIST = Path(__file__).resolve().parents[2].parent / "frontend" / "dist"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    migrations.migrate_scene_ids()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="grimoire", lifespan=_lifespan)
    # character detail responses run to hundreds of KB of JSON; payloads under
    # the floor (and streaming responses) pass through untouched
    # compresslevel 6 over the default 9: ~2-3x less CPU for ~1% larger output
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(router, prefix="/api")

    if DIST.exists():
        app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")

    return app


app = create_app()
