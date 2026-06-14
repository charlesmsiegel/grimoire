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
from dataclasses import dataclass, field

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.heuristics import HeuristicOutput, run_heuristics
from grimoire.extractor.llm_strategy import (
    LLMGatewayLike,
    LLMStrategyOutput,
    extract_with_llm,
)
from grimoire.extractor.merge import merge_candidates, merge_deltas
from grimoire.extractor.protocols import (
    ContradictionChecker,
    EntityResolver,
    MechanicsValidator,
)
from grimoire.extractor.rule_based import extract_rule_based
from grimoire.extractor.together import (
    TrackerMalformedError,
    extract_tracker_block,
    parse_tracker_text,
    project_tracker_to_candidates,
    project_tracker_to_cast_changes,
    project_tracker_to_deltas,
)
from grimoire.extractor.tool_use import ToolCall, project_cast_changes, project_tool_calls
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.types.common import CampaignId, EntityKind, Json, Scope, TurnId
from grimoire.types.extraction import (
    EntityCandidate,
    ExtractionFlag,
    ExtractionResult,
    FlagLevel,
)
from grimoire.types.extraction_modes import ExtractionMode
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
        resolver: EntityResolver | None = None,
        config: ExtractorConfig | None = None,
        source: str = "extractor",
        auto_disable: object | None = None,
        provider_id: str = "",
        model: str = "",
        metrics: MetricsRegistryProtocol = NULL_METRICS,
    ) -> None:
        self._gateway = gateway
        self._mechanics = mechanics
        self._contradictions = contradictions
        self._resolver = resolver
        self._config = config or ExtractorConfig()
        self._source = source
        self._auto_disable = auto_disable
        self._provider_id = provider_id
        self._model = model
        self._metrics: MetricsRegistryProtocol = metrics

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
        turn_id: TurnId | None = None,
        mode: ExtractionMode = ExtractionMode.SEPARATE,
        together_tracker_text: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> ExtractionResult:
        async with self._metrics.measure("extractor", "extract"):
            return await self._extract_inner(
                response_text,
                scene,
                campaign_id,
                prior_state_snapshot,
                pre_roll_resolved=pre_roll_resolved,
                turn_id=turn_id,
                mode=mode,
                together_tracker_text=together_tracker_text,
                tool_calls=tool_calls,
            )

    async def _extract_inner(
        self,
        response_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        prior_state_snapshot: StateSnapshot,
        *,
        pre_roll_resolved: bool = False,
        turn_id: TurnId | None = None,
        mode: ExtractionMode = ExtractionMode.SEPARATE,
        together_tracker_text: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> ExtractionResult:
        """Extract state changes from a model-authored response.

        `mode` controls which strategy runs:

        * `SEPARATE` — the original three-strategy pipeline (default).
        * `TOGETHER` — parse `together_tracker_text` as the JSON tracker
          block emitted alongside prose; falls back to `SEPARATE` if the
          payload is malformed.
        * `TOOL_USE` — project `tool_calls` accumulated during streaming;
          falls back to `SEPARATE` when the list is empty.
        * `NONE`     — short-circuit (auxiliary tasks); returns an empty
          result without invoking any strategy.
        """
        if mode == ExtractionMode.NONE:
            return ExtractionResult()
        if mode == ExtractionMode.TOGETHER:
            return await self._run_together(
                response_text=response_text,
                tracker_text=together_tracker_text,
                scene=scene,
                campaign_id=campaign_id,
                snapshot=prior_state_snapshot,
                pre_roll_resolved=pre_roll_resolved,
                turn_id=turn_id,
            )
        if mode == ExtractionMode.TOOL_USE:
            return await self._run_tool_use(
                response_text=response_text,
                tool_calls=tool_calls or [],
                scene=scene,
                campaign_id=campaign_id,
                snapshot=prior_state_snapshot,
                pre_roll_resolved=pre_roll_resolved,
                turn_id=turn_id,
            )
        return await self._run(
            text=response_text,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=prior_state_snapshot,
            from_player=False,
            player_pc_ref=None,
            pre_roll_resolved=pre_roll_resolved,
            turn_id=turn_id,
        )

    async def extract_from_user_text(
        self,
        user_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        *,
        snapshot: StateSnapshot | None = None,
        player_pc_ref: str | None = None,
        turn_id: TurnId | None = None,
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
            turn_id=turn_id,
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
        turn_id: TurnId | None = None,
    ) -> ExtractionResult:
        started = time.monotonic()
        strategies_to_run = set(self._config.parallel_strategies)
        ran: list[str] = []
        coros = []

        if "rule_based" in strategies_to_run:
            ran.append("rule_based")
            coros.append(self._run_rule_based(text, campaign_id, scene))
        else:
            coros.append(_noop_list())

        if "structured_llm" in strategies_to_run:
            ran.append("structured_llm")
            coros.append(self._run_llm(text, scene, snapshot, campaign_id, turn_id))
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
        llm_out = self._unwrap_strategy(
            results[1],
            flags=flags,
            output_type=LLMStrategyOutput,
            code="structured_llm_failed",
            label="structured-llm",
        )
        heur = self._unwrap_strategy(
            results[2],
            flags=flags,
            output_type=HeuristicOutput,
            code="heuristic_failed",
            label="heuristic",
        )

        # Merge deltas (rule + llm). Heuristic strategy emits only flags.
        merged_deltas = merge_deltas(rule_deltas, llm_out.deltas)

        # Speaker authority: testimony from a character is less authoritative
        # than GM-voice narration (spec 04 §Confidence scoring).
        merged_deltas = [self._apply_speaker_authority(d) for d in merged_deltas]

        # Commitment-id resolution: catch hallucinated commitment refs before
        # they reach the State Store (spec extractor-remaining §7).
        if snapshot is not None:
            merged_deltas, commitment_flags = self._resolve_commitment_ids(
                merged_deltas, snapshot=snapshot
            )
            flags.extend(commitment_flags)

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
                merged_deltas, campaign_id=campaign_id, turn_id=turn_id
            )
            flags.extend(contra_flags)

        # Library-drift detection: deltas mutating a library-scoped entity
        # are routed to review as proposed campaign-local overrides
        # (spec extractor-remaining §1).
        if self._resolver is not None:
            merged_deltas, drift_flags = await self._detect_library_drift(
                merged_deltas, campaign_id=campaign_id
            )
            flags.extend(drift_flags)

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

        # Extras proposals from the heuristic strategy. Per design
        # §Extractor, drop any with confidence below the review threshold
        # and cap to ``max_proposals_per_turn_per_entity`` (1 in v1).
        extras_proposals = self._filter_extras_proposals(heur.extras_proposals)

        return ExtractionResult(
            deltas=merged_deltas,
            candidates=candidates,
            extras_proposals=extras_proposals,
            flags=flags,
            transient_updates=list(llm_out.transient_updates),
            cast_changes=list(llm_out.cast_changes),
            confidence_overall=confidence_overall,
            extraction_strategies_run=ran,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _fallback_to_separate(
        self,
        *,
        mode: str,
        code: str,
        message: str,
        response_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        snapshot: StateSnapshot,
        pre_roll_resolved: bool,
        turn_id: TurnId | None,
        evidence: str = "",
    ) -> ExtractionResult:
        """Record a failed primary-mode call and re-run via SEPARATE.

        Shared by the together/tool_use dispatchers when the primary input is
        missing or malformed: log the mode miss, run the SEPARATE pipeline over
        ``response_text``, and append a WARNING flag describing the fallback.
        """
        await self._record_mode_call(mode, success=False)
        result = await self._run(
            text=response_text,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=snapshot,
            from_player=False,
            player_pc_ref=None,
            pre_roll_resolved=pre_roll_resolved,
            turn_id=turn_id,
        )
        result.flags.append(
            ExtractionFlag(
                level=FlagLevel.WARNING,
                code=code,
                message=message,
                evidence=evidence,
            )
        )
        return result

    async def _run_together(
        self,
        *,
        response_text: str,
        tracker_text: str | None,
        scene: Scene,
        campaign_id: CampaignId,
        snapshot: StateSnapshot,
        pre_roll_resolved: bool,
        turn_id: TurnId | None,
    ) -> ExtractionResult:
        """Together-mode dispatch.

        If `tracker_text` is `None` we attempt to pull the block out of
        `response_text` ourselves (useful for tests / non-streaming
        providers where the frontend hasn't stripped it).
        """
        if tracker_text is None:
            tracker_text = extract_tracker_block(response_text)
        if not tracker_text:
            return await self._fallback_to_separate(
                mode="together",
                code="together_no_tracker",
                message=(
                    "together mode requested but no tracker block found — fell back to SEPARATE"
                ),
                response_text=response_text,
                scene=scene,
                campaign_id=campaign_id,
                snapshot=snapshot,
                pre_roll_resolved=pre_roll_resolved,
                turn_id=turn_id,
            )
        try:
            parsed = parse_tracker_text(tracker_text)
        except TrackerMalformedError as exc:
            return await self._fallback_to_separate(
                mode="together",
                code="together_malformed",
                message=f"tracker malformed: {exc} — fell back to SEPARATE",
                evidence=tracker_text[:200],
                response_text=response_text,
                scene=scene,
                campaign_id=campaign_id,
                snapshot=snapshot,
                pre_roll_resolved=pre_roll_resolved,
                turn_id=turn_id,
            )

        await self._record_mode_call("together", success=True)
        tracker_deltas = project_tracker_to_deltas(
            parsed,
            campaign_id=campaign_id,
            source=self._source + ":together",
        )
        tracker_candidates = project_tracker_to_candidates(parsed)
        tracker_cast_changes = project_tracker_to_cast_changes(parsed)
        sanity = await self._run_sanity_layer(
            text=response_text,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=snapshot,
            pre_roll_resolved=pre_roll_resolved,
        )
        return await self._merge_with_sanity(
            primary_deltas=tracker_deltas,
            primary_candidates=tracker_candidates,
            sanity=sanity,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=snapshot,
            turn_id=turn_id,
            strategies_run=["together", *sanity.strategies_run],
            primary_cast_changes=tracker_cast_changes,
        )

    async def _run_tool_use(
        self,
        *,
        response_text: str,
        tool_calls: list[ToolCall],
        scene: Scene,
        campaign_id: CampaignId,
        snapshot: StateSnapshot,
        pre_roll_resolved: bool,
        turn_id: TurnId | None,
    ) -> ExtractionResult:
        if not tool_calls:
            return await self._fallback_to_separate(
                mode="tool_use",
                code="tool_use_no_calls",
                message="tool_use mode produced no tool calls — fell back to SEPARATE",
                response_text=response_text,
                scene=scene,
                campaign_id=campaign_id,
                snapshot=snapshot,
                pre_roll_resolved=pre_roll_resolved,
                turn_id=turn_id,
            )

        await self._record_mode_call("tool_use", success=True)
        tool_deltas, tool_candidates = project_tool_calls(
            tool_calls,
            campaign_id=campaign_id,
        )
        tool_cast_changes = project_cast_changes(tool_calls)
        sanity = await self._run_sanity_layer(
            text=response_text,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=snapshot,
            pre_roll_resolved=pre_roll_resolved,
        )
        return await self._merge_with_sanity(
            primary_deltas=tool_deltas,
            primary_candidates=tool_candidates,
            sanity=sanity,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=snapshot,
            turn_id=turn_id,
            strategies_run=["tool_use", *sanity.strategies_run],
            primary_cast_changes=tool_cast_changes,
        )

    async def _run_sanity_layer(
        self,
        *,
        text: str,
        scene: Scene,
        campaign_id: CampaignId,
        snapshot: StateSnapshot,
        pre_roll_resolved: bool,
    ) -> _SanityOutput:
        """Cheap deterministic strategies (rule-based + heuristics) used
        as a sanity layer alongside Together / Tool-use. No structured-LLM
        call — that's the whole point of avoiding `SEPARATE`.
        """
        rule_deltas: list[StateDelta] = []
        heur_flags: list[ExtractionFlag] = []
        heur_candidates: list[EntityCandidate] = []
        heur_extras: list = []
        strategies: list[str] = []
        try:
            rule_deltas = await self._run_rule_based(text, campaign_id, scene)
            strategies.append("rule_based")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("rule_based sanity layer failed: %s", exc)
        try:
            heur = await _run_heuristics_async(
                text,
                scene=scene,
                snapshot=snapshot,
                pre_roll_resolved=pre_roll_resolved,
                max_candidates=self._config.max_new_entities_per_turn,
                campaign_id=campaign_id,
            )
            heur_flags = list(heur.flags)
            heur_candidates = list(heur.candidates)
            heur_extras = list(heur.extras_proposals)
            strategies.append("heuristic_flags")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("heuristic sanity layer failed: %s", exc)
        return _SanityOutput(
            deltas=rule_deltas,
            flags=heur_flags,
            candidates=heur_candidates,
            extras_proposals=heur_extras,
            strategies_run=strategies,
        )

    async def _merge_with_sanity(
        self,
        *,
        primary_deltas: list[StateDelta],
        primary_candidates: list[EntityCandidate],
        sanity: _SanityOutput,
        scene: Scene,
        campaign_id: CampaignId,
        snapshot: StateSnapshot,
        turn_id: TurnId | None,
        strategies_run: list[str],
        primary_cast_changes: list | None = None,
    ) -> ExtractionResult:
        """Combine tracker/tool deltas with the sanity layer + downstream
        validation (mechanics, contradictions, library drift).
        """
        started = time.monotonic()
        merged_deltas = merge_deltas(sanity.deltas, primary_deltas)
        merged_deltas = [self._apply_speaker_authority(d) for d in merged_deltas]

        flags: list[ExtractionFlag] = list(sanity.flags)
        if snapshot is not None:
            merged_deltas, commitment_flags = self._resolve_commitment_ids(
                merged_deltas, snapshot=snapshot
            )
            flags.extend(commitment_flags)
        if self._mechanics is not None:
            merged_deltas, mech_flags = await self._validate_mechanical_events(
                merged_deltas, scene=scene, campaign_id=campaign_id
            )
            flags.extend(mech_flags)
        if self._contradictions is not None:
            merged_deltas, contra_flags = await self._check_contradictions(
                merged_deltas, campaign_id=campaign_id, turn_id=turn_id
            )
            flags.extend(contra_flags)
        if self._resolver is not None:
            merged_deltas, drift_flags = await self._detect_library_drift(
                merged_deltas, campaign_id=campaign_id
            )
            flags.extend(drift_flags)

        candidates = merge_candidates(primary_candidates, sanity.candidates)
        if len(candidates) > self._config.max_new_entities_per_turn:
            candidates = candidates[: self._config.max_new_entities_per_turn]

        confidence_overall = (
            sum(d.confidence for d in merged_deltas) / len(merged_deltas) if merged_deltas else 0.0
        )
        extras_proposals = self._filter_extras_proposals(sanity.extras_proposals)
        return ExtractionResult(
            deltas=merged_deltas,
            candidates=candidates,
            extras_proposals=extras_proposals,
            flags=flags,
            cast_changes=list(primary_cast_changes or []),
            confidence_overall=confidence_overall,
            extraction_strategies_run=strategies_run,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _record_mode_call(self, mode: str, *, success: bool) -> None:
        if self._auto_disable is None or not self._provider_id or not self._model:
            return
        try:
            await self._auto_disable.record_call(
                self._provider_id, self._model, mode, success=success
            )
        except Exception as exc:  # pragma: no cover - observability only
            logger.debug("auto_disable.record_call failed: %s", exc)

    def _filter_extras_proposals(self, proposals: list) -> list:
        """Apply review-threshold + per-entity cap to heuristic proposals."""
        threshold = float(getattr(self._config, "extras_review_threshold", 0.70))
        max_per_entity = int(getattr(self._config, "extras_max_proposals_per_turn_per_entity", 1))
        out: list = []
        per_entity: dict[str, int] = {}
        for proposal in proposals:
            if proposal.confidence < threshold:
                continue
            count = per_entity.get(proposal.entity_id, 0)
            if count >= max_per_entity:
                continue
            per_entity[proposal.entity_id] = count + 1
            out.append(proposal)
        return out

    async def _run_rule_based(
        self,
        text: str,
        campaign_id: CampaignId,
        scene: Scene | None = None,
    ) -> list[StateDelta]:
        return list(
            extract_rule_based(
                text,
                campaign_id=campaign_id,
                config=self._config,
                source=self._source,
                scene_location_ref=getattr(scene, "location_ref", None) if scene else None,
            )
        )

    async def _run_llm(
        self,
        text: str,
        scene: Scene | None,
        snapshot: StateSnapshot | None,
        campaign_id: CampaignId,
        turn_id: TurnId | None = None,
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
            turn_id=turn_id,
        )

    def _apply_speaker_authority(self, delta: StateDelta) -> StateDelta:
        """Subtract `testimony_confidence_penalty` for character-spoken facts.

        Spec 04 §Confidence scoring: GM-voice narration is more authoritative
        than a character's in-fiction claim. We treat `speaker_id is None`
        (or missing) as the GM narrator marker and apply the penalty only
        to FACT_ADD deltas tagged with a concrete speaker.
        """
        if delta.kind != DeltaKind.FACT_ADD:
            return delta
        speaker_id = delta.after.get("speaker_id")
        if not isinstance(speaker_id, str) or not speaker_id.strip():
            return delta
        penalty = self._config.testimony_confidence_penalty
        if penalty <= 0:
            return delta
        adjusted = max(0.0, delta.confidence - penalty)
        return delta.model_copy(update={"confidence": adjusted})

    def _resolve_commitment_ids(
        self,
        deltas: list[StateDelta],
        *,
        snapshot: StateSnapshot,
    ) -> tuple[list[StateDelta], list[ExtractionFlag]]:
        """Validate COMMITMENT_RESOLVE deltas against the snapshot's open commitments.

        Unmatched ids get demoted by `contradiction_confidence_penalty` (which
        normally lands them in the review band) and produce a CONTRADICTION
        flag so the orchestrator routes them to the review queue.
        """
        flags: list[ExtractionFlag] = []
        known_ids = {
            str(c.get("id"))
            for c in snapshot.open_commitments
            if isinstance(c, dict) and c.get("id") is not None
        }
        out: list[StateDelta] = []
        for delta in deltas:
            if delta.kind != DeltaKind.COMMITMENT_RESOLVE:
                out.append(delta)
                continue
            commitment_id = delta.after.get("commitment_id")
            if not isinstance(commitment_id, str) or commitment_id in known_ids:
                out.append(delta)
                continue
            penalty = self._config.contradiction_confidence_penalty
            adjusted = max(0.0, delta.confidence - penalty)
            out.append(delta.model_copy(update={"confidence": adjusted}))
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.CONTRADICTION,
                    code="unresolved_commitment_reference",
                    message=(f"commitment_id {commitment_id!r} does not match any open commitment"),
                    evidence=delta.evidence,
                    payload={
                        "commitment_id": commitment_id,
                        "known_ids": sorted(known_ids),
                    },
                )
            )
        return out, flags

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
        turn_id: TurnId | None = None,
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
                conflicts = await self._contradictions.check(
                    campaign_id, fact_text, about, turn_id=turn_id
                )
            except Exception as exc:
                logger.warning("contradiction check failed: %s", exc)
                out.append(delta)
                continue
            if not conflicts:
                out.append(delta)
                continue
            # Subtract the configured penalty as before, then cap below the
            # auto-apply threshold so a high-confidence (e.g. 0.99) fact still
            # lands in the review bucket — spec extractor-remaining §2 calls
            # out that contradicting facts must never silently auto-apply,
            # regardless of starting confidence.
            penalty = self._config.contradiction_confidence_penalty
            adjusted = max(0.0, delta.confidence - penalty)
            review_cap = max(0.0, self._config.auto_apply_threshold - 0.001)
            forced = min(adjusted, review_cap)
            conflict_dicts = [c.model_dump() for c in conflicts]
            updated_extra = dict(delta.extra)
            updated_extra["contradictions"] = list(conflict_dicts)
            out.append(delta.model_copy(update={"confidence": forced, "extra": updated_extra}))
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.CONTRADICTION,
                    code="fact_contradiction",
                    message=f"fact contradicts existing state ({len(conflicts)})",
                    evidence=fact_text,
                    payload={"conflicts": list(conflict_dicts)},
                )
            )
        return out, flags

    async def _detect_library_drift(
        self,
        deltas: list[StateDelta],
        *,
        campaign_id: CampaignId,
    ) -> tuple[list[StateDelta], list[ExtractionFlag]]:
        """Flag deltas that mutate a library-scoped entity as proposed overrides.

        For each CHARACTER_STATE_UPDATE / SCENE_CHANGE that targets an entity
        resolving through the library, compare the proposed value against the
        card's current value. On divergence: annotate `extra["override_of_library"]`
        and clamp confidence into `[review_threshold, auto_apply_threshold)` so
        the orchestrator routes it to the review queue with the `library_drift`
        flag set — giving the UI the signal it needs to render the three-option
        "add as override / edit library card / treat as transient" prompt.
        """
        flags: list[ExtractionFlag] = []
        out: list[StateDelta] = []
        for delta in deltas:
            target = _library_drift_target(delta)
            if target is None:
                out.append(delta)
                continue
            entity_ref, kind, field, proposed_value = target
            try:
                resolved = await self._resolver.resolve(campaign_id, entity_ref, kind)
            except Exception as exc:
                logger.warning("entity resolver failed: %s", exc)
                flags.append(
                    ExtractionFlag(
                        level=FlagLevel.WARNING,
                        code="entity_resolver_failed",
                        message=f"entity resolver raised: {type(exc).__name__}",
                        evidence=delta.evidence,
                    )
                )
                out.append(delta)
                continue
            if resolved is None or resolved.scope is not Scope.LIBRARY:
                out.append(delta)
                continue
            if field is None:
                # The delta references the library entity but doesn't assert a
                # value for any card field — nothing to diverge from.
                out.append(delta)
                continue
            current_value = resolved.card.get(field)
            if current_value == proposed_value:
                out.append(delta)
                continue
            updated_extra = dict(delta.extra)
            updated_extra["override_of_library"] = True
            updated_extra["library_card_value"] = current_value
            out.append(
                delta.model_copy(
                    update={
                        "confidence": self._clamp_into_review_band(delta.confidence),
                        "extra": updated_extra,
                    }
                )
            )
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.CONTRADICTION,
                    code="library_drift",
                    message=(
                        f"prose modifies library entity {entity_ref!r} "
                        f"(field {field!r}) — propose as campaign-local override"
                    ),
                    evidence=delta.evidence,
                    related=[entity_ref],
                    payload={
                        "entity_ref": entity_ref,
                        "entity_kind": str(kind),
                        "field": field,
                        "library_value": current_value,
                        "proposed_value": proposed_value,
                    },
                )
            )
        return out, flags

    def _clamp_into_review_band(self, confidence: float) -> float:
        """Force `confidence` into `[review_threshold, auto_apply_threshold)`.

        Used by drift detection to guarantee the delta routes to review
        regardless of where the original confidence landed.
        """
        review = self._config.review_threshold
        auto = self._config.auto_apply_threshold
        # Subtract a small epsilon so a value exactly at `auto` doesn't
        # auto-apply; `route_deltas` uses `>=` against `auto_apply_threshold`.
        upper = max(review, auto - 1e-6)
        return min(max(confidence, review), upper)

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
    def _unwrap_strategy[OutputT](
        value: object,
        *,
        flags: list[ExtractionFlag],
        output_type: type[OutputT],
        code: str,
        label: str,
    ) -> OutputT:
        """Coerce a strategy result (or a raised exception) into its output type.

        A strategy run via ``asyncio.gather(return_exceptions=True)`` yields
        either its output or the exception it raised; on failure we record a
        warning flag and fall back to an empty ``output_type()``.
        """
        if isinstance(value, BaseException):
            flags.append(
                ExtractionFlag(
                    level=FlagLevel.WARNING,
                    code=code,
                    message=f"{label} strategy raised {type(value).__name__}",
                    evidence=str(value)[:200],
                )
            )
            return output_type()
        if isinstance(value, output_type):
            return value
        return output_type()


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


def _library_drift_target(
    delta: StateDelta,
) -> tuple[str, EntityKind, str | None, object] | None:
    """Extract `(entity_ref, kind, field, proposed_value)` if `delta` is eligible.

    Returns `None` for deltas the drift step ignores. The fields returned
    name the entity to resolve and the card field whose value is being
    asserted by the delta; `field is None` means "no specific field" (e.g.
    SCENE_CHANGE merely references the location).
    """
    after = delta.after
    if delta.kind == DeltaKind.CHARACTER_STATE_UPDATE:
        character_id = after.get("character_id")
        if not isinstance(character_id, str) or not character_id.strip():
            return None
        field = after.get("field")
        return (
            character_id,
            EntityKind.CHARACTER,
            field if isinstance(field, str) and field else None,
            after.get("after"),
        )
    if delta.kind == DeltaKind.SCENE_CHANGE:
        to_location = after.get("to_location")
        if not isinstance(to_location, str) or not to_location.strip():
            return None
        # SCENE_CHANGE doesn't assert a value for any specific card field —
        # surface it for resolution but with no field/proposed-value pair,
        # so drift only fires when the resolver itself signals an issue
        # (today that means: never; left in place so future SCENE_CHANGE
        # payloads carrying location-attribute claims wire up cleanly).
        return (to_location, EntityKind.LOCATION, None, None)
    return None


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


@dataclass
class _SanityOutput:
    """Output of the deterministic sanity layer used by Together / Tool-use modes."""

    deltas: list[StateDelta] = field(default_factory=list)
    flags: list[ExtractionFlag] = field(default_factory=list)
    candidates: list[EntityCandidate] = field(default_factory=list)
    extras_proposals: list = field(default_factory=list)
    strategies_run: list[str] = field(default_factory=list)


# Re-export EntityCandidate for callers that need to type-hint candidate handling.
__all__ = ["EntityCandidate", "ExtractorService"]
