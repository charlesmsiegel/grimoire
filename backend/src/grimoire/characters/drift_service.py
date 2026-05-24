"""Drift detection collaborator for CharactersService."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from grimoire.types.characters import Character, DriftReport
from grimoire.types.common import CampaignId, CharacterRef
from grimoire.types.scene import Post
from grimoire.types.state import CharacterState

from .config import DriftConfig
from .drift import (
    CallableDriftChecker,
    DriftChecker,
    DriftEvent,
    DriftEventSink,
    DriftInput,
    HeuristicDriftChecker,
    LLMCallable,
)

_log = logging.getLogger(__name__)

PostFetcher = Callable[[str], Awaitable[list[Post]]]


class CharacterDriftService:
    """Drift sampling and cadence gating (spec characters-remaining §3-4)."""

    def __init__(
        self,
        *,
        config: DriftConfig,
        post_fetcher: PostFetcher | None = None,
        drift_checker: DriftChecker | None = None,
        drift_event_sink: DriftEventSink | None = None,
    ) -> None:
        self._config = config
        self._post_fetcher = post_fetcher
        self._drift_checker: DriftChecker = drift_checker or HeuristicDriftChecker(
            drift_threshold=config.threshold
        )
        self._drift_event_sink = drift_event_sink

    async def check_drift(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        character: Character,
        state: CharacterState,
        *,
        window: int = 10,
        recent_posts: list[Post] | None = None,
        current_scene_id: str | None = None,
    ) -> DriftReport:
        posts: list[Post] = recent_posts or []
        if not posts and self._post_fetcher is not None:
            scene_id = current_scene_id or state.current_scene_id
            if scene_id:
                fetched = await self._post_fetcher(scene_id)
                posts = list(fetched[-window:])
        report = await self._drift_checker.evaluate(
            DriftInput(character=character, recent_posts=posts, window=window)
        )

        if (
            self._drift_event_sink is not None
            and report.drift_score >= self._config.threshold
        ):
            event = DriftEvent(
                character_ref=ref,
                campaign_id=campaign_id,
                drift_score=report.drift_score,
                threshold=self._config.threshold,
                report=report,
            )
            try:
                await self._drift_event_sink(event)
            except Exception:
                _log.warning(
                    "drift_event_sink raised for %s in %s", ref, campaign_id, exc_info=True
                )
        return report

    def should_check(self, state: CharacterState, *, force: bool = False) -> bool:
        threshold = self._config.check_every_n_appearances
        if force:
            return True
        return threshold <= 0 or state.appearances_since_last_drift_check >= threshold

    def corrective_text(self, character: Character) -> str:
        from .drift import _corrective_text

        return _corrective_text(character, [])

    def set_drift_checker(self, checker: DriftChecker | LLMCallable) -> None:
        if callable(checker) and not hasattr(checker, "evaluate"):
            self._drift_checker = CallableDriftChecker(checker)  # type: ignore[arg-type]
        else:
            self._drift_checker = checker  # type: ignore[assignment]
