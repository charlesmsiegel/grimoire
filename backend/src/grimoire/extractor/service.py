"""`ExtractorService`: orchestrates the three parallel strategies.

Inputs are a model response (or player text) plus the current scene and
a `StateSnapshot`. The service runs the configured strategies in
parallel, merges and dedupes their outputs, downgrades confidences for
contradictions and mechanical-event mismatches, and returns a typed
`ExtractionResult` ready for the Orchestrator to route into the State
Store / review queue.
"""

from __future__ import annotations

import asyncio
import logging
import time

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.heuristics import HeuristicOutput, run_heuristics
from grimoire.extractor.llm_strategy import (
    LLMGatewayLike,
    LLMStrategyOutput,
    extract_with_llm,
)
from grimoire.extractor.merge import merge_candidates, merge_deltas
from grimoire.extractor.protocols import ContradictionChecker, MechanicsValidator
from grimoire.extractor.rule_based import extract_rule_based
from grimoire.types.common import CampaignId, Json
from grimoire.types.extraction import (
    EntityCandidate,
    ExtractionFlag,
    ExtractionResult,
    FlagLevel,
)
from grimoire.types.mechanics import NarratedEvent
from grimoire.types.scene import Scene, SceneContext
from grimoire.types.state import DeltaKind, StateDelta, StateSnapshot

logger = logging.getLogger(__name__)


class ExtractorService:
    """Concrete `Extractor` implementing spec 04.

    Dependencies are passed via keyword args at construction; everything
    optional except the LLM gateway. If `gateway` is None, the
    structured-LLM strategy is skipped and a flag is emitted explaining
    the degradation.
    """

    def __init__(
        self,
        *,
        gateway: LLMGatewayLike | None = None,
        mechanics: MechanicsValidator | None = None,
        contradictions: ContradictionChecker | None = None,
        config: ExtractorConfig | None = None,
        source: str = "extractor",
    ) -> None:
        self._gateway = gateway
        self._mechanics = mechanics
        self._contradictions = contradictions
        self._config = config or ExtractorConfig()
        self._source = source

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #

    async def extract(
        self,
        response_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        prior_state_snapshot: StateSnapshot,
        *,
        pre_roll_resolved: bool = False,
    ) -> ExtractionResult:
        """Extract state changes from a model-authored response."""
        return await self._run(
            text=response_text,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=prior_state_snapshot,
            from_player=False,
            player_pc_ref=None,
            pre_roll_resolved=pre_roll_resolved,
        )

    async def extract_from_user_text(
        self,
        user_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        *,
        snapshot: StateSnapshot | None = None,
        player_pc_ref: str | None = None,
    ) -> ExtractionResult:
        """Extract state changes from a player-authored post."""
        return await self._run(
            text=user_text,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=snapshot,
            from_player=True,
            player_pc_ref=player_pc_ref or _author_pc(scene),
            pre_roll_resolved=False,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _run(
        self,
        *,
        text: str,
        scene: Scene,
        campaign_id: CampaignId,
        snapshot: StateSnapshot | None,
        from_player: bool,
        player_pc_ref: str | None,
        pre_roll_resolved: bool,
    ) -> ExtractionResult:
        started = time.monotonic()
        strategies_to_run = set(self._config.parallel_strategies)
        ran: list[str] = []
        coros = []

        if "rule_based" in strategies_to_run:
            ran.append("rule_based")
            coros.append(self._run_rule_based(text, campaign_id))
        else:
            coros.append(_noop_list())

        if "structured_llm" in strategies_to_run:
            ran.append("structured_llm")
            coros.append(self._run_llm(text, scene, snapshot, campaign_id))
        else:
            coros.append(_noop_llm())

        if "heuristic_flags" in strategies_to_run:
            ran.append("heuristic_flags")
            coros.append(
                _run_heuristics_async(
                    text,
                    scene=scene,
                    snapshot=snapshot,
                    pre_roll_resolved=pre_roll_resolved,
                    max_candidates=self._config.max_new_entities_per_turn,
                    campaign_id=campaign_id,
                )
            )
        else:
            coros.append(_noop_heuristic())

        rule_deltas: list[StateDelta]
        llm_out: LLMStrategyOutput
        heur: HeuristicOutput
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=True),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError:
            return ExtractionResult(
                flags=[
                    ExtractionFlag(
                        level=FlagLevel.WARNING,
                        code="extraction_timeout",
                        message=(
                            f"extraction exceeded {self._config.timeout_seconds}s "
                            "— turn proceeded without it"
                        ),
                    )
                ],
                extraction_strategies_run=ran,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        flags: list[ExtractionFlag] = []
        rule_deltas = self._unwrap_list(results[0], strategy="rule_based", flags=flags)
        llm_out = self._unwrap_llm(results[1], flags=flags)
        heur = self._unwrap_heuristic(results[2], flags=flags)

        # Merge deltas (rule + llm). Heuristic strategy emits only flags.
        merged_deltas = merge_deltas(rule_deltas, llm_out.deltas)

        # Player text: clamp confidence on non-PC subjects.
        if from_player and player_pc_ref:
            merged_deltas = [
                self._clamp_player_authority(d, player_pc_ref=player_pc_ref) for d in merged_deltas
            ]

        # Mechanical event validation.
        if self._mechanics is not None:
            merged_deltas, mech_flags = await self._validate_mechanical_events(
                merged_deltas, scene=scene, campaign_id=campaign_id
            )
            flags.extend(mech_flags)

        # Contradiction detection.
        if self._contradictions is not None:
            merged_deltas, contra_flags = await self._check_contradictions(
                merged_deltas, campaign_id=campaign_id
            )
            flags.extend(contra_flags)

        # Apply LLM + heuristic flags.
        flags.extend(llm_out.flags)
        flags.extend(heur.flags)

        # Merge candidates (LLM-proposed + heuristic name detection).
        candidates = merge_candidates(llm_out.candidates, heur.candidates)
        # Trim to budget.
        if len(candidates) > self._config.max_new_entities_per_turn:
            candidates = candidates[: self._config.max_new_entities_per_turn]

        confidence_overall = (
            sum(d.confidence for d in merged_deltas) / len(merged_deltas) if merged_deltas else 0.0
        )

        return ExtractionResult(
            deltas=merged_deltas,
            candidates=candidates,
            flags=flags,
            confidence_overall=confidence_overall,
            extraction_strategies_run=ran,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _run_rule_based(self, text: str, campaign_id: CampaignId) -> list[StateDelta]:
        return list(
            extract_rule_based(
                text,
                campaign_id=campaign_id,
                config=self._config,
                source=self._source,
            )
        )

    async def _run_llm(
        self,
        text: str,
        scene: Scene | None,
        snapshot: StateSnapshot | None,
        campaign_id: CampaignId,
    ) -> LLMStrategyOutput:
        if self._gateway is None:
            return LLMStrategyOutput(
                flags=[
                    ExtractionFlag(
                        level=FlagLevel.INFO,
                        code="llm_strategy_disabled",
                        message="structured-LLM extraction skipped (no gateway configured)",
                    )
                ]
            )
        return await extract_with_llm(
            response_text=text,
            scene=scene,
            snapshot=snapshot,
            campaign_id=campaign_id,
            gateway=self._gateway,
            config=self._config,
            source=self._source,
        )

    def _clamp_player_authority(self, delta: StateDelta, *, player_pc_ref: str) -> StateDelta:
        """Apply the player-authority heuristic (spec 04 §Handling player text).

        Player declarations about their own PC stay at face value; everything
        else gets the cap from `player_other_subject_confidence_cap`.
        """
        about_pc = _delta_is_about(delta, player_pc_ref)
        cap = self._config.player_other_subject_confidence_cap
        if about_pc:
            return delta
        if delta.confidence > cap:
            return delta.model_copy(update={"confidence": cap})
        return delta

    async def _validate_mechanical_events(
        self,
        deltas: list[StateDelta],
        *,
        scene: Scene,
        campaign_id: CampaignId,
    ) -> tuple[list[StateDelta], list[ExtractionFlag]]:
        flags: list[ExtractionFlag] = []
        out: list[StateDelta] = []
        ctx = SceneContext(scene=scene)
        for delta in deltas:
            if delta.kind != DeltaKind.MECHANICAL_EVENT:
                out.append(delta)
                continue
            event = NarratedEvent(
                kind=str(delta.after.get("event_kind", "unknown")),
                actor_ref=delta.after.get("actor_ref"),
                target_ref=delta.after.get("target_ref"),
                description=str(delta.after.get("description") or delta.after.get("amount") or ""),
                evidence=delta.evidence,
                metadata=dict(delta.extra),
            )
            try:
                result = await self._mechanics.validate_narrated_event(campaign_id, event, ctx)
            except Exception as exc:  # same flag-and-continue contract
                logger.warning("mechanics validation failed: %s", exc)
                flags.append(
                    ExtractionFlag(
                        level=FlagLevel.WARNING,
                        code="mechanics_validation_failed",
                        message=f"mechanics validation raised: {type(exc).__name__}",
                        evidence=delta.evidence,
                    )
                )
                out.append(delta)
                continue
            if not result.valid:
                # Demote the delta and surface a flag so the user sees it.
                penalty = self._config.contradiction_confidence_penalty
                adjusted = max(0.0, delta.confidence - penalty)
                out.append(delta.model_copy(update={"confidence": adjusted}))
                flags.append(
                    ExtractionFlag(
                        level=FlagLevel.MISSING_MECHANIC,
                        code="mechanics_rejected",
                        message="mechanics module rejected narrated event",
                        evidence=delta.evidence,
                        payload={
                            "errors": list(result.errors),
                            "warnings": list(result.warnings),
                        },
                    )
                )
            else:
                out.append(delta)
        return out, flags

    async def _check_contradictions(
        self,
        deltas: list[StateDelta],
        *,
        campaign_id: CampaignId,
    ) -> tuple[list[StateDelta], list[ExtractionFlag]]:
        flags: list[ExtractionFlag] = []
        out: list[StateDelta] = []
        for delta in deltas:
            if delta.kind != DeltaKind.FACT_ADD:
                out.append(delta)
                continue
            fact_text = str(delta.after.get("text", ""))
            about_raw = delta.after.get("about") or {}
            about = _normalize_about(about_raw)
            try:
                conflicts = await self._contradictions.check(campaign_id, fact_text, about)
            except Exception as exc:
                logger.warning("contradiction check failed: %s", exc)
                out.append(delta)
                continue
            if not conflicts:
                out.append(delta)
                continue
            penalty = self._config.contradiction_confidence_penalty
            adjusted = max(0.0, delta.confidence - penalty)
            updated_extra = dict(delta.extra)
            updated_extra["contradictions"] = list(conflicts)
            out.append(delta.model_copy(update={"confidence": adjusted, "extra": updated_extra}))
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.CONTRADICTION,
                    code="fact_contradiction",
                    message=f"fact contradicts existing state ({len(conflicts)})",
                    evidence=fact_text,
                    payload={"conflicts": list(conflicts)},
                )
            )
        return out, flags

    # ------------------------------------------------------------------ #
    # Result unwrapping helpers (gather with return_exceptions=True)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _unwrap_list(
        value: object, *, strategy: str, flags: list[ExtractionFlag]
    ) -> list[StateDelta]:
        if isinstance(value, BaseException):
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.WARNING,
                    code=f"{strategy}_failed",
                    message=f"{strategy} strategy raised {type(value).__name__}",
                    evidence=str(value)[:200],
                )
            )
            return []
        if isinstance(value, list):
            return value
        return []

    @staticmethod
    def _unwrap_llm(value: object, *, flags: list[ExtractionFlag]) -> LLMStrategyOutput:
        if isinstance(value, BaseException):
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.WARNING,
                    code="structured_llm_failed",
                    message=f"structured-llm strategy raised {type(value).__name__}",
                    evidence=str(value)[:200],
                )
            )
            return LLMStrategyOutput()
        if isinstance(value, LLMStrategyOutput):
            return value
        return LLMStrategyOutput()

    @staticmethod
    def _unwrap_heuristic(value: object, *, flags: list[ExtractionFlag]) -> HeuristicOutput:
        if isinstance(value, BaseException):
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.WARNING,
                    code="heuristic_failed",
                    message=f"heuristic strategy raised {type(value).__name__}",
                    evidence=str(value)[:200],
                )
            )
            return HeuristicOutput()
        if isinstance(value, HeuristicOutput):
            return value
        return HeuristicOutput()


def _author_pc(scene: Scene) -> str | None:
    """Best guess at the player's PC ref for a player-authored post."""
    if scene.pov_character_ref:
        return scene.pov_character_ref
    if scene.present_pc_refs:
        return scene.present_pc_refs[0]
    return None


def _delta_is_about(delta: StateDelta, pc_ref: str) -> bool:
    """Check if a delta's subject is the player's own PC."""
    after = delta.after
    fields = ("character_id", "actor_ref", "from", "subject")
    for f in fields:
        v = after.get(f)
        if not isinstance(v, str) or not v:
            continue
        # Require a namespace separator on partial matches so that
        # `pc_ref="julian"` doesn't accidentally match `v="crasher"` or
        # `v="her"` via raw suffix overlap.
        if v == pc_ref or v.endswith(f":{pc_ref}") or pc_ref.endswith(f":{v}"):
            return True
    about = after.get("about")
    if isinstance(about, dict):
        chars = about.get("character_ids") or []
        if pc_ref in chars:
            return True
    return False


def _normalize_about(about: Json) -> dict[str, list[str]]:
    if not isinstance(about, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key in ("character_ids", "location_ids", "faction_ids", "item_ids"):
        v = about.get(key)
        if isinstance(v, list):
            out[key] = [str(x) for x in v]
        else:
            out[key] = []
    return out


async def _noop_list() -> list[StateDelta]:
    return []


async def _noop_llm() -> LLMStrategyOutput:
    return LLMStrategyOutput()


async def _noop_heuristic() -> HeuristicOutput:
    return HeuristicOutput()


async def _run_heuristics_async(
    text: str,
    *,
    scene: Scene | None,
    snapshot: StateSnapshot | None,
    pre_roll_resolved: bool,
    max_candidates: int,
    campaign_id: CampaignId,
) -> HeuristicOutput:
    return run_heuristics(
        text,
        scene=scene,
        snapshot=snapshot,
        pre_roll_resolved=pre_roll_resolved,
        max_candidates=max_candidates,
        campaign_id=campaign_id,
    )


# Re-export EntityCandidate for callers that need to type-hint candidate handling.
__all__ = ["EntityCandidate", "ExtractorService"]
