"""Shared helpers for the route modules.

Dependency-injection providers, the pydantic-version shim, the response-scope
read/write pair, image serving, and the 404 guards every domain module reuses.
This module holds no routes and imports no sibling route module, so it is
always safe to import from one.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..openai_compatible import OpenAICompatibleClient


# The idle bound is passed as a resolver, not a number: llm.py must not import
# the store (#239), and reading config.md per call is what lets a
# Configuration-page change land without a restart (#243).
_llm = LLMClient(timeout=store.config.llm_timeout)
_openai_compatible_client = OpenAICompatibleClient()


def _dump(model: BaseModel) -> dict:
    """model_dump() on pydantic v2, dict() on v1. The Android build may pin the
    pure-python pydantic 1.x wheel (docs/android-architecture.md §7); this is
    the only v2-specific API the codebase uses."""
    dump = getattr(model, "model_dump", None)
    return dump() if dump is not None else model.dict()


def _turn_override(body) -> dict | None:
    """A request body's one-shot per-turn response override as a plain dict.

    The wire type is `ResponseSettings`, like every other response write path,
    so a malformed payload is rejected at the boundary instead of reaching
    response_presets.resolve mid-generation. Unset fields are dropped: a scope
    dict means "these fields have an opinion", and a None would read as one.
    """
    if body is None or getattr(body, "response", None) is None:
        return None
    return {k: v for k, v in _dump(body.response).items() if v is not None}


def _record_prompt(cid: str, sid: str, task: str, breakdown: dict | None) -> None:
    """Freeze what this turn's model is about to see (#157).

    Called with the breakdown from the SAME `context.compose_*` call that
    produced the messages being sent — see `store.prompt_log`. The scene's
    stamped model rides along so a snapshot still names its provider after the
    scene is repointed at another one.

    Called once the turn is committed to happening — after the stream object
    exists, so the pre-stream claim has already succeeded — but NOT from inside
    the stream itself. The finalizers carry delicate turn-ownership and abort
    semantics that a debug write has no business joining, and a turn the
    provider *failed* is one of the turns whose prompt is most worth having.
    `prompt_log.record` swallows its own storage failures and never waits on a
    lock, so this cannot cost the turn either way.
    """
    # None means the caller composed with `describe=False` because capture is
    # off. Nothing to record, and nothing was built to record.
    if breakdown is None:
        return
    # The scene check and the append are ONE critical section, on the same lock
    # `record` uses. Another client can rename or delete the scene between the
    # composition and this call, and its cleanup (`repoint_scenes` /
    # `forget_scene`) will already have run -- so a row appended afterwards under
    # the obsolete id is one nothing will ever repoint or remove, waiting for the
    # id to be recycled and shown as the replacement scene's own prompt. Checking
    # outside the lock would only narrow that window; checking inside closes it.
    #
    # Non-blocking, and skipping on contention, for the reason `record` is: this
    # runs on the generating path (see `store.prompt_log.record`). The lock is
    # reentrant, so `record`'s own acquisition inside this one is free.
    #
    # The `with` is INSIDE the try, not around it: acquiring takes a file lock,
    # so entering the context manager can raise OSError on its own (the
    # machine-local lock directory gone or unwritable) -- outside any guard,
    # that aborted the route over a debug side effect. Most visible on the
    # opener, where no stream is constructed to fail into.
    try:
        with store.locks.campaign_lock_nowait(cid) as got:
            if not got:
                return
            # Frontmatter only. `read_scene` would re-parse the whole transcript
            # for one field, on a path the turn is already about to pay for
            # several times over.
            meta = store.scenes.read_scene_meta(cid, sid)
            store.prompt_log.record(cid, sid, task, breakdown,
                                    model=meta.get("model", ""))
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound,
            store.locks.StoreBusy, OSError):
        return   # gone, contended, or unreadable: capture nothing, cost nothing


def _abandon(task: asyncio.Task) -> None:
    """Ask an overrun call to stop, then stop waiting on it.

    Retrieving the exception in a callback is what keeps asyncio from logging
    the abandoned task as never-retrieved (`llm._swallow`'s job, kept local:
    routes does not reach into that module's privates). Cancellation is not
    awaited here on purpose -- awaiting it is the very thing that lets the
    ceiling be overrun.
    """
    task.cancel()
    task.add_done_callback(lambda t: None if t.cancelled() else t.exception())


async def _bounded_call(coro):
    """Await one non-streaming generation under a total-duration ceiling (#272).

    The facade's own bound is an *idle* one -- the gap between deltas -- which is
    the right shape for streamed prose (cutting a healthy long generation off
    mid-sentence is worse than letting it finish) but leaves an upstream that
    emits a frame every `llm_timeout - 1` seconds holding its request forever.
    The one-shot generation routes have no partial output to protect: nothing is
    visible until the call returns, and a truncated one costs only a retry. So
    they get a stopwatch, and `stream` deliberately does not.

    Absorb is not routed through here. It carries `_Budget`, which bounds a whole
    *sequence* and knows which of its steps are droppable -- and whose `0` means
    "no ceiling at all, however long the calls take". Folding this ceiling into
    the facade would silently narrow that escape hatch for every absorb step, so
    the ceiling stays where the policy is: the routes that opt into it.

    An overrun is raised as the same `LLMError("timeout", ...)` an upstream stall
    already raises, so every caller's existing 502 handler covers it with no new
    branch. `llm_call_budget <= 0` disables the ceiling.

    `asyncio.wait_for` is deliberately NOT used, for the reason `llm._settle`
    spells out: it cancels the call and then waits for that cancellation to
    finish, so the ceiling is only as hard as the unwinding underneath it. Here
    that unwinding is `_guard`'s `finally`, which grants the pull `_CLOSE_TIMEOUT`
    to settle and the provider another to close -- so a stalled upstream can
    hold the request ~10s past a ceiling that promised to give up at `seconds`,
    and a client that swallows cancellation holds it for good. Waiting is
    therefore capped here and the cancelled call is left to unwind on its own.
    A detached task is a leak we can live with; a wedged request is not.
    """
    seconds = store.config.llm_call_budget()
    if seconds <= 0:
        return await coro
    task = asyncio.ensure_future(coro)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
    except asyncio.CancelledError:
        # The caller went away (SSE disconnect, shutdown). `wait_for` propagated
        # that inward for free; `asyncio.wait` does not, and an uncancelled task
        # here would outlive the request that wanted it.
        _abandon(task)
        raise
    if not done:
        _abandon(task)
        raise LLMError(
            "timeout", f"the reply did not finish within {seconds:g}s — giving up")
    try:
        return task.result()
    except asyncio.TimeoutError as exc:
        # asyncio.TimeoutError IS the builtin TimeoutError from 3.11 on, so an
        # upstream that gives up on its own lands in the same 502 handler as an
        # expired ceiling. It keeps its own message: blaming a setting that had
        # nothing to do with it would send the user to tune the wrong knob.
        raise LLMError("timeout", str(exc) or "the call timed out") from exc


def get_llm() -> LLMClient:
    return _llm


def get_openai_compatible_client() -> OpenAICompatibleClient:
    return _openai_compatible_client


# ---- response bundle (scope endpoints) ----
def _response_body(scene_meta: dict, campaign_meta: dict, cfg: dict, own: dict) -> dict:
    """The shape every scope returns. `own` is that scope's raw frontmatter."""
    resolved = store.response_presets.resolve(
        scene_meta=scene_meta, campaign_meta=campaign_meta, config=cfg)
    fields = {k: own.get(k, "") for k in store.scenes.RESPONSE_FIELDS}
    # Global stores the style as default_style_id; normalize so the picker sees
    # one spelling at every scope. The on-disk key is deliberately unchanged.
    if not fields["style_id"]:
        fields["style_id"] = own.get("default_style_id", "")
    return {**fields,
            "effective": {k: resolved[k] for k in ("style_id",) + store.lengths.KNOBS},
            "provenance": resolved["provenance"]}


def _write_response(setter, fields: dict, style_key: str = "style_id") -> None:
    """Map the picker's style_id back onto the scope's own spelling."""
    out = dict(fields)
    if style_key != "style_id" and "style_id" in out:
        out[style_key] = out.pop("style_id")
    setter(out)


# ---- image serving ----
_IMAGE_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}


def _serve_image(root, cid: str, vid: str, name: str, base: str = "characters",
                 request: Request | None = None):
    p = store.assets.image_path(root, cid, vid, name, base)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _serve_image_file(p, request)


def _serve_image_file(p: Path, request: Request | None = None) -> Response:
    """Serve one image file with the app's caching contract.

    Bare URLs are no-cache: promotions swap file contents under stable URLs,
    so the browser must revalidate — with an ETag that's a 304, not a
    re-download. A `?v=` URL (built from list responses' version tokens) names
    one exact content state, so it caches immutable: zero requests on later
    renders.

    A `FileNotFoundError` reading the file is a 404, not a 500: an image can be
    replaced or removed between the caller resolving its path and this reading
    it, and that is a missing image rather than a server fault. That applies to
    every image route, not only covers — a deliberate widening, since a 500 was
    never the right answer for a file that went away mid-request.

    Only that one, though. Catching `OSError` whole would swallow a
    `PermissionError`, a Windows sharing violation, an exhausted file-descriptor
    table or a disk read error — cases where the image is still there — and
    report a real operational fault to the user as missing data, with the
    frontend dutifully marking a valid cover broken. Those surface as a 500,
    which is what they are.
    """
    try:
        st = p.stat()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
    versioned = request is not None and "v" in request.query_params
    cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
    headers = {"Cache-Control": cache, "ETag": etag}
    if request is not None and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    # ?w= asks for a downscaled variant — tiles shouldn't pull multi-MB originals.
    # An undecodable source just serves the original bytes.
    if request is not None and (w := request.query_params.get("w", "")).isdigit():
        tp = store.thumbs.thumbnail(p, max(16, min(1024, int(w))))
        if tp is not None:
            try:
                thumb = tp.read_bytes()
            except OSError:
                thumb = None  # cache entry swept between generation and read
            if thumb is not None:
                return Response(content=thumb, media_type="image/webp", headers=headers)
    ext = p.suffix.lstrip(".").lower()
    try:
        content = p.read_bytes()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return Response(content=content,
                    media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers=headers)


# ---- uploaded archives ----
@contextlib.asynccontextmanager
async def _spooled_upload(request: Request, cap: int, too_large: str):
    """Stream the request body to a temp file, yield its path, then remove it.

    Both zip-import routes (module packs, world bundles) need the same three
    things and get them here rather than each writing their own: the
    declared-length pre-check that refuses an oversized upload before a byte is
    read, the running count that refuses one whose Content-Length lied, and the
    unlink on every exit path. A world bundle runs to a gigabyte, so the body
    is never held in memory.
    """
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > cap:
        raise HTTPException(status_code=413, detail=too_large)
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    try:
        total = 0
        # atomic-ok: a system temp file for the uploaded archive, not a store
        # record; read by the importer and unlinked in the finally below
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > cap:
                    raise HTTPException(status_code=413, detail=too_large)
                f.write(chunk)
        yield Path(tmp_name)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


# ---- 404 guards and other lookups shared by worlds and campaigns ----
def _world_root_or_404(wid: str):
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")
    return store.worlds.world_root(wid)


def _campaign_root_or_404(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)  # lazy slim of pre-overlay campaigns
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.campaigns.campaign_root(cid)


def _content_fields(kind: str, content: dict) -> dict:
    return {k: content[k] for k in store.entity_schema.field_keys(kind) if k in content}


def _require_connection() -> dict:
    conn = store.llm_connections.get_active()
    if conn is None:
        raise HTTPException(
            status_code=409, detail={"detail": "No LLM connection selected", "kind": "missing_key"})
    if conn["kind"] == "openrouter" and not conn["api_key"]:
        raise HTTPException(
            status_code=409, detail={"detail": "OpenRouter key not set", "kind": "missing_key"})
    if conn["kind"] == "openai_compatible" and not conn["base_url"]:
        raise HTTPException(
            status_code=409, detail={"detail": "Endpoint base URL not set", "kind": "missing_key"})
    return conn


def _require_scene(cid: str, sid: str) -> dict:
    try:
        return store.scenes.read_scene(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")


def computes_only(fn):
    """Mark a campaign-scoped POST that persists nothing.

    POST is not a synonym for write. These routes compute and return -- a
    generated voice anchor for the user to accept or discard, scene
    suggestions, a roll replayed against its stored inputs -- so treating them
    as campaign activity moves a campaign up the recents rail for merely being
    *looked* at.

    Declared at the route rather than as a path list in the middleware,
    deliberately: a path list sits far from the thing it describes and goes
    stale silently, which is how the activity sweep leaked for six rounds. The
    next preview route's author sees this on its neighbours.
    """
    fn.grimoire_computes_only = True
    return fn
