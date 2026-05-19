"""Auxiliary-task runner — a separate loop, not the canonical turn.

The canonical loop (``OrchestratorService._run_turn``) handles pre-rolls,
mechanics, drift correction, extraction, state writes and time advance.
None of that applies to auxiliary tasks. This module owns the fork:
build an auxiliary prompt (suppression already enforced by the Context
Builder's `auxiliary_task` branch), stream the response through the LLM
gateway with per-task routing, return an `AuxiliaryResult`.

The runner is invoked from `OrchestratorService.run_auxiliary_task` and
parks the result in `_inflight_aux` until the user accepts or discards.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from grimoire.auxiliary.types import (
    AuxiliaryResult,
    AuxiliaryTask,
    TaskKind,
    commit_action_for,
)
from grimoire.llm_gateway.errors import RouteNotFoundError
from grimoire.types.common import CampaignId
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.types.llm import CompletionRequest
from grimoire.util import new_id

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str], Awaitable[None]] | None


def _route_task(kind: TaskKind) -> str:
    return f"auxiliary.{kind.value}"


def _synthesize_input(task: AuxiliaryTask) -> str:
    """The aux prompt is fully system-side; we pass an empty user message
    so the assembled prompt stays purely instructional. The Context
    Builder's aux path ignores ``player_input`` anyway.
    """
    return ""


async def run_auxiliary_task(
    orchestrator: Any,
    *,
    campaign_id: CampaignId,
    task: AuxiliaryTask,
    on_token: TokenCallback = None,
    branch_id: str | None = None,
) -> AuxiliaryResult:
    """Execute one auxiliary task end-to-end.

    No mechanics, no extractor, no state writes. Per-task model routing
    via `auxiliary.<kind>`; on `RouteNotFoundError` we fall back to the
    canonical turn route with a warning attached to the result.
    """
    result_id = new_id("ar")
    warnings: list[str] = []

    # Register the in-flight slot early so concurrent callers see it.
    inflight: dict[str, AuxiliaryResult] = orchestrator._inflight_aux  # type: ignore[attr-defined]

    prompt = await orchestrator._context.build(
        player_input=_synthesize_input(task),
        campaign_id=campaign_id,
        branch_id=branch_id,
        extractor_mode=ExtractionMode.NONE,
        auxiliary_task=task,
    )

    primary_task = _route_task(task.kind)
    fallback_task = orchestrator._config.main_llm_task
    route_task = primary_task
    router = getattr(orchestrator._gateway, "_router", None)
    if router is not None:
        try:
            router.resolve(primary_task, campaign_id)
        except RouteNotFoundError:
            warnings.append("fallback_to_canonical_model")
            route_task = fallback_task

    params = getattr(prompt, "params", None)
    request = CompletionRequest(
        model="",
        messages=list(prompt.messages),
        max_tokens=getattr(params, "max_tokens", 4096),
        temperature=getattr(params, "temperature", 1.0),
        seed=getattr(params, "seed", None),
    )

    accumulated: list[str] = []
    model_used = ""
    try:
        stream = orchestrator._gateway.stream(
            route_task,
            request,
            campaign_id=campaign_id,
        )
        async for chunk in stream:
            delta = getattr(chunk, "delta", "")
            if delta:
                accumulated.append(delta)
                if on_token is not None:
                    await on_token(delta)
                await orchestrator._push_to_ws(
                    campaign_id,
                    {"type": "aux_token", "result_id": result_id, "delta": delta},
                )
            if getattr(chunk, "is_final", False):
                break
        # The gateway doesn't surface the resolved model; query the router.
        if router is not None:
            try:
                model_used = router.resolve(route_task, campaign_id).model
            except Exception:
                model_used = ""
    except Exception as exc:
        logger.warning("auxiliary task %s failed: %s", task.kind.value, exc)
        await orchestrator._push_to_ws(
            campaign_id,
            {"type": "aux_error", "result_id": result_id, "error": str(exc)},
        )
        raise

    text = "".join(accumulated)
    result = AuxiliaryResult(
        id=result_id,
        task=task,
        text=text,
        completed_at=datetime.now(UTC),
        model_used=model_used,
        tokens=_estimate_tokens(text),
        pending_commit_action=commit_action_for(task.kind),
        warnings=warnings,
    )
    inflight[result_id] = result

    logger.info(
        "[aux] task=%s campaign=%s model=%s tokens=%d result=%s",
        task.kind.value,
        campaign_id,
        model_used or "?",
        result.tokens,
        result_id,
    )
    await orchestrator._push_to_ws(
        campaign_id,
        {
            "type": "aux_complete",
            "result_id": result_id,
            "tokens": result.tokens,
            "model": model_used,
        },
    )
    return result


def _estimate_tokens(text: str) -> int:
    # Cheap heuristic: ~4 chars per token. Avoids dragging the full
    # tokenizer into the aux runner; the audit value is approximate.
    if not text:
        return 0
    return max(1, len(text) // 4)


__all__ = ["run_auxiliary_task"]
