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
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from grimoire.context.cache import ContextBuilderCache, make_cache_key
from grimoire.event_bus import Event, EventBus
from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.routing import Decision, route_deltas
from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.errors import (
    AlternateNotFoundError,
    CampaignIdExists,
    CannotDeletePrimaryError,
    LatestPostOnlyError,
    NoTurnsToUndoError,
    OrchestratorError,
    RetconInFlightError,
    TurnCancelledError,
    TurnTimeoutError,
    UnknownCampaignError,
    UnknownPCError,
)
from grimoire.orchestrator.fork_images import fork_image_files
from grimoire.orchestrator.retcon_replay import RetconReplaySession
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import Alternate as SceneAlternate
from grimoire.scenes.types import AuthorKind as SceneAuthorKind
from grimoire.scenes.types import Post as SceneFilePost
from grimoire.scenes.types import Scene as SceneFileScene
from grimoire.scenes.types import SceneInit as SceneFileInit
from grimoire.state_store.fork import bulk_copy, fingerprint, replay_to_turn
from grimoire.types.common import CampaignId, CharacterRef, PostId, SceneId, TurnId
from grimoire.types.extraction import ExtractionResult
from grimoire.types.llm import CompletionRequest
from grimoire.types.mechanics import (
    MechanicsResult,
    ProposalResolution,
    ProposedRoll,
    Roll,
    RollModifier,
)
from grimoire.types.orchestrator import (
    ForkCampaignResult,
    ForkResult,
    RegeneratePostResult,
    RegenerateResult,
    ReplayBatchStateView,
    RetconResult,
    SubmitResult,
    TurnStatus,
    UndoResult,
)
from grimoire.types.scene import AdvanceResult
from grimoire.types.scene import Scene as PydanticScene
from grimoire.types.scene import SceneContext as PydanticSceneContext
from grimoire.types.state import DeltaKind, StateSnapshot

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
        continuity: Any | None = None,
        ws_push: WSPushFn | None = None,
        extractor_config: ExtractorConfig | None = None,
        config: OrchestratorConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
        library: Any | None = None,
        context_cache: ContextBuilderCache | None = None,
    ) -> None:
        self._bus = event_bus
        self._scenes = scene_manager
        self._gateway = llm_gateway
        self._context = context_builder
        self._extractor = extractor
        self._store = state_store
        self._mechanics = mechanics
        self._world = world  # §5: optional, used to dispatch weather-override deltas
        self._library = library
        # §5: optional continuity (registry or single service). When wired,
        # FACT_* / COMMITMENT_* / KNOWLEDGE_REVEAL deltas route to the
        # continuity store with a contradiction check first, rather than
        # being applied through the generic state-store path.
        self._continuity = continuity
        self._ws_push = ws_push
        self._extractor_config = extractor_config or ExtractorConfig()
        self._config = config or OrchestratorConfig()
        self._clock = clock
        self._rng = rng or random.Random()
        self._campaigns: dict[CampaignId, _CampaignTurnState] = {}
        # Lazy-initialised on first access — see :pyattr:`retcon_replay`.
        self._retcon_replay: RetconReplaySession | None = None
        # § Spec context-builder-remaining §11. The cache lives at the
        # orchestrator boundary so invalidation sits next to the regenerate
        # logic. Defaults to a fresh in-memory store when not provided.
        self._context_cache = context_cache or ContextBuilderCache()

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
                type="pc_post_appended",
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

    async def advance(self, campaign_id: CampaignId, scene_id: SceneId) -> AdvanceResult:
        await self._require_campaign(campaign_id)
        adv = await self._scenes.on_advance_requested(scene_id)
        # §10: scene manager emits ADVANCE_REQUESTED on its own (scene) bus
        # which is a different bus type; the orchestrator owns surfacing it
        # on the shared event bus.
        await self._bus.emit(
            Event(
                type="advance_requested",
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
    # Swipes / alternates
    # ------------------------------------------------------------------ #

    async def _find_scene_and_post(self, post_id: PostId) -> tuple[SceneFileScene, SceneFilePost]:
        return await self._scenes._find_post(post_id)

    async def _ensure_latest_model_post(self, scene: SceneFileScene, post: SceneFilePost) -> None:
        """Enforce the latest-post-only rule for alternate mutations."""
        posts = await self._scenes.get_posts(scene.id)
        # Find the last model post (non-PC, non-player).
        last_model: SceneFilePost | None = None
        for p in posts:
            if p.author_kind != SceneAuthorKind.PC and not p.is_player:
                last_model = p
        if last_model is None or last_model.id != post.id:
            raise LatestPostOnlyError(post.id)

    async def regenerate_post(
        self,
        *,
        campaign_id: CampaignId,
        post_id: PostId,
        steering_hint: str | None = None,
        model_override: str | None = None,
    ) -> RegeneratePostResult:
        """Re-sample the model for an existing post, producing a new alternate.

        Per the swipes-alternates design (branch C): finds the player input
        that drove the post, re-runs the canonical generation, atomically
        rewinds the current primary's delta set and applies the new deltas
        under a fresh ``delta_set_id``, then appends a non-primary
        :class:`Alternate` to the post's sidecar.

        The new alternate is **not** auto-promoted to primary; the user
        reviews via the swipes UI and accepts with
        :meth:`switch_primary_alternate`. Latest-model-post-only.
        """
        await self._require_campaign(campaign_id)
        scene, post = await self._find_scene_and_post(post_id)
        await self._ensure_latest_model_post(scene, post)
        return await self._regenerate_post_core(
            scene=scene,
            post=post,
            campaign_id=campaign_id,
            steering_hint=steering_hint,
            model_override=model_override,
        )

    async def _regenerate_post_core(
        self,
        *,
        scene: SceneFileScene,
        post: SceneFilePost,
        campaign_id: CampaignId,
        steering_hint: str | None = None,
        model_override: str | None = None,
        replay_batch_id: str | None = None,
    ) -> RegeneratePostResult:
        """Body of :meth:`regenerate_post`, minus the latest-post check.

        The retcon replay path (``orchestrator/retcon_replay.py``) calls this
        directly to re-sample model posts that are deliberately *not* the
        latest one in their scene. ``replay_batch_id`` is stamped on the new
        alternate so the replay UI can group it with its batch.
        """
        post_id = post.id

        # Walk back from this post to find the triggering player input.
        posts = await self._scenes.get_posts(scene.id)
        player_input = ""
        pc_ref: CharacterRef | None = None
        for prior in reversed([p for p in posts if p.order_in_scene < post.order_in_scene]):
            if prior.is_player:
                player_input = prior.body
                pc_ref = prior.author_pc_ref
                break

        branch_id = scene.branch_id or "main"
        new_alt_id = f"a_{uuid.uuid4().hex[:16]}"
        new_ds_id = f"ds_{uuid.uuid4().hex[:16]}"
        applied = False

        try:
            prompt = await self._context.build(
                player_input,
                campaign_id,
                mechanics_results=[],
                pc_ref=pc_ref,
                turn_id=post.turn_id,
                extra=steering_hint,
            )
            response_text = await self._stream_main_response(
                campaign_id=campaign_id,
                turn_id=post.turn_id,
                prompt=prompt,
            )
            scene_obj = await self._scenes.get_scene(scene.id)
            extraction = await self._do_extract(
                response_text=response_text,
                scene=scene_obj,
                campaign_id=campaign_id,
                turn_id=post.turn_id,
            )
            deltas = list(extraction.deltas) if extraction is not None else []

            # Atomic swap: rewind current primary's set + apply new deltas
            # under the fresh set. Falls back to plain apply when the
            # current primary has no associated delta set (legacy posts).
            current_primary = next(
                (a for a in post.alternates if a.id == post.primary_alternate_id),
                None,
            )
            rewind_ds = (
                current_primary.delta_set_id
                if current_primary and current_primary.delta_set_id
                else None
            )
            if rewind_ds:
                await self._store.swap_delta_set(
                    rewind_set_id=rewind_ds,
                    apply_deltas=deltas,
                    apply_set_id=new_ds_id,
                    campaign_id=campaign_id,
                    branch_id=branch_id,
                    turn_id=post.turn_id,
                    source="orchestrator:regenerate",
                )
            else:
                await self._store.apply_delta_set(
                    deltas=deltas,
                    delta_set_id=new_ds_id,
                    campaign_id=campaign_id,
                    branch_id=branch_id,
                    turn_id=post.turn_id,
                    source="orchestrator:regenerate",
                )
            applied = True

            alt = SceneAlternate(
                id=new_alt_id,
                post_id=post_id,
                text=response_text,
                delta_set_id=new_ds_id,
                author_kind=post.author_kind,
                model=model_override,
                steering_hint=steering_hint,
                created_at=self._clock(),
                is_primary=False,
                replay_batch_id=replay_batch_id,
            )
            await self._scenes.append_alternate(post_id, alt)

            # Track that the new set is currently active for this post.
            # The primary pointer on the post still references the old
            # alternate; switch_primary_alternate will reconcile.
            await self._store.set_current_alternate_delta_set(
                campaign_id=campaign_id,
                branch_id=branch_id,
                post_id=post_id,
                delta_set_id=new_ds_id,
            )

            await self._bus.emit(
                Event(
                    type="alternate_added",
                    payload={
                        "campaign_id": campaign_id,
                        "post_id": post_id,
                        "alternate_id": new_alt_id,
                        "delta_set_id": new_ds_id,
                    },
                )
            )

            # Eviction: if the post now exceeds the per-post cap of
            # non-primary, non-pinned alternates, drop the oldest one. The
            # just-added alternate is the newest by created_at, so it is
            # never the eviction target.
            try:
                await self._evict_overflow_alternate(post_id)
            except Exception:  # pragma: no cover - eviction is best-effort
                logger.warning(
                    "alternate eviction after regenerate_post failed for %s",
                    post_id,
                    exc_info=True,
                )
            return RegeneratePostResult(
                post_id=post_id,
                new_alternate_id=new_alt_id,
                delta_set_id=new_ds_id,
            )
        except Exception:
            if applied:
                # Best-effort restore: rewind the new set, then re-activate
                # the prior primary's set so the world state matches the
                # unchanged primary pointer.
                try:
                    await self._store.rewind_delta_set(
                        new_ds_id, campaign_id=campaign_id, branch_id=branch_id
                    )
                except Exception:
                    logger.warning(
                        "rollback of new delta set %s during regenerate_post failed",
                        new_ds_id,
                        exc_info=True,
                    )
                if rewind_ds:
                    try:
                        await self._store.re_activate_delta_set(
                            delta_set_id=rewind_ds,
                            campaign_id=campaign_id,
                            branch_id=branch_id,
                        )
                    except Exception:
                        logger.warning(
                            "re-activate of prior set %s during regenerate_post rollback failed",
                            rewind_ds,
                            exc_info=True,
                        )
            raise

    async def switch_primary_alternate(
        self,
        *,
        campaign_id: CampaignId,
        post_id: PostId,
        alternate_id: str,
    ) -> dict[str, Any]:
        """Atomically swap which alternate is primary for ``post_id``.

        Rewinds the current primary's delta set, re-activates the target's,
        rewrites the scene .md from primaries, updates the materialized view,
        and emits a ``primary_switched`` event.
        """
        await self._require_campaign(campaign_id)
        scene, post = await self._find_scene_and_post(post_id)
        await self._ensure_latest_model_post(scene, post)
        return await self._switch_primary_alternate_core(
            scene=scene,
            post=post,
            campaign_id=campaign_id,
            alternate_id=alternate_id,
        )

    async def _switch_primary_alternate_core(
        self,
        *,
        scene: SceneFileScene,
        post: SceneFilePost,
        campaign_id: CampaignId,
        alternate_id: str,
    ) -> dict[str, Any]:
        """Body of :meth:`switch_primary_alternate`, minus the latest-post
        check. Used by the retcon replay path, which deliberately switches
        primaries on earlier posts as the user accepts each replayed turn."""
        post_id = post.id
        target = next((a for a in post.alternates if a.id == alternate_id), None)
        if target is None:
            raise AlternateNotFoundError(post_id, alternate_id)

        if post.primary_alternate_id == alternate_id:
            return {"unchanged": True, "post_id": post_id, "alternate_id": alternate_id}

        current = next(
            (a for a in post.alternates if a.id == post.primary_alternate_id),
            None,
        )
        if current is None or not current.delta_set_id or not target.delta_set_id:
            # Legacy alternates without delta_set_id can still have their
            # primary pointer switched + .md rebuilt; just skip the delta swap.
            await self._scenes.set_primary_alternate(post_id, alternate_id)
            await self._scenes.rebuild_md_from_primaries(scene.id)
            return {
                "unchanged": False,
                "post_id": post_id,
                "from": current.id if current else None,
                "to": alternate_id,
                "delta_swap": False,
            }

        branch_id = scene.branch_id or "main"
        await self._store.swap_delta_set(
            rewind_set_id=current.delta_set_id,
            apply_deltas=None,
            apply_set_id=target.delta_set_id,
            campaign_id=campaign_id,
            branch_id=branch_id,
            turn_id=post.turn_id,
            source="orchestrator:switch-primary",
        )
        await self._scenes.set_primary_alternate(post_id, alternate_id)
        await self._scenes.rebuild_md_from_primaries(scene.id)
        await self._store.set_current_alternate_delta_set(
            campaign_id=campaign_id,
            branch_id=branch_id,
            post_id=post_id,
            delta_set_id=target.delta_set_id,
        )
        await self._bus.emit(
            Event(
                type="primary_switched",
                payload={
                    "campaign_id": campaign_id,
                    "post_id": post_id,
                    "from": current.id,
                    "to": alternate_id,
                },
            )
        )
        return {
            "unchanged": False,
            "post_id": post_id,
            "from": current.id,
            "to": alternate_id,
            "delta_swap": True,
        }

    async def pin_alternate(
        self,
        *,
        post_id: PostId,
        alternate_id: str,
        pinned: bool,
    ) -> None:
        scene, post = await self._find_scene_and_post(post_id)
        if not any(a.id == alternate_id for a in post.alternates):
            raise AlternateNotFoundError(post_id, alternate_id)
        await self._scenes.update_alternate(post_id, alternate_id, pinned=pinned)
        await self._bus.emit(
            Event(
                type="alternate_pinned",
                payload={
                    "campaign_id": scene.campaign_id,
                    "post_id": post_id,
                    "alternate_id": alternate_id,
                    "pinned": bool(pinned),
                },
            )
        )

    async def delete_alternate(
        self,
        *,
        post_id: PostId,
        alternate_id: str,
    ) -> None:
        """Remove a non-primary alternate. Rewinds its delta set first."""
        scene, post = await self._find_scene_and_post(post_id)
        if post.primary_alternate_id == alternate_id:
            raise CannotDeletePrimaryError(post_id, alternate_id)
        target = next((a for a in post.alternates if a.id == alternate_id), None)
        if target is None:
            raise AlternateNotFoundError(post_id, alternate_id)
        branch_id = scene.branch_id or "main"
        if target.delta_set_id:
            # Rewind only if not already reversed (idempotent at the row level).
            try:
                await self._store.rewind_delta_set(
                    target.delta_set_id,
                    campaign_id=scene.campaign_id,
                    branch_id=branch_id,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "failed to rewind delta set %s on delete_alternate: %s",
                    target.delta_set_id,
                    exc,
                )
        await self._scenes.remove_alternate(post_id, alternate_id)
        await self._bus.emit(
            Event(
                type="alternate_deleted",
                payload={
                    "campaign_id": scene.campaign_id,
                    "post_id": post_id,
                    "alternate_id": alternate_id,
                },
            )
        )

    async def _evict_overflow_alternate(self, post_id: PostId) -> None:
        """Drop the oldest non-primary, non-pinned alternate if over the cap."""
        cap = self._config.swipes.max_alternates_per_post
        if cap <= 0:
            return
        _scene, post = await self._find_scene_and_post(post_id)
        eligible = [
            a
            for a in post.alternates
            if not a.pinned and a.id != post.primary_alternate_id and a.created_at is not None
        ]
        if len(eligible) <= cap:
            return
        oldest = min(eligible, key=lambda a: a.created_at)  # type: ignore[arg-type, return-value]
        await self.delete_alternate(post_id=post_id, alternate_id=oldest.id)

    async def purge_stale_alternates(
        self,
        campaign_id: CampaignId,
        *,
        older_than_days: int | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        """Vacuum alternates older than the retention threshold.

        Walks every scene in the campaign and deletes any alternate that is
        not the primary, not pinned, and whose ``created_at`` is older than
        the configured threshold. Returns the list of deleted alternate ids
        for observability. Safe to invoke repeatedly; no-op when no scenes
        contain stale alternates.
        """
        threshold_days = (
            older_than_days
            if older_than_days is not None
            else self._config.swipes.auto_purge_older_than_days
        )
        if threshold_days <= 0:
            return []
        await self._require_campaign(campaign_id)
        reference = now or datetime.now(UTC)
        cutoff = reference - timedelta(days=threshold_days)
        deleted: list[str] = []
        scenes = await self._scenes.list_scenes(campaign_id)
        for scene in scenes:
            posts = await self._scenes.get_posts(scene.id)
            for post in posts:
                stale = [
                    a
                    for a in list(post.alternates)
                    if not a.pinned
                    and a.id != post.primary_alternate_id
                    and a.created_at is not None
                    and a.created_at < cutoff
                ]
                for alt in stale:
                    try:
                        await self.delete_alternate(post_id=post.id, alternate_id=alt.id)
                        deleted.append(alt.id)
                    except Exception:  # pragma: no cover - sweep is best-effort
                        logger.warning(
                            "purge_stale_alternates: failed to delete %s on %s",
                            alt.id,
                            post.id,
                            exc_info=True,
                        )
        return deleted

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
                    type="turn_undone",
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

    async def retcon_post(
        self,
        post_id: PostId,
        new_text: str,
        *,
        campaign_id: CampaignId | None = None,
        replay_subsequent: bool = False,
    ) -> RetconResult:
        """Edit a past post and either leave subsequent turns alone or replay them.

        The leave-as-is path (default) is the existing behavior: rewind the
        edited post's deltas, re-extract from ``new_text``, flag downstream
        turns whose deltas touch the same targets. When ``replay_subsequent``
        is ``True`` a :class:`RetconReplaySession` opens (one batch per
        campaign at a time) and the user reviews each subsequent model post
        via the replay control routes; the returned ``RetconResult`` carries
        the ``replay_batch_id`` so the client can poll batch state.
        """
        # When the caller asked for a replay, check the in-flight guard
        # BEFORE doing any mutation — otherwise a 409 would leave the
        # leave-as-is edit (delta rewind + new body + new extraction)
        # already applied while pretending the call was a no-op.
        if replay_subsequent:
            if campaign_id is None:
                scene_file, _ = await self._scenes._find_post(post_id)  # type: ignore[attr-defined]
                campaign_id = scene_file.campaign_id
            if self.retcon_replay.is_active(campaign_id):
                raise RetconInFlightError(campaign_id)

        base = await self._retcon_leave_as_is(post_id, new_text)
        if not replay_subsequent:
            return base
        assert campaign_id is not None  # guarded above
        state = await self.retcon_replay.start(campaign_id=campaign_id, edited_post_id=post_id)
        return base.model_copy(update={"replay_batch_id": state.batch_id})

    @property
    def retcon_replay(self) -> RetconReplaySession:
        if self._retcon_replay is None:
            self._retcon_replay = RetconReplaySession(self, event_bus=self._bus)
        return self._retcon_replay

    async def accept_replay(
        self, campaign_id: CampaignId, *, batch_id: str | None = None
    ) -> ReplayBatchStateView:
        """``batch_id`` is the path parameter the client thought it was
        acting on. When supplied, the session validates the open batch's
        id matches — closing a TOCTOU race where cancel + start between
        the GET (which validated the id) and the POST could silently
        operate on a different batch."""
        await self._require_campaign(campaign_id)
        state = await self.retcon_replay.accept(campaign_id, expected_batch_id=batch_id)
        return state.to_view()

    async def try_again_replay(
        self, campaign_id: CampaignId, *, batch_id: str | None = None
    ) -> ReplayBatchStateView:
        await self._require_campaign(campaign_id)
        state = await self.retcon_replay.try_again(campaign_id, expected_batch_id=batch_id)
        return state.to_view()

    async def cancel_replay(
        self, campaign_id: CampaignId, *, batch_id: str | None = None
    ) -> ReplayBatchStateView:
        await self._require_campaign(campaign_id)
        state = await self.retcon_replay.cancel(campaign_id, expected_batch_id=batch_id)
        return state.to_view()

    async def get_replay_state(
        self, campaign_id: CampaignId, batch_id: str
    ) -> ReplayBatchStateView:
        await self._require_campaign(campaign_id)
        state = self.retcon_replay.get(batch_id)
        if state.campaign_id != campaign_id:
            from grimoire.orchestrator.errors import RetconBatchNotFoundError

            raise RetconBatchNotFoundError(batch_id)
        return state.to_view()

    async def _retcon_leave_as_is(self, post_id: PostId, new_text: str) -> RetconResult:
        """Edit a past post, reverse its deltas, re-run extraction (the
        leave-as-is variant). The replay variant wraps this and then opens
        a :class:`RetconReplaySession`."""
        # Find the post & scene
        scene_file, post = await self._scenes._find_post(post_id)  # type: ignore[attr-defined]
        original = post.body
        # Capture target_ids the retconned turn touched BEFORE reversal so we
        # can walk forward and flag downstream turns that touch any of them.
        downstream_targets: set[str] = set()
        if post.turn_id:
            try:
                pre_log = await self._store.get_delta_log(
                    campaign_id=scene_file.campaign_id,
                    turn_id=post.turn_id,
                    include_reversed=False,
                )
                for record in pre_log:
                    target = getattr(record, "target_id", None)
                    if target:
                        downstream_targets.add(str(target))
            except Exception:
                logger.debug("retcon: could not compute downstream targets", exc_info=True)

        # Reverse deltas sourced from this post's turn (if any).
        reversed_ids: list[str] = []
        if post.turn_id:
            try:
                reversed_ids = await self._reverse_turn_deltas(scene_file.campaign_id, post.turn_id)
            except Exception as exc:
                logger.warning("retcon: could not reverse deltas for %s: %s", post_id, exc)
        # Update the post body on disk.
        await self._scenes.edit_post(post_id, new_text, source="retcon")

        # Re-run extractor on the new text.
        new_delta_ids: list[str] = []
        try:
            snapshot = StateSnapshot(
                campaign_id=scene_file.campaign_id,
                branch_id=scene_file.branch_id,
                scene_id=scene_file.id,
            )
            pyd_scene = _pydantic_scene(scene_file)
            result: ExtractionResult = await self._extractor.extract_from_user_text(
                new_text,
                pyd_scene,
                scene_file.campaign_id,
                snapshot=snapshot,
                player_pc_ref=post.author_pc_ref,
                turn_id=post.turn_id,
            )
            routing = route_deltas(list(result.deltas), config=self._extractor_config)
            for delta in routing.auto_apply:
                did = await self._store.apply_delta(
                    delta=delta,
                    source="retcon",
                    turn_id=post.turn_id,
                    branch_id=scene_file.branch_id,
                    campaign_id=scene_file.campaign_id,
                )
                new_delta_ids.append(did)
            for delta in routing.review:
                await self._store.queue_for_review(
                    delta=delta, source="retcon", campaign_id=scene_file.campaign_id
                )
        except Exception as exc:
            logger.warning("retcon: extractor failure on post %s: %s", post_id, exc)

        flagged: list[TurnId] = []
        if downstream_targets and post.turn_id:
            try:
                full_log = await self._store.get_delta_log(
                    campaign_id=scene_file.campaign_id,
                    include_reversed=True,
                )
                seen: set[TurnId] = set()
                # Walk in application order; flag the first occurrence of any
                # turn whose deltas touch a target the retconned turn produced.
                # Skip the retconned turn itself and the just-applied retcon
                # deltas (which carry the same turn_id).
                for record in full_log:
                    tid = getattr(record, "turn_id", None)
                    if not tid or tid == post.turn_id or tid in seen:
                        continue
                    target = getattr(record, "target_id", None)
                    if target and str(target) in downstream_targets:
                        flagged.append(tid)
                        seen.add(tid)
            except Exception:
                logger.debug("retcon: downstream flagging walk failed", exc_info=True)

        return RetconResult(
            post_id=post_id,
            original_text=original,
            new_text=new_text,
            reversed_delta_ids=reversed_ids,
            new_delta_ids=new_delta_ids,
            downstream_flagged_turns=flagged,
        )

    async def fork(
        self,
        campaign_id: CampaignId,
        from_turn_id: TurnId,
        label: str,
    ) -> ForkResult:
        await self._require_campaign(campaign_id)
        parent_branch = f"{campaign_id}:main"
        new_branch_id = await self._store.fork_branch(
            campaign_id=campaign_id,
            parent_branch_id=parent_branch,
            new_label=label,
            at_turn_id=from_turn_id,
        )
        # Copy-on-write scene files into the new branch directory. If the
        # branch already has a scenes dir, that's fine.
        with contextlib.suppress(FileExistsError):
            await self._scenes.fork_scenes_for_branch(
                campaign_id, new_branch_id, from_branch_id="main"
            )
        return ForkResult(
            new_branch_id=new_branch_id,
            from_turn_id=from_turn_id,
            label=label,
            created_at=self._clock(),
        )

    async def fork_campaign(
        self,
        *,
        campaign_id: CampaignId,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: str | None = None,
        description: str | None = None,
        make_active: bool = False,
    ) -> ForkCampaignResult:
        """Branch a whole campaign into a new campaign id.

        With ``fork_at_post_id=None`` the entire current state of the
        source is duplicated. With a post id, state is materialized via
        copy-and-truncate at that post's ``created_at``; a fingerprint
        comparison guards correctness and the ``degraded`` flag surfaces
        any mismatch.

        Forks requested while a turn is streaming are queued in
        ``pending_forks`` and processed once the active turn completes.
        """
        await self._require_campaign(campaign_id)

        if await self._campaign_exists(new_campaign_id):
            raise CampaignIdExists(new_campaign_id)

        if self._is_streaming(campaign_id):
            return await self._enqueue_fork(
                campaign_id=campaign_id,
                new_campaign_id=new_campaign_id,
                new_name=new_name,
                fork_at_post_id=fork_at_post_id,
                description=description,
                make_active=make_active,
            )

        return await self._execute_fork(
            campaign_id=campaign_id,
            new_campaign_id=new_campaign_id,
            new_name=new_name,
            fork_at_post_id=fork_at_post_id,
            description=description,
            make_active=make_active,
        )

    async def _execute_fork(
        self,
        *,
        campaign_id: CampaignId,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: str | None,
        description: str | None,
        make_active: bool,
    ) -> ForkCampaignResult:
        db = self._store.db
        data_root = self._store.data_root
        src_dir = data_root / "campaigns" / campaign_id
        new_dir = data_root / "campaigns" / new_campaign_id

        cutoff_iso: str | None = None
        cutoff_turn_id: str | None = None
        deltas_replayed = 0
        fingerprint_match = True
        degraded = False

        if fork_at_post_id is not None:
            row = await db.fetchone(
                "SELECT created_at, turn_id FROM posts WHERE id = ? AND campaign_id = ?",
                (fork_at_post_id, campaign_id),
            )
            if row is None:
                raise OrchestratorError(
                    f"fork_at_post_id {fork_at_post_id!r} not found in campaign {campaign_id!r}"
                )
            cutoff_iso = row["created_at"]
            cutoff_turn_id = row["turn_id"]

        await self._bus.emit(
            Event(
                type="campaign_fork_started",
                payload={
                    "source": campaign_id,
                    "new": new_campaign_id,
                    "at_post": fork_at_post_id,
                },
            )
        )

        # 1. Create the new campaign row up front (cloned from the source
        #    with id, name, and provenance fields rewritten). bulk_copy
        #    skips the ``campaigns`` table itself.
        try:
            await self._clone_campaign_row(
                source_id=campaign_id,
                new_id=new_campaign_id,
                new_name=new_name,
                description=description,
                fork_at_post_id=fork_at_post_id,
                cutoff_turn_id=cutoff_turn_id,
            )

            # 2. Bulk-copy / replay state into the new campaign id.
            if cutoff_iso is None:
                await bulk_copy(db, original=campaign_id, new=new_campaign_id, cutoff_iso=None)
            else:
                fp_origin = await fingerprint(db, campaign_id)
                deltas_replayed = await replay_to_turn(
                    db,
                    original=campaign_id,
                    new=new_campaign_id,
                    cutoff_iso=cutoff_iso,
                )
                fp_new = await fingerprint(db, new_campaign_id)
                if fp_origin != fp_new:
                    fingerprint_match = False
                    degraded = True
        except Exception as exc:
            await self._wipe_failed_fork(new_campaign_id, new_dir)
            await self._bus.emit(
                Event(
                    type="campaign_fork_failed",
                    payload={
                        "source": campaign_id,
                        "new": new_campaign_id,
                        "error": str(exc),
                    },
                )
            )
            raise

        # 3. Copy narrative files (scene markdown + sidecars + sheets etc.).
        try:
            self._copy_campaign_files(src_dir, new_dir)
        except Exception as exc:
            logger.warning("fork file copy failed: %s", exc, exc_info=True)

        # 4. Images.
        new_dir.mkdir(parents=True, exist_ok=True)
        img_result = await fork_image_files(src_dir, new_dir)
        await db.execute(
            "UPDATE campaigns SET forked_image_handling = ? WHERE id = ?",
            (img_result.handling, new_campaign_id),
        )

        if make_active:
            await db.execute(
                "UPDATE campaigns SET last_played_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), new_campaign_id),
            )

        await self._bus.emit(
            Event(
                type="campaign_forked",
                payload={
                    "source": campaign_id,
                    "new": new_campaign_id,
                    "at_post": fork_at_post_id,
                    "image_handling": img_result.handling,
                    "deltas_replayed": deltas_replayed,
                    "degraded": degraded,
                },
            )
        )

        return ForkCampaignResult(
            new_campaign_id=new_campaign_id,
            new_name=new_name,
            forked_from_campaign_id=campaign_id,
            forked_at_post_id=fork_at_post_id,
            image_handling=img_result.handling,
            files_copied=img_result.files_copied,
            deltas_replayed=deltas_replayed,
            fingerprint_match=fingerprint_match,
            degraded=degraded,
            queued=False,
            created_at=self._clock(),
        )

    async def _clone_campaign_row(
        self,
        *,
        source_id: str,
        new_id: str,
        new_name: str,
        description: str | None,
        fork_at_post_id: str | None,
        cutoff_turn_id: str | None,
    ) -> None:
        db = self._store.db
        src = await db.fetchone("SELECT * FROM campaigns WHERE id = ?", (source_id,))
        if src is None:
            raise UnknownCampaignError(source_id)
        # Read column names so we copy whatever columns exist in this DB.
        async with db.acquire() as conn:
            cur = await conn.execute("PRAGMA table_info(campaigns)")
            cols = [r["name"] for r in await cur.fetchall()]
            await cur.close()
        overrides = {
            "id": new_id,
            "name": new_name,
            "description": description if description is not None else src["description"],
            "created_at": datetime.now(UTC).isoformat(),
            "last_played_at": None,
            "forked_from_campaign_id": source_id,
            "forked_at_post_id": fork_at_post_id,
            "forked_at_turn_id": cutoff_turn_id,
            "forked_image_handling": None,
        }
        values = []
        for col in cols:
            if col in overrides:
                values.append(overrides[col])
            else:
                values.append(src[col])
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        await db.execute(
            f"INSERT INTO campaigns ({col_list}) VALUES ({placeholders})",
            tuple(values),
        )

    async def _campaign_exists(self, campaign_id: str) -> bool:
        row = await self._store.db.fetchone("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,))
        return row is not None

    def _is_streaming(self, campaign_id: str) -> bool:
        state = self._campaigns.get(campaign_id)
        return state is not None and state.active is not None

    async def _enqueue_fork(
        self,
        *,
        campaign_id: str,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: str | None,
        description: str | None,
        make_active: bool,
    ) -> ForkCampaignResult:
        pending_id = f"pf_{uuid.uuid4().hex[:16]}"
        await self._store.db.execute(
            """
            INSERT INTO pending_forks (
                id, source_campaign_id, new_campaign_id, new_name,
                fork_at_post_id, description, make_active, enqueued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pending_id,
                campaign_id,
                new_campaign_id,
                new_name,
                fork_at_post_id,
                description,
                1 if make_active else 0,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self._bus.emit(
            Event(
                type="campaign_fork_queued",
                payload={
                    "source": campaign_id,
                    "new": new_campaign_id,
                    "pending_id": pending_id,
                },
            )
        )
        return ForkCampaignResult(
            new_campaign_id=new_campaign_id,
            new_name=new_name,
            forked_from_campaign_id=campaign_id,
            forked_at_post_id=fork_at_post_id,
            image_handling="pending",
            queued=True,
            created_at=self._clock(),
        )

    async def list_pending_forks(self, campaign_id: str) -> list[dict]:
        rows = await self._store.db.fetchall(
            """
            SELECT id, new_campaign_id, new_name, fork_at_post_id,
                   description, make_active, enqueued_at, started_at,
                   completed_at, error
              FROM pending_forks
             WHERE source_campaign_id = ?
               AND completed_at IS NULL
             ORDER BY enqueued_at
            """,
            (campaign_id,),
        )
        return [dict(r) for r in rows]

    async def process_pending_forks(self, campaign_id: str) -> list[ForkCampaignResult]:
        """Drain queued forks for ``campaign_id``. Caller must ensure the
        source campaign is no longer streaming."""
        if self._is_streaming(campaign_id):
            return []
        results: list[ForkCampaignResult] = []
        while True:
            row = await self._store.db.fetchone(
                """
                SELECT id, new_campaign_id, new_name, fork_at_post_id,
                       description, make_active
                  FROM pending_forks
                 WHERE source_campaign_id = ?
                   AND completed_at IS NULL
                 ORDER BY enqueued_at
                 LIMIT 1
                """,
                (campaign_id,),
            )
            if row is None:
                break
            pending_id = row["id"]
            await self._store.db.execute(
                "UPDATE pending_forks SET started_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), pending_id),
            )
            try:
                result = await self._execute_fork(
                    campaign_id=campaign_id,
                    new_campaign_id=row["new_campaign_id"],
                    new_name=row["new_name"],
                    fork_at_post_id=row["fork_at_post_id"],
                    description=row["description"],
                    make_active=bool(row["make_active"]),
                )
                await self._store.db.execute(
                    "UPDATE pending_forks SET completed_at = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(), pending_id),
                )
                results.append(result)
            except Exception as exc:
                await self._store.db.execute(
                    "UPDATE pending_forks SET completed_at = ?, error = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(), str(exc), pending_id),
                )
                logger.warning("pending fork %s failed: %s", pending_id, exc)
        return results

    async def get_lineage(self, campaign_id: str) -> dict:
        """Return ancestors + descendants tree rooted at ``campaign_id``."""
        ancestors = await self.get_lineage_ancestors(campaign_id)
        rows = await self._store.db.fetchall(
            """
            WITH RECURSIVE descendants(id, depth) AS (
                SELECT id, 0 FROM campaigns WHERE id = ?
                UNION ALL
                SELECT c.id, descendants.depth + 1
                  FROM campaigns c
                  JOIN descendants ON c.forked_from_campaign_id = descendants.id
            )
            SELECT c.id, c.name, c.forked_from_campaign_id,
                   c.forked_at_post_id, c.forked_at_turn_id, c.created_at,
                   descendants.depth AS depth
              FROM descendants
              JOIN campaigns c ON c.id = descendants.id
             ORDER BY depth, c.id
            """,
            (campaign_id,),
        )
        return {
            "root": campaign_id,
            "ancestors": ancestors,
            "descendants": [dict(r) for r in rows],
        }

    async def get_lineage_ancestors(self, campaign_id: str) -> list[dict]:
        """Walk parents from ``campaign_id`` up to the root."""
        chain: list[dict] = []
        current: str | None = campaign_id
        seen: set[str] = set()
        while current is not None and current not in seen:
            seen.add(current)
            row = await self._store.db.fetchone(
                """
                SELECT id, name, forked_from_campaign_id,
                       forked_at_post_id, forked_at_turn_id, created_at
                  FROM campaigns
                 WHERE id = ?
                """,
                (current,),
            )
            if row is None:
                break
            chain.append(dict(row))
            current = row["forked_from_campaign_id"]
        return chain

    def _copy_campaign_files(self, src_dir, new_dir) -> None:
        """Mirror narrative files (scenes/sheets/overrides/emergent) from
        ``src_dir`` to ``new_dir``. Images are handled separately via
        :func:`fork_image_files`."""
        import shutil

        if not src_dir.exists():
            return
        new_dir.mkdir(parents=True, exist_ok=True)
        for child in src_dir.iterdir():
            if child.name == "images":
                continue
            target = new_dir / child.name
            if child.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

    async def _wipe_failed_fork(self, new_campaign_id: str, new_dir) -> None:
        import shutil

        db = self._store.db
        # Best-effort cleanup of any partial rows the bulk_copy / replay
        # transaction may have committed before failing.
        from grimoire.state_store.fork import CAMPAIGN_SCOPED_TABLES

        for spec in CAMPAIGN_SCOPED_TABLES:
            try:
                await db.execute(
                    f"DELETE FROM {spec['table']} WHERE campaign_id = ?",
                    (new_campaign_id,),
                )
            except Exception:
                continue
        with contextlib.suppress(Exception):
            await db.execute("DELETE FROM campaigns WHERE id = ?", (new_campaign_id,))
        if new_dir.exists():
            shutil.rmtree(new_dir, ignore_errors=True)

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
                    "turn_timed_out",
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
                    "turn_cancelled",
                    turn_id,
                    campaign_id,
                    active.scene_id,
                )
                await self._rollback_player_post(active)
            except _StreamFailure as exc:
                await self._emit_turn_event(
                    "turn_failed",
                    turn_id,
                    campaign_id,
                    active.scene_id,
                    reason="llm_gateway",
                    partial_response=exc.partial_text,
                )
                if self._config.errors.surface_partial_response_on_llm_error and exc.partial_text:
                    try:
                        partial_post = self._new_post(
                            author_kind=SceneAuthorKind.NARRATOR,
                            body=exc.partial_text,
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
                    "turn_failed",
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
        # Resolve the branch id up-front so turn_started carries it; the
        # scene break path may swap scene_id but the branch is stable.
        initial_scene = await self._scenes.get_scene(scene_id)
        await self._emit_turn_event(
            "turn_started",
            turn_id,
            campaign_id,
            scene_id,
            branch_id=initial_scene.branch_id,
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
                "pre_roll_pending",
                turn_id,
                campaign_id,
                scene_id,
                proposals=[p.model_dump(mode="json") for p in pre_roll.pending],
            )
            return True

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
        scene_obj_for_cache = await self._scenes.get_scene(scene_id)
        branch_id_for_cache = getattr(scene_obj_for_cache, "branch_id", None)
        composition_hash = await self._composition_hash(campaign_id)
        cache_key = make_cache_key(
            campaign_id=campaign_id,
            player_input=player_input,
            composition_hash=composition_hash,
            scene_id=scene_id,
            branch_id=branch_id_for_cache,
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
            )
            self._context_cache.put(cache_key, prompt)
        # ``context_summary`` / ``composition_snapshot`` are deliberately
        # omitted: ``AssembledPrompt`` exposes them as plain primitives
        # (str / dict) which don't satisfy ``ContextSummary`` /
        # ``CompositionSnapshot``. They'll be filled by ContextBuilder
        # enrichment in a follow-up pass.
        await self._emit_turn_event(
            "context_built",
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
            "model_response_received",
            turn_id,
            campaign_id,
            scene_id,
            length=len(response_text),
            response_text=response_text,
        )

        active.stage = "extracting"
        scene_obj = await self._scenes.get_scene(scene_id)
        extract_started = self._clock()
        extraction = await self._do_extract(
            response_text=response_text,
            scene=scene_obj,
            campaign_id=campaign_id,
            turn_id=turn_id,
        )
        extract_duration_ms = int((self._clock() - extract_started).total_seconds() * 1000)
        await self._emit_turn_event(
            "deltas_extracted",
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
            applied_ids, queued_ids = await self._apply_routing(
                campaign_id=campaign_id,
                branch_id=scene_obj.branch_id,
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

        response_post = self._new_post(
            author_kind=SceneAuthorKind.NARRATOR,
            body=response_text,
            is_player=False,
            turn_id=turn_id,
        )
        await self._scenes.append_post(scene_id, response_post)
        await self._emit_fragment(turn_id, campaign_id, scene_appended=True)

        await self._emit_turn_event(
            "turn_complete",
            turn_id,
            campaign_id,
            scene_id,
            branch_id=scene_obj.branch_id,
            time_advances=time_advance_durations,
        )

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
        if not player_input or triggering_pc is None:
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
                    type="scene_break_suggested",
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
        request = CompletionRequest(
            model="",  # routing resolves the actual model
            messages=list(prompt.messages),
            max_tokens=getattr(params, "max_tokens", 4096),
            temperature=getattr(params, "temperature", 1.0),
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

    async def _do_extract(
        self,
        *,
        response_text: str,
        scene: SceneFileScene,
        campaign_id: CampaignId,
        turn_id: TurnId,
    ) -> ExtractionResult | None:
        snapshot = StateSnapshot(
            campaign_id=campaign_id,
            branch_id=scene.branch_id,
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

    async def _apply_routing(
        self,
        *,
        campaign_id: CampaignId,
        branch_id: str,
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
                # §5 Domain-specific dispatch: weather override deltas go
                # through WorldService.override_weather so the row gets
                # tagged source="override" (which the read path looks for).
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
                # §5 (continuity remaining-design): route continuity-shaped
                # deltas to the per-campaign Continuity service. Falls
                # through to apply_delta when no continuity is wired so
                # tests that don't compose the registry still get rows in
                # the state-store delta log.
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
                        branch_id=branch_id,
                        turn_id=turn_id,
                    )
                    if handled:
                        continue
                did = await self._store.apply_delta(
                    delta=delta,
                    source=delta.source or "extractor",
                    turn_id=turn_id,
                    branch_id=branch_id,
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
                    type="deltas_applied",
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
                        type="review_item_added",
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

    async def _apply_continuity_delta(
        self,
        *,
        delta: Any,
        campaign_id: CampaignId,
        branch_id: str,
        turn_id: TurnId,
    ) -> bool:
        """Dispatch a continuity-shaped delta to the Continuity service.

        Returns ``True`` when the delta was handled (so the caller skips
        the generic ``state_store.apply_delta`` fallthrough), ``False``
        if the routing decided it couldn't translate the payload — in
        which case the caller falls back to the state-store path so
        nothing silently disappears.

        FACT_ADD runs a contradiction check first: when the check
        returns a non-empty conflict list the fact lands in the State
        Store review queue instead of the ledger so the user can pick a
        resolution via ``POST /campaigns/{id}/contradictions/{id}``.
        """
        from grimoire.continuity.registry import resolve_continuity
        from grimoire.continuity.service import ContinuityService

        service = resolve_continuity(self._continuity, campaign_id, branch_id=branch_id)
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
                # §5: contradiction check before write. A report with
                # non-empty conflicts blocks the write and queues the
                # delta for the State Store review queue.
                report = await service.check_contradictions(fact, turn_id=turn_id)
                if report.conflicts:
                    review_id = await self._store.queue_for_review(
                        delta=delta,
                        source=delta.source or "extractor",
                        campaign_id=campaign_id,
                    )
                    await self._bus.emit(
                        Event(
                            type="review_item_added",
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
            # Fall back to the state-store path so the delta is still
            # logged somewhere visible.
            return False

        # Unknown / unsupported continuity kind — fall back.
        del ContinuityService  # imported for type-check clarity only
        return False

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
                type="turn_audit_fragment",
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


# --------------------------------------------------------------------------- #
# Conversion helpers (scenes dataclass ↔ pydantic Scene)
# --------------------------------------------------------------------------- #


def _pydantic_scene(scene: SceneFileScene) -> PydanticScene:
    """Adapt a scene-manager dataclass into the pydantic Scene used by
    Extractor/Mechanics.

    The pydantic model expects an ``InGameTime`` for ``in_game_start`` /
    ``in_game_end``; the dataclass stores a ``datetime``. We drop those
    fields here — the Extractor reads them but doesn't fail when absent.
    """
    return PydanticScene(
        id=scene.id,
        campaign_id=scene.campaign_id,
        branch_id=scene.branch_id,
        ordinal=scene.ordinal,
        slug=scene.slug,
        file_path="",
        title=scene.title or "",
        location_ref=scene.location_ref,
        in_game_start=None,
        in_game_end=None,
        greeting_id=scene.greeting_id,
        pov_character_ref=scene.pov_character_ref,
        present_character_refs=list(scene.present_character_refs),
        present_pc_refs=list(scene.present_pc_refs),
        mood=scene.mood or "",
        post_count=scene.post_count,
        threads_introduced=[],
        threads_paid_off=[],
        tags=list(scene.tags),
        closed=scene.closed,
        closed_at_turn=scene.closed_at_turn,
        last_advance_at_post=scene.last_advance_at_post or None,
        running_summary=scene.running_summary or "",
        summary=scene.final_summary or "",
        key_beats=list(scene.key_beats),
        emotional_arc="",
    )


def _pydantic_post(post: SceneFilePost) -> Any:
    from grimoire.types.scene import AuthorKind as PydAuthorKind
    from grimoire.types.scene import Post as PydPost

    return PydPost(
        id=post.id,
        scene_id=post.scene_id,
        order_in_scene=post.order_in_scene,
        author_kind=PydAuthorKind(post.author_kind.value),
        body=post.body,
        is_player=post.is_player,
        created_at=post.created_at,
        turn_id=post.turn_id,
        author_pc_ref=post.author_pc_ref,
        author_npc_ref=post.author_npc_ref,
    )


def _build_continuity_fact(
    *,
    payload: dict,
    confidence: float,
    source: str,
    turn_id: TurnId,
) -> Any:
    """Build a dataclass :class:`Fact` from an extractor FACT_ADD payload.

    The extractor emits a dict-shaped delta; the Continuity service
    expects a :mod:`grimoire.continuity.types` dataclass. The
    conversion lives here rather than in the extractor so the extractor
    stays JSON-clean and tests of the extractor don't drag in the
    continuity types.
    """
    from grimoire.continuity.types import Fact, FactSource, FactSubject, InGameTime

    about_data = payload.get("about") or {}
    if isinstance(about_data, FactSubject):
        about = about_data
    else:
        about = FactSubject(
            character_ids=list(about_data.get("character_ids") or []),
            location_ids=list(about_data.get("location_ids") or []),
            faction_ids=list(about_data.get("faction_ids") or []),
            item_ids=list(about_data.get("item_ids") or []),
            scope=str(about_data.get("scope") or "public"),
        )
    src_raw = payload.get("source") or source
    try:
        fact_source = FactSource(str(src_raw))
    except ValueError:
        fact_source = FactSource.NARRATOR
    when_data = payload.get("in_game_when") or {}
    when = InGameTime(
        day_count=int(when_data.get("day_count", 0)),
        label=str(when_data.get("label", "")),
    )
    return Fact(
        id="",
        text=str(payload.get("text", "")),
        established_in_post=str(payload.get("established_in_post") or turn_id),
        established_at_in_game=when,
        confidence=float(confidence),
        source=fact_source,
        speaker_id=payload.get("speaker_id"),
        about=about,
        keywords=list(payload.get("keywords") or []),
    )


def _build_continuity_commitment(
    *,
    payload: dict,
    turn_id: TurnId,
) -> Any | None:
    """Build a :class:`Commitment` from an extractor COMMITMENT_ADD payload.

    Returns ``None`` when the payload is missing the required ``text``
    field; the caller falls back to the generic state-store path so the
    delta stays in the log even if the continuity ledger can't accept it.
    """
    from grimoire.continuity.types import (
        Commitment,
        CommitmentKind,
        CommitmentStatus,
        InGameTime,
    )

    text = str(payload.get("text") or "").strip()
    if not text:
        return None
    kind_raw = str(payload.get("kind") or "promise").lower()
    try:
        kind = CommitmentKind(kind_raw)
    except ValueError:
        kind = CommitmentKind.PROMISE
    when_data = payload.get("in_game_created_at") or {}
    created_at = InGameTime(
        day_count=int(when_data.get("day_count", 0)),
        label=str(when_data.get("label", "")),
    )
    due_data = payload.get("due") or payload.get("due_by")
    due_by: InGameTime | None = None
    if isinstance(due_data, dict):
        due_by = InGameTime(
            day_count=int(due_data.get("day_count", 0)),
            label=str(due_data.get("label", "")),
        )
    return Commitment(
        id="",
        kind=kind,
        text=text,
        created_in_post=str(payload.get("created_in_post") or turn_id),
        in_game_created_at=created_at,
        weight=int(payload.get("weight") or 1),
        from_id=payload.get("from") or payload.get("from_id"),
        to_id=payload.get("to") or payload.get("to_id"),
        due_by=due_by,
        status=CommitmentStatus.OPEN,
    )


@dataclass
class _PreRollOutcome:
    """Result of partitioning + resolving pre-roll proposals."""

    results: list[MechanicsResult]
    pending: list[ProposedRoll]


def _proposed_to_roll(proposal: ProposedRoll) -> Roll:
    """Materialise a ``ProposedRoll`` into a concrete ``Roll`` ready for resolve.

    The proposal carries label/kind/pool/difficulty/modifiers; the Roll
    needs an id and a seed. The id is derived from the label so retries
    of the same proposal stay deterministic; the seed is zero by default
    and the mechanics service mixes it with the branch seed.
    """
    return Roll(
        id=f"proposal:{proposal.label}",
        kind=proposal.kind,
        pool=proposal.pool,
        seed=0,
        actor_ref=proposal.actor_ref,
        target_ref=proposal.target_ref,
        difficulty=proposal.difficulty,
        modifiers=list(proposal.modifiers),
        metadata=dict(proposal.metadata),
    )


def _clean_modifications(modifications: dict) -> dict:
    """Filter caller-supplied overrides to fields ``ProposedRoll`` actually accepts.

    Modifiers are re-validated through ``RollModifier`` so a malformed
    entry surfaces as a ``ValueError`` before resolution.
    """
    allowed = {
        "kind",
        "pool",
        "difficulty",
        "actor_ref",
        "target_ref",
        "rationale",
        "high_stakes",
        "modifiers",
        "metadata",
    }
    out: dict = {}
    for key, value in modifications.items():
        if key not in allowed:
            continue
        if key == "modifiers" and isinstance(value, list):
            out[key] = [
                v if isinstance(v, RollModifier) else RollModifier.model_validate(v) for v in value
            ]
        else:
            out[key] = value
    return out


__all__ = ["OrchestratorService", "WSPushFn"]
