"""FastAPI app assembly."""

from __future__ import annotations

import logging
import os
import anyio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException

from .routes import router
from .store import campaigns, locks, migrations, module_edit

DEFAULT_DIST = Path(__file__).resolve().parents[2].parent / "frontend" / "dist"  # paths-ok: DEFAULT_DIST only; GRIMOIRE_DIST overrides it on Android


def dist_dir() -> Path:
    """Resolve the built-frontend directory.

    ``GRIMOIRE_DIST`` overrides the repo-relative default for builds where the
    source tree isn't laid out as a checkout (the Android APK extracts the
    bundle to app storage).
    """
    env = os.environ.get("GRIMOIRE_DIST")
    return Path(env) if env else DEFAULT_DIST


class SPAStaticFiles(StaticFiles):
    """The built frontend, with a client-route fallback to ``index.html``.

    The frontend is a ``BrowserRouter`` SPA: ``/worlds``, ``/campaigns/<cid>``
    and now ``/campaigns/<cid>/scenes/<sid>`` (#87) are paths the *browser*
    resolves. But a reload, a bookmark or a shared link asks the SERVER for
    that path first, and plain ``StaticFiles`` has no file to answer with.
    ``html=True`` does not cover it -- it serves directory indexes and looks
    for a ``404.html``, it does not route unknown paths to the root document --
    so every client route answered 404 in the packaged desktop and Android
    builds. Only the Vite dev server had a fallback, which is why development
    never saw it. Surviving a reload is the whole of #87, so the feature needed
    this to be true anywhere but ``vite dev``.

    Two things deliberately keep their 404:

    - **Anything under ``/api``.** An unknown endpoint must stay a 404 for the
      caller that asked for it, not become an HTML page with a 200 on it.
    - **Requests that did not ask for a page.** A missing ``.js`` or image
      answered with ``index.html`` turns "file not found" into a document the
      browser tries to parse as a script -- a clear error replaced by a
      confusing one. Navigations send ``text/html`` in ``Accept``; subresource
      requests do not.
    """

    @staticmethod
    def _under_api(path: str) -> bool:
        """Is ``path`` the ``api`` mount or something below it?

        ``path`` arrives from ``StaticFiles.get_path``, which resolves the URL
        with ``os.path`` -- so on Windows it is spelled ``api\\endpoint`` and a
        comparison against ``"api/"`` never fires, handing every mistyped
        endpoint the SPA fallback and a 200 (#313). Ubuntu CI cannot produce
        that separator, so both are normalized here rather than trusting the
        platform's.
        """
        return path.replace("\\", "/").split("/", 1)[0] == "api"

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or self._under_api(path):
                raise
            if "text/html" not in Headers(scope=scope).get("accept", ""):
                raise
            return await super().get_response("index.html", scope)


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


class _CampaignActivityStamp:
    """Record campaign activity for any successful campaign-scoped write.

    Enumerating the mutators does not converge. Six review rounds each found
    another one -- scenes, overlay entities, greetings, plot edges, actors,
    calendar, group state, sheets, module binding, images, climate, weather,
    play state -- because the store deliberately takes *roots* rather than cids
    at its leaves, so there is no one function they all pass through. They do
    all pass through here: a mutating method on a route that bound a `cid` is
    exactly the set, by definition, and a route added later is covered without
    anyone having to remember.

    Written as raw ASGI rather than `@app.middleware("http")` on purpose.
    That decorator wraps `BaseHTTPMiddleware`, which reshapes how exceptions
    leave the app -- a route raising a non-HTTPException surfaces as
    "No response returned" instead of propagating. The scene-commit
    crash-recovery test caught exactly that, and transcripts are the one
    artifact in this app that cannot be regenerated, so this stays out of the
    exception path entirely: it awaits the app, and anything raised passes
    through untouched because there is nothing here to catch it.

    Conditions are deliberately narrow: only mutating methods, so a GET never
    stamps; only 2xx, so a rejected write records nothing; only when routing
    bound a `cid`, so world and config routes are untouched.
    """

    _MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    #: `cid` is not reserved: `/api/worlds/{wid}/characters/{cid}` binds a
    #: *character* id under that name, so matching on the parameter alone would
    #: stamp whichever campaign happened to share the character's slug. The
    #: path prefix is what actually says "this is campaign-scoped".
    _PREFIX = "/api/campaigns/"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") not in self._MUTATING
                or not scope.get("path", "").startswith(self._PREFIX)):
            return await self.app(scope, receive, send)
        async def _send(message):
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers") or []}
                # A stream's HTTP status is sent before its outcome is known:
                # the chat route answers 200 and can still fail mid-stream,
                # rolling back the message it had posted. Skipping those costs
                # nothing -- a turn that *does* land advances the scene's own
                # `updated`, which the campaign's activity already folds in.
                streaming = b"text/event-stream" in headers.get(b"content-type", b"")
                # `path_params` is populated by the router during handling, so
                # by now it holds what actually matched.
                cid = (scope.get("path_params") or {}).get("cid")
                # POST is not a synonym for write. Routes that compute and
                # return without persisting -- a generated voice anchor, scene
                # suggestions, a replayed roll -- declare themselves with
                # @computes_only, so merely *looking* at a campaign does not
                # move it up the recents rail.
                preview = getattr(getattr(scope.get("route"), "endpoint", None),
                                  "grimoire_computes_only", False)
                if message["status"] < 300 and not streaming and cid and not preview:
                    # BEFORE forwarding the status, so the stamp has landed by
                    # the time the client can observe success. Otherwise a
                    # caller can navigate on the response and have the sidebar's
                    # refetch read the pre-stamp value, which is exactly the
                    # stale ordering the stamp exists to prevent.
                    #
                    # Off the event loop even so: this is an atomic replace plus
                    # fsync, and `atomic` sleeps through Windows sharing-violation
                    # retries, so on a synced or removable store it can block long
                    # enough to stall every other request and live stream on this
                    # worker. Sync route bodies get a thread from Starlette;
                    # middleware does not. `touch_quietly` never raises, so this
                    # cannot turn a completed write into a failure.
                    await anyio.to_thread.run_sync(campaigns.touch_quietly, cid)
            await send(message)

        await self.app(scope, receive, _send)


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

    app.add_middleware(_CampaignActivityStamp)

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
        app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="static")

    return app


app = create_app()
