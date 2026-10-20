"""worlds: bundles responsibilities."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ... import store
from .. import runs
from ..common import _spooled_upload, _world_root_or_404
from ._router import router as router

# ---- world bundles: export / import (#54) ----
#
# `export.zip` is a literal third segment and `entities` registers the generic
# `/worlds/{wid}/{kind}` that would swallow it -- safe because `entities` is
# included last (see routes/__init__), and pinned by tests/test_route_order.py.


@router.get("/worlds/{wid}/export.zip")
def get_world_export(wid: str, request: Request):
    """The whole world directory as a bundle.

    Built to a temp file and streamed rather than returned as bytes: a world
    with a full character gallery runs past a gigabyte, and buffering that into
    a response is how the Android build dies. A sync `def` route, so FastAPI
    runs the zipping in its threadpool and the event loop keeps serving.

    Held against image-store maintenance while the bundle is BUILT -- the walk
    reads every placement and the object it names -- and not while it streams,
    which reads only the finished temp file.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with runs.maintenance_excluded(request.app):
            _world_root_or_404(wid)
            store.world_bundle.write_bundle(wid, tmp)
        size = tmp.stat().st_size
    except store.world_bundle.BundleError as exc:
        # A world this grimoire cannot bundle (an image past the cap its own
        # import holds a bundle to): the world's state, not a server fault,
        # and the message names the picture to replace.
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    def stream():
        try:
            with open(tmp, "rb") as f:      # atomic guard: read-only, not a record write
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            tmp.unlink(missing_ok=True)

    return StreamingResponse(
        stream(), media_type="application/zip",
        # `identity` is what makes GZipMiddleware stand aside (it skips any
        # response that already declares an encoding). Deflating a zip of
        # already-deflated records buys nothing and costs a full pass over a
        # gigabyte -- and standing aside is also what keeps Content-Length,
        # so the browser can show real download progress.
        headers={"Content-Disposition":
                 f'attachment; filename="{store.world_bundle.bundle_filename(wid)}"',
                 "Content-Encoding": "identity",
                 "Content-Length": str(size)})


@router.post("/worlds/import")
async def post_world_import(request: Request):
    """Import a bundle as a **new** world; the body is the raw zip.

    Raw rather than multipart, matching `POST /modules/import`: the two are the
    same operation, and a gigabyte of multipart framing buys nothing.
    """
    # One cap, the store's: export refuses to write a bundle past it.
    async with _spooled_upload(request, store.world_bundle.MAX_BUNDLE_BYTES,
                               "bundle too large") as tmp:
        try:
            # In a worker thread: unzipping a large world would otherwise block
            # the event loop for the whole import (see post_module_import).
            wid = await run_in_threadpool(store.world_bundle.import_bundle, tmp)
        except store.world_bundle.BundleConflict as exc:
            # A good bundle that lost an id race, not a bad upload -- 409, so a
            # client can retry it rather than reading 400 as "this file is
            # broken" (Codex review). Must be caught before BundleError.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except store.world_bundle.BundleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Same reason as `post_world`: a store someone imported a world into has
    # been set up, so deleting that world later must not reopen the wizard
    # (#194). Past the rename the world exists, so a failure here must not be
    # reported as a failed import -- the caller would retry and import a second
    # copy (Codex review). The flag is backfilled by the next config read
    # anyway; this only saves that read a scan.
    with contextlib.suppress(OSError):
        store.config.mark_setup_done()
    return {"id": wid}
