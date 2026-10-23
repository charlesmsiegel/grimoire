"""Read-only VN story projection and separate optimistic visual controls."""

from contextlib import contextmanager

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..store.expression_library import contracts
from ..store.vn import overrides, read
from . import common

router = APIRouter(prefix="/campaigns/{cid}/scenes/{sid}/vn")


class Presentation(BaseModel):
    scene_identity: str
    expected_revision: str
    actors: dict = Field(default_factory=dict)
    background: str | None = None


@contextmanager
def errors():
    try:
        yield
    except overrides.ConflictError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except overrides.UnavailableError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except (overrides.InvalidError, contracts.ExpressionInvalidError) as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("")
def scene(cid: str, sid: str):
    common._require_scene(cid, sid)
    with errors():
        return read.scene(cid, sid)


@router.get("/posts/{post_id}")
def post(cid: str, sid: str, post_id: str):
    common._require_scene(cid, sid)
    with errors():
        return read.post(cid, sid, post_id)


@router.put("/posts/{post_id}/presentation")
def save(cid: str, sid: str, post_id: str, body: Presentation):
    common._require_scene(cid, sid)
    with errors():
        return read.save(cid, sid, post_id, body.scene_identity, body.expected_revision, body.actors, body.background)
