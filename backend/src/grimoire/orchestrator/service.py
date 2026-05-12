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
from typing import Any

from grimoire.event_bus import Event, EventBus
from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.routing import Decision, route_deltas
from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.errors import (
    NoTurnsToUndoError,
    OrchestratorError,
    UnknownCampaignError,
    UnknownPCError,
)
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import AuthorKind as SceneAuthorKind
from grimoire.scenes.types import Post as SceneFilePost
from grimoire.scenes.types import Scene as SceneFileScene
from grimoire.scenes.types import SceneInit as SceneFileInit
from grimoire.types.common import CampaignId, CharacterRef, PostId, SceneId, TurnId
from grimoire.types.extraction import ExtractionResult
from grimoire.types.llm import CompletionRequest
from grimoire.types.mechanics import MechanicsResult, ProposedRoll
from grimoire.types.orchestrator import (
    ForkResult,
    RegenerateResult,
    RetconResult,
    SubmitResult,
    TurnStatus,
    UndoResult,
)
from grimoire.types.scene import AdvanceResult
from grimoire.types.scene import Scene as PydanticScene
from grimoire.types.scene import SceneContext as PydanticSceneContext
from grimoire.types.state import StateSnapshot

logger = logging.getLogger(__name__)

WSPushFn = Callable[[str, dict], Awaitable[None]]


@dataclass
class _ActiveTurn:
    turn_id: TurnId
    campaign_id: CampaignId
    scene_id: SceneId
    started_at: datetime
    stage: str = "starting"


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
        ws_push: WSPushFn | None = None,
        extractor_config: ExtractorConfig | None = None,
        config: OrchestratorConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
    ) -> None:
        self._bus = event_bus
        self._scenes = scene_manager
        self._gateway = llm_gateway
        self._context = context_builder
        self._extractor = extractor
        self._store = state_store
        self._mechanics = mechanics
        self._ws_push = ws_push
        self._extractor_config = extractor_config or ExtractorConfig()
        self._config = config or OrchestratorConfig()
        self._clock = clock
        self._rng = rng or random.Random()
        self._campaigns: dict[CampaignId, _CampaignTurnState] = {}

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
        )
        return RegenerateResult(turn_id=turn_id, accepted=True, reason="regenerated")

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
        return UndoResult(
            turns_undone=undone, reversed_delta_ids=reversed_ids, warnings=warnings
        )

    async def retcon_post(self, post_id: PostId, new_text: str) -> RetconResult:
        """Replace a past post, reverse its deltas, re-run extraction."""
        # Find the post & scene
        scene_file, post = await self._scenes._find_post(post_id)  # type: ignore[attr-defined]
        original = post.body
        # Reverse deltas sourced from this post's turn (if any).
        reversed_ids: list[str] = []
        if post.turn_id:
            try:
                reversed_ids = await self._reverse_turn_deltas(
                    scene_file.campaign_id, post.turn_id
                )
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

        return RetconResult(
            post_id=post_id,
            original_text=original,
            new_text=new_text,
            reversed_delta_ids=reversed_ids,
            new_delta_ids=new_delta_ids,
            downstream_flagged_turns=[],
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
    ) -> TurnId:
        state = self._state_for(campaign_id)
        state.queued += 1
        try:
            await state.lock.acquire()
        except Exception:
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
        )
        state.active = active

        try:
            await self._emit_turn_event("turn_started", turn_id, campaign_id, scene_id)
            scene_id = await self._maybe_break_scene(
                campaign_id=campaign_id,
                scene_id=scene_id,
                player_input=player_input,
                triggering_pc=triggering_pc,
                turn_id=turn_id,
            )

            active.stage = "mechanics_pre_roll"
            mechanics_results = await self._do_pre_roll(
                campaign_id=campaign_id, scene_id=scene_id, player_input=player_input
            )

            active.stage = "context_build"
            prompt = await self._context.build(
                player_input,
                campaign_id,
                mechanics_results=mechanics_results,
                pc_ref=triggering_pc,
            )
            await self._emit_turn_event(
                "context_built",
                turn_id,
                campaign_id,
                scene_id,
                budget_used={str(k): v for k, v in prompt.budget_used.items()},
            )

            active.stage = "streaming"
            response_text = await self._stream_main_response(
                campaign_id=campaign_id,
                turn_id=turn_id,
                prompt=prompt,
            )
            await self._emit_turn_event(
                "model_response_received",
                turn_id,
                campaign_id,
                scene_id,
                length=len(response_text),
            )

            active.stage = "extracting"
            scene_obj = await self._scenes.get_scene(scene_id)
            extraction = await self._do_extract(
                response_text=response_text,
                scene=scene_obj,
                campaign_id=campaign_id,
                turn_id=turn_id,
            )
            await self._emit_turn_event(
                "deltas_extracted",
                turn_id,
                campaign_id,
                scene_id,
                count=len(extraction.deltas) if extraction else 0,
            )

            active.stage = "applying"
            if extraction is not None:
                await self._apply_routing(
                    campaign_id=campaign_id,
                    branch_id=scene_obj.branch_id,
                    turn_id=turn_id,
                    extraction=extraction,
                )

            # Append the response post to the scene.
            response_post = self._new_post(
                author_kind=SceneAuthorKind.NARRATOR,
                body=response_text,
                is_player=False,
                turn_id=turn_id,
            )
            await self._scenes.append_post(scene_id, response_post)

            await self._emit_turn_event(
                "turn_complete",
                turn_id,
                campaign_id,
                scene_id,
            )
            state.last_turn_id = turn_id
        finally:
            state.active = None
            state.lock.release()
        return turn_id

    async def _maybe_break_scene(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: SceneId,
        player_input: str,
        triggering_pc: CharacterRef | None,
        turn_id: TurnId,
    ) -> SceneId:
        if not player_input or triggering_pc is None:
            return scene_id
        try:
            decision = await self._scenes.is_scene_break(scene_id, player_input)
        except Exception:
            return scene_id
        if not decision.is_break:
            return scene_id
        if decision.confidence < self._config.scene_break.auto_threshold:
            # Below threshold: surface to caller via event but keep current scene.
            await self._bus.emit(
                Event(
                    type="scene_break_suggested",
                    payload={
                        "campaign_id": campaign_id,
                        "scene_id": scene_id,
                        "confidence": decision.confidence,
                        "reason": decision.reason,
                    },
                )
            )
            return scene_id

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
    ) -> list[MechanicsResult]:
        if self._mechanics is None:
            return []
        try:
            scene = await self._scenes.get_scene(scene_id)
        except KeyError:
            return []
        ctx = PydanticSceneContext(scene=_pydantic_scene(scene))
        try:
            proposed: list[ProposedRoll] = await self._mechanics.evaluate_pre_roll(
                campaign_id, player_input, ctx
            )
        except Exception as exc:
            logger.warning("mechanics pre-roll failed: %s", exc)
            return []
        if not proposed:
            return []
        if self._config.pre_roll.confirm_before_executing == "always":
            return []
        results: list[MechanicsResult] = []
        for proposal in proposed:
            try:
                roll = proposal.to_roll() if hasattr(proposal, "to_roll") else None
                if roll is None:
                    continue
                outcome = await self._mechanics.resolve_roll(campaign_id, roll)
                results.append(MechanicsResult(roll=roll, result=outcome))
            except Exception as exc:
                logger.warning("mechanics roll resolution failed: %s", exc)
        return results

    async def _stream_main_response(
        self,
        *,
        campaign_id: CampaignId,
        turn_id: TurnId,
        prompt: Any,
    ) -> str:
        request = CompletionRequest(
            model="",  # routing resolves the actual model
            messages=list(prompt.messages),
            max_tokens=getattr(prompt.params, "max_tokens", 4096),
            temperature=getattr(prompt.params, "temperature", 1.0),
        )
        accumulated: list[str] = []
        stream = self._gateway.stream(
            self._config.main_llm_task,
            request,
            campaign_id=campaign_id,
        )
        async for chunk in stream:
            if chunk.delta:
                accumulated.append(chunk.delta)
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
        try:
            return await self._extractor.extract(
                response_text,
                pyd_scene,
                campaign_id,
                snapshot,
            )
        except Exception as exc:
            logger.warning("extractor failed for turn %s: %s", turn_id, exc)
            return None

    async def _apply_routing(
        self,
        *,
        campaign_id: CampaignId,
        branch_id: str,
        turn_id: TurnId,
        extraction: ExtractionResult,
    ) -> None:
        routing = route_deltas(list(extraction.deltas), config=self._extractor_config)
        for delta, decision in routing.decisions():
            if decision is Decision.DROP:
                continue
            try:
                if decision is Decision.AUTO_APPLY:
                    await self._store.apply_delta(
                        delta=delta,
                        source=delta.source or "extractor",
                        turn_id=turn_id,
                        branch_id=branch_id,
                        campaign_id=campaign_id,
                    )
                else:
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
                            },
                        )
                    )
            except Exception as exc:
                logger.warning(
                    "applying delta failed (kind=%s turn=%s): %s",
                    delta.kind,
                    turn_id,
                    exc,
                )

    # ------------------------------------------------------------------ #
    # Undo helpers
    # ------------------------------------------------------------------ #

    async def _recent_turn_ids(self, campaign_id: CampaignId, count: int) -> list[TurnId]:
        """Return the last ``count`` turn ids (most recent first)."""
        log = await self._store.get_delta_log(
            campaign_id=campaign_id, include_reversed=False
        )
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
        self, campaign_id: CampaignId, turn_id: TurnId
    ) -> list[str]:
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
        row = await self._store.db.fetchone(
            "SELECT id FROM campaigns WHERE id = ?", (campaign_id,)
        )
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


__all__ = ["OrchestratorService", "WSPushFn"]
