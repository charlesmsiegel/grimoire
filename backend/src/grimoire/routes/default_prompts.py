"""Scope-owned authorial settings, independent of transcript/run ownership.

Literal routes are registered before the generic entity surface. Saves publish
only the addressed field; optional-field reads report unreadable without
turning a transient failure into a misleading empty value.
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, HTTPException

from .. import store
from . import runs
from .models import PromptText

router = APIRouter()


@contextmanager
def _scope_errors():
    """Keep six endpoints consistent without exposing filesystem exception prose."""
    try:
        yield
    except (store.worlds.WorldNotFound, store.campaigns.CampaignNotFound,
            store.scenes.SceneNotFound) as exc:
        raise HTTPException(status_code=404, detail="scope not found") from exc
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": "the field could not be saved; its existing record is unchanged"}) from exc


@router.get("/worlds/{wid}/default-prompt")
def get_world_default_prompt(wid: str):
    with _scope_errors():
        return store.default_prompts.read_world(wid)


@router.put("/worlds/{wid}/default-prompt")
def put_world_default_prompt(wid: str, body: PromptText):
    with _scope_errors():
        return store.default_prompts.set_world(wid, body.text)


@router.get("/campaigns/{cid}/default-prompt")
def get_campaign_default_prompt(cid: str):
    with _scope_errors():
        return store.default_prompts.resolve(cid)


@router.put("/campaigns/{cid}/default-prompt")
def put_campaign_default_prompt(cid: str, body: PromptText):
    with _scope_errors():
        return store.default_prompts.set_campaign(cid, body.text)


@router.get("/campaigns/{cid}/scenes/{sid}/focus")
def get_scene_focus(cid: str, sid: str):
    with _scope_errors():
        return store.scenes.focus.read(cid, sid)


@router.put("/campaigns/{cid}/scenes/{sid}/focus")
def put_scene_focus(cid: str, sid: str, body: PromptText):
    with _scope_errors(), store.locks.campaign_lock(cid):
        # Closed branches are read-only, but a live response need not stop:
        # the new focus applies to future compositions, not its frozen prompt.
        runs.require_scene_open(cid, sid)
        return store.scenes.focus.set(cid, sid, body.text)
