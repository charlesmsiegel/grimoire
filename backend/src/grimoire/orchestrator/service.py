"""``OrchestratorService`` — the turn loop driver (spec 01).

Wires together Scene Manager, Mechanics, Context Builder, LLM Gateway,
Extractor and State Store. Owns the in-process event bus and per-campaign
turn locks.

The service is deliberately duck-typed at construction so tests can wire
in fakes for any subset of collaborators. Production wiring passes the
concrete services from each module.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from grimoire import events
from grimoire.context.cache import ContextBuilderCache, make_cache_key
from grimoire.event_bus import Event, EventBus
from grimoire.extractor.config import ExtractorConfig
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.orchestrator.alternates import AlternatesManager
from grimoire.orchestrator.auxiliary import AuxiliaryCoordinator
from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.delta_applier import DeltaApplier
from grimoire.orchestrator.errors import (
    NoTurnsToUndoError,
    OrchestratorError,
    TurnCancelledError,
    TurnTimeoutError,
    UnknownCampaignError,
    UnknownPCError,
)
from grimoire.orchestrator.fork import ForkCoordinator
from grimoire.orchestrator.helpers import (
    _campaign_generation_overrides,
    _clean_modifications,
    _PreRollOutcome,
    _proposed_to_roll,
    _pydantic_post,
    _pydantic_scene,
)
from grimoire.orchestrator.retcon import RetconCoordinator
from grimoire.orchestrator.retcon_replay import RetconReplaySession  # re-exported via property
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import AuthorKind as SceneAuthorKind
from grimoire.scenes.types import Post as SceneFilePost
from grimoire.scenes.types import SceneInit as SceneFileInit
from grimoire.types.common import CampaignId, CharacterRef, PostId, SceneId, TurnId
from grimoire.types.extraction import ExtractionResult
from grimoire.types.llm import CompletionRequest
from grimoire.types.mechanics import (
    MechanicsResult,
    ProposalResolution,
    ProposedRoll,
)
from grimoire.types.orchestrator import (
    ForkCampaignResult,
    RegeneratePostResult,
    RegenerateResult,
    ReplayBatchStateView,
    RetconResult,
    SubmitResult,
    TurnStatus,
    UndoResult,
)
from grimoire.types.scene import AdvanceResult
from grimoire.types.scene import SceneContext as PydanticSceneContext

logger = logging.getLogger(__name__)

WSPushFn = Callable[[str, dict], Awaitable[None]]


@dataclass
class _ActiveTurn:
    turn_id: TurnId
    campaign_id: CampaignId
    scene_id: SceneId
    started_at: datetime
    stage: str = "starting"
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_chunk_at: datetime | None = None
    player_post_id: PostId | None = None
    scene_break_choice: asyncio.Future | None = None
    # Captured at the start of the turn so a resumed continuation has the
    # original inputs available.
    player_input: str = ""
    triggering_pc: CharacterRef | None = None


class _StreamFailure(Exception):
    """Raised by ``_stream_main_response`` when the gateway errors mid-stream.

    Carries any text accumulated before the failure so the caller can decide
    whether to surface a partial response to the user.
    """

    def __init__(self, partial_text: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.partial_text = partial_text
        self.cause = cause


@dataclass
class _PendingPreRoll:
    """Per-campaign state for a turn paused on pre_roll_pending."""

    turn_id: TurnId
    campaign_id: CampaignId
    scene_id: SceneId
    player_input: str
    triggering_pc: CharacterRef | None
    proposals: list[ProposedRoll]
    # Proposals that were auto-resolved (high_stakes filtering) and don't
    # need user confirmation but should be threaded into the final results.
    auto_resolved: list[MechanicsResult] = field(default_factory=list)


class _NullAutoDisable:
    """Permissive `select_mode` collaborator used until AutoDisableState lands.

    The Orchestrator owns runtime failure tracking; until that's wired,
    every mode is reported as enabled and `select_mode` picks based purely
    on provider capabilities + campaign preference. Falls back per call are
    still surfaced as `ExtractionFlag` warnings by the Extractor itself.
    """

    async def together_disabled(self, provider_id: str, model: str) -> bool:
        return False

    async def tool_use_disabled(self, provider_id: str, model: str) -> bool:
        # Streaming tool_call surfacing isn't wired through the gateway yet,
        # so TOOL_USE is reported as auto-disabled to keep AUTO from
        # repeatedly choosing it and falling back. Flip to ``False`` once the
        # gateway streams tool calls.
        return True


@dataclass
class _CampaignTurnState:
    """Lightweight per-campaign coordination state.

    The ``lock`` serializes turns; ``queued`` is bumped while waiters are
    blocked so :meth:`OrchestratorService.queue_length` can report it.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    queued: int = 0
    active: _ActiveTurn | None = None
    last_turn_id: TurnId | None = None
    pending_pre_roll: _PendingPreRoll | None = None
    speaker_loop_event: asyncio.Event | None = None


def _canonical_cast_ref(ref: str) -> str:
    """Normalize an emergent-character ref to its canonical form (#464).

    Scenes may store the ``emergent/character/<id>`` shorthand (e.g. the scene
    route reconciles ``present_pc_refs`` with that prefix), while
    ``find_cast_ref`` returns the canonical ``campaign:emergent/character/<id>``.
    Normalizing both sides lets presence checks and removals line up.
    """
    if ref.startswith("emergent/"):
        return f"campaign:{ref}"
    return ref


async def resolve_cast_changes(
    *,
    extraction: ExtractionResult,
    scene: Any,
    campaign_id: CampaignId,
    turn_id: str | None,
    characters: Any,
    scenes: Any,
) -> list[str]:
    """Resolve extractor cast-change proposals and queue the known ones (#464).

    Known characters → ``SceneManager.queue_cast_change`` (pending review).
    Unknown names → appended to ``extraction.candidates`` (new-character flow).
    No-ops (enter already-present / leave not-present) are dropped. Returns the
    list of queued pending-cast-change ids.
    """
    from grimoire.types.common import EntityKind
    from grimoire.types.extraction import EntityCandidate
    from grimoire.types.scene import CastChange
    from grimoire.util import slugify_id

    queued: list[str] = []
    if characters is None or scenes is None:
        return queued
    if not extraction.cast_changes:
        return queued
    # PCs may sit in ``present_pc_refs`` before they appear in
    # ``present_character_refs`` (a freshly started scene seeds them
    # separately), so union both when deciding what counts as already-present.
    # Map canonical form → the ref as actually stored, so a LEAVE removes the
    # exact stored ref (canonical or emergent shorthand) and presence checks
    # match regardless of which form the scene holds.
    canon_to_stored: dict[str, str] = {}
    for r in (
        *(getattr(scene, "present_character_refs", []) or []),
        *(getattr(scene, "present_pc_refs", []) or []),
    ):
        canon_to_stored.setdefault(_canonical_cast_ref(r), r)
    # Campaign PC registrations keyed by canonical form → the ref as registered
    # (often the ``emergent/...`` shorthand). A PC ENTER queues this exact ref so
    # confirming it keys present_pc_refs / _pc_current_scene the same way the PC
    # subsystem and the frontend's submitted pc_ref do; queuing the canonical ref
    # instead would strand an emergent PC ("no active scene") (#464).
    pc_canon_to_ref: dict[str, str] = {}
    for pc in await characters.list_pcs(campaign_id):
        pc_canon_to_ref.setdefault(_canonical_cast_ref(pc.character_ref), pc.character_ref)
    for proposal in extraction.cast_changes:
        cast_ref = await characters.find_cast_ref(campaign_id, proposal.character_ref)
        if cast_ref is None:
            name = proposal.character_ref.strip()
            if name and not any(c.proposed_name == name for c in extraction.candidates):
                extraction.candidates.append(
                    EntityCandidate(
                        kind=EntityKind.CHARACTER,
                        proposed_id=slugify_id(name, fallback="unknown"),
                        proposed_name=name,
                        evidence=proposal.evidence,
                        confidence=proposal.confidence,
                        suggested_card={"name": name, "scope": "campaign-local"},
                    )
                )
            continue
        canon = _canonical_cast_ref(cast_ref.character_ref)
        present = canon in canon_to_stored
        if proposal.change == CastChange.ENTER and present:
            continue
        if proposal.change == CastChange.LEAVE and not present:
            continue
        # LEAVE removes the exact stored form; ENTER adds the canonical ref —
        # or, for a registered PC, the campaign PC registration ref so the PC
        # subsystem (present_pc_refs / _pc_current_scene) can match it.
        if proposal.change == CastChange.LEAVE:
            queue_ref = canon_to_stored[canon]
        elif cast_ref.is_pc and canon in pc_canon_to_ref:
            queue_ref = pc_canon_to_ref[canon]
        else:
            queue_ref = cast_ref.character_ref
        change_id = await scenes.queue_cast_change(
            scene.id,
            character_ref=queue_ref,
            change=proposal.change,
            is_pc=cast_ref.is_pc,
            evidence=proposal.evidence,
            confidence=proposal.confidence,
            turn_id=turn_id,
        )
        queued.append(change_id)
    return queued


class OrchestratorService:
    """Concrete Orchestrator.

    Constructor takes the modules it coordinates. ``ws_push`` is an
    optional async callable ``(campaign_id, message) -> None`` used to
    forward streaming chunks and lifecycle events to the Frontend.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        scene_manager: SceneManager,
        llm_gateway: Any,
        context_builder: Any,
        extractor: Any,
        state_store: Any,
        mechanics: Any | None = None,
        world: Any | None = None,
        characters: Any | None = None,
        continuity: Any | None = None,
        transient_state: Any | None = None,
        inventory: Any | None = None,
        ws_push: WSPushFn | None = None,
        extractor_config: ExtractorConfig | None = None,
        config: OrchestratorConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
        library: Any | None = None,
        context_cache: ContextBuilderCache | None = None,
        auto_disable: Any | None = None,
        metrics: MetricsRegistryProtocol = NULL_METRICS,
    ) -> None:
        self._bus = event_bus
        self._scenes = scene_manager
        self._gateway = llm_gateway
        self._context = context_builder
        self._extractor = extractor
        self._store = state_store
        self._mechanics = mechanics
        self._world = world  # §5: optional, used to dispatch weather-override deltas
        self._characters = characters  # §464: optional, used for cast-change resolution
        self._library = library
        # §5: optional continuity (registry or single service). When wired,
        # FACT_* / COMMITMENT_* / KNOWLEDGE_REVEAL deltas route to the
        # continuity store with a contradiction check first, rather than
        # being applied through the generic state-store path.
        self._continuity = continuity
        self._transient_state = transient_state
        self._inventory = inventory
        self._ws_push = ws_push
        self._extractor_config = extractor_config or ExtractorConfig()
        self._config = config or OrchestratorConfig()
        self._clock = clock
        self._rng = rng or random.Random()
        self._campaigns: dict[CampaignId, _CampaignTurnState] = {}
        # § Spec context-builder-remaining §11. The cache lives at the
        # orchestrator boundary so invalidation sits next to the regenerate
        # logic. Defaults to a fresh in-memory store when not provided.
        self._context_cache = context_cache or ContextBuilderCache()
        # In-memory parking lot for auxiliary tasks awaiting accept/discard.
        # Transient: cleared on restart per spec.
        self._inflight_aux: dict[str, Any] = {}
        self._auto_disable = auto_disable or _NullAutoDisable()
        self._metrics: MetricsRegistryProtocol = metrics
        self._delta = DeltaApplier(
            state_store=self._store,
            continuity=self._continuity,
            extractor=self._extractor,
            world=self._world,
            event_bus=self._bus,
            gateway=self._gateway,
            extractor_config=self._extractor_config,
            config=self._config,
            auto_disable=self._auto_disable,
            ws_push=self._ws_push,
        )
        self._alternates = AlternatesManager(
            scenes=self._scenes,
            state_store=self._store,
            event_bus=self._bus,
            context_builder=self._context,
            delta=self._delta,
            config=self._config,
            clock=self._clock,
            stream_response=self._stream_main_response,
        )
        self._auxiliary = AuxiliaryCoordinator(
            host=self,
            scenes=self._scenes,
            state_store=self._store,
            context_builder=self._context,
            extractor=self._extractor,
            inflight_aux=self._inflight_aux,
        )
        self._retcon = RetconCoordinator(
            host=self,
            scenes=self._scenes,
            state_store=self._store,
            event_bus=self._bus,
            extractor=self._extractor,
            extractor_config=self._extractor_config,
        )
        self._fork = ForkCoordinator(
            host=self,
            scenes=self._scenes,
            state_store=self._store,
            event_bus=self._bus,
            clock=self._clock,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def event_bus(self) -> EventBus:
        return self._bus

    async def turn_in_progress(self, campaign_id: CampaignId) -> TurnStatus | None:
        state = self._campaigns.get(campaign_id)
        if state is None or state.active is None:
            return None
        active = state.active
        return TurnStatus(
            turn_id=active.turn_id,
            campaign_id=active.campaign_id,
            started_at=active.started_at,
            stage=active.stage,
        )

    async def queue_length(self, campaign_id: CampaignId) -> int:
        state = self._campaigns.get(campaign_id)
        return state.queued if state else 0

    async def submit_post(
        self,
        campaign_id: CampaignId,
        pc_ref: CharacterRef,
        text: str,
        metadata: dict | None = None,
    ) -> SubmitResult:
        await self._require_campaign(campaign_id)
        await self._require_pc(campaign_id, pc_ref)

        scene = await self._scenes.active_scene_for_pc(campaign_id, pc_ref)
        if scene is None:
            raise OrchestratorError(
                f"no active scene for pc {pc_ref!r} in campaign {campaign_id!r}"
            )

        post = self._new_post(
            author_kind=SceneAuthorKind.PC,
            body=text,
            is_player=True,
            author_pc_ref=pc_ref,
        )
        await self._scenes.append_post(scene.id, post)
        try:
            await self._store.mark_pc_played(campaign_id=campaign_id, character_ref=pc_ref)
        except Exception:
            logger.warning("mark_pc_played failed", exc_info=True)
        await self._bus.emit(
            Event(
                type=events.PC_POST_APPENDED,
                payload={
                    "campaign_id": campaign_id,
                    "scene_id": scene.id,
                    "post_id": post.id,
                    "pc_ref": pc_ref,
                },
            )
        )

        decision = await self._scenes.on_post_submitted(scene.id, post)
        if not decision.auto_respond:
            return SubmitResult(
                accepted=True,
                turn_id=None,
                auto_responding=False,
                reason=decision.reason,
            )

        turn_id = await self._run_turn(
            campaign_id=campaign_id,
            scene_id=scene.id,
            player_input=text,
            triggering_pc=pc_ref,
            player_post_id=post.id,
        )
        return SubmitResult(
            accepted=True,
            turn_id=turn_id,
            auto_responding=True,
            reason=decision.reason,
        )

    async def submit_direction(
        self,
        campaign_id: CampaignId,
        scene_id: SceneId,
        text: str | None = None,
    ) -> SubmitResult:
        await self._require_campaign(campaign_id)

        scene = await self._scenes.get_scene(scene_id)
        if scene.campaign_id != campaign_id:
            raise OrchestratorError(
                f"scene {scene_id!r} does not belong to campaign {campaign_id!r}"
            )
        if scene.present_pc_refs:
            raise OrchestratorError(
                f"scene {scene_id!r} is not a PC-absent scene; use submit_post instead"
            )
        if scene.closed:
            raise OrchestratorError(f"scene {scene_id!r} is closed")

        active_scene = await self._scenes.active_scene_for_campaign(campaign_id)
        active_scene_id = getattr(active_scene, "id", None)
        if active_scene_id is not None and active_scene_id != scene.id:
            raise OrchestratorError(
                f"scene {scene_id!r} is not the active scene for campaign {campaign_id!r}"
            )

        player_input = text or ""
        # Always append a direction post (even for empty Continue) so regenerate
        # can reconstruct the exact input via _strip_response_for_turn instead of
        # walking back to an older direction.
        post = self._new_post(
            author_kind=SceneAuthorKind.SYSTEM,
            body=player_input,
            is_player=True,
        )
        await self._scenes.append_post(scene.id, post)
        direction_post_id = post.id

        turn_id = await self._run_turn(
            campaign_id=campaign_id,
            scene_id=scene.id,
            player_input=player_input,
            triggering_pc=None,
            player_post_id=direction_post_id,
        )
        return SubmitResult(
            accepted=True,
            turn_id=turn_id,
            auto_responding=True,
            reason="direction",
        )

    async def advance(self, campaign_id: CampaignId, scene_id: SceneId) -> AdvanceResult:
        await self._require_campaign(campaign_id)
        adv = await self._scenes.on_advance_requested(scene_id)
        # §10: scene manager emits ADVANCE_REQUESTED on its own (scene) bus
        # which is a different bus type; the orchestrator owns surfacing it
        # on the shared event bus.
        await self._bus.emit(
            Event(
                type=events.ADVANCE_REQUESTED,
                payload={
                    "campaign_id": campaign_id,
                    "scene_id": scene_id,
                    "pending_post_count": len(adv.pending_posts),
                },
            )
        )
        # Build a "combined" player input from the pending PC posts so the
        # Context Builder can pack both as the prompt; the response addresses
        # both.
        combined_input = "\n\n".join(
            f"[{p.author_pc_ref or 'pc'}] {p.body}" for p in adv.pending_posts
        )
        turn_id = await self._run_turn(
            campaign_id=campaign_id,
            scene_id=scene_id,
            player_input=combined_input,
            triggering_pc=None,
            player_post_id=None,
        )
        return AdvanceResult(
            scene=_pydantic_scene(adv.scene),
            pending_posts=[_pydantic_post(p) for p in adv.pending_posts],
            turn_id=turn_id,
            note="advance dispatched",
        )

    async def next_speaker(self, campaign_id: CampaignId) -> None:
        """Signal the speaker loop to pick and stream the next character."""
        state = self._state_for(campaign_id)
        if state.speaker_loop_event is not None:
            state.speaker_loop_event.set()

    async def regenerate_last(self, campaign_id: CampaignId) -> RegenerateResult:
        """Undo the most recent model response and re-run the turn.

        Looks up the last turn id from the delta log, reverses its deltas,
        deletes the model post if present, then re-runs the turn from the
        triggering player input.
        """
        await self._require_campaign(campaign_id)
        state = self._state_for(campaign_id)
        if state.last_turn_id is None:
            return RegenerateResult(
                turn_id="", accepted=False, reason="no prior turn to regenerate"
            )
        prior_turn_id = state.last_turn_id
        # Reverse all deltas attributed to the last turn.
        await self._reverse_turn_deltas(campaign_id, prior_turn_id)

        # Look up the last narrator/system post for the turn and delete it,
        # then pull the preceding player input to re-prompt.
        player_input, scene_id, pc_ref = await self._strip_response_for_turn(
            campaign_id, prior_turn_id
        )
        if scene_id is None:
            return RegenerateResult(
                turn_id="", accepted=False, reason="no scene found for regenerate"
            )
        turn_id = await self._run_turn(
            campaign_id=campaign_id,
            scene_id=scene_id,
            player_input=player_input or "",
            triggering_pc=pc_ref,
            reuse_prompt_cache=True,
            player_post_id=None,
        )
        return RegenerateResult(turn_id=turn_id, accepted=True, reason="regenerated")

    # ------------------------------------------------------------------ #
    # Swipes / alternates (delegated to AlternatesManager)
    # ------------------------------------------------------------------ #

    async def regenerate_post(self, **kw: Any) -> RegeneratePostResult:
        return await self._alternates.regenerate_post(**kw)

    async def switch_primary_alternate(self, **kw: Any) -> dict[str, Any]:
        return await self._alternates.switch_primary_alternate(**kw)

    async def pin_alternate(self, **kw: Any) -> None:
        return await self._alternates.pin_alternate(**kw)

    async def delete_alternate(self, **kw: Any) -> None:
        return await self._alternates.delete_alternate(**kw)

    async def purge_stale_alternates(self, campaign_id: CampaignId, **kw: Any) -> list[str]:
        return await self._alternates.purge_stale_alternates(campaign_id, **kw)

    # ------------------------------------------------------------------ #
    # Auxiliary tasks (delegated to AuxiliaryCoordinator)
    # ------------------------------------------------------------------ #

    async def run_auxiliary_task(self, **kw: Any) -> Any:
        return await self._auxiliary.run_auxiliary_task(**kw)

    async def discard_auxiliary(self, result_id: str) -> bool:
        return self._auxiliary.discard_auxiliary(result_id)

    def list_inflight_auxiliary(self, campaign_id: CampaignId | None = None) -> list[Any]:
        return self._auxiliary.list_inflight_auxiliary(campaign_id)

    async def accept_auxiliary(
        self, campaign_id: CampaignId, result_id: str, **kw: Any
    ) -> dict[str, Any]:
        return await self._auxiliary.accept_auxiliary(campaign_id, result_id, **kw)

    async def undo_turn(self, campaign_id: CampaignId, count: int = 1) -> UndoResult:
        await self._require_campaign(campaign_id)
        if count <= 0:
            return UndoResult()
        turn_ids = await self._recent_turn_ids(campaign_id, count)
        if not turn_ids:
            raise NoTurnsToUndoError(campaign_id)

        undone: list[TurnId] = []
        reversed_ids: list[str] = []
        warnings: list[str] = []
        for turn_id in turn_ids:
            try:
                ids = await self._reverse_turn_deltas(campaign_id, turn_id)
            except Exception as exc:
                warnings.append(f"failed to reverse turn {turn_id}: {exc}")
                break
            undone.append(turn_id)
            reversed_ids.extend(ids)
            await self._bus.emit(
                Event(
                    type=events.TURN_UNDONE,
                    payload={
                        "campaign_id": campaign_id,
                        "turn_id": turn_id,
                        "reversed_deltas": ids,
                    },
                )
            )

        # Reset the last_turn_id pointer so regenerate refers to the now-top turn.
        state = self._state_for(campaign_id)
        all_ids = await self._recent_turn_ids(campaign_id, 1)
        state.last_turn_id = all_ids[0] if all_ids else None
        return UndoResult(turns_undone=undone, reversed_delta_ids=reversed_ids, warnings=warnings)

    # ------------------------------------------------------------------ #
    # Retcon / replay (delegated to RetconCoordinator)
    # ------------------------------------------------------------------ #

    async def retcon_post(self, post_id: PostId, new_text: str, **kw: Any) -> RetconResult:
        return await self._retcon.retcon_post(post_id, new_text, **kw)

    @property
    def retcon_replay(self) -> RetconReplaySession:
        return self._retcon.retcon_replay

    async def accept_replay(self, campaign_id: CampaignId, **kw: Any) -> ReplayBatchStateView:
        return await self._retcon.accept_replay(campaign_id, **kw)

    async def try_again_replay(self, campaign_id: CampaignId, **kw: Any) -> ReplayBatchStateView:
        return await self._retcon.try_again_replay(campaign_id, **kw)

    async def cancel_replay(self, campaign_id: CampaignId, **kw: Any) -> ReplayBatchStateView:
        return await self._retcon.cancel_replay(campaign_id, **kw)

    async def get_replay_state(
        self, campaign_id: CampaignId, batch_id: str
    ) -> ReplayBatchStateView:
        return await self._retcon.get_replay_state(campaign_id, batch_id)

    # ------------------------------------------------------------------ #
    # Fork (delegated to ForkCoordinator)
    # ------------------------------------------------------------------ #

    async def fork_campaign(self, **kw: Any) -> ForkCampaignResult:
        return await self._fork.fork_campaign(**kw)

    async def list_pending_forks(self, campaign_id: str) -> list[dict]:
        return await self._fork.list_pending_forks(campaign_id)

    async def process_pending_forks(self, campaign_id: str) -> list[ForkCampaignResult]:
        return await self._fork.process_pending_forks(campaign_id)

    async def get_lineage(self, campaign_id: str) -> dict:
        return await self._fork.get_lineage(campaign_id)

    async def get_lineage_ancestors(self, campaign_id: str) -> list[dict]:
        return await self._fork.get_lineage_ancestors(campaign_id)

    async def route_analysis_deltas(
        self,
        campaign_id: CampaignId,
        extraction: ExtractionResult,
        scene_id: SceneId | None = None,
    ) -> tuple[list[str], list[str]]:
        """Route deltas from a scene analysis through the standard pipeline.

        Filters out TIME_ADVANCE deltas (the Time Engine subscriber owns
        calendar advancement; applying them directly would race it) and
        routes transient updates when a transient-state service is wired.
        """
        from grimoire.types.state import DeltaKind

        turn_id = f"analysis:{uuid.uuid4().hex[:12]}"

        filtered = ExtractionResult(
            deltas=[d for d in extraction.deltas if d.kind != DeltaKind.TIME_ADVANCE],
            candidates=extraction.candidates,
            flags=extraction.flags,
            transient_updates=extraction.transient_updates,
            cast_changes=extraction.cast_changes,
            confidence_overall=extraction.confidence_overall,
            extraction_strategies_run=extraction.extraction_strategies_run,
        )

        # §464: resolve cast-change proposals *before* apply_routing so an
        # unknown-name candidate it appends is routed with the rest, matching
        # the invariant in _continue_turn_after_pre_roll.
        if scene_id is not None and self._characters is not None and filtered.cast_changes:
            scene_obj = await self._scenes.get_scene(scene_id)
            await resolve_cast_changes(
                extraction=filtered,
                scene=scene_obj,
                campaign_id=campaign_id,
                turn_id=turn_id,
                characters=self._characters,
                scenes=self._scenes,
            )
            # resolve_cast_changes may append unknown-name candidates to the
            # filtered copy; surface them on the caller's extraction so the
            # analyze route serializes them as entity_candidates for review.
            extraction.candidates = filtered.candidates
            await self._push_pending_cast_changes(campaign_id, scene_id)

        applied_ids, queued_ids = await self._delta.apply_routing(
            campaign_id=campaign_id,
            turn_id=turn_id,
            extraction=filtered,
        )

        if self._transient_state is not None and filtered.transient_updates:
            from grimoire.transient_state.routing import route_transient_updates

            await route_transient_updates(
                campaign_id=campaign_id,
                proposals=list(filtered.transient_updates),
                transient_state=self._transient_state,
                source_post_id=turn_id,
                continuity=self._continuity,
            )

        return applied_ids, queued_ids

    # ------------------------------------------------------------------ #
    # Turn loop
    # ------------------------------------------------------------------ #

    async def _run_turn(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        player_input: str,
        triggering_pc: CharacterRef | None,
        reuse_prompt_cache: bool = False,
        player_post_id: PostId | None = None,
    ) -> TurnId:
        async with self._metrics.measure("orchestrator", "turn"):
            return await self._run_turn_inner(
                campaign_id=campaign_id,
                scene_id=scene_id,
                player_input=player_input,
                triggering_pc=triggering_pc,
                reuse_prompt_cache=reuse_prompt_cache,
                player_post_id=player_post_id,
            )

    async def _run_turn_inner(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        player_input: str,
        triggering_pc: CharacterRef | None,
        reuse_prompt_cache: bool = False,
        player_post_id: PostId | None = None,
    ) -> TurnId:
        state = self._state_for(campaign_id)
        state.queued += 1
        try:
            await state.lock.acquire()
        except BaseException:
            # BaseException so asyncio.CancelledError also decrements the
            # counter; with `except Exception` a cancelled acquire() would
            # leave `state.queued` permanently inflated.
            state.queued -= 1
            raise
        state.queued = max(0, state.queued - 1)

        turn_id = f"t_{uuid.uuid4().hex[:16]}"
        active = _ActiveTurn(
            turn_id=turn_id,
            campaign_id=campaign_id,
            scene_id=scene_id,
            started_at=self._clock(),
            stage="starting",
            player_post_id=player_post_id,
            player_input=player_input,
            triggering_pc=triggering_pc,
        )
        state.active = active

        heartbeat_task: asyncio.Task | None = None
        # The lock is released either at the end of the body (normal
        # completion) or by ``resolve_pre_roll`` when it picks up a paused
        # turn. The pause path returns the turn_id early and lets the
        # campaign sit in ``stage = pre_roll_pending`` until a follow-up
        # call finishes the work.
        release_lock = True
        try:
            if self._config.heartbeat.enabled and self._ws_push is not None:
                heartbeat_task = asyncio.create_task(self._heartbeat_loop(active))
            try:
                paused = await asyncio.wait_for(
                    self._run_turn_body(
                        active=active,
                        campaign_id=campaign_id,
                        scene_id=scene_id,
                        player_input=player_input,
                        triggering_pc=triggering_pc,
                        turn_id=turn_id,
                        reuse_prompt_cache=reuse_prompt_cache,
                    ),
                    timeout=self._config.turn_timeout_seconds,
                )
                if paused:
                    # The turn parked on pre_roll_pending; ``resolve_pre_roll``
                    # owns lock release once the user submits their answer.
                    release_lock = False
                else:
                    state.last_turn_id = turn_id
            except TimeoutError:
                active.cancel_event.set()
                await self._emit_turn_event(
                    events.TURN_TIMED_OUT,
                    turn_id,
                    campaign_id,
                    active.scene_id,
                )
                await self._rollback_player_post(active)
                raise TurnTimeoutError(
                    f"turn {turn_id} exceeded {self._config.turn_timeout_seconds}s"
                ) from None
            except TurnCancelledError:
                await self._emit_turn_event(
                    events.TURN_CANCELLED,
                    turn_id,
                    campaign_id,
                    active.scene_id,
                )
                await self._rollback_player_post(active)
            except _StreamFailure as exc:
                await self._emit_turn_event(
                    events.TURN_FAILED,
                    turn_id,
                    campaign_id,
                    active.scene_id,
                    reason="llm_gateway",
                    partial_response=exc.partial_text,
                )
                if self._config.errors.surface_partial_response_on_llm_error and exc.partial_text:
                    try:
                        from grimoire.extractor.together import strip_tracker_block

                        partial_post = self._new_post(
                            author_kind=SceneAuthorKind.NARRATOR,
                            body=strip_tracker_block(exc.partial_text),
                            is_player=False,
                            turn_id=turn_id,
                        )
                        await self._scenes.append_post(active.scene_id, partial_post)
                    except Exception:
                        logger.debug("appending partial response post failed", exc_info=True)
                await self._rollback_player_post(active)
                raise OrchestratorError(
                    f"llm gateway failed for turn {turn_id}: {exc.cause}"
                ) from exc.cause
            except Exception as exc:
                await self._emit_turn_event(
                    events.TURN_FAILED,
                    turn_id,
                    campaign_id,
                    active.scene_id,
                    reason="orchestrator",
                    partial_response="",
                )
                await self._rollback_player_post(active)
                if isinstance(exc, OrchestratorError):
                    raise
                raise OrchestratorError(f"turn {turn_id} failed: {exc}") from exc
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(BaseException):
                    await heartbeat_task
            if release_lock:
                state.active = None
                state.lock.release()
        return turn_id

    async def _run_turn_body(
        self,
        *,
        active: _ActiveTurn,
        campaign_id: CampaignId,
        scene_id: SceneId,
        player_input: str,
        triggering_pc: CharacterRef | None,
        turn_id: TurnId,
        reuse_prompt_cache: bool = False,
    ) -> bool:
        """Run the turn until completion or a pre_roll_pending pause.

        Returns ``True`` when the turn parked on a pre-roll confirmation (the
        caller leaves the per-campaign lock held so ``resolve_pre_roll`` can
        pick up where this left off). Returns ``False`` for normal completion.
        """
        await self._emit_turn_event(
            events.TURN_STARTED,
            turn_id,
            campaign_id,
            scene_id,
            player_input=player_input,
            options={"pc_ref": triggering_pc},
        )
        self._check_cancelled(active)

        scene_id = await self._maybe_break_scene(
            campaign_id=campaign_id,
            scene_id=scene_id,
            player_input=player_input,
            triggering_pc=triggering_pc,
            turn_id=turn_id,
            active=active,
        )
        active.scene_id = scene_id
        self._check_cancelled(active)

        active.stage = "mechanics_pre_roll"
        pre_roll = await self._do_pre_roll(
            campaign_id=campaign_id, scene_id=scene_id, player_input=player_input
        )
        # Emit any rolls that were auto-resolved (high_stakes filtering may
        # leave the rest in ``pre_roll.pending``); the paused proposals are
        # surfaced via the pre_roll_pending event below.
        if pre_roll.results:
            await self._emit_fragment(
                turn_id,
                campaign_id,
                resolved_rolls=[r.model_dump(mode="json") for r in pre_roll.results],
            )
        self._check_cancelled(active)

        state = self._campaigns.get(campaign_id)
        if pre_roll.pending and state is not None:
            state.pending_pre_roll = _PendingPreRoll(
                turn_id=turn_id,
                campaign_id=campaign_id,
                scene_id=scene_id,
                player_input=player_input,
                triggering_pc=triggering_pc,
                proposals=list(pre_roll.pending),
                auto_resolved=list(pre_roll.results),
            )
            active.stage = "pre_roll_pending"
            await self._emit_turn_event(
                events.PRE_ROLL_PENDING,
                turn_id,
                campaign_id,
                scene_id,
                proposals=[p.model_dump(mode="json") for p in pre_roll.pending],
            )
            return True

        # Check narrator mode — multi-call enters the speaker loop instead
        from grimoire.scenes.narrator_mode import PER_CHARACTER_MULTI_CALL, effective_response_mode

        campaign_row: dict | None = None
        with contextlib.suppress(Exception):
            campaign_row = await self._store.get_campaign_row(campaign_id)
        scene_for_mode = await self._scenes.get_scene(scene_id)
        narrator_mode = effective_response_mode(
            scene_override=scene_for_mode.narrator_response_mode,
            campaign_row=campaign_row,
        )
        if narrator_mode == PER_CHARACTER_MULTI_CALL:
            await self._run_speaker_loop(
                campaign_id=campaign_id,
                scene_id=scene_id,
                turn_id=turn_id,
                active=active,
                player_input=player_input,
                triggering_pc=triggering_pc,
            )
            pending_cast = await self._scenes.list_pending_cast_changes(scene_id)
            await self._emit_turn_event(
                events.TURN_COMPLETE,
                turn_id,
                campaign_id,
                scene_id,
                pending_cast_changes=[p.model_dump(mode="json") for p in pending_cast],
            )
            return False

        await self._continue_turn_after_pre_roll(
            active=active,
            resolved_results=pre_roll.results,
            reuse_prompt_cache=reuse_prompt_cache,
        )
        return False

    async def _continue_turn_after_pre_roll(
        self,
        *,
        active: _ActiveTurn,
        resolved_results: list[MechanicsResult],
        reuse_prompt_cache: bool = False,
    ) -> None:
        """Drive the turn from context-build through turn_complete.

        Shared by ``_run_turn_body`` (when no pre-roll pause is needed) and
        ``resolve_pre_roll`` (when a paused turn is resumed). The active
        turn carries the original player_input / triggering_pc captured at
        ``submit_post`` time.
        """
        campaign_id = active.campaign_id
        turn_id = active.turn_id
        scene_id = active.scene_id
        player_input = active.player_input
        triggering_pc = active.triggering_pc

        active.stage = "context_build"
        composition_hash = await self._composition_hash(campaign_id)
        # Decide the extraction mode for this turn before assembling the
        # prompt so the Context Builder can attach tracker instructions or
        # tool declarations as appropriate.
        extract_mode = await self._delta.select_extract_mode(campaign_id=campaign_id)
        cache_key = make_cache_key(
            campaign_id=campaign_id,
            player_input=player_input,
            composition_hash=composition_hash,
            scene_id=scene_id,
            pc_ref=triggering_pc,
        )
        cached = self._context_cache.get(cache_key) if reuse_prompt_cache else None
        if cached is not None:
            prompt = cached
        else:
            prompt = await self._context.build(
                player_input,
                campaign_id,
                mechanics_results=resolved_results,
                pc_ref=triggering_pc,
                turn_id=turn_id,
                extractor_mode=extract_mode,
            )
            self._context_cache.put(cache_key, prompt)
        # ``context_summary`` / ``composition_snapshot`` are deliberately
        # omitted: ``AssembledPrompt`` exposes them as plain primitives
        # (str / dict) which don't satisfy ``ContextSummary`` /
        # ``CompositionSnapshot``. They'll be filled by ContextBuilder
        # enrichment in a follow-up pass.
        await self._emit_turn_event(
            events.CONTEXT_BUILT,
            turn_id,
            campaign_id,
            scene_id,
            budget_used={str(k): v for k, v in prompt.budget_used.items()},
            messages_hash=getattr(prompt, "messages_hash", "") or "",
            context_sources=[s.model_dump(mode="json") for s in getattr(prompt, "sources", [])],
            assembled_messages=[m.model_dump(mode="json") for m in prompt.messages],
        )
        self._check_cancelled(active)

        active.stage = "streaming"
        response_text = await self._stream_main_response(
            campaign_id=campaign_id,
            turn_id=turn_id,
            prompt=prompt,
            active=active,
        )
        if active.cancel_event.is_set():
            raise TurnCancelledError()
        # The gateway emits ``llm_response_received`` with provider/model/
        # tokens/cost/latency/retries; the TurnAuditor merges those into
        # the audit buffer keyed by turn_id. Here we only carry the
        # response text and length.
        await self._emit_turn_event(
            events.MODEL_RESPONSE_RECEIVED,
            turn_id,
            campaign_id,
            scene_id,
            length=len(response_text),
            response_text=response_text,
        )

        active.stage = "extracting"
        scene_obj = await self._scenes.get_scene(scene_id)
        extract_started = self._clock()
        extraction = await self._delta.extract(
            response_text=response_text,
            scene=scene_obj,
            campaign_id=campaign_id,
            turn_id=turn_id,
            mode=extract_mode,
        )
        extract_duration_ms = int((self._clock() - extract_started).total_seconds() * 1000)

        # §464: resolve cast-change proposals *before* routing/emitting, so an
        # unknown-name candidate it appends is part of the extraction for every
        # downstream consumer (DELTAS_EXTRACTED, apply_routing), and pending
        # cast changes are queued for review.
        if extraction is not None and self._characters is not None:
            pending_ids = await resolve_cast_changes(
                extraction=extraction,
                scene=scene_obj,
                campaign_id=campaign_id,
                turn_id=turn_id,
                characters=self._characters,
                scenes=self._scenes,
            )
            if pending_ids:
                pending = await self._scenes.list_pending_cast_changes(scene_id)
                await self._emit_fragment(
                    turn_id,
                    campaign_id,
                    pending_cast_changes=[p.model_dump(mode="json") for p in pending],
                )
                await self._push_pending_cast_changes(campaign_id, scene_id)

        await self._emit_turn_event(
            events.DELTAS_EXTRACTED,
            turn_id,
            campaign_id,
            scene_id,
            count=len(extraction.deltas) if extraction else 0,
            deltas=([d.model_dump(mode="json") for d in extraction.deltas] if extraction else []),
            strategies_run=(list(getattr(extraction, "strategies_run", [])) if extraction else []),
            flags=(
                [f.model_dump(mode="json") for f in getattr(extraction, "flags", [])]
                if extraction
                else []
            ),
            duration_ms=extract_duration_ms,
        )
        await self._delta.emit_integrated_deltas_fallback(
            extraction=extraction,
            turn_id=turn_id,
            campaign_id=campaign_id,
            scene_id=scene_id,
        )
        self._check_cancelled(active)

        # Time Engine subscriber drives the calendar advance from these
        # durations — applying TIME_ADVANCE deltas directly would race the
        # engine's own pipeline.
        time_advance_durations: list[dict[str, Any]] = []
        if extraction is not None:
            from grimoire.time_engine import extract_time_advances_from_deltas

            for d in extract_time_advances_from_deltas(list(extraction.deltas)):
                time_advance_durations.append(d.model_dump(mode="json"))

        active.stage = "applying"
        applied_ids: list[str] = []
        queued_ids: list[str] = []
        if extraction is not None:
            applied_ids, queued_ids = await self._delta.apply_routing(
                campaign_id=campaign_id,
                turn_id=turn_id,
                extraction=extraction,
            )
        if applied_ids or queued_ids:
            await self._emit_fragment(
                turn_id,
                campaign_id,
                applied_deltas=[{"id": did} for did in applied_ids],
                queued_for_review=[{"id": qid} for qid in queued_ids],
            )

        if self._inventory is not None and extraction is not None:
            # The inventory service is injected at startup (Protocol-style);
            # the orchestrator hands it raw deltas and never imports the
            # inventory package (no orchestrator -> inventory module edge).
            try:
                await self._inventory.apply_from_deltas(
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    deltas=list(extraction.deltas),
                )
            except Exception:
                logger.exception("inventory apply failed; continuing turn")

        if (
            extraction is not None
            and self._transient_state is not None
            and getattr(extraction, "transient_updates", None)
        ):
            from grimoire.transient_state.routing import route_transient_updates

            ts_summary = await route_transient_updates(
                campaign_id=campaign_id,
                proposals=list(extraction.transient_updates),
                transient_state=self._transient_state,
                source_post_id=turn_id,
                continuity=self._continuity,
            )
            if ts_summary.writes or ts_summary.conflicts:
                await self._emit_fragment(
                    turn_id,
                    campaign_id,
                    transient_state_writes=ts_summary.writes,
                    transient_state_conflicts=ts_summary.conflicts,
                )

        from grimoire.extractor.together import strip_tracker_block
        from grimoire.orchestrator.post_splitting import create_response_posts
        from grimoire.scenes.narrator_mode import effective_response_mode

        cleaned_text = strip_tracker_block(response_text)
        campaign_row: dict | None = None
        with contextlib.suppress(Exception):
            campaign_row = await self._store.get_campaign_row(campaign_id)
        narrator_mode = effective_response_mode(
            scene_override=scene_obj.narrator_response_mode,
            campaign_row=campaign_row,
        )
        response_posts = create_response_posts(
            response_text=cleaned_text,
            narrator_mode=narrator_mode,
            turn_id=turn_id,
            clock=self._clock,
        )
        for rp in response_posts:
            await self._scenes.append_post(scene_id, rp)
        await self._emit_fragment(turn_id, campaign_id, scene_appended=True)

        pending_cast = await self._scenes.list_pending_cast_changes(scene_id)
        await self._emit_turn_event(
            events.TURN_COMPLETE,
            turn_id,
            campaign_id,
            scene_id,
            time_advances=time_advance_durations,
            pending_cast_changes=[p.model_dump(mode="json") for p in pending_cast],
        )

    async def _run_speaker_loop(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        turn_id: TurnId,
        active: _ActiveTurn,
        player_input: str,
        triggering_pc: CharacterRef | None,
    ) -> None:
        """Multi-call speaker loop for ``per_character_multi_call`` mode.

        Loops: select speaker → build context → stream → create NPC post
        → emit ``speaker_round_waiting`` → wait for ``next_speaker`` or
        player input.  Exits when the wait times out (disconnect) or the
        event is never set (player typed instead of clicking Next).
        """
        from grimoire.extractor.together import strip_tracker_block
        from grimoire.orchestrator.speaker_select import select_fallback_speaker

        scene_obj = await self._scenes.get_scene(scene_id)
        pc_refs = set(scene_obj.present_pc_refs)
        present_npcs = [r for r in scene_obj.present_character_refs if r not in pc_refs]
        if not present_npcs:
            return

        state = self._state_for(campaign_id)
        recent_speakers: list[str] = []

        while True:
            self._check_cancelled(active)

            # Speaker selection
            if len(present_npcs) == 1:
                speaker_ref = present_npcs[0]
            else:
                speaker_ref = select_fallback_speaker(present_npcs, recent_speakers, self._rng)

            recent_speakers.append(speaker_ref)

            # Build context with this character foregrounded
            extract_mode = await self._delta.select_extract_mode(campaign_id=campaign_id)
            prompt = await self._context.build(
                player_input,
                campaign_id,
                extra=None,
                pc_ref=triggering_pc,
                turn_id=turn_id,
                extractor_mode=extract_mode,
            )

            # Stream response
            active.stage = "streaming"
            response_text = await self._stream_main_response(
                campaign_id=campaign_id,
                turn_id=turn_id,
                prompt=prompt,
                active=active,
            )
            if active.cancel_event.is_set():
                raise TurnCancelledError()

            # Extract deltas
            active.stage = "extracting"
            extraction = await self._delta.extract(
                response_text=response_text,
                scene=scene_obj,
                campaign_id=campaign_id,
                turn_id=turn_id,
                mode=extract_mode,
            )
            if extraction is not None:
                # §464: resolve before apply_routing (same invariant as the
                # single-response path) so unknown-name candidates are routed.
                if self._characters is not None:
                    await resolve_cast_changes(
                        extraction=extraction,
                        scene=scene_obj,
                        campaign_id=campaign_id,
                        turn_id=turn_id,
                        characters=self._characters,
                        scenes=self._scenes,
                    )
                    # Surface prompts queued this round without waiting for the
                    # loop's final turn_complete.
                    await self._push_pending_cast_changes(campaign_id, scene_id)
                await self._delta.apply_routing(
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    extraction=extraction,
                )

            # Create NPC post
            cleaned = strip_tracker_block(response_text)
            post = self._new_post(
                author_kind=SceneAuthorKind.NPC,
                body=cleaned,
                is_player=False,
                turn_id=turn_id,
                author_npc_ref=speaker_ref,
            )
            await self._scenes.append_post(scene_id, post)
            await self._emit_fragment(turn_id, campaign_id, scene_appended=True)

            # Signal frontend: ready for next
            await self._push_to_ws(
                campaign_id,
                {"type": events.SPEAKER_ROUND_WAITING, "turn_id": turn_id},
            )

            # Wait for next_speaker signal or timeout
            evt = asyncio.Event()
            state.speaker_loop_event = evt
            try:
                await asyncio.wait_for(
                    evt.wait(),
                    timeout=self._config.speaker_loop.timeout_seconds,
                )
            except TimeoutError:
                break
            finally:
                state.speaker_loop_event = None

            if active.cancel_event.is_set():
                break

            # Refresh scene + present-NPC list so a cast change confirmed during
            # the wait (enter/leave) takes effect on the next speaker selection
            # (#464): a removed NPC is no longer selectable, an arrival becomes one.
            scene_obj = await self._scenes.get_scene(scene_id)
            pc_refs = set(scene_obj.present_pc_refs)
            present_npcs = [r for r in scene_obj.present_character_refs if r not in pc_refs]
            if not present_npcs:
                break

    def _check_cancelled(self, active: _ActiveTurn) -> None:
        if active.cancel_event.is_set():
            raise TurnCancelledError()

    async def _rollback_player_post(self, active: _ActiveTurn) -> None:
        if active.player_post_id is None:
            return
        try:
            await self._scenes.delete_post(active.player_post_id, source="rollback")
        except Exception:
            logger.debug("rollback delete_post for %s failed", active.player_post_id, exc_info=True)
        active.player_post_id = None

    async def _heartbeat_loop(self, active: _ActiveTurn) -> None:
        interval = max(0.001, self._config.heartbeat.interval_seconds)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._push_to_ws(
                    active.campaign_id,
                    {"type": "heartbeat", "turn_id": active.turn_id},
                )
        except asyncio.CancelledError:
            return

    async def cancel_turn(self, campaign_id: CampaignId, turn_id: TurnId) -> bool:
        """Signal cooperative cancellation of an in-flight turn.

        Returns True if the turn was active and the cancel flag was set;
        False if no turn matched.
        """
        state = self._campaigns.get(campaign_id)
        if state is None or state.active is None:
            return False
        if state.active.turn_id != turn_id:
            return False
        state.active.cancel_event.set()
        # Wake any scene-break prompt that's still pending.
        choice = state.active.scene_break_choice
        if choice is not None and not choice.done():
            choice.set_result("continue")
        return True

    async def resolve_scene_break(
        self,
        campaign_id: CampaignId,
        turn_id: TurnId,
        choice: Literal["continue", "new_scene"],
    ) -> bool:
        """Resolve a pending medium-confidence scene-break prompt."""
        state = self._campaigns.get(campaign_id)
        if state is None or state.active is None:
            return False
        if state.active.turn_id != turn_id:
            return False
        fut = state.active.scene_break_choice
        if fut is None or fut.done():
            return False
        fut.set_result(choice)
        return True

    async def _maybe_break_scene(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        player_input: str,
        triggering_pc: CharacterRef | None,
        turn_id: TurnId,
        active: _ActiveTurn | None = None,
    ) -> SceneId:
        if not player_input:
            return scene_id
        try:
            decision = await self._scenes.is_scene_break(scene_id, player_input)
        except Exception:
            return scene_id
        if not decision.is_break:
            return scene_id

        sb_cfg = self._config.scene_break
        if decision.confidence < sb_cfg.prompt_threshold:
            return scene_id

        if decision.confidence < sb_cfg.auto_threshold:
            await self._bus.emit(
                Event(
                    type=events.SCENE_BREAK_SUGGESTED,
                    payload={
                        "campaign_id": campaign_id,
                        "scene_id": scene_id,
                        "turn_id": turn_id,
                        "confidence": decision.confidence,
                        "reason": decision.reason,
                    },
                )
            )
            if active is None:
                return scene_id
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            active.scene_break_choice = fut
            try:
                choice = await asyncio.wait_for(fut, timeout=sb_cfg.prompt_resume_timeout_seconds)
            except TimeoutError:
                logger.warning(
                    "scene-break prompt timed out for turn %s; continuing in scene", turn_id
                )
                choice = "continue"
            finally:
                active.scene_break_choice = None
            if choice != "new_scene":
                return scene_id
            # Fall through to the new-scene path below.

        # Close the current and open a new one.
        try:
            await self._scenes.close_scene(scene_id, closed_at_turn=turn_id)
        except Exception as exc:
            logger.warning("scene close failed: %s", exc)

        init = decision.proposed_new_scene or SceneFileInit(
            campaign_id=campaign_id,
            present_pc_refs=[triggering_pc] if triggering_pc else [],
            present_character_refs=[triggering_pc] if triggering_pc else [],
        )
        new_scene = await self._scenes.start_scene(init)
        return new_scene.id

    async def _do_pre_roll(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        player_input: str,
    ) -> _PreRollOutcome:
        """Evaluate, partition (auto-resolve vs. pending), and resolve proposals.

        The mode selector is :attr:`PreRollConfig.confirm_before_executing`:

        - ``"never"``: resolve every proposal inline.
        - ``"always"``: every proposal is pending.
        - ``"high_stakes"``: resolve only proposals with ``high_stakes=False``;
          the rest go to pending.
        """
        if self._mechanics is None:
            return _PreRollOutcome([], [])
        try:
            scene = await self._scenes.get_scene(scene_id)
        except KeyError:
            return _PreRollOutcome([], [])
        ctx = PydanticSceneContext(scene=_pydantic_scene(scene))
        try:
            proposed: list[ProposedRoll] = await self._mechanics.evaluate_pre_roll(
                campaign_id, player_input, ctx
            )
        except Exception as exc:
            logger.warning("mechanics pre-roll failed: %s", exc)
            return _PreRollOutcome([], [])
        if not proposed:
            return _PreRollOutcome([], [])

        mode = self._config.pre_roll.confirm_before_executing
        pending: list[ProposedRoll] = []
        inline: list[ProposedRoll] = []
        if mode == "always":
            pending = list(proposed)
        elif mode == "high_stakes":
            for p in proposed:
                if p.high_stakes:
                    pending.append(p)
                else:
                    inline.append(p)
        else:  # "never" or unknown → resolve everything inline
            inline = list(proposed)

        results = await self._resolve_proposals(campaign_id, inline)
        return _PreRollOutcome(results=results, pending=pending)

    async def _resolve_proposals(
        self,
        campaign_id: CampaignId,
        proposals: list[ProposedRoll],
    ) -> list[MechanicsResult]:
        out: list[MechanicsResult] = []
        for proposal in proposals:
            roll = _proposed_to_roll(proposal)
            try:
                outcome = await self._mechanics.resolve_roll(campaign_id, roll)
                out.append(MechanicsResult(roll=roll, result=outcome))
            except Exception as exc:
                logger.warning("mechanics roll resolution failed: %s", exc)
        return out

    async def resolve_pre_roll(
        self,
        campaign_id: CampaignId,
        turn_id: TurnId,
        resolutions: list[ProposalResolution],
    ) -> SubmitResult:
        """Resume a paused turn after the user accepts / modifies / declines.

        For each proposal: ``accepted=False`` drops it; ``modifications`` is
        a dict whose keys (``pool``, ``difficulty``, ``modifiers``, ...)
        override the corresponding fields on the proposal.
        """
        state = self._state_for(campaign_id)
        pending = state.pending_pre_roll
        if pending is None or pending.turn_id != turn_id:
            raise OrchestratorError(
                f"no pre_roll_pending turn {turn_id!r} for campaign {campaign_id!r}"
            )

        active = state.active
        if active is None or active.turn_id != turn_id:
            raise OrchestratorError(f"active turn {turn_id!r} not in pre_roll_pending stage")

        # Index resolutions by label; missing labels are treated as accepted
        # with no modifications so the caller can omit them.
        by_label: dict[str, ProposalResolution] = {r.label: r for r in resolutions}
        final_proposals: list[ProposedRoll] = []
        for proposal in pending.proposals:
            resolution = by_label.get(proposal.label)
            if resolution is None:
                final_proposals.append(proposal)
                continue
            if not resolution.accepted:
                continue
            if resolution.modifications:
                merged = proposal.model_copy(update=_clean_modifications(resolution.modifications))
                final_proposals.append(merged)
            else:
                final_proposals.append(proposal)

        resolved = await self._resolve_proposals(campaign_id, final_proposals)
        # Combine with any inline (non-high-stakes) results from the pause.
        all_results = list(pending.auto_resolved) + resolved

        # Clear pending so a second call doesn't double-process.
        state.pending_pre_roll = None
        try:
            await self._continue_turn_after_pre_roll(
                active=active,
                resolved_results=all_results,
            )
            state.last_turn_id = turn_id
        finally:
            state.active = None
            state.lock.release()
        return SubmitResult(
            accepted=True,
            turn_id=turn_id,
            auto_responding=True,
            reason="pre_roll resolved",
        )

    async def _composition_hash(self, campaign_id: CampaignId) -> str:
        """SHA-256 fingerprint of the campaign's current composition.

        Used to key the regenerate prompt cache (spec
        context-builder-remaining §11). When the library service is not
        wired we fall back to the empty string — the cache key is still
        deterministic, it just degrades to "ignore composition changes".
        """
        if self._library is None:
            return ""
        try:
            composition = await self._library.get_composition(campaign_id)
        except Exception:
            return ""
        if composition is None:
            return ""
        import hashlib as _h

        try:
            payload = composition.model_dump_json()  # type: ignore[attr-defined]
        except Exception:
            payload = str(composition)
        return _h.sha256(payload.encode("utf-8")).hexdigest()

    async def _stream_main_response(
        self,
        *,
        campaign_id: CampaignId,
        turn_id: TurnId,
        prompt: Any,
        active: _ActiveTurn | None = None,
    ) -> str:
        params = getattr(prompt, "params", None)
        seed = getattr(params, "seed", None) if params is not None else None
        max_tokens = getattr(params, "max_tokens", 4096)
        temperature = getattr(params, "temperature", 1.0)
        # Per-campaign generation overrides (campaigns.config["generation"]).
        # If the user sets ``max_tokens`` / ``temperature`` in the campaign
        # settings UI, those values take precedence over the
        # ContextBuilder's app-wide defaults.
        gen = await _campaign_generation_overrides(self._store, campaign_id)
        if gen.get("max_tokens") is not None:
            max_tokens = int(gen["max_tokens"])
        if gen.get("temperature") is not None:
            temperature = float(gen["temperature"])
        request = CompletionRequest(
            model="",  # routing resolves the actual model
            messages=list(prompt.messages),
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
        )
        accumulated: list[str] = []
        try:
            stream = self._gateway.stream(
                self._config.main_llm_task,
                request,
                campaign_id=campaign_id,
                turn_id=turn_id,
            )
            async for chunk in stream:
                if active is not None and active.cancel_event.is_set():
                    break
                if chunk.delta:
                    accumulated.append(chunk.delta)
                    if active is not None:
                        active.last_chunk_at = self._clock()
                    await self._push_to_ws(
                        campaign_id,
                        {
                            "type": "token",
                            "turn_id": turn_id,
                            "delta": chunk.delta,
                        },
                    )
                if chunk.is_final:
                    break
        except (TurnCancelledError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise _StreamFailure("".join(accumulated), exc) from exc
        return "".join(accumulated)

    # ------------------------------------------------------------------ #
    # Undo helpers
    # ------------------------------------------------------------------ #

    async def _recent_turn_ids(self, campaign_id: CampaignId, count: int) -> list[TurnId]:
        """Return the last ``count`` turn ids (most recent first)."""
        log = await self._store.get_delta_log(campaign_id=campaign_id, include_reversed=False)
        seen: list[TurnId] = []
        seen_set: set[TurnId] = set()
        for record in reversed(log):
            tid = getattr(record, "turn_id", None)
            if not tid or tid in seen_set:
                continue
            seen.append(tid)
            seen_set.add(tid)
            if len(seen) >= count:
                break
        return seen

    async def _reverse_turn_deltas(self, campaign_id: CampaignId, turn_id: TurnId) -> list[str]:
        log = await self._store.get_delta_log(
            campaign_id=campaign_id, turn_id=turn_id, include_reversed=False
        )
        # Reverse in LIFO order to undo most recent first.
        reversed_ids: list[str] = []
        for record in reversed(log):
            try:
                await self._store.reverse_delta(record.id)
                reversed_ids.append(record.id)
            except Exception as exc:
                logger.warning("reverse_delta(%s) failed: %s", record.id, exc)
        return reversed_ids

    async def _strip_response_for_turn(
        self, campaign_id: CampaignId, turn_id: TurnId
    ) -> tuple[str | None, SceneId | None, CharacterRef | None]:
        """Locate the scene + player input for ``turn_id``; delete the model post.

        Returns ``(player_input, scene_id, pc_ref)`` for re-running.
        """
        # Walk every campaign scene; find posts with this turn_id.
        scene_id: SceneId | None = None
        player_input: str | None = None
        pc_ref: CharacterRef | None = None
        for scene in await self._scenes.list_scenes(campaign_id):
            posts = await self._scenes.get_posts(scene.id)
            response_post = next(
                (p for p in posts if p.turn_id == turn_id and not p.is_player),
                None,
            )
            if response_post is None:
                continue
            scene_id = scene.id
            # The player input is the most recent player post before the response.
            preceding = [p for p in posts if p.order_in_scene < response_post.order_in_scene]
            for p in reversed(preceding):
                if p.is_player:
                    player_input = p.body
                    pc_ref = p.author_pc_ref
                    break
            await self._scenes.delete_post(response_post.id, source="regenerate")
            break
        return player_input, scene_id, pc_ref

    # ------------------------------------------------------------------ #
    # Validation + utility
    # ------------------------------------------------------------------ #

    async def _require_campaign(self, campaign_id: CampaignId) -> None:
        row = await self._store.db.fetchone("SELECT id FROM campaigns WHERE id = ?", (campaign_id,))
        if row is None:
            raise UnknownCampaignError(campaign_id)

    async def _require_pc(self, campaign_id: CampaignId, pc_ref: CharacterRef) -> None:
        row = await self._store.db.fetchone(
            "SELECT character_ref FROM campaign_pcs WHERE campaign_id = ? AND character_ref = ?",
            (campaign_id, pc_ref),
        )
        if row is None:
            raise UnknownPCError(campaign_id, pc_ref)

    def _state_for(self, campaign_id: CampaignId) -> _CampaignTurnState:
        state = self._campaigns.get(campaign_id)
        if state is None:
            state = _CampaignTurnState()
            self._campaigns[campaign_id] = state
        return state

    def _new_post(
        self,
        *,
        author_kind: SceneAuthorKind,
        body: str,
        is_player: bool,
        author_pc_ref: CharacterRef | None = None,
        author_npc_ref: CharacterRef | None = None,
        turn_id: TurnId | None = None,
    ) -> SceneFilePost:
        return SceneFilePost(
            id=str(uuid.uuid4()),
            scene_id="",
            order_in_scene=0,
            author_kind=author_kind,
            author_pc_ref=author_pc_ref,
            author_npc_ref=author_npc_ref,
            body=body,
            is_player=is_player,
            created_at=self._clock(),
            turn_id=turn_id or str(uuid.uuid4()),
        )

    async def _emit_fragment(
        self,
        turn_id: TurnId,
        campaign_id: CampaignId,
        **fields: Any,
    ) -> None:
        await self._bus.emit(
            Event(
                type=events.TURN_AUDIT_FRAGMENT,
                payload={"turn_id": turn_id, "campaign_id": campaign_id, **fields},
            )
        )

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
        await self._push_to_ws(
            campaign_id,
            {
                "type": type_,
                "turn_id": turn_id,
                "scene_id": scene_id,
                **payload,
            },
        )

    async def _push_to_ws(self, campaign_id: CampaignId, message: dict) -> None:
        if self._ws_push is None:
            return
        try:
            await self._ws_push(campaign_id, message)
        except Exception as exc:
            logger.debug("ws_push failed: %s", exc)

    async def _push_pending_cast_changes(self, campaign_id: CampaignId, scene_id: SceneId) -> None:
        """Push the scene's pending cast changes to the frontend (#464).

        Lets ``CastChangePrompt`` surface prompts queued mid-turn (speaker-loop
        rounds) or from a scene analysis, without waiting for ``turn_complete``
        or a manual reload.
        """
        pending = await self._scenes.list_pending_cast_changes(scene_id)
        await self._push_to_ws(
            campaign_id,
            {
                "type": events.PENDING_CAST_CHANGES,
                "scene_id": scene_id,
                "pending_cast_changes": [p.model_dump(mode="json") for p in pending],
            },
        )


__all__ = ["OrchestratorService", "WSPushFn"]
