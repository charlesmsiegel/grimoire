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
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from grimoire import events
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
    SceneClosedError,
    TurnAlreadyInProgressError,
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
    CascadeDeleteResult,
    ForkCampaignResult,
    RegeneratePostResult,
    ReplayBatchStateView,
    RetconResult,
    SubmitResult,
    TurnStatus,
    UndoResult,
)
from grimoire.types.scene import AdvanceResult
from grimoire.types.scene import SceneContext as PydanticSceneContext
from grimoire.util import new_id

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
    # Set by the idle watchdog when the turn is cancelled for exceeding the
    # idle (time-to-first-token / inter-token) budget, so the wrapper can tell
    # a timeout-cancel apart from a user cancel.
    timed_out: bool = False
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
    # Claimed (synchronously) by resolve_pre_roll before its first await so a
    # concurrent resolve call for the same turn can't double-roll or run the
    # continuation twice; released only when the turn re-parks after a failure.
    resolving: bool = False


@dataclass
class _ExtractionApplyResult:
    """Output of the shared post-extraction state stage (#603).

    Returned per call so the speaker loop can merge round results and emit
    one audit fragment per turn — the TurnAuditor's fragment merge is
    last-write-wins per key, so per-round emission would persist only the
    final round.
    """

    applied_ids: list[str] = field(default_factory=list)
    queued_ids: list[str] = field(default_factory=list)
    transient_writes: list[dict] = field(default_factory=list)
    transient_conflicts: list[dict] = field(default_factory=list)
    # WarningRecord-shaped dicts (stage failures flagged on the turn audit).
    warnings: list[dict] = field(default_factory=list)
    # Per-holder inventory pre-images from a committed apply, handed back to
    # InventoryService.restore_holders when a later turn stage fails (#584).
    inventory_rollback: list = field(default_factory=list)

    def merge(self, other: _ExtractionApplyResult) -> None:
        self.applied_ids.extend(other.applied_ids)
        self.queued_ids.extend(other.queued_ids)
        self.transient_writes.extend(other.transient_writes)
        self.transient_conflicts.extend(other.transient_conflicts)
        self.warnings.extend(other.warnings)
        self.inventory_rollback.extend(other.inventory_rollback)


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
    # Bumped while a submit_post/submit_direction is in flight — i.e. from
    # before the player post is appended until the turn it spawns is done.
    # delete_post_cascade rejects while this is > 0 so it can't truncate the
    # scene between the append and the turn that consumes that post (the append
    # itself is done under ``lock``, which the delete also holds).
    submitting: int = 0


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
    from grimoire.util import canonicalize_character_ref, slugify_id

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
        canon_to_stored.setdefault(canonicalize_character_ref(r), r)
    # Campaign PC registrations keyed by canonical form → the ref as registered
    # (often the ``emergent/...`` shorthand). A PC ENTER queues this exact ref so
    # confirming it keys present_pc_refs / _pc_current_scene the same way the PC
    # subsystem and the frontend's submitted pc_ref do; queuing the canonical ref
    # instead would strand an emergent PC ("no active scene") (#464).
    pc_canon_to_ref: dict[str, str] = {}
    for pc in await characters.list_pcs(campaign_id):
        pc_canon_to_ref.setdefault(canonicalize_character_ref(pc.character_ref), pc.character_ref)
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
        canon = canonicalize_character_ref(cast_ref.character_ref)
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
        # Mark the submission in flight and append the player post under the
        # per-campaign turn lock, so a concurrent delete_post_cascade (which
        # rejects on submitting > 0 and holds the same lock) can't truncate the
        # scene between this append and the turn it spawns — which would strand
        # the queued turn on a now-deleted player_post_id.
        state = self._state_for(campaign_id)
        state.submitting += 1
        try:
            async with state.lock:
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
        finally:
            state.submitting -= 1

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
        # Always append a direction post (even for empty Continue) so a later
        # regenerate can reconstruct the exact input from the preceding player
        # post instead of walking back to an older direction.
        post = self._new_post(
            author_kind=SceneAuthorKind.SYSTEM,
            body=player_input,
            is_player=True,
        )
        # See submit_post: announce the in-flight submission and append under
        # the turn lock so a concurrent cascade delete can't truncate away the
        # direction post before the turn it spawns runs.
        state = self._state_for(campaign_id)
        state.submitting += 1
        try:
            async with state.lock:
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
        finally:
            state.submitting -= 1

    async def advance(self, campaign_id: CampaignId, scene_id: SceneId) -> AdvanceResult:
        await self._require_campaign(campaign_id)
        # Like submit_post: on_advance_requested snapshots the pending PC posts
        # and moves the advance watermark before _run_turn sets state.active/
        # queued. Announce the in-flight advance via state.submitting and run the
        # trigger under the turn lock, so a concurrent delete_post_cascade can't
        # truncate the pending posts this advance turn is about to consume.
        state = self._state_for(campaign_id)
        state.submitting += 1
        try:
            async with state.lock:
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
        finally:
            state.submitting -= 1

    async def next_speaker(self, campaign_id: CampaignId) -> None:
        """Signal the speaker loop to pick and stream the next character."""
        state = self._state_for(campaign_id)
        if state.speaker_loop_event is not None:
            state.speaker_loop_event.set()

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

    async def delete_post_cascade(
        self,
        campaign_id: CampaignId,
        scene_id: SceneId,
        post_id: PostId,
    ) -> CascadeDeleteResult:
        """Delete ``post_id`` and every later post in the scene, reverting state.

        Fully-contained turns (all posts at/after the cut) have their deltas
        reversed. Straddling turns (split-mode turns with posts on both sides of
        the cut) are reversed and re-queued for human review. Rejected on a
        closed scene.
        """
        await self._require_campaign(campaign_id)
        scene = await self._scenes.get_scene(scene_id)
        if scene.campaign_id != campaign_id:
            raise OrchestratorError(
                f"scene {scene_id!r} does not belong to campaign {campaign_id!r}"
            )
        if scene.closed:
            raise SceneClosedError(scene_id)

        # Refuse to truncate while any turn is active, queued, *or being
        # submitted* for this campaign. An active turn would keep applying
        # deltas and append its model post after the cut, leaving an orphan
        # response and un-reversed state. A queued turn is just as dangerous:
        # submit_post appends the player post before _run_turn_inner acquires
        # the lock and records state.active, so a queued same-scene turn has no
        # matching active entry yet. And a submission that has only just begun
        # has neither — submit_post bumps state.submitting before it appends.
        # This is a fast-path rejection before we queue for the lock.
        state = self._state_for(campaign_id)
        if state.active is not None or state.queued > 0 or state.submitting > 0:
            raise TurnAlreadyInProgressError(campaign_id)

        # Hold the turn lock across the read→reverse→truncate sequence so a
        # concurrent turn body can't extract into / append to the scene between
        # the snapshot we reverse and the truncation that removes it.
        async with state.lock:
            # Re-check under the lock: the fast-path check above is *not* atomic
            # with the acquire. submit_post/submit_direction bump state.submitting
            # and append the player post under this same lock, then release it
            # before _run_turn re-acquires it. So a submission can slip past the
            # fast-path check, grab the lock first, append, and release — and we
            # would otherwise acquire the lock next and truncate that
            # just-appended prompt, stranding the queued turn on a deleted
            # player_post_id. state.submitting stays > 0 for the whole submission
            # (decremented only after _run_turn), so re-checking here closes the
            # window. (active/queued are likewise re-evaluated for completeness.)
            if state.active is not None or state.queued > 0 or state.submitting > 0:
                raise TurnAlreadyInProgressError(campaign_id)
            return await self._delete_post_cascade_locked(campaign_id, scene_id, post_id, state)

    async def _delete_post_cascade_locked(
        self,
        campaign_id: CampaignId,
        scene_id: SceneId,
        post_id: PostId,
        state: _CampaignTurnState,
    ) -> CascadeDeleteResult:
        posts = await self._scenes.get_posts(scene_id)
        target = next((p for p in posts if p.id == post_id), None)
        if target is None:
            raise KeyError(f"post {post_id!r} not found in scene {scene_id!r}")
        cut = target.order_in_scene

        min_order: dict[str, int] = {}
        max_order: dict[str, int] = {}
        for p in posts:
            tid = p.turn_id
            if not tid:
                continue
            min_order[tid] = min(min_order.get(tid, p.order_in_scene), p.order_in_scene)
            max_order[tid] = max(max_order.get(tid, p.order_in_scene), p.order_in_scene)

        deleted_post_ids = [p.id for p in posts if p.order_in_scene >= cut]
        fully_contained = [t for t, lo in min_order.items() if lo >= cut]
        straddling = [t for t, lo in min_order.items() if lo < cut <= max_order[t]]

        reversed_turn_ids: list[TurnId] = []
        requeued_review_ids: list[str] = []
        warnings: list[str] = []

        # Queued-but-unapplied review deltas must not be reversed/re-queued.
        skip_ids: set[str] = set()
        getter = getattr(self._store, "pending_review_delta_ids", None)
        if getter is not None:
            try:
                skip_ids = set(await getter(campaign_id))
            except Exception:
                skip_ids = set()

        for tid in sorted(fully_contained, key=lambda t: min_order[t], reverse=True):
            failed: list[str] = []
            try:
                ids = await self._reverse_turn_deltas(
                    campaign_id, tid, failed_out=failed, skip_ids=skip_ids
                )
            except Exception as exc:
                warnings.append(f"failed to reverse turn {tid}: {exc}")
                continue
            if ids:
                reversed_turn_ids.append(tid)
            if failed:
                warnings.append(
                    f"turn {tid}: {len(failed)} delta(s) could not be reversed "
                    f"and remain applied: {failed}"
                )
            await self._bus.emit(
                Event(
                    type=events.TURN_UNDONE,
                    payload={
                        "campaign_id": campaign_id,
                        "turn_id": tid,
                        "reversed_deltas": ids,
                    },
                )
            )

        for tid in sorted(straddling, key=lambda t: min_order[t], reverse=True):
            try:
                ids, review_ids, failed, requeue_failed = await self._reverse_and_requeue_turn(
                    campaign_id, tid, skip_ids=skip_ids
                )
            except Exception as exc:
                warnings.append(f"failed to reverse straddling turn {tid}: {exc}")
                continue
            if ids:
                reversed_turn_ids.append(tid)
            requeued_review_ids.extend(review_ids)
            if failed:
                warnings.append(
                    f"turn {tid}: {len(failed)} delta(s) could not be reversed "
                    f"and remain applied: {failed}"
                )
            if requeue_failed:
                warnings.append(
                    f"turn {tid}: {len(requeue_failed)} delta(s) were reversed but could "
                    f"not be re-queued for review (state removed without a re-approval "
                    f"prompt): {requeue_failed}"
                )
            await self._bus.emit(
                Event(
                    type=events.TURN_UNDONE,
                    payload={
                        "campaign_id": campaign_id,
                        "turn_id": tid,
                        "reversed_deltas": ids,
                    },
                )
            )

        await self._scenes.truncate_scene_from(post_id, source="cascade_delete")

        # Continuity facts/commitments are written outside the reversible delta
        # log (DeltaApplier routes them to the continuity service), so the
        # reversals above don't retract them — do it explicitly for fully
        # removed turns. Straddling turns keep theirs (a post still survives),
        # but those writes aren't post-attributed, so warn when a straddling
        # turn wrote continuity state that may now reference removed prose.
        await self._retract_continuity_for_turns(campaign_id, fully_contained, warnings)
        await self._warn_straddling_continuity(campaign_id, straddling, warnings)

        # Cast-change review prompts live in a separate store, not the delta
        # log; dismiss any queued by fully removed turns so the HUD can't later
        # apply a change whose evidence was deleted. A change the user already
        # *confirmed* has mutated the cast through the presence APIs (no
        # reversible delta), and reversing it soundly is ambiguous (the member
        # may have other evidence), so surface it as a warning instead.
        await self._dismiss_cast_changes_for_turns(scene_id, set(fully_contained), warnings)
        await self._warn_confirmed_cast_changes_for_turns(scene_id, set(fully_contained), warnings)

        # Fully-removed turns may have queued low-confidence deltas for review.
        # Those were skipped (never applied, so not reversed), but their
        # review_queue rows stay pending — approving one later would re-apply
        # state from a turn whose evidence is gone. Reject them outright.
        await self._reject_review_items_for_turns(campaign_id, set(fully_contained), warnings)

        top = await self._recent_turn_ids(campaign_id, 1)
        state.last_turn_id = top[0] if top else None

        return CascadeDeleteResult(
            deleted_post_ids=deleted_post_ids,
            reversed_turn_ids=reversed_turn_ids,
            requeued_review_ids=requeued_review_ids,
            warnings=warnings,
        )

    async def _retract_continuity_for_turns(
        self, campaign_id: CampaignId, turn_ids: list[TurnId], warnings: list[str]
    ) -> None:
        if not turn_ids or self._continuity is None:
            return
        from grimoire.continuity.registry import resolve_continuity

        service = resolve_continuity(self._continuity, campaign_id)
        if service is None or not hasattr(service, "retract_turn"):
            return
        had_fact_update = getattr(service, "turn_had_fact_update", None)
        for tid in turn_ids:
            try:
                await service.retract_turn(tid)
            except Exception as exc:
                warnings.append(f"turn {tid}: continuity retraction failed: {exc}")
            # FACT_UPDATE patches a fact in place with no pre-image, so
            # retract_turn leaves them applied; surface that to the user.
            if had_fact_update is not None:
                try:
                    if had_fact_update(tid):
                        warnings.append(
                            f"turn {tid}: a fact edit (FACT_UPDATE) could not be reverted "
                            f"(no pre-image) and remains applied"
                        )
                except Exception:
                    pass

    async def _warn_straddling_continuity(
        self, campaign_id: CampaignId, turn_ids: list[TurnId], warnings: list[str]
    ) -> None:
        """Warn for straddling turns that wrote continuity state.

        A straddling turn's posts span the cut, so a post survives and we keep
        its continuity writes — but those writes are attributed to the turn, not
        individual posts, so we can't tell which the deleted segment made. Rather
        than retract writes a surviving post may own (or trust writes whose
        evidence was removed), surface a warning so the user can review them.
        """
        if not turn_ids or self._continuity is None:
            return
        from grimoire.continuity.registry import resolve_continuity

        service = resolve_continuity(self._continuity, campaign_id)
        detector = getattr(service, "turn_has_continuity_writes", None)
        if detector is None:
            return
        for tid in turn_ids:
            try:
                if await detector(tid):
                    warnings.append(
                        f"turn {tid} straddles the deletion: its continuity "
                        "facts/commitments/knowledge were not retracted and may "
                        "reference removed posts"
                    )
            except Exception:
                continue

    async def _reject_review_items_for_turns(
        self, campaign_id: CampaignId, turn_ids: set[TurnId], warnings: list[str]
    ) -> None:
        if not turn_ids:
            return
        lister = getattr(self._store, "pending_review_items", None)
        rejecter = getattr(self._store, "reject_review_item", None)
        if lister is None or rejecter is None:
            return
        try:
            items = await lister(campaign_id)
        except Exception:
            return
        for review_id, turn_id in items:
            if turn_id in turn_ids:
                try:
                    await rejecter(review_id, notes="cascade_delete")
                except Exception as exc:
                    warnings.append(f"review item {review_id}: reject failed: {exc}")

    async def _dismiss_cast_changes_for_turns(
        self, scene_id: SceneId, turn_ids: set[TurnId], warnings: list[str]
    ) -> None:
        if not turn_ids:
            return
        try:
            pending = await self._scenes.list_pending_cast_changes(scene_id)
        except Exception:
            return
        for change in pending:
            if getattr(change, "turn_id", None) in turn_ids:
                try:
                    await self._scenes.dismiss_cast_change(scene_id, change.id)
                except Exception as exc:
                    warnings.append(f"cast change {change.id}: dismiss failed: {exc}")

    async def _warn_confirmed_cast_changes_for_turns(
        self, scene_id: SceneId, turn_ids: set[TurnId], warnings: list[str]
    ) -> None:
        if not turn_ids:
            return
        lister = getattr(self._scenes, "list_confirmed_cast_changes", None)
        if lister is None:
            return
        try:
            confirmed = await lister(scene_id)
        except Exception:
            return
        for change in confirmed:
            if getattr(change, "turn_id", None) in turn_ids:
                warnings.append(
                    f"cast change {getattr(change, 'id', '?')} "
                    f"({getattr(change, 'change', '?')} "
                    f"{getattr(change, 'character_ref', '?')}) was confirmed by a removed "
                    f"turn and already changed the scene cast; it was not reverted"
                )

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

        turn_id = new_id("analysis")

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
        player_post_id: PostId | None = None,
    ) -> TurnId:
        async with self._metrics.measure("orchestrator", "turn"):
            return await self._run_turn_inner(
                campaign_id=campaign_id,
                scene_id=scene_id,
                player_input=player_input,
                triggering_pc=triggering_pc,
                player_post_id=player_post_id,
            )

    async def _run_turn_inner(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        player_input: str,
        triggering_pc: CharacterRef | None,
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

        turn_id = new_id("t", length=16)
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
                paused = await self._run_with_idle_timeout(
                    active,
                    self._run_turn_body(
                        active=active,
                        campaign_id=campaign_id,
                        scene_id=scene_id,
                        player_input=player_input,
                        triggering_pc=triggering_pc,
                        turn_id=turn_id,
                    ),
                    self._config.turn_timeout_seconds,
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
        )
        return False

    async def _continue_turn_after_pre_roll(
        self,
        *,
        active: _ActiveTurn,
        resolved_results: list[MechanicsResult],
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
        # Decide the extraction mode for this turn before assembling the
        # prompt so the Context Builder can attach tracker instructions or
        # tool declarations as appropriate.
        extract_mode = await self._delta.select_extract_mode(campaign_id=campaign_id)
        prompt = await self._context.build(
            player_input,
            campaign_id,
            mechanics_results=resolved_results,
            pc_ref=triggering_pc,
            turn_id=turn_id,
            extractor_mode=extract_mode,
        )
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
        await self._resolve_extraction_cast_changes(
            extraction=extraction,
            scene_obj=scene_obj,
            campaign_id=campaign_id,
            scene_id=scene_id,
            turn_id=turn_id,
        )

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
        applied_state = await self._apply_extraction_state(
            campaign_id=campaign_id,
            scene_id=scene_id,
            turn_id=turn_id,
            extraction=extraction,
        )

        # applied_state is committed (the apply stage compensates itself); any
        # failure from here until completion publishes must unwind it — and
        # the response posts appended below — before propagating, or the turn
        # fails half-applied (#584). BaseException so the idle-timeout
        # cancellation also unwinds.
        appended_post_ids: list[str] = []
        try:
            await self._emit_extraction_state_fragment(turn_id, campaign_id, applied_state)

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
                appended_post_ids.append(rp.id)
            await self._emit_fragment(turn_id, campaign_id, scene_appended=True)

            pending_cast = await self._scenes.list_pending_cast_changes(scene_id)
        except BaseException:
            await self._unwind_after_apply(
                campaign_id=campaign_id,
                turn_id=turn_id,
                applied_ids=applied_state.applied_ids,
                appended_post_ids=appended_post_ids,
                queued_review_ids=applied_state.queued_ids,
                inventory_rollback=applied_state.inventory_rollback,
            )
            raise

        # Publishing completion is the commit boundary: turn_complete
        # subscribers (Time Engine, Continuity, Characters) react with
        # independent writes the unwind above can't compensate, so it must
        # never run once this event is out.
        await self._emit_turn_event(
            events.TURN_COMPLETE,
            turn_id,
            campaign_id,
            scene_id,
            time_advances=time_advance_durations,
            pending_cast_changes=[p.model_dump(mode="json") for p in pending_cast],
        )

    async def _resolve_extraction_cast_changes(
        self,
        *,
        extraction: ExtractionResult | None,
        scene_obj: Any,
        campaign_id: CampaignId,
        scene_id: SceneId,
        turn_id: TurnId,
    ) -> None:
        """Resolve extractor cast-change proposals and surface queued ones (#464).

        Shared post-extraction stage: both the single-response pipeline and
        the speaker loop run this between extraction and
        :meth:`_apply_extraction_state`, so the two paths can't drift (#603).
        No-op when extraction failed or the Characters module isn't wired.
        """
        if extraction is None or self._characters is None:
            return
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

    async def _apply_extraction_state(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        turn_id: TurnId,
        extraction: ExtractionResult | None,
    ) -> _ExtractionApplyResult:
        """Apply an extraction's state effects: routing, inventory, transient.

        This is the single post-extraction stage list shared by the
        single-response pipeline and the speaker loop (#603) — add any new
        state-affecting stage here, never to just one caller, so the two
        pipeline copies stay structurally identical until #518 folds them
        into a TurnCoordinator. No-ops when extraction failed.

        Audit fragments for the applied state are NOT emitted here: callers
        emit once per turn via :meth:`_emit_extraction_state_fragment` (the
        speaker loop merges round results first, since the TurnAuditor keeps
        only the last value per fragment key).
        """
        result = _ExtractionApplyResult()
        if extraction is None:
            return result

        applied_ids, queued_ids = await self._delta.apply_routing(
            campaign_id=campaign_id,
            turn_id=turn_id,
            extraction=extraction,
        )
        result.applied_ids = list(applied_ids)
        result.queued_ids = list(queued_ids)

        # apply_routing compensates its own batch mid-flight; once it returns,
        # the batch is committed — a failure in the stages below unwinds it
        # (and their own committed writes) before propagating, so the turn
        # never ends half-applied (#584). BaseException so the idle-timeout
        # cancellation also unwinds.
        try:
            if self._inventory is not None:
                # The inventory service is injected at startup (Protocol-style);
                # the orchestrator hands it raw deltas and never imports the
                # inventory package (no orchestrator -> inventory module edge).
                # It restores its own partially-written holders before raising;
                # on success the result carries the holder pre-images so a
                # *later* stage failure can hand them back to restore_holders.
                inventory_result = await self._inventory.apply_from_deltas(
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    deltas=list(extraction.deltas),
                )
                result.inventory_rollback = list((inventory_result or {}).get("rollback") or [])

            if self._transient_state is not None and getattr(extraction, "transient_updates", None):
                from grimoire.transient_state.routing import route_transient_updates

                ts_summary = await route_transient_updates(
                    campaign_id=campaign_id,
                    proposals=list(extraction.transient_updates),
                    transient_state=self._transient_state,
                    source_post_id=turn_id,
                    continuity=self._continuity,
                )
                result.transient_writes = list(ts_summary.writes)
                result.transient_conflicts = list(ts_summary.conflicts)
        except BaseException:
            await self._unwind_after_apply(
                campaign_id=campaign_id,
                turn_id=turn_id,
                applied_ids=result.applied_ids,
                appended_post_ids=[],
                queued_review_ids=result.queued_ids,
                inventory_rollback=result.inventory_rollback,
            )
            raise

        return result

    async def _emit_extraction_state_fragment(
        self,
        turn_id: TurnId,
        campaign_id: CampaignId,
        result: _ExtractionApplyResult,
    ) -> None:
        """Emit the turn-audit fragment for applied extraction state (#603).

        Called once per turn — the TurnAuditor merges fragments
        last-write-wins per key, so the speaker loop merges all round
        results into one ``_ExtractionApplyResult`` before emitting.
        """
        if result.applied_ids or result.queued_ids:
            await self._emit_fragment(
                turn_id,
                campaign_id,
                applied_deltas=[{"id": did} for did in result.applied_ids],
                queued_for_review=[{"id": qid} for qid in result.queued_ids],
            )
        if result.transient_writes or result.transient_conflicts:
            await self._emit_fragment(
                turn_id,
                campaign_id,
                transient_state_writes=result.transient_writes,
                transient_state_conflicts=result.transient_conflicts,
            )
        if result.warnings:
            await self._emit_fragment(turn_id, campaign_id, warnings=result.warnings)

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
        # Merged across rounds; emitted as one audit fragment after the loop
        # so earlier rounds aren't clobbered by the auditor's last-write-wins
        # fragment merge.
        applied_state = _ExtractionApplyResult()

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
            # §464: resolve before applying (same invariant as the
            # single-response path) so unknown-name candidates are routed and
            # prompts queued this round surface without waiting for the loop's
            # final turn_complete; then run the shared state-application
            # stages (routing, inventory, transient) so a speaker round
            # applies the same state effects as a single-response turn (#603).
            await self._resolve_extraction_cast_changes(
                extraction=extraction,
                scene_obj=scene_obj,
                campaign_id=campaign_id,
                scene_id=scene_id,
                turn_id=turn_id,
            )
            round_state = await self._apply_extraction_state(
                campaign_id=campaign_id,
                scene_id=scene_id,
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
            try:
                await self._scenes.append_post(scene_id, post)
            except BaseException:
                # The round's state committed but its post can't land — unwind
                # the round before propagating so the failed loop never leaves
                # deltas without the prose they came from (#584). Earlier
                # rounds stay: their posts are already part of the scene.
                await self._unwind_after_apply(
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    applied_ids=round_state.applied_ids,
                    appended_post_ids=[],
                    queued_review_ids=round_state.queued_ids,
                    inventory_rollback=round_state.inventory_rollback,
                )
                raise
            # Merge only after the round's post landed, so the end-of-loop
            # audit fragment reflects committed rounds.
            applied_state.merge(round_state)
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

        await self._emit_extraction_state_fragment(turn_id, campaign_id, applied_state)

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

    async def _unwind_after_apply(
        self,
        *,
        campaign_id: CampaignId,
        turn_id: TurnId,
        applied_ids: list[str],
        appended_post_ids: list[str],
        queued_review_ids: list[str] | None = None,
        inventory_rollback: Any | None = None,
    ) -> None:
        """Compensate a turn that failed after its delta batch committed (#584).

        LIFO across stages: response posts appended after the batch are deleted
        first, then committed inventory holders restore to their pre-images,
        then the batch unwinds through the same ``reverse_delta`` walk the
        DeltaApplier uses mid-batch; review items the routing queued are
        rejected so the queue doesn't surface a turn that no longer exists (a
        retry re-queues fresh ones). Each step is best-effort — a failed
        reversal is logged and the rest still unwind — and the outcome lands in
        the turn's audit fragment so a partial unwind is visible, not silent.
        """
        deleted_post_ids: list[str] = []
        for pid in reversed(appended_post_ids):
            try:
                await self._scenes.delete_post(pid, source="rollback")
                deleted_post_ids.append(pid)
            except Exception:
                logger.warning(
                    "rollback of response post %s failed during turn unwind",
                    pid,
                    exc_info=True,
                )
        if inventory_rollback and self._inventory is not None:
            try:
                await self._inventory.restore_holders(
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    rollback=inventory_rollback,
                )
            except Exception:
                logger.warning(
                    "inventory restore failed during turn unwind (turn %s)",
                    turn_id,
                    exc_info=True,
                )
        reversed_ids: list[str] = []
        for did in reversed(applied_ids):
            try:
                await self._store.reverse_delta(did)
                reversed_ids.append(did)
            except Exception:
                logger.warning(
                    "rollback of delta %s failed during turn unwind",
                    did,
                    exc_info=True,
                )
        rejected_review_ids: list[str] = []
        for rid in reversed(queued_review_ids or []):
            try:
                await self._store.reject_review_item(rid, notes="turn unwound after failure")
                rejected_review_ids.append(rid)
            except Exception:
                logger.warning(
                    "rejection of review item %s failed during turn unwind",
                    rid,
                    exc_info=True,
                )
        if reversed_ids or deleted_post_ids or rejected_review_ids:
            await self._emit_fragment(
                turn_id,
                campaign_id,
                compensated_deltas=[{"id": did} for did in reversed_ids],
                compensated_posts=[{"id": pid} for pid in deleted_post_ids],
                compensated_review_items=[{"id": rid} for rid in rejected_review_ids],
            )

    async def _run_with_idle_timeout(
        self,
        active: _ActiveTurn,
        coro: Awaitable[bool],
        idle_seconds: float,
    ) -> bool:
        """Run the turn body, failing only on *idle* — no streamed token for
        ``idle_seconds`` — rather than on total turn duration.

        Before the first token this measures time-to-first-token; after it,
        it resets on every token, so a slow-but-steady local model runs to
        completion and only a genuine stall (or a model stuck before any
        output) trips the timeout. On trip, the body is cancelled and a
        ``TimeoutError`` is raised so the caller's existing timeout handling
        (TURN_TIMED_OUT + rollback) applies unchanged.

        Progress is detected by watching ``active.last_chunk_at`` change and
        timed with ``time.monotonic()``, so it does not depend on the injected
        wall clock.
        """
        body_task = asyncio.ensure_future(coro)
        poll = min(max(idle_seconds / 4, 0.05), 5.0)

        async def _watchdog() -> None:
            last_seen = active.last_chunk_at
            last_progress = time.monotonic()
            while not body_task.done():
                await asyncio.sleep(poll)
                if body_task.done():
                    return
                if active.last_chunk_at != last_seen:
                    last_seen = active.last_chunk_at
                    last_progress = time.monotonic()
                    continue
                if time.monotonic() - last_progress >= idle_seconds:
                    active.timed_out = True
                    body_task.cancel()
                    return

        watchdog = asyncio.ensure_future(_watchdog())
        try:
            return await body_task
        except asyncio.CancelledError:
            if active.timed_out:
                raise TimeoutError from None
            raise
        finally:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog

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

        If the resumed pipeline fails, the turn re-parks on ``pre_roll_pending``
        with its committed state unwound, and this method may be called again
        with the same (or different) resolutions; accepted proposals are then
        re-rolled.
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

        if pending.resolving:
            raise OrchestratorError(f"pre_roll for turn {turn_id!r} is already being resolved")
        # Claim before the first await: pending_pre_roll stays populated through
        # the continuation (for resumability), so without the claim a concurrent
        # resolve call would pass the checks above and run the pipeline twice.
        pending.resolving = True

        try:
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
                    merged = proposal.model_copy(
                        update=_clean_modifications(resolution.modifications)
                    )
                    final_proposals.append(merged)
                else:
                    final_proposals.append(proposal)

            resolved = await self._resolve_proposals(campaign_id, final_proposals)
            # Combine with any inline (non-high-stakes) results from the pause.
            all_results = list(pending.auto_resolved) + resolved
        except BaseException:
            # Nothing ran yet — release the claim so the turn stays resolvable.
            pending.resolving = False
            raise

        # The pending pre-roll is cleared only once the continuation commits
        # (or the turn is cancelled) — clearing it up front made any pipeline
        # failure unrecoverable: pre-roll gone, batch half-applied, lock
        # released (#584). On failure the continuation has already unwound
        # whatever it committed, so the turn re-parks on ``pre_roll_pending``
        # — lock held, player post and proposals intact — and the player can
        # simply re-submit their resolutions.
        try:
            await self._continue_turn_after_pre_roll(
                active=active,
                resolved_results=all_results,
            )
        except TurnCancelledError:
            # Mirror _run_turn's cancel handling: drop the paused turn cleanly.
            # Emit + roll the player post back while the lock is still held so
            # a queued submission can't build context against the doomed post;
            # the finally guarantees the lock is released exactly once.
            state.pending_pre_roll = None
            try:
                await self._emit_turn_event(
                    events.TURN_CANCELLED,
                    turn_id,
                    campaign_id,
                    active.scene_id,
                )
                await self._rollback_player_post(active)
            finally:
                state.active = None
                state.lock.release()
            return SubmitResult(
                accepted=True,
                turn_id=turn_id,
                auto_responding=False,
                reason="turn cancelled",
            )
        except _StreamFailure as exc:
            pending.resolving = False
            active.stage = "pre_roll_pending"
            await self._emit_turn_event(
                events.TURN_FAILED,
                turn_id,
                campaign_id,
                active.scene_id,
                reason="llm_gateway",
                partial_response=exc.partial_text,
                pre_roll_resumable=True,
            )
            raise OrchestratorError(
                f"llm gateway failed for turn {turn_id}: {exc.cause}"
            ) from exc.cause
        except Exception as exc:
            pending.resolving = False
            active.stage = "pre_roll_pending"
            await self._emit_turn_event(
                events.TURN_FAILED,
                turn_id,
                campaign_id,
                active.scene_id,
                reason="orchestrator",
                partial_response="",
                pre_roll_resumable=True,
            )
            if isinstance(exc, OrchestratorError):
                raise
            raise OrchestratorError(f"turn {turn_id} failed: {exc}") from exc
        except BaseException:
            # Task cancellation (client disconnect, shutdown): re-park silently
            # so the turn stays resumable; the continuation already unwound.
            pending.resolving = False
            active.stage = "pre_roll_pending"
            raise

        state.pending_pre_roll = None
        state.last_turn_id = turn_id
        state.active = None
        state.lock.release()
        return SubmitResult(
            accepted=True,
            turn_id=turn_id,
            auto_responding=True,
            reason="pre_roll resolved",
        )

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

    async def _reverse_turn_deltas(
        self,
        campaign_id: CampaignId,
        turn_id: TurnId,
        *,
        failed_out: list[str] | None = None,
        skip_ids: set[str] | None = None,
    ) -> list[str]:
        log = await self._store.get_delta_log(
            campaign_id=campaign_id, turn_id=turn_id, include_reversed=False
        )
        # Reverse in LIFO order to undo most recent first. Deltas that can't be
        # reversed are logged and, when ``failed_out`` is supplied, collected so
        # the caller can surface them rather than report a clean success.
        # ``skip_ids`` holds queued-but-unapplied review deltas, which must not
        # be reversed (reversing a never-applied delta deletes a live row).
        reversed_ids: list[str] = []
        for record in reversed(log):
            if skip_ids is not None and record.id in skip_ids:
                continue
            try:
                await self._store.reverse_delta(record.id)
                reversed_ids.append(record.id)
            except Exception as exc:
                logger.warning("reverse_delta(%s) failed: %s", record.id, exc)
                if failed_out is not None:
                    failed_out.append(record.id)
        return reversed_ids

    async def _reverse_and_requeue_turn(
        self, campaign_id: CampaignId, turn_id: TurnId, *, skip_ids: set[str] | None = None
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """Reverse a turn's deltas, then re-queue each for human review.

        Used for turns that straddle a cascade-delete cut: the deltas can no
        longer be auto-trusted, so they are reversed and re-queued. Approve
        (existing review flow) re-applies; reject leaves them reversed. Returns
        ``(reversed_ids, review_ids, failed_ids, requeue_failed_ids)`` so the
        caller can surface deltas that could not be reversed *or* could not be
        re-queued. ``skip_ids`` holds queued-but-unapplied review deltas, which
        are left as-is (already in the review queue) rather than reversed.
        """
        log = await self._store.get_delta_log(
            campaign_id=campaign_id, turn_id=turn_id, include_reversed=False
        )
        reversed_ids: list[str] = []
        failed_ids: list[str] = []
        # Reverse LIFO (newest first) so dependent rows unwind in the opposite
        # order they were applied.
        reversed_records: list[Any] = []
        for record in reversed(log):
            if skip_ids is not None and record.id in skip_ids:
                continue
            try:
                await self._store.reverse_delta(record.id)
                reversed_ids.append(record.id)
                reversed_records.append(record)
            except Exception as exc:
                logger.warning("reverse_delta(%s) failed: %s", record.id, exc)
                failed_ids.append(record.id)
        # Re-queue in original *apply* order (oldest first): approving the review
        # items must replay the deltas in the order they were first applied (e.g.
        # A→B then B→C lands on C), not the LIFO reversal order (which would land
        # on B). The reversal above is only about safe unwinding.
        reversed_set = {r.id for r in reversed_records}
        review_ids: list[str] = []
        requeue_failed_ids: list[str] = []
        for record in log:
            if record.id not in reversed_set:
                continue
            try:
                review_id = await self._store.queue_for_review(
                    delta=record,
                    source="cascade_delete",
                    campaign_id=campaign_id,
                )
            except Exception as exc:
                # The delta is already reversed; if we can't re-queue it the
                # caller must warn so the user knows state was removed without a
                # re-approval prompt.
                logger.warning("queue_for_review(%s) failed: %s", record.id, exc)
                requeue_failed_ids.append(record.id)
                continue
            if review_id:
                review_ids.append(str(review_id))
                await self._bus.emit(
                    Event(
                        type=events.REVIEW_ITEM_ADDED,
                        payload=self._review_item_added_payload(
                            campaign_id, review_id, record, turn_id
                        ),
                    )
                )
        return reversed_ids, review_ids, failed_ids, requeue_failed_ids

    @staticmethod
    def _review_item_added_payload(
        campaign_id: CampaignId, review_id: Any, record: Any, turn_id: TurnId
    ) -> dict[str, Any]:
        """Build a REVIEW_ITEM_ADDED payload the frontend can consume live.

        ``useCampaignEvent`` pushes a review onto the queue only when the message
        carries ``item.id`` + ``item.summary``; the bare ``review_id``/``turn_id``
        shape left the cascade-requeued items invisible until a reload. Keep the
        legacy keys for other consumers and add the ``item`` envelope.
        """
        delta = getattr(record, "delta", None)
        kind = getattr(delta, "kind", None)
        kind_str = getattr(kind, "value", None) or (str(kind) if kind is not None else "")
        target = getattr(delta, "target_id", None) or getattr(record, "target_id", None)
        summary = " ".join(part for part in (kind_str, str(target) if target else "") if part)
        return {
            "campaign_id": campaign_id,
            "review_id": review_id,
            "turn_id": turn_id,
            "item": {"id": str(review_id), "summary": summary or "review item"},
        }

    # ------------------------------------------------------------------ #
    # Validation + utility
    # ------------------------------------------------------------------ #

    async def _require_campaign(self, campaign_id: CampaignId) -> None:
        if not await self._store.campaign_exists(campaign_id):
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
