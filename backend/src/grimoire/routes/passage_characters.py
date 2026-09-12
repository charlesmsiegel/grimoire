"""Optional passage drafts and explicitly reviewed campaign-local creation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from .. import prompts
from ..llm import LLMClient
from ..store import appearances, characters, passage_evidence, responses
from . import runs
from .common import _require_connection, _require_scene, draft_completion, get_llm

router = APIRouter()


class PassageDraft(BaseModel):
    name: str
    passage: str
    source_text: str


class PassageSave(PassageDraft):
    description: str = ""
    mes_example: str = ""
    existing_ref: str = ""


def _source(cid: str, sid: str, rid: str, body: PassageDraft) -> dict:
    scene = _require_scene(cid, sid)
    try:
        response = responses.get(cid, sid, rid)
    except responses.ResponseNotFound:
        raise HTTPException(404, "Response no longer exists.") from None
    if response.get("speaker") != "Grimoire" or response.get("status") != "complete":
        raise HTTPException(422, "Choose a completed Grimoire response.")
    if response["content"] != body.source_text:
        raise HTTPException(409, detail={"kind": "source_changed", "detail":
            "This response changed. Reopen the passage from the current response before saving."})
    try:
        passage_evidence.validate(body.name, body.passage, body.source_text, "")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return scene


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/character-draft", status_code=202)
def draft_character(cid: str, sid: str, rid: str, body: PassageDraft, request: Request,
                          client: LLMClient = Depends(get_llm),
                          x_grimoire_attempt: str | None = Header(default=None)):
    scene = _source(cid, sid, rid, body)
    conn = _require_connection("character-from-passage", cid)
    posts = scene["messages"]
    index = next((i for i, post in enumerate(posts) if post.get("response_id") == rid), None)
    neighbors = [] if index is None else posts[max(0, index - 2):index]
    context = "\n\n".join(str(post.get("speaker", post["role"])) + ": "
                           + post["content"][-2000:] for post in neighbors)
    messages = [{"role": "system", "content": prompts.render("character_from_passage/system.j2")},
                {"role": "user", "content": prompts.render("character_from_passage/user.j2",
                    name=body.name, passage=body.passage, context=context)}]

    def shape(text: str) -> dict:
        return {"name": body.name, "description": text.strip()[:16000],
                "mes_example": passage_evidence.examples(body.passage, body.name),
                "quotes": passage_evidence.quotes(body.passage, body.name)}

    async def work():
        return await draft_completion(client, conn, messages, "character-from-passage", shape, cid=cid, sid=sid)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "character-from-passage",
                          x_grimoire_attempt, work)


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/character")
def save_character(cid: str, sid: str, rid: str, body: PassageSave, request: Request):
    with runs.scene_held_free(request.app, cid, sid):
        _source(cid, sid, rid, body)
        if body.existing_ref:
            kind, _, aid = body.existing_ref.partition(":")
            appeared = appearances.record(cid).get(f"{kind}/{aid}")
            if appeared and appeared["role"] != "npc":
                raise HTTPException(422, "Choose an NPC character; this character is already the player.")
        try:
            result = passage_evidence.save(cid, sid, rid, name=body.name,
                description=body.description, passage=body.passage, source_text=body.source_text,
                mes_example=body.mes_example, existing_ref=body.existing_ref)
            appearances.appear(cid, sid, "characters", result["character"], result["version"], "npc")
        except (characters.CharacterNotFound, characters.VersionNotFound):
            raise HTTPException(404, "Character no longer exists.") from None
        except (ValueError, appearances.AppearError) as exc:
            raise HTTPException(422, str(exc)) from exc
    return result
