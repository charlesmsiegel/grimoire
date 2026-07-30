"""FastAPI app assembly."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import router
from .store import locks, migrations, module_edit

DEFAULT_DIST = Path(__file__).resolve().parents[2].parent / "frontend" / "dist"  # paths-ok: DEFAULT_DIST only; GRIMOIRE_DIST overrides it on Android


def dist_dir() -> Path:
    """Resolve the built-frontend directory.

    ``GRIMOIRE_DIST`` overrides the repo-relative default for builds where the
    source tree isn't laid out as a checkout (the Android APK extracts the
    bundle to app storage).
    """
    env = os.environ.get("GRIMOIRE_DIST")
    return Path(env) if env else DEFAULT_DIST


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Every step here can now hit a cross-process lock (#234) --
    # migrate_scene_ids reaches one through scenes.repad, not just recover().
    # A second backend starting while the first is mid-edit must SERVE, not
    # refuse to start: all three steps are idempotent and re-run later
    # (recovery at the head of every module_edit operation, the migrations at
    # the next startup), so skipping beats failing to boot. Startup is also the
    # one place the 409 handler cannot reach.
    #
    # Each step is guarded separately so contention in one does not skip the
    # others, and each logs at WARNING: silently starting with a stale journal
    # or a half-applied migration is exactly the kind of thing that should be
    # visible in the log.
    log = logging.getLogger(__name__)
    for step in (migrations.migrate_scene_ids, migrations.bake_char_macros,
                 module_edit.recover):
        try:
            step()
        except locks.StoreBusy as exc:
            log.warning("startup step %s skipped -- %s; it will be retried",
                        step.__name__, exc)
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

    @app.exception_handler(locks.StoreBusy)
    async def store_busy_handler(request: Request, exc: locks.StoreBusy):
        # One handler rather than a try/except at every one of the ~35 call
        # sites that can take a campaign or module-edit lock (#234).
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    app.include_router(router, prefix="/api")

    dist = dist_dir()
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")

    return app


app = create_app()
