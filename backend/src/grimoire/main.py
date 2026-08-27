"""FastAPI app assembly."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import runner
from .health import ProviderHealth
from .routes import build_llm, build_openai_compatible_client, router, runs
from .store import backups, campaigns, locks, logs, migrations, module_edit, revision

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
        that separator, so BOTH are normalized here rather than trusting the
        platform's: a fix keyed to ``os.sep`` would be correct and untestable.

        The *resolved* path is deliberately what gets asked, rather than the
        raw ``scope["path"]`` -- which would sidestep the separator question
        entirely and be worse. ``get_path`` has already collapsed ``..``, so
        ``/x/../api/gone`` arrives here as ``api/gone``; a check against the URL
        would read the literal spelling, miss it, and hand back the SPA for a
        request the file lookup had already resolved into ``/api``.

        Whole first segment, not a prefix: ``/apiary`` is a client route, and
        answering it with a 404 would cost a real page its fallback.
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


#: How often the backup schedule is *checked*. Not how often a backup happens —
#: that is `backup_interval_hours`, which this tick compares against the newest
#: archive's own timestamp. An hour is the resolution the interval is expressed
#: in, so checking more often would only find the same answer sooner.
BACKUP_TICK_SECONDS = 3600.0


async def _backup_ticker() -> None:
    """The whole of the backup schedule (#32): check at startup, then hourly.

    There is no cron and no daemon — this app is a local server someone runs
    while they are using it, and the store is only mutated while it runs, so
    "for however long the server is up" covers everything that can change.
    A machine that never opens grimoire has nothing new to back up.

    Off the event loop: zipping a library is minutes of blocking I/O, and a
    live scene stream shares this worker. The thread is not abandoned on
    cancellation (anyio's default), so a shutdown that lands mid-archive waits
    for it — a pause on quit, in exchange for never leaving a thread writing
    into a store the next process is about to open. Every failure is caught and logged
    rather than allowed to end the loop — a full disk tonight must not mean no
    backups after it is cleared, and `BaseException` is deliberately not caught
    so cancellation at shutdown still stops the task.
    """
    log = logging.getLogger(__name__)
    while True:
        try:
            made = await anyio.to_thread.run_sync(backups.run_scheduled)
            if made is not None:
                log.info("wrote scheduled backup %s", made.name)
        except Exception as exc:  # noqa: BLE001 — see the docstring
            log.warning("scheduled backup skipped -- %s", exc)
        await anyio.sleep(BACKUP_TICK_SECONDS)


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
                 migrations.backfill_scene_identities, module_edit.recover):
        try:
            step()
        except locks.StoreBusy as exc:
            log.warning("startup step %s skipped -- %s; it will be retried",
                        step.__name__, exc)
    # The ticker outlives startup and is cancelled on the way out, so a server
    # stopping mid-zip does not leave a thread writing into a store the next
    # process is about to open. `atomic.streaming_write` is what makes that
    # safe: an interrupted archive was never published.
    try:
        # The portal is what lets a SYNCHRONOUS streaming handler hand work to
        # this loop: every producing route is `def`, so FastAPI runs it in a
        # threadpool worker, and `tg.start_soon` is not thread-safe from there.
        # `runner.install` also swaps the registry's event factory for one that
        # builds on this loop -- see `_PortalEvent`.
        async with (anyio.create_task_group() as tg,
                    anyio.from_thread.BlockingPortal() as portal):
            app.state.run_portal = portal
            runner.install(app, tg)
            tg.start_soon(_backup_ticker)
            yield
            tg.cancel_scope.cancel()
    finally:
        # Closing the gateway clients' `httpx` pools (#215). Worth nothing
        # where the server *is* the process -- the pool dies with it -- and
        # everything where an app is rebuilt inside a living one: the Android
        # entry point starts uvicorn in-process, and this suite builds an app
        # per test.
        #
        # Outside the task group: an `await` in a scope that has just been
        # cancelled is cancelled itself, so a close in there would drain
        # nothing. In a `finally`: a shutdown arriving as an exception is still
        # a shutdown. Each close guarded: a failing one must neither strand the
        # other pool nor bury the exception on its way out.
        #
        # Blind spot: these are the pools an *app* owns. The `EmbeddingsClient`
        # singletons in `store/semsearch` and `store/context/semantic` are
        # reachable only from store code with no app to hang them on, and are
        # still closed by nobody. `test_llm_lifecycle` fails if a closable is
        # added to `app.state` and left out of the loop below.
        for client in (app.state.llm, app.state.openai_compatible):
            try:
                await client.aclose()
            except Exception as exc:  # noqa: BLE001 -- see above
                log.warning("closing %s failed -- %s", type(client).__name__, exc)


def _record_campaign_write(cid: str, stamp: bool, changed: bool) -> None:
    """Both records a campaign write leaves behind, in one thread hop.

    The activity stamp orders the recents rail; the revision token (#409) is
    what a caller compares to ask "is this campaign still in the state I priced
    against?". Recorded together because they are recorded at the same moment
    and by nearly the same rule, and taken as two flags because the two places
    the rules differ are both deliberate:

    - **A STREAM stamps no activity and does move the revision.** A stream's
      status line is sent before its outcome is known, so activity would rank a
      campaign for a turn that then failed and rolled back -- but by the time
      that line is sent, every producing route has already committed its setup
      under the campaign lock (the player's post, the speaker stamp, the
      retired proposal), and a token that had not moved would let an advance
      priced before the send confirm against a transcript that has already
      grown. The two errors are not symmetric: a stamp for a turn that failed
      misorders a list, a token that failed to move breaks the one promise this
      value makes. `routes/streaming.py`'s `_persist_reply` bumps again for the
      reply itself, minutes later.
    - **A route that mutates a DIFFERENT campaign stamps activity and does not
      move the revision.** `POST /campaigns/{cid}/fork` is something that
      happened in the campaign it names and writes nothing to it
      (`store/fork.py`: "The source is never written to"), so a fork that moved
      the source's revision would invalidate the very expectation the caller
      took the fork to protect. `@leaves_campaign_unchanged` is how it says so.

    Neither call raises, which is what makes it safe to run after the mutation
    has already committed -- see `campaigns.touch_quietly` and `revision.bump`.
    """
    if stamp:
        campaigns.touch_quietly(cid)
    if changed:
        revision.bump(cid)


class _CampaignActivityStamp:
    """Record campaign activity for any successful campaign-scoped write — and,
    since #409, the campaign's write token beside it (`_record_campaign_write`).

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
                # rolling back the message it had posted. Skipping the STAMP for
                # those costs nothing -- a turn that *does* land advances the
                # scene's own `updated`, which the campaign's activity already
                # folds in. The revision is not skipped, and
                # `_record_campaign_write` says why the two part company here.
                streaming = b"text/event-stream" in headers.get(b"content-type", b"")
                # `path_params` is populated by the router during handling, so
                # by now it holds what actually matched.
                cid = (scope.get("path_params") or {}).get("cid")
                # POST is not a synonym for write. Routes that compute and
                # return without persisting -- a generated voice anchor, scene
                # suggestions, a replayed roll -- declare themselves with
                # @computes_only, so merely *looking* at a campaign does not
                # move it up the recents rail.
                endpoint = getattr(scope.get("route"), "endpoint", None)
                preview = getattr(endpoint, "grimoire_computes_only", False)
                # A route that mutates a DIFFERENT campaign than the one it
                # names still counts as activity here and must not move this
                # campaign's revision -- see `_record_campaign_write`.
                changed = not getattr(endpoint, "grimoire_leaves_campaign", False)
                if message["status"] < 300 and cid and not preview:
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
                    # middleware does not. Neither record raises, so this
                    # cannot turn a completed write into a failure.
                    await anyio.to_thread.run_sync(
                        _record_campaign_write, cid, not streaming, changed)
            await send(message)

        await self.app(scope, receive, _send)


def create_app() -> FastAPI:
    # Before anything else that can log. Fifteen modules under this package
    # call `logging.getLogger(__name__)` and, until this line existed, logged
    # to nobody: nothing ever attached a handler, so `logging`'s last-resort
    # one printed WARNING and above to a stderr that a packaged desktop build
    # or an Android APK does not show anyone. `store.logs` explains what it
    # attaches to and, more importantly, what it deliberately does not.
    logs.install()
    app = FastAPI(title="grimoire", lifespan=_lifespan)
    # What each connection's provider last did, for this app (#146). Built
    # before the gateway because the gateway reports into it: every generation
    # that settles tells the registry what happened, which is what keeps the
    # status bar honest between explicit checks.
    app.state.health = ProviderHealth()
    # The gateway clients belong to the app, not the module (#215): each owns an
    # `httpx` connection pool, and `_lifespan` closes both on shutdown. Built
    # here rather than in the lifespan so the dependency resolves for a
    # `TestClient` that never runs one -- and it costs nothing to, since neither
    # client opens a socket before its first call.
    app.state.llm = build_llm(app.state.health)
    app.state.openai_compatible = build_openai_compatible_client()
    # Same reasoning as the gateway clients directly above: the run registry is
    # pure data with nothing to close, and a bare `TestClient` never runs a
    # lifespan -- so a registry created only at startup would be absent for
    # every route test and every migrated handler. `runner.install` attaches the
    # parts that do need a running loop.
    runs.install_registry(app)
    # character detail responses run to hundreds of KB of JSON; payloads under
    # the floor (and streaming responses) pass through untouched
    # compresslevel 6 over the default 9: ~2-3x less CPU for ~1% larger output
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
        # `Retry-After` is not one of CORS's safelisted response headers, so a
        # caller on one of the origins above cannot read it unless it is named
        # here -- and it is the only thing that makes a 429 actionable (#213).
        # The app's own frontend reaches the API through vite's `/api` proxy and
        # so is same-origin, which is exactly why this would otherwise stay
        # broken unnoticed for whoever does call across.
        expose_headers=["Retry-After"],
    )

    app.add_middleware(_CampaignActivityStamp)

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException):
        # `headers` is forwarded rather than dropped: it is how a 429 carries the
        # provider's own `Retry-After` (#213), and a normalizer that silently ate
        # response headers would make every future one arrive nowhere.
        content = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

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
