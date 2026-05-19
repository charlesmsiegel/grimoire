"""Auxiliary-task REST routes.

Seven start endpoints (one per `TaskKind`), an accept and a discard
route, and an in-flight listing endpoint. The start endpoints kick off
streaming through the gateway and return the in-memory `AuxiliaryResult`
once the stream completes; live tokens flow over the campaign WebSocket
as ``aux_token`` / ``aux_complete`` / ``aux_error`` events.

See `docs/superpowers/specs/2026-05-19-auxiliary-tasks-design.md`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from grimoire.api.deps import OrchestratorDep
from grimoire.api.util import map_lookup_errors, to_payload
from grimoire.auxiliary.types import AuxiliaryTask, TaskKind
from grimoire.orchestrator.errors import (
    AuxiliaryAlreadyCommittedError,
    AuxiliaryNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns")


# --------------------------------------------------------------------------- #
# Request payloads
# --------------------------------------------------------------------------- #


class ImpersonatePCPayload(BaseModel):
    steering_hint: str | None = None


class RewritePostPayload(BaseModel):
    post_id: str
    edit_instruction: str


class ContinueAsPayload(BaseModel):
    character_ref: str
    target_post_id: str | None = None
    steering_hint: str | None = None


class WhatWouldXSayPayload(BaseModel):
    character_ref: str
    snippet: str


class BrainstormPayload(BaseModel):
    prompt: str = Field(..., alias="prompt")


class EditProsePayload(BaseModel):
    snippet: str
    edit_instruction: str


class TranslatePayload(BaseModel):
    snippet: str
    target_language: str


class AcceptAuxiliaryPayload(BaseModel):
    edited_text: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _run(
    orchestrator: Any,
    campaign_id: str,
    task: AuxiliaryTask,
) -> dict[str, Any]:
    try:
        result = await orchestrator.run_auxiliary_task(
            campaign_id=campaign_id, task=task
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return _result_payload(result)


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "id": result.id,
        "kind": result.task.kind.value,
        "text": result.text,
        "model_used": result.model_used,
        "tokens": result.tokens,
        "pending_commit_action": result.pending_commit_action.value,
        "warnings": list(result.warnings),
        "completed_at": result.completed_at.isoformat(),
    }


# --------------------------------------------------------------------------- #
# Start endpoints (7)
# --------------------------------------------------------------------------- #


@router.post("/{campaign_id}/auxiliary/impersonate-pc")
async def impersonate_pc(
    campaign_id: str,
    payload: ImpersonatePCPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    task = AuxiliaryTask(
        kind=TaskKind.IMPERSONATE_PC,
        steering_hint=payload.steering_hint,
    )
    return await _run(orchestrator, campaign_id, task)


@router.post("/{campaign_id}/auxiliary/rewrite-post")
async def rewrite_post(
    campaign_id: str,
    payload: RewritePostPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    task = AuxiliaryTask(
        kind=TaskKind.REWRITE_POST,
        target_post_id=payload.post_id,
        edit_instruction=payload.edit_instruction,
    )
    return await _run(orchestrator, campaign_id, task)


@router.post("/{campaign_id}/auxiliary/continue-as")
async def continue_as(
    campaign_id: str,
    payload: ContinueAsPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    task = AuxiliaryTask(
        kind=TaskKind.CONTINUE_AS,
        target_character_ref=payload.character_ref,
        target_post_id=payload.target_post_id,
        steering_hint=payload.steering_hint,
    )
    return await _run(orchestrator, campaign_id, task)


@router.post("/{campaign_id}/auxiliary/what-would-x-say")
async def what_would_x_say(
    campaign_id: str,
    payload: WhatWouldXSayPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    task = AuxiliaryTask(
        kind=TaskKind.WHAT_WOULD_X_SAY,
        target_character_ref=payload.character_ref,
        snippet=payload.snippet,
    )
    return await _run(orchestrator, campaign_id, task)


@router.post("/{campaign_id}/auxiliary/brainstorm")
async def brainstorm(
    campaign_id: str,
    payload: BrainstormPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet=payload.prompt)
    return await _run(orchestrator, campaign_id, task)


@router.post("/{campaign_id}/auxiliary/edit-prose")
async def edit_prose(
    campaign_id: str,
    payload: EditProsePayload,
    orchestrator: OrchestratorDep,
) -> Any:
    task = AuxiliaryTask(
        kind=TaskKind.EDIT_PROSE,
        snippet=payload.snippet,
        edit_instruction=payload.edit_instruction,
    )
    return await _run(orchestrator, campaign_id, task)


@router.post("/{campaign_id}/auxiliary/translate")
async def translate(
    campaign_id: str,
    payload: TranslatePayload,
    orchestrator: OrchestratorDep,
) -> Any:
    task = AuxiliaryTask(
        kind=TaskKind.TRANSLATE,
        snippet=payload.snippet,
        target_language=payload.target_language,
    )
    return await _run(orchestrator, campaign_id, task)


# --------------------------------------------------------------------------- #
# Accept / Discard
# --------------------------------------------------------------------------- #


@router.post("/{campaign_id}/auxiliary/{result_id}/accept")
async def accept_auxiliary(
    campaign_id: str,
    result_id: str,
    orchestrator: OrchestratorDep,
    payload: AcceptAuxiliaryPayload | None = None,
) -> Any:
    edited_text = payload.edited_text if payload else None
    try:
        out = await orchestrator.accept_auxiliary(
            campaign_id, result_id, edited_text=edited_text
        )
    except AuxiliaryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuxiliaryAlreadyCommittedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(out)


@router.post("/{campaign_id}/auxiliary/{result_id}/discard")
async def discard_auxiliary(
    campaign_id: str,
    result_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    discarded = await orchestrator.discard_auxiliary(result_id)
    if not discarded:
        raise HTTPException(status_code=404, detail=f"auxiliary not found: {result_id}")
    return {"discarded": True, "result_id": result_id}


# --------------------------------------------------------------------------- #
# In-flight listing
# --------------------------------------------------------------------------- #


@router.get("/{campaign_id}/auxiliary/in-flight")
async def list_in_flight(
    campaign_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    # All in-flight aux results are listed; the API doesn't currently
    # tag results with a campaign id, so the response is global. Frontend
    # filters by id pattern when needed.
    results = list(orchestrator._inflight_aux.values())
    return [_result_payload(r) for r in results]


__all__ = ["router"]
