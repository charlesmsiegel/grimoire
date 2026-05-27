"""DeltaApplier — handles extraction mode selection, delta extraction, routing,
and continuity dispatch for the orchestrator."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.mode_select import select_mode
from grimoire.extractor.routing import Decision, route_deltas
from grimoire.llm_gateway.capabilities import ProviderCapabilities
from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.helpers import (
    _build_continuity_commitment,
    _build_continuity_fact,
    _pydantic_scene,
)
from grimoire.scenes.types import Scene as SceneFileScene
from grimoire.types.common import CampaignId, SceneId, TurnId
from grimoire.types.extraction import ExtractionResult
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.types.state import DeltaKind, StateSnapshot

logger = logging.getLogger(__name__)

WSPushFn = Callable[[str, dict], Awaitable[None]]


class DeltaApplier:
    """Handles extraction, delta routing, and continuity dispatch."""

    def __init__(
        self,
        *,
        state_store: Any,
        continuity: Any | None,
        extractor: Any,
        world: Any | None,
        event_bus: EventBus,
        gateway: Any,
        extractor_config: ExtractorConfig,
        config: OrchestratorConfig,
        auto_disable: Any,
        ws_push: WSPushFn | None = None,
    ) -> None:
        self._store = state_store
        self._continuity = continuity
        self._extractor = extractor
        self._world = world
        self._bus = event_bus
        self._gateway = gateway
        self._extractor_config = extractor_config
        self._config = config
        self._auto_disable = auto_disable
        self._ws_push = ws_push

    # ------------------------------------------------------------------ #
    # Extraction mode
    # ------------------------------------------------------------------ #

    async def select_extract_mode(
        self,
        *,
        campaign_id: CampaignId,
        aux_task: Any | None = None,
    ) -> ExtractionMode:
        if await self._campaign_integrated_deltas(campaign_id):
            return ExtractionMode.TOGETHER
        provider_id = "unknown"
        model = "unknown"
        resolve = getattr(self._gateway, "resolve_route", None)
        if resolve is not None:
            try:
                route = resolve(self._config.main_llm_task, campaign_id)
                provider_id = route.provider_id
                model = route.model
            except Exception:
                pass
        caps_for = getattr(self._gateway, "capabilities_for", None)
        caps = caps_for(provider_id) if caps_for is not None else ProviderCapabilities()
        return await select_mode(
            campaign_config=self._extractor_config,
            provider_caps=caps,
            auto_disable=self._auto_disable,
            aux_task=aux_task,
            provider_id=provider_id,
            model=model,
        )

    async def _campaign_integrated_deltas(self, campaign_id: CampaignId) -> bool:
        try:
            row = await self._store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (campaign_id,)
            )
        except Exception:
            return False
        if not row:
            return False
        raw = row["config"] if row else None
        if not raw:
            return False
        import json as _json

        try:
            data = _json.loads(raw)
        except (TypeError, ValueError):
            return False
        return bool(data.get("integrated_deltas")) if isinstance(data, dict) else False

    # ------------------------------------------------------------------ #
    # Extraction
    # ------------------------------------------------------------------ #

    async def extract(
        self,
        *,
        response_text: str,
        scene: SceneFileScene,
        campaign_id: CampaignId,
        turn_id: TurnId,
        mode: ExtractionMode = ExtractionMode.SEPARATE,
    ) -> ExtractionResult | None:
        snapshot = StateSnapshot(
            campaign_id=campaign_id,
            scene_id=scene.id,
        )
        pyd_scene = _pydantic_scene(scene)
        retries = max(0, int(self._config.errors.retry_extractor_on_parse_failure))
        attempts = retries + 1
        parse_failure_codes = {
            "llm_json_unparseable",
            "structured_llm_failed",
            "llm_call_failed",
        }
        last_result: ExtractionResult | None = None
        for attempt in range(attempts):
            try:
                result = await self._extractor.extract(
                    response_text,
                    pyd_scene,
                    campaign_id,
                    snapshot,
                    turn_id=turn_id,
                    mode=mode,
                )
            except Exception as exc:
                logger.warning("extractor failed for turn %s: %s", turn_id, exc)
                return None
            last_result = result
            flags = getattr(result, "flags", []) or []
            has_parse_failure = any(getattr(f, "code", None) in parse_failure_codes for f in flags)
            if not has_parse_failure or attempt == attempts - 1:
                return result
        return last_result

    # ------------------------------------------------------------------ #
    # Integrated-deltas fallback event
    # ------------------------------------------------------------------ #

    async def emit_integrated_deltas_fallback(
        self,
        *,
        extraction: ExtractionResult | None,
        turn_id: TurnId,
        campaign_id: CampaignId,
        scene_id: SceneId,
    ) -> None:
        if extraction is None:
            return
        _FALLBACK_CODES = {"together_no_tracker": "no_block", "together_malformed": "json_parse"}
        for flag in getattr(extraction, "flags", []):
            code = getattr(flag, "code", None)
            if code in _FALLBACK_CODES:
                await self._emit_turn_event(
                    "integrated_deltas_fallback",
                    turn_id,
                    campaign_id,
                    scene_id,
                    reason=_FALLBACK_CODES[code],
                )
                return

    # ------------------------------------------------------------------ #
    # Delta routing + application
    # ------------------------------------------------------------------ #

    async def apply_routing(
        self,
        *,
        campaign_id: CampaignId,
        turn_id: TurnId,
        extraction: ExtractionResult,
    ) -> tuple[list[str], list[str]]:
        routing = route_deltas(list(extraction.deltas), config=self._extractor_config)
        auto_deltas = [d for d, dec in routing.decisions() if dec is Decision.AUTO_APPLY]
        review_deltas = [d for d, dec in routing.decisions() if dec is Decision.REVIEW]

        applied_ids: list[str] = []
        queued_ids: list[str] = []
        try:
            for delta in auto_deltas:
                if (
                    self._world is not None
                    and delta.kind == DeltaKind.OVERRIDE_WRITE
                    and delta.target_table == "location_state"
                ):
                    try:
                        await self._world.apply_weather_override_delta(delta)
                        continue
                    except Exception:
                        logger.exception("world weather-override apply failed; falling through")
                if self._continuity is not None and delta.kind in (
                    DeltaKind.FACT_ADD,
                    DeltaKind.FACT_RETIRE,
                    DeltaKind.FACT_UPDATE,
                    DeltaKind.COMMITMENT_ADD,
                    DeltaKind.COMMITMENT_RESOLVE,
                    DeltaKind.KNOWLEDGE_REVEAL,
                ):
                    handled = await self._apply_continuity_delta(
                        delta=delta,
                        campaign_id=campaign_id,
                        turn_id=turn_id,
                    )
                    if handled:
                        continue
                did = await self._store.apply_delta(
                    delta=delta,
                    source=delta.source or "extractor",
                    turn_id=turn_id,
                    campaign_id=campaign_id,
                )
                applied_ids.append(did)
        except Exception:
            for did in reversed(applied_ids):
                try:
                    await self._store.reverse_delta(did)
                except Exception:
                    logger.warning(
                        "rollback of delta %s failed during apply-batch unwind",
                        did,
                        exc_info=True,
                    )
            raise

        if applied_ids:
            await self._bus.emit(
                Event(
                    type=events.DELTAS_APPLIED,
                    payload={
                        "turn_id": turn_id,
                        "campaign_id": campaign_id,
                        "count": len(applied_ids),
                        "ids": list(applied_ids),
                    },
                )
            )

        for delta in review_deltas:
            try:
                review_id = await self._store.queue_for_review(
                    delta=delta,
                    source=delta.source or "extractor",
                    campaign_id=campaign_id,
                )
                if review_id:
                    queued_ids.append(str(review_id))
                await self._bus.emit(
                    Event(
                        type=events.REVIEW_ITEM_ADDED,
                        payload={
                            "campaign_id": campaign_id,
                            "review_id": review_id,
                            "turn_id": turn_id,
                        },
                    )
                )
            except Exception as exc:
                logger.warning(
                    "queue_for_review failed (kind=%s turn=%s): %s",
                    delta.kind,
                    turn_id,
                    exc,
                )

        return applied_ids, queued_ids

    # ------------------------------------------------------------------ #
    # Continuity delta dispatch
    # ------------------------------------------------------------------ #

    async def _apply_continuity_delta(
        self,
        *,
        delta: Any,
        campaign_id: CampaignId,
        turn_id: TurnId,
    ) -> bool:
        from grimoire.continuity.registry import resolve_continuity
        from grimoire.continuity.service import ContinuityService

        service = resolve_continuity(self._continuity, campaign_id)
        if service is None:
            return False

        payload = delta.after or {}
        try:
            if delta.kind == DeltaKind.FACT_ADD:
                fact = _build_continuity_fact(
                    payload=payload,
                    confidence=delta.confidence,
                    source=delta.source or "extractor",
                    turn_id=turn_id,
                )
                report = await service.check_contradictions(fact, turn_id=turn_id)
                if report.conflicts:
                    review_id = await self._store.queue_for_review(
                        delta=delta,
                        source=delta.source or "extractor",
                        campaign_id=campaign_id,
                    )
                    await self._bus.emit(
                        Event(
                            type=events.REVIEW_ITEM_ADDED,
                            payload={
                                "campaign_id": campaign_id,
                                "review_id": review_id,
                                "turn_id": turn_id,
                                "report_id": report.id,
                                "reason": "contradiction_detected",
                            },
                        )
                    )
                    return True
                await service.add_fact(fact, source=delta.source or "extractor")
                return True

            if delta.kind == DeltaKind.FACT_RETIRE:
                fact_id = payload.get("fact_id") or payload.get("id")
                if not fact_id:
                    return False
                await service.retire_fact(
                    fact_id,
                    in_post=str(payload.get("in_post") or turn_id),
                    reason=str(payload.get("reason") or "retconned"),
                )
                return True

            if delta.kind == DeltaKind.FACT_UPDATE:
                fact_id = payload.get("fact_id") or payload.get("id")
                if not fact_id:
                    return False
                patch = payload.get("patch") or {}
                await service.update_fact(fact_id, patch)
                return True

            if delta.kind == DeltaKind.COMMITMENT_ADD:
                commitment = _build_continuity_commitment(
                    payload=payload,
                    turn_id=turn_id,
                )
                if commitment is None:
                    return False
                await service.add_commitment(commitment, source=delta.source or "extractor")
                return True

            if delta.kind == DeltaKind.COMMITMENT_RESOLVE:
                from grimoire.continuity.types import CommitmentStatus

                cid = payload.get("commitment_id") or payload.get("id")
                status_raw = str(payload.get("status") or "paid").lower()
                if not cid:
                    return False
                try:
                    status = CommitmentStatus(status_raw)
                except ValueError:
                    return False
                await service.resolve_commitment(
                    cid, status, in_post=str(payload.get("in_post") or turn_id)
                )
                return True

            if delta.kind == DeltaKind.KNOWLEDGE_REVEAL:
                fact_id = payload.get("fact_id")
                to_refs = payload.get("to") or payload.get("character_ids") or []
                if not fact_id or not to_refs:
                    return False
                await service.reveal(
                    fact_id,
                    list(to_refs),
                    in_post=str(payload.get("in_post") or turn_id),
                    source=delta.source or "extractor",
                )
                return True
        except Exception:
            logger.exception(
                "continuity delta apply failed (kind=%s campaign=%s turn=%s)",
                delta.kind,
                campaign_id,
                turn_id,
            )
            return False

        del ContinuityService
        return False

    # ------------------------------------------------------------------ #
    # Internal event helpers
    # ------------------------------------------------------------------ #

    async def _emit_turn_event(
        self,
        type_: str,
        turn_id: TurnId,
        campaign_id: CampaignId,
        scene_id: SceneId,
        **payload: Any,
    ) -> None:
        await self._bus.emit(
            Event(
                type=type_,
                payload={
                    "turn_id": turn_id,
                    "campaign_id": campaign_id,
                    "scene_id": scene_id,
                    **payload,
                },
            )
        )
        if self._ws_push is not None:
            try:
                await self._ws_push(
                    campaign_id,
                    {
                        "type": type_,
                        "turn_id": turn_id,
                        "scene_id": scene_id,
                        **payload,
                    },
                )
            except Exception as exc:
                logger.debug("ws_push failed: %s", exc)
