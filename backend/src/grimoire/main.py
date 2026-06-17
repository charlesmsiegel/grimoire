"""FastAPI app assembly."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import router

DIST = Path(__file__).resolve().parents[2].parent / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="grimoire")
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
