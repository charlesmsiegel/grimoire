"""Routing for extractor TransientUpdateProposal candidates.

Per spec §Extractor integration:
    confidence >= auto_apply_threshold → set(provenance=extractor:auto)
    confidence >= review_threshold     → enqueue for human review
    otherwise                          → discarded

Kept as a standalone function rather than a method on the extractor
service so it can be re-used by mechanics modules that emit proposals
through their own path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from grimoire.transient_state.config import TransientStateConfig
from grimoire.transient_state.service import TransientStateService
from grimoire.types.transient import (
    EntityKind,
    Provenance,
    TransientUpdateProposal,
)

ReviewEnqueuer = Callable[[TransientUpdateProposal, str], Awaitable[str]]
"""Async callable: (proposal, campaign_id) -> review_id."""


@dataclass(frozen=True, slots=True)
class RoutingSummary:
    auto_applied: int = 0
    enqueued_for_review: int = 0
    discarded: int = 0


async def route_transient_updates(
    *,
    campaign_id: str,
    proposals: list[TransientUpdateProposal],
    transient_state: TransientStateService,
    source_post_id: str | None,
    config: TransientStateConfig | None = None,
    review_enqueuer: ReviewEnqueuer | None = None,
    branch_id: str | None = None,
) -> RoutingSummary:
    """Dispatch each proposal to set / review-queue / discard.

    ``review_enqueuer`` is optional: if not provided, medium-confidence
    proposals are still counted as enqueued but no row is written. This
    keeps callers that haven't wired the review path runnable.
    """
    cfg = config or transient_state.config
    auto_applied = 0
    enqueued = 0
    discarded = 0
    for proposal in proposals:
        if proposal.confidence >= cfg.auto_apply_threshold:
            await transient_state.set(
                campaign_id,
                EntityKind(proposal.entity_kind),
                proposal.entity_id,
                proposal.field,
                proposal.value,
                provenance=Provenance.EXTRACTOR_AUTO,
                confidence=proposal.confidence,
                source_post_id=source_post_id,
                branch_id=branch_id,
            )
            auto_applied += 1
        elif proposal.confidence >= cfg.review_threshold:
            if review_enqueuer is not None:
                await review_enqueuer(proposal, campaign_id)
            enqueued += 1
        else:
            discarded += 1
    return RoutingSummary(
        auto_applied=auto_applied,
        enqueued_for_review=enqueued,
        discarded=discarded,
    )
