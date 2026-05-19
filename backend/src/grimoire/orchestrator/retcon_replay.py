"""Retcon replay batch state machine (spec ``2026-05-19-retcon-design``).

When the user retcons a post and opts to *replay* the subsequent turns
(rather than leave them as-is and let Continuity surface contradictions),
the Orchestrator enters a sequential review mode driven by this module:

1. Collect every model-authored post that follows the edited one.
2. For each, re-sample via :meth:`OrchestratorService._regenerate_post_core`
   to produce a new alternate using the now-current scene context (which
   already reflects the retconned text — see "context injection" in the
   design).
3. Wait for the user's Accept / Try again / Cancel decision before moving
   to the next post.

One open batch per campaign; concurrent retcons raise
:class:`RetconInFlightError`. Cancel finalizes the batch at whichever post
is in flight (its in-flight alternate is dropped via
:meth:`OrchestratorService.delete_alternate` and the original primary's
delta set is re-activated so the world state matches the unchanged
primary pointer); subsequent posts keep their original primaries,
matching the "leave-as-is from cancel point onward" guarantee from the
design.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator.errors import (
    RetconBatchClosedError,
    RetconBatchNotFoundError,
    RetconInFlightError,
)
from grimoire.types.common import CampaignId, PostId
from grimoire.types.orchestrator import EventType, ReplayBatchStateView

if TYPE_CHECKING:
    from grimoire.orchestrator.service import OrchestratorService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReplayBatchState:
    batch_id: str
    campaign_id: CampaignId
    edited_post_id: PostId
    subsequent_post_ids: list[PostId]
    current_index: int = 0
    current_alternate_id: str | None = None
    accepted_post_ids: list[PostId] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    completed: bool = False
    cancelled_at_post_id: PostId | None = None

    @property
    def current_post_id(self) -> PostId | None:
        if self.current_index >= len(self.subsequent_post_ids):
            return None
        return self.subsequent_post_ids[self.current_index]

    def to_view(self) -> ReplayBatchStateView:
        return ReplayBatchStateView(
            batch_id=self.batch_id,
            campaign_id=self.campaign_id,
            edited_post_id=self.edited_post_id,
            subsequent_post_ids=list(self.subsequent_post_ids),
            current_index=self.current_index,
            current_post_id=self.current_post_id,
            current_alternate_id=self.current_alternate_id,
            accepted_post_ids=list(self.accepted_post_ids),
            contradictions=list(self.contradictions),
            completed=self.completed,
            cancelled_at_post_id=self.cancelled_at_post_id,
        )


class RetconReplaySession:
    """One-batch-per-campaign retcon replay coordinator.

    The orchestrator holds a single instance and routes
    :meth:`start` / :meth:`accept` / :meth:`try_again` / :meth:`cancel`
    through to it. Each state transition emits a ``retcon_*`` event on the
    in-process event bus, which the WebSocket bridge forwards to the
    replay UI.

    Mutations are serialised per-campaign via :pyattr:`_locks` — two
    concurrent ``accept`` calls would otherwise both pass the open-batch
    check, both advance ``current_index``, and silently skip a post. The
    lock pattern mirrors :class:`_CampaignTurnState` in ``service.py``.
    """

    def __init__(self, orchestrator: OrchestratorService, *, event_bus: EventBus) -> None:
        self._orch = orchestrator
        self._bus = event_bus
        # campaign_id -> currently-open batch. Closed batches (finalised by
        # accept-to-end or cancel) move into ``_closed`` so the frontend can
        # still poll their terminal state until a new batch on the same
        # campaign evicts them.
        self._open: dict[CampaignId, ReplayBatchState] = {}
        self._closed: dict[str, ReplayBatchState] = {}
        # Per-campaign mutation lock. Created lazily on first touch.
        self._locks: dict[CampaignId, asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # Status queries
    # ------------------------------------------------------------------ #

    def is_active(self, campaign_id: CampaignId) -> bool:
        state = self._open.get(campaign_id)
        return state is not None and not state.completed

    def get(self, batch_id: str) -> ReplayBatchState:
        for state in self._open.values():
            if state.batch_id == batch_id:
                return state
        state = self._closed.get(batch_id)
        if state is None:
            raise RetconBatchNotFoundError(batch_id)
        return state

    def _lock_for(self, campaign_id: CampaignId) -> asyncio.Lock:
        lock = self._locks.get(campaign_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[campaign_id] = lock
        return lock

    # ------------------------------------------------------------------ #
    # State transitions
    # ------------------------------------------------------------------ #

    async def start(
        self,
        *,
        campaign_id: CampaignId,
        edited_post_id: PostId,
    ) -> ReplayBatchState:
        async with self._lock_for(campaign_id):
            if self.is_active(campaign_id):
                raise RetconInFlightError(campaign_id)
            # Evict any prior closed-batch entries for this campaign so
            # ``_closed`` doesn't grow without bound across long-lived
            # processes. New batches on this campaign start clean; the
            # tradeoff is that clients can't go back to a prior batch's
            # terminal state once a new one opens (acceptable — the
            # frontend caches the final state in the modal it just
            # closed).
            self._closed = {
                bid: s for bid, s in self._closed.items() if s.campaign_id != campaign_id
            }
            batch_id = f"rb_{uuid.uuid4().hex[:16]}"
            subsequent = await self._collect_subsequent_post_ids(campaign_id, edited_post_id)
            state = ReplayBatchState(
                batch_id=batch_id,
                campaign_id=campaign_id,
                edited_post_id=edited_post_id,
                subsequent_post_ids=subsequent,
            )
            self._open[campaign_id] = state
            await self._emit(
                campaign_id,
                EventType.RETCON_STARTED,
                {
                    "post_id": edited_post_id,
                    "replay_subsequent": True,
                    "batch_id": batch_id,
                    "subsequent_post_ids": list(subsequent),
                },
            )
            if not subsequent:
                await self._finalize(state, complete=True)
                return state
            await self._generate_alternate_for_current(state)
            return state

    async def accept(
        self,
        campaign_id: CampaignId,
        *,
        expected_batch_id: str | None = None,
    ) -> ReplayBatchState:
        async with self._lock_for(campaign_id):
            state = self._require_open(campaign_id, expected_batch_id=expected_batch_id)
            post_id = state.current_post_id
            alt_id = state.current_alternate_id
            if post_id is None or alt_id is None:
                raise RetconBatchClosedError(state.batch_id)
            scene, post = await self._orch._find_scene_and_post(post_id)
            await self._orch._switch_primary_alternate_core(
                scene=scene, post=post, campaign_id=campaign_id, alternate_id=alt_id
            )
            await self._collect_contradictions(state, post_id)
            state.accepted_post_ids.append(post_id)
            await self._emit(
                campaign_id,
                EventType.RETCON_POST_ACCEPTED,
                {
                    "post_id": post_id,
                    "alternate_id": alt_id,
                    "batch_id": state.batch_id,
                },
            )
            state.current_index += 1
            state.current_alternate_id = None
            if state.current_index >= len(state.subsequent_post_ids):
                await self._finalize(state, complete=True)
            else:
                await self._generate_alternate_for_current(state)
            return state

    async def try_again(
        self,
        campaign_id: CampaignId,
        *,
        expected_batch_id: str | None = None,
    ) -> ReplayBatchState:
        async with self._lock_for(campaign_id):
            state = self._require_open(campaign_id, expected_batch_id=expected_batch_id)
            post_id = state.current_post_id
            if post_id is None:
                raise RetconBatchClosedError(state.batch_id)
            # Drop the in-flight alternate and restore the original primary's
            # contribution so the world state matches the unchanged pointer
            # before we generate again. Without the re-activate step, both
            # delta sets end up rewound and the world is in a phantom state.
            if state.current_alternate_id:
                await self._discard_in_flight_alternate(state, post_id, state.current_alternate_id)
                state.current_alternate_id = None
            await self._generate_alternate_for_current(state)
            return state

    async def cancel(
        self,
        campaign_id: CampaignId,
        *,
        expected_batch_id: str | None = None,
    ) -> ReplayBatchState:
        async with self._lock_for(campaign_id):
            state = self._require_open(campaign_id, expected_batch_id=expected_batch_id)
            post_id_at_cancel = state.current_post_id
            if state.current_alternate_id and post_id_at_cancel is not None:
                await self._discard_in_flight_alternate(
                    state, post_id_at_cancel, state.current_alternate_id
                )
            state.cancelled_at_post_id = post_id_at_cancel
            state.current_alternate_id = None
            await self._finalize(state, complete=False)
            return state

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _require_open(
        self,
        campaign_id: CampaignId,
        *,
        expected_batch_id: str | None = None,
    ) -> ReplayBatchState:
        """Return the open batch for ``campaign_id`` or raise.

        When ``expected_batch_id`` is supplied (route-level path
        parameter), the open batch's id must match. Without this check a
        TOCTOU race could cancel batch B1 and open B2 between the
        ``GET /retcon/replay/{B1}`` validation and the ``POST .../accept``
        action — so the accept would silently operate on B2 instead.
        """
        state = self._open.get(campaign_id)
        if state is None or state.completed:
            raise RetconBatchClosedError(
                state.batch_id if state else f"<no-active-batch:{campaign_id}>"
            )
        if expected_batch_id is not None and state.batch_id != expected_batch_id:
            # The batch the client thought it was acting on is gone; whatever
            # is open now is a different one. Treat as a "not found" rather
            # than blindly mutating the new batch.
            raise RetconBatchNotFoundError(expected_batch_id)
        return state

    async def _discard_in_flight_alternate(
        self,
        state: ReplayBatchState,
        post_id: PostId,
        alternate_id: str,
    ) -> None:
        """Drop an in-flight alternate and put the world back where it was.

        ``_regenerate_post_core`` rewound the original primary's delta set
        and applied the new alternate's. ``delete_alternate`` rewinds the
        new alternate's set — so without re-activating the original we'd
        leave neither set applied. Best-effort: log + continue on either
        failure rather than corrupting batch state.
        """
        original_primary_ds: str | None = None
        branch_id = "main"
        try:
            scene, post = await self._orch._find_scene_and_post(post_id)
            branch_id = scene.branch_id or "main"
            original_primary_ds = next(
                (
                    a.delta_set_id
                    for a in post.alternates
                    if a.id == post.primary_alternate_id and a.delta_set_id
                ),
                None,
            )
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "retcon-replay: could not resolve original primary for %s",
                post_id,
                exc_info=True,
            )

        try:
            await self._orch.delete_alternate(post_id=post_id, alternate_id=alternate_id)
        except Exception:  # pragma: no cover - best effort
            logger.warning(
                "retcon-replay: failed to delete in-flight alternate %s on %s",
                alternate_id,
                post_id,
                exc_info=True,
            )
            return

        if original_primary_ds is None:
            return
        try:
            await self._orch._store.re_activate_delta_set(
                delta_set_id=original_primary_ds,
                campaign_id=state.campaign_id,
                branch_id=branch_id,
            )
        except Exception:  # pragma: no cover - best effort
            logger.warning(
                "retcon-replay: failed to re-activate original primary set %s on %s",
                original_primary_ds,
                post_id,
                exc_info=True,
            )

    async def _generate_alternate_for_current(self, state: ReplayBatchState) -> None:
        post_id = state.current_post_id
        if post_id is None:
            return
        scene, post = await self._orch._find_scene_and_post(post_id)
        regen = await self._orch._regenerate_post_core(
            scene=scene,
            post=post,
            campaign_id=state.campaign_id,
            replay_batch_id=state.batch_id,
        )
        state.current_alternate_id = regen.new_alternate_id
        await self._emit(
            state.campaign_id,
            EventType.RETCON_POST_REPLAYED,
            {
                "post_id": post_id,
                "new_alternate_id": regen.new_alternate_id,
                "batch_id": state.batch_id,
            },
        )

    async def _collect_contradictions(self, state: ReplayBatchState, post_id: PostId) -> None:
        """Best-effort: pull unresolved contradictions whose candidate fact
        was established on the just-accepted post and append their ids.

        Surfaced as a list of report ids; clients use the existing
        ``GET .../continuity/ledger`` route to fetch the full reports.
        """
        continuity = getattr(self._orch, "_continuity", None)
        if continuity is None:
            return
        try:
            reports = await continuity.pending_contradictions(limit=50)
        except Exception:  # pragma: no cover - defensive
            logger.debug("retcon-replay: pending_contradictions probe failed", exc_info=True)
            return
        for report in reports:
            candidate = getattr(report, "candidate_fact", None)
            established = getattr(candidate, "established_in_post", None) if candidate else None
            if established == post_id:
                report_id = getattr(report, "id", None)
                if report_id and report_id not in state.contradictions:
                    state.contradictions.append(report_id)

    async def _collect_subsequent_post_ids(
        self,
        campaign_id: CampaignId,
        edited_post_id: PostId,
    ) -> list[PostId]:
        """All model-authored posts strictly after the edited one in temporal
        order: the rest of its scene, then every post in later scenes."""
        edited_scene, edited_post = await self._orch._find_scene_and_post(edited_post_id)
        branch_id = edited_scene.branch_id or "main"
        scenes = await self._orch._scenes.list_scenes(campaign_id, branch_id)
        ordered: list[PostId] = []
        seen_edited_scene = False
        for scene in scenes:
            if scene.id == edited_scene.id:
                seen_edited_scene = True
                posts = await self._orch._scenes.get_posts(scene.id)
                for p in posts:
                    if p.order_in_scene <= edited_post.order_in_scene:
                        continue
                    if not _is_model_post(p):
                        continue
                    ordered.append(p.id)
                continue
            if not seen_edited_scene:
                continue
            posts = await self._orch._scenes.get_posts(scene.id)
            for p in posts:
                if not _is_model_post(p):
                    continue
                ordered.append(p.id)
        return ordered

    async def _finalize(self, state: ReplayBatchState, *, complete: bool) -> None:
        state.completed = True
        self._open.pop(state.campaign_id, None)
        self._closed[state.batch_id] = state
        if complete:
            await self._emit(
                state.campaign_id,
                EventType.RETCON_COMPLETE,
                {
                    "batch_id": state.batch_id,
                    "post_id": state.edited_post_id,
                    "accepted_post_ids": list(state.accepted_post_ids),
                },
            )
        else:
            await self._emit(
                state.campaign_id,
                EventType.RETCON_CANCELLED,
                {
                    "batch_id": state.batch_id,
                    "cancelled_at_post_id": state.cancelled_at_post_id,
                },
            )

    async def _emit(
        self,
        campaign_id: CampaignId,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        await self._bus.emit(
            Event(
                type=event_type.value,
                payload={"campaign_id": campaign_id, **payload},
            )
        )


def _is_model_post(post: Any) -> bool:
    """A model-authored canonical post — what the replay re-samples."""
    is_player = getattr(post, "is_player", False)
    author_kind = getattr(post, "author_kind", None)
    kind_value = getattr(author_kind, "value", author_kind)
    return (not is_player) and kind_value != "pc"


__all__ = [
    "ReplayBatchState",
    "RetconReplaySession",
]
