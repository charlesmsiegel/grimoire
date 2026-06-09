"""RetconCoordinator — retcon and replay operations for the orchestrator."""

from __future__ import annotations

import logging
from typing import Any

from grimoire.event_bus import EventBus
from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.routing import route_deltas
from grimoire.orchestrator.errors import (
    RetconExtractionError,
    RetconInFlightError,
    RetconStateError,
    UnknownCampaignError,
)
from grimoire.orchestrator.helpers import _pydantic_scene
from grimoire.orchestrator.retcon_replay import RetconReplaySession
from grimoire.scenes.manager import SceneManager
from grimoire.types.common import CampaignId, PostId, TurnId
from grimoire.types.extraction import ExtractionResult
from grimoire.types.orchestrator import (
    ReplayBatchStateView,
    RetconResult,
)
from grimoire.types.state import StateSnapshot

logger = logging.getLogger(__name__)


class RetconCoordinator:
    """Manages retcon editing and replay sessions."""

    def __init__(
        self,
        *,
        host: Any,
        scenes: SceneManager,
        state_store: Any,
        event_bus: EventBus,
        extractor: Any,
        extractor_config: ExtractorConfig,
    ) -> None:
        self._host = host
        self._scenes = scenes
        self._store = state_store
        self._bus = event_bus
        self._extractor = extractor
        self._extractor_config = extractor_config
        self._retcon_replay: RetconReplaySession | None = None

    async def _require_campaign(self, campaign_id: CampaignId) -> None:
        row = await self._store.db.fetchone("SELECT id FROM campaigns WHERE id = ?", (campaign_id,))
        if row is None:
            raise UnknownCampaignError(campaign_id)

    @property
    def retcon_replay(self) -> RetconReplaySession:
        if self._retcon_replay is None:
            self._retcon_replay = RetconReplaySession(self._host, event_bus=self._bus)
        return self._retcon_replay

    async def retcon_post(
        self,
        post_id: PostId,
        new_text: str,
        *,
        campaign_id: CampaignId | None = None,
        replay_subsequent: bool = False,
    ) -> RetconResult:
        if replay_subsequent:
            if campaign_id is None:
                scene_file, _ = await self._scenes._find_post(post_id)
                campaign_id = scene_file.campaign_id
            if self.retcon_replay.is_active(campaign_id):
                raise RetconInFlightError(campaign_id)

        base = await self._retcon_leave_as_is(post_id, new_text)
        if not replay_subsequent:
            return base
        if campaign_id is None:
            raise RuntimeError("retcon: campaign_id must be resolved before starting replay")
        state = await self.retcon_replay.start(campaign_id=campaign_id, edited_post_id=post_id)
        return base.model_copy(update={"replay_batch_id": state.batch_id})

    async def accept_replay(
        self, campaign_id: CampaignId, *, batch_id: str | None = None
    ) -> ReplayBatchStateView:
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
        scene_file, post = await self._scenes._find_post(post_id)
        original = post.body
        warnings: list[str] = []
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
                logger.warning(
                    "retcon: could not compute downstream targets for post %s (turn %s)",
                    post_id,
                    post.turn_id,
                    exc_info=True,
                )
                warnings.append(
                    f"could not compute downstream targets for turn {post.turn_id}; "
                    "downstream_flagged_turns may be incomplete"
                )

        # Re-extract BEFORE touching any state: a failed extraction must leave
        # the post text and the turn's deltas exactly as they were (#583).
        try:
            snapshot = StateSnapshot(
                campaign_id=scene_file.campaign_id,
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
        except Exception as exc:
            logger.warning("retcon: extractor failure on post %s: %s", post_id, exc)
            raise RetconExtractionError(post_id) from exc
        routing = route_deltas(list(result.deltas), config=self._extractor_config)

        await self._scenes.edit_post(post_id, new_text, source="retcon")

        # Swap the turn's deltas for the re-extracted ones as one atomic unit:
        # either the old deltas are reversed and every replacement is applied,
        # or the store rolls the whole swap back. On failure, restore the post
        # text so the retcon as a whole is a no-op, then surface the error.
        try:
            swap = await self._store.swap_turn_deltas(
                campaign_id=scene_file.campaign_id,
                turn_id=post.turn_id or None,
                deltas=list(routing.auto_apply),
                source="retcon",
            )
        except Exception as exc:
            logger.warning(
                "retcon: delta swap failed for post %s (turn %s); state rolled back: %s",
                post_id,
                post.turn_id,
                exc,
            )
            try:
                await self._scenes.edit_post(post_id, original, source="retcon")
            except Exception:
                logger.exception(
                    "retcon: could not restore original text of post %s after failed swap",
                    post_id,
                )
            raise RetconStateError(post_id) from exc
        reversed_ids = [record.id for record in swap.rewound]
        new_delta_ids = [record.id for record in swap.applied]

        for delta in routing.review:
            try:
                await self._store.queue_for_review(
                    delta=delta, source="retcon", campaign_id=scene_file.campaign_id
                )
            except Exception as exc:
                logger.warning(
                    "retcon: queue_for_review failed (kind=%s post=%s): %s",
                    delta.kind,
                    post_id,
                    exc,
                )
                warnings.append(f"a low-confidence delta (kind={delta.kind}) could not be queued")

        flagged: list[TurnId] = []
        if downstream_targets and post.turn_id:
            try:
                full_log = await self._store.get_delta_log(
                    campaign_id=scene_file.campaign_id,
                    include_reversed=True,
                )
                seen: set[TurnId] = set()
                for record in full_log:
                    tid = getattr(record, "turn_id", None)
                    if not tid or tid == post.turn_id or tid in seen:
                        continue
                    target = getattr(record, "target_id", None)
                    if target and str(target) in downstream_targets:
                        flagged.append(tid)
                        seen.add(tid)
            except Exception:
                logger.warning(
                    "retcon: downstream flagging walk failed for post %s",
                    post_id,
                    exc_info=True,
                )
                warnings.append("downstream flagging walk failed; flagged turns may be incomplete")

        return RetconResult(
            post_id=post_id,
            original_text=original,
            new_text=new_text,
            reversed_delta_ids=reversed_ids,
            new_delta_ids=new_delta_ids,
            downstream_flagged_turns=flagged,
            warnings=warnings,
        )
