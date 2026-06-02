"""AlternatesManager — swipe/alternate lifecycle for the orchestrator."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.delta_applier import DeltaApplier
from grimoire.orchestrator.errors import (
    AlternateNotFoundError,
    CannotDeletePrimaryError,
    LatestPostOnlyError,
    UnknownCampaignError,
)
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import Alternate as SceneAlternate
from grimoire.scenes.types import AuthorKind as SceneAuthorKind
from grimoire.scenes.types import Post as SceneFilePost
from grimoire.scenes.types import Scene as SceneFileScene
from grimoire.types.common import CampaignId, CharacterRef, PostId
from grimoire.types.orchestrator import RegeneratePostResult

logger = logging.getLogger(__name__)

StreamResponseFn = Callable[..., Awaitable[str]]


class AlternatesManager:
    """Manages alternate generation, switching, pinning, deletion, and purge."""

    def __init__(
        self,
        *,
        scenes: SceneManager,
        state_store: Any,
        event_bus: EventBus,
        context_builder: Any,
        delta: DeltaApplier,
        config: OrchestratorConfig,
        clock: Callable[[], datetime],
        stream_response: StreamResponseFn,
    ) -> None:
        self._scenes = scenes
        self._store = state_store
        self._bus = event_bus
        self._context = context_builder
        self._delta = delta
        self._config = config
        self._clock = clock
        self._stream_response = stream_response

    async def _require_campaign(self, campaign_id: CampaignId) -> None:
        row = await self._store.db.fetchone("SELECT id FROM campaigns WHERE id = ?", (campaign_id,))
        if row is None:
            raise UnknownCampaignError(campaign_id)

    async def find_scene_and_post(self, post_id: PostId) -> tuple[SceneFileScene, SceneFilePost]:
        return await self._scenes._find_post(post_id)

    async def ensure_latest_model_post(self, scene: SceneFileScene, post: SceneFilePost) -> None:
        posts = await self._scenes.get_posts(scene.id)
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
        await self._require_campaign(campaign_id)
        scene, post = await self.find_scene_and_post(post_id)
        await self.ensure_latest_model_post(scene, post)
        return await self.regenerate_post_core(
            scene=scene,
            post=post,
            campaign_id=campaign_id,
            steering_hint=steering_hint,
            model_override=model_override,
        )

    async def regenerate_post_core(
        self,
        *,
        scene: SceneFileScene,
        post: SceneFilePost,
        campaign_id: CampaignId,
        steering_hint: str | None = None,
        model_override: str | None = None,
        replay_batch_id: str | None = None,
    ) -> RegeneratePostResult:
        post_id = post.id

        posts = await self._scenes.get_posts(scene.id)
        player_input = ""
        pc_ref: CharacterRef | None = None
        for prior in reversed([p for p in posts if p.order_in_scene < post.order_in_scene]):
            if prior.is_player:
                player_input = prior.body
                pc_ref = prior.author_pc_ref
                break

        new_alt_id = f"a_{uuid.uuid4().hex[:16]}"
        new_ds_id = f"ds_{uuid.uuid4().hex[:16]}"

        # Capture the current primary's delta set up front so the rollback path
        # can re-activate it even if generation fails before we touch state.
        current_primary = next(
            (a for a in post.alternates if a.id == post.primary_alternate_id),
            None,
        )
        rewind_ds = (
            current_primary.delta_set_id
            if current_primary and current_primary.delta_set_id
            else None
        )
        rewound = False

        try:
            extract_mode = await self._delta.select_extract_mode(campaign_id=campaign_id)
            prompt = await self._context.build(
                player_input,
                campaign_id,
                mechanics_results=[],
                pc_ref=pc_ref,
                turn_id=post.turn_id,
                extra=steering_hint,
                extractor_mode=extract_mode,
            )
            response_text = await self._stream_response(
                campaign_id=campaign_id,
                turn_id=post.turn_id,
                prompt=prompt,
            )
            scene_obj = await self._scenes.get_scene(scene.id)
            extraction = await self._delta.extract(
                response_text=response_text,
                scene=scene_obj,
                campaign_id=campaign_id,
                turn_id=post.turn_id,
                mode=extract_mode,
            )

            # Apply the new delta set through the same routing the main turn
            # uses (apply_routing): continuity / inventory / weather deltas are
            # dispatched to their owning services, while directly-applicable
            # SQLite + file deltas are tagged with ``new_ds_id`` so the swipe
            # swap path can rewind them. Feeding raw extractor deltas to the
            # low-level ``apply_delta_set`` upsert instead corrupts/crashes on
            # continuity deltas (e.g. COMMITMENT_ADD lacks the ``commitments``
            # primary key). See swipes-alternates design §"contradiction-check
            # already part of the _apply_routing flow".
            if rewind_ds:
                await self._store.rewind_delta_set(rewind_ds, campaign_id=campaign_id)
                rewound = True
            if extraction is not None:
                await self._delta.apply_routing(
                    campaign_id=campaign_id,
                    turn_id=post.turn_id,
                    extraction=extraction,
                    delta_set_id=new_ds_id,
                )

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

            await self._store.set_current_alternate_delta_set(
                campaign_id=campaign_id,
                post_id=post_id,
                delta_set_id=new_ds_id,
            )

            await self._bus.emit(
                Event(
                    type=events.ALTERNATE_ADDED,
                    payload={
                        "campaign_id": campaign_id,
                        "post_id": post_id,
                        "alternate_id": new_alt_id,
                        "delta_set_id": new_ds_id,
                    },
                )
            )

            try:
                await self._evict_overflow_alternate(post_id)
            except Exception:
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
            # Reverse any deltas we tagged with the new set (no-op if none were
            # applied), then restore the prior primary's set if we rewound it.
            try:
                await self._store.rewind_delta_set(new_ds_id, campaign_id=campaign_id)
            except Exception:
                logger.warning(
                    "rollback of new delta set %s during regenerate_post failed",
                    new_ds_id,
                    exc_info=True,
                )
            if rewound and rewind_ds:
                try:
                    await self._store.re_activate_delta_set(
                        delta_set_id=rewind_ds,
                        campaign_id=campaign_id,
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
        await self._require_campaign(campaign_id)
        scene, post = await self.find_scene_and_post(post_id)
        await self.ensure_latest_model_post(scene, post)
        return await self.switch_primary_alternate_core(
            scene=scene,
            post=post,
            campaign_id=campaign_id,
            alternate_id=alternate_id,
        )

    async def switch_primary_alternate_core(
        self,
        *,
        scene: SceneFileScene,
        post: SceneFilePost,
        campaign_id: CampaignId,
        alternate_id: str,
    ) -> dict[str, Any]:
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
            await self._scenes.set_primary_alternate(post_id, alternate_id)
            await self._scenes.rebuild_md_from_primaries(scene.id)
            return {
                "unchanged": False,
                "post_id": post_id,
                "from": current.id if current else None,
                "to": alternate_id,
                "delta_swap": False,
            }

        await self._store.swap_delta_set(
            rewind_set_id=current.delta_set_id,
            apply_deltas=None,
            apply_set_id=target.delta_set_id,
            campaign_id=campaign_id,
            turn_id=post.turn_id,
            source="orchestrator:switch-primary",
        )
        await self._scenes.set_primary_alternate(post_id, alternate_id)
        await self._scenes.rebuild_md_from_primaries(scene.id)
        await self._store.set_current_alternate_delta_set(
            campaign_id=campaign_id,
            post_id=post_id,
            delta_set_id=target.delta_set_id,
        )
        await self._bus.emit(
            Event(
                type=events.PRIMARY_SWITCHED,
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
        scene, post = await self.find_scene_and_post(post_id)
        if not any(a.id == alternate_id for a in post.alternates):
            raise AlternateNotFoundError(post_id, alternate_id)
        await self._scenes.update_alternate(post_id, alternate_id, pinned=pinned)
        await self._bus.emit(
            Event(
                type=events.ALTERNATE_PINNED,
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
        scene, post = await self.find_scene_and_post(post_id)
        if post.primary_alternate_id == alternate_id:
            raise CannotDeletePrimaryError(post_id, alternate_id)
        target = next((a for a in post.alternates if a.id == alternate_id), None)
        if target is None:
            raise AlternateNotFoundError(post_id, alternate_id)
        if target.delta_set_id:
            try:
                await self._store.rewind_delta_set(
                    target.delta_set_id,
                    campaign_id=scene.campaign_id,
                )
            except Exception as exc:
                logger.warning(
                    "failed to rewind delta set %s on delete_alternate: %s",
                    target.delta_set_id,
                    exc,
                )
        await self._scenes.remove_alternate(post_id, alternate_id)
        await self._bus.emit(
            Event(
                type=events.ALTERNATE_DELETED,
                payload={
                    "campaign_id": scene.campaign_id,
                    "post_id": post_id,
                    "alternate_id": alternate_id,
                },
            )
        )

    async def _evict_overflow_alternate(self, post_id: PostId) -> None:
        cap = self._config.swipes.max_alternates_per_post
        if cap <= 0:
            return
        _scene, post = await self.find_scene_and_post(post_id)
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
                    except Exception:
                        logger.warning(
                            "purge_stale_alternates: failed to delete %s on %s",
                            alt.id,
                            post.id,
                            exc_info=True,
                        )
        return deleted
