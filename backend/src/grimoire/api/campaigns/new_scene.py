"""API routes for the new-scene workflow and Scene Ledger."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from grimoire.api.deps import (
    ContinuityDep,
    LibraryDep,
    LLMGatewayDep,
    SceneLedgerDep,
    ScenesDep,
    StateStoreDep,
)
from grimoire.api.util import to_payload
from grimoire.scenes.suggest import SceneSuggestionEngine, SuggestionContext

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LedgerStatusUpdate(BaseModel):
    status: str


class PreviewRequest(BaseModel):
    ledger_id: str | None = None
    generated_suggestion: dict[str, Any] | None = None
    custom_text: str | None = None
    greeting_id: str | None = None


class PreviewResponse(BaseModel):
    title: str
    location_ref: str | None = None
    in_game_start: str | None = None
    present_character_refs: list[str] = []
    present_pc_refs: list[str] = []
    greeting_id: str | None = None
    first_post_source: str
    ledger_id: str | None = None


class StartRequest(BaseModel):
    title: str
    location_ref: str | None = None
    in_game_start: str | None = None
    present_character_refs: list[str] = []
    present_pc_refs: list[str] = []
    greeting_id: str | None = None
    first_post_source: str
    ledger_id: str | None = None
    unchosen_generated: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Ledger CRUD
# ---------------------------------------------------------------------------


@router.get("/{campaign_id}/scene-ledger")
async def list_ledger(
    campaign_id: str,
    ledger: SceneLedgerDep,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status == "active":
        return await ledger.list_active(campaign_id)
    return await ledger.list_all(campaign_id)


@router.patch("/{campaign_id}/scene-ledger/{item_id}")
async def update_ledger_item(
    campaign_id: str,
    item_id: str,
    body: LedgerStatusUpdate,
    ledger: SceneLedgerDep,
) -> dict[str, str]:
    await ledger.set_status(campaign_id, item_id, body.status)
    return {"id": item_id, "status": body.status}


# ---------------------------------------------------------------------------
# Suggest → Preview → Start workflow
# ---------------------------------------------------------------------------


@router.post("/{campaign_id}/scenes/suggest")
async def suggest_scenes(
    campaign_id: str,
    ledger: SceneLedgerDep,
    gateway: LLMGatewayDep,
    scenes: ScenesDep,
    continuity: ContinuityDep,
    state_store: StateStoreDep,
) -> dict[str, Any]:
    all_scenes = await scenes.list_scenes(campaign_id)
    closed = [s for s in all_scenes if s.closed]
    recent_summaries = [
        s.final_summary or s.running_summary or ""
        for s in closed[-3:]
        if s.final_summary or s.running_summary
    ]

    pc_rows = await state_store.list_pcs(campaign_id)
    active_pcs = [r["character_ref"] for r in pc_rows if r.get("character_ref")]

    open_threads: list[str] = []
    try:
        continuity_svc = continuity.for_campaign(campaign_id)
        commitments = await continuity_svc.open_commitments(limit=10)
        open_threads = [c.text for c in commitments]
    except Exception:
        pass

    active_items = await ledger.list_active(campaign_id)
    greeting_names = [i["summary"] for i in active_items if i["source"] == "greeting"]

    last_location = closed[-1].location_ref if closed else None
    last_time: str | None = None
    if closed:
        last = closed[-1]
        if last.in_game_end:
            last_time = str(last.in_game_end)
        elif last.in_game_start:
            last_time = str(last.in_game_start)

    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    ctx = SuggestionContext(
        campaign_id=campaign_id,
        recent_summaries=recent_summaries,
        open_threads=open_threads,
        active_pcs=active_pcs,
        last_location=last_location,
        in_game_time=last_time,
        unused_greeting_names=greeting_names,
    )
    return await engine.suggest(ctx)


@router.post("/{campaign_id}/scenes/preview")
async def preview_scene(
    campaign_id: str,
    body: PreviewRequest,
    ledger: SceneLedgerDep,
    gateway: LLMGatewayDep,
    state_store: StateStoreDep,
) -> PreviewResponse:
    from grimoire.types.llm import CompletionRequest, Message, MessageRole

    pc_rows = await state_store.list_pcs(campaign_id)
    active_pc_refs = [r["character_ref"] for r in pc_rows if r.get("character_ref")]

    description: str = ""
    greeting_id: str | None = body.greeting_id
    ledger_id: str | None = None

    if body.ledger_id:
        item = await ledger.get(campaign_id, body.ledger_id)
        if item:
            description = item["summary"]
            greeting_id = greeting_id or item.get("greeting_id")
            ledger_id = item["id"]
    elif body.generated_suggestion:
        description = body.generated_suggestion.get("summary", "")
    elif body.custom_text:
        description = body.custom_text

    first_post_source = "greeting" if greeting_id else "generated"

    prompt = (
        "Given this scene description for a TTRPG campaign, extract structured metadata.\n\n"
        f"Description: {description}\n\n"
        "Return JSON with keys: title (short scene title), "
        "location_ref (place name or null), in_game_start (time "
        "description or null), present_character_refs (list of names)."
    )
    request = CompletionRequest(
        model="default",
        messages=[Message(role=MessageRole.USER, content=prompt)],
        max_tokens=512,
        temperature=0.3,
    )
    response = await gateway.complete("scene_preview", request, campaign_id=campaign_id)

    try:
        meta = json.loads(response.text)
        if not isinstance(meta, dict):
            meta = {}
    except (json.JSONDecodeError, TypeError):
        meta = {}

    return PreviewResponse(
        title=meta.get("title", description[:50]),
        location_ref=meta.get("location_ref"),
        in_game_start=meta.get("in_game_start"),
        present_character_refs=meta.get("present_character_refs", []),
        present_pc_refs=active_pc_refs,
        greeting_id=greeting_id,
        first_post_source=first_post_source,
        ledger_id=ledger_id,
    )


@router.post("/{campaign_id}/scenes/start")
async def start_new_scene(
    campaign_id: str,
    body: StartRequest,
    scenes: ScenesDep,
    ledger: SceneLedgerDep,
    gateway: LLMGatewayDep,
    state_store: StateStoreDep,
    library: LibraryDep,
) -> dict[str, Any]:
    from grimoire.scenes import new_post
    from grimoire.scenes.types import AuthorKind, SceneInit
    from grimoire.types.llm import CompletionRequest, Message, MessageRole

    # Generate the first post BEFORE creating the scene so a failure
    # doesn't leave an empty scene on disk (issue #9).
    first_post_body: str | None = None
    used_greeting = False

    if body.first_post_source == "greeting" and body.greeting_id:
        from grimoire.api.campaigns.helpers import _seed_greeting_first_post

        composition = await library.get_composition(campaign_id)
        world_refs = getattr(composition, "worlds", []) or []
        greeting = None
        world_id = None
        for ref in world_refs:
            wid = getattr(ref, "world_id", None) or (
                ref.get("world_id") if isinstance(ref, dict) else None
            )
            if not wid:
                continue
            try:
                greeting = await library.get_greeting(wid, body.greeting_id)
                world_id = wid
                break
            except Exception:
                continue
        if greeting:
            used_greeting = True
        # Fallback to generated if greeting not found (issue #6)

    if not used_greeting:
        prompt = (
            "Write the opening narrator post for a TTRPG scene.\n\n"
            f"Title: {body.title}\n"
            f"Location: {body.location_ref or 'unspecified'}\n"
            f"Present characters: "
            f"{', '.join(body.present_character_refs) or 'unspecified'}\n\n"
            "Write 2-3 paragraphs of atmospheric scene-setting in second "
            "person. Do not include any metadata or headers — just the "
            "narrative text."
        )
        request = CompletionRequest(
            model="default",
            messages=[Message(role=MessageRole.USER, content=prompt)],
            max_tokens=1024,
            temperature=0.9,
        )
        response = await gateway.complete("scene_first_post", request, campaign_id=campaign_id)
        first_post_body = response.text.strip()

    in_game_start: datetime | None = None
    if body.in_game_start:
        with contextlib.suppress(ValueError):
            in_game_start = datetime.fromisoformat(body.in_game_start)

    init = SceneInit(
        campaign_id=campaign_id,
        title=body.title,
        location_ref=body.location_ref,
        in_game_start=in_game_start,
        greeting_id=body.greeting_id,
        present_character_refs=body.present_character_refs,
        present_pc_refs=body.present_pc_refs,
    )
    scene = await scenes.start_scene(init)

    # Append the first post
    first_post = None
    if used_greeting and greeting:
        await _seed_greeting_first_post(
            scenes=scenes,
            scene=scene,
            greeting=greeting,
            state_store=state_store,
            library=library,
            world_id=world_id,
        )
        posts = await scenes.get_posts(scene.id)
        first_post = posts[0] if posts else None
    elif first_post_body:
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=first_post_body,
            is_player=False,
        )
        await scenes.append_post(scene.id, post)
        first_post = post

    # Mark ledger item used (issue #3: scoped by campaign_id)
    if body.ledger_id:
        await ledger.mark_used(campaign_id, body.ledger_id, scene_id=scene.id)

    for suggestion in body.unchosen_generated:
        summary = suggestion.get("summary", "")
        if summary:
            await ledger.add(
                campaign_id=campaign_id,
                summary=summary,
                source="llm",
                proposed_location=suggestion.get("proposed_location"),
                proposed_cast=json.dumps(suggestion.get("proposed_cast", [])),
            )

    return {
        "scene": to_payload(scene),
        "first_post": to_payload(first_post) if first_post else None,
    }
