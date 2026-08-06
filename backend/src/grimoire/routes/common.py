"""Shared helpers for the route modules.

Dependency-injection providers, the pydantic-version shim, the response-scope
read/write pair, image serving, and the 404 guards every domain module reuses.
This module holds no routes and imports no sibling route module, so it is
always safe to import from one.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import store
from ..llm import LLMClient
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

    At build time rather than after the stream, deliberately. The stream
    finalizers carry delicate turn-ownership and abort semantics that a debug
    write has no business joining, and a turn the provider failed is one of the
    turns whose prompt is most worth having. `prompt_log.record` swallows its
    own storage failures, so this cannot cost the turn either way.
    """
    # None means the caller composed with `describe=False` because capture is
    # off. Nothing to record, and nothing was built to record.
    if breakdown is None:
        return
    scene_model = ""
    try:
        # Frontmatter only. `read_scene` would re-parse the whole transcript for
        # one field, on a path the turn is already about to pay for several
        # times over.
        scene_model = store.scenes.read_scene_meta(cid, sid).get("model", "")
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound, OSError):
        pass
    store.prompt_log.record(cid, sid, task, breakdown, model=scene_model)


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
    # Bare URLs are no-cache: promotions swap file contents under stable URLs,
    # so the browser must revalidate — with an ETag that's a 304, not a re-download.
    # A `?v=` URL (built from list responses' version tokens) names one exact
    # content state, so it caches immutable: zero requests on later renders.
    st = p.stat()
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
            return Response(content=tp.read_bytes(), media_type="image/webp", headers=headers)
    ext = p.suffix.lstrip(".").lower()
    return Response(content=p.read_bytes(),
                    media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers=headers)


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
