"""HTTP surface for grimoire."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import store
from .openrouter import OpenRouterClient, OpenRouterError

router = APIRouter()
_openrouter = OpenRouterClient()


def get_openrouter() -> OpenRouterClient:
    return _openrouter


class ConfigUpdate(BaseModel):
    model: str | None = None
    theme: str | None = None
    openrouter_key: str | None = None


class NewConversation(BaseModel):
    title: str | None = None


class RenameConversation(BaseModel):
    title: str


class ChatTurn(BaseModel):
    content: str


def _public_config(cfg: dict[str, str]) -> dict:
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"])}


@router.get("/config")
def get_config():
    return _public_config(store.read_config())


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    return _public_config(store.write_config(**fields))


@router.get("/conversations")
def get_conversations():
    return store.list_conversations()


@router.post("/conversations")
def post_conversation(body: NewConversation):
    title = body.title or "New chat"
    return {"id": store.create_conversation(title)}


@router.get("/conversations/{cid}")
def get_conversation(cid: str):
    try:
        return store.read_conversation(cid)
    except store.ConversationNotFound:
        raise HTTPException(status_code=404, detail="conversation not found")


@router.put("/conversations/{cid}")
def put_conversation(cid: str, body: RenameConversation):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        new_cid = store.rename_conversation(cid, title)
    except store.ConversationNotFound:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"id": new_cid, "title": title}


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str):
    try:
        store.delete_conversation(cid)
    except store.ConversationNotFound:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


def _require_conversation(cid: str) -> dict:
    try:
        return store.read_conversation(cid)
    except store.ConversationNotFound:
        raise HTTPException(status_code=404, detail="conversation not found")


def _require_key(cfg: dict[str, str]) -> None:
    if not cfg["openrouter_key"]:
        raise HTTPException(
            status_code=409,
            detail={"detail": "OpenRouter key not set", "kind": "missing_key"},
        )


def _chat_stream(cid: str, messages: list[dict], cfg: dict, client: OpenRouterClient):
    async def event_stream():
        parts: list[str] = []
        try:
            async for delta in client.stream(messages, cfg["model"], cfg["openrouter_key"]):
                parts.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            store.append_message(cid, "assistant", "".join(parts))
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OpenRouterError as exc:
            if parts:
                store.append_message(cid, "assistant", "".join(parts))
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/conversations/{cid}/chat")
def post_chat(cid: str, turn: ChatTurn, client: OpenRouterClient = Depends(get_openrouter)):
    conv = _require_conversation(cid)
    cfg = store.read_config()
    _require_key(cfg)

    store.append_message(cid, "user", turn.content)
    messages = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]
    messages.append({"role": "user", "content": turn.content})
    return _chat_stream(cid, messages, cfg, client)


@router.post("/conversations/{cid}/retry")
def post_retry(cid: str, client: OpenRouterClient = Depends(get_openrouter)):
    conv = _require_conversation(cid)
    cfg = store.read_config()
    _require_key(cfg)

    messages = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]
    if not messages:
        raise HTTPException(status_code=400, detail="nothing to retry")
    return _chat_stream(cid, messages, cfg, client)
