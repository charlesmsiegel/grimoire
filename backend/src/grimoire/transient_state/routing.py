"""Routing for extractor TransientUpdateProposal candidates.

Per spec §Extractor integration:
    confidence >= auto_apply_threshold → set(provenance=extractor:auto)
    confidence >= review_threshold     → enqueue for human review
    otherwise                          → discarded

Reinforcement detection (§Promotion to facts): when a proposal's value
matches the last ``promote_to_fact.reinforcement_count - 1`` history rows
for the same entity+field with distinct source_post_ids, the proposal is
marked as ``promote_to_fact`` and — when ``continuity`` is wired — the
value is written through ``ContinuityService.add_fact`` and the just-set
transient row is superseded.

The summary carries per-proposal write descriptors so the orchestrator
can surface them on ``TurnAudit.transient_state_writes`` /
``transient_state_conflicts``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from grimoire.transient_state.config import TransientStateConfig
from grimoire.transient_state.service import TransientStateService
from grimoire.types.transient import (
    EntityKind,
    Provenance,
    TransientUpdateProposal,
)

ReviewEnqueuer = Callable[[TransientUpdateProposal, str], Awaitable[str]]
"""Async callable: (proposal, campaign_id) -> review_id."""


@dataclass
class RoutingSummary:
    auto_applied: int = 0
    enqueued_for_review: int = 0
    discarded: int = 0
    promoted_to_fact: int = 0
    writes: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def _write_record(
    proposal: TransientUpdateProposal,
    *,
    new_value_id: int,
    provenance: str,
) -> dict[str, Any]:
    return {
        "entity_kind": proposal.entity_kind.value,
        "entity_id": proposal.entity_id,
        "field": proposal.field,
        "new_value_id": new_value_id,
        "provenance": provenance,
        "confidence": proposal.confidence,
    }


def _conflict_record(
    proposal: TransientUpdateProposal,
    *,
    current_id: int,
    losing_id: int,
) -> dict[str, Any]:
    return {
        "entity_kind": proposal.entity_kind.value,
        "entity_id": proposal.entity_id,
        "field": proposal.field,
        "current_id": current_id,
        "losing_id": losing_id,
    }


async def _should_promote(
    proposal: TransientUpdateProposal,
    *,
    transient_state: TransientStateService,
    campaign_id: str,
    cfg: TransientStateConfig,
) -> bool:
    """True when the last N entries (including this proposal) carry the
    same value with distinct source_post_ids — the spec's reinforcement
    rule.
    """
    if proposal.proposed_decay_override and proposal.proposed_decay_override.promote_to_fact:
        return True
    needed = cfg.promote_to_fact.reinforcement_count
    if needed <= 1:
        return False
    history = await transient_state.history(
        campaign_id,
        EntityKind(proposal.entity_kind),
        proposal.entity_id,
        proposal.field,
        limit=needed * 2,
    )
    matches = 0
    seen_posts: set[str] = set()
    for h in history:
        if h.value != proposal.value:
            break
        if h.source_post_id and h.source_post_id in seen_posts:
            continue
        if h.source_post_id:
            seen_posts.add(h.source_post_id)
        matches += 1
        if matches >= needed - 1:
            break
    return matches >= needed - 1


async def _promote_via_continuity(
    proposal: TransientUpdateProposal,
    *,
    new_value_id: int,
    transient_state: TransientStateService,
    continuity: Any,
    campaign_id: str,
    source_post_id: str | None,
) -> bool:
    """Run the standard add_fact path + supersede the just-set row.

    Returns True on success; False (silently no-ops) when ``continuity``
    is missing or doesn't expose ``add_fact``.
    """
    if continuity is None or not hasattr(continuity, "add_fact"):
        return False
    from grimoire.types.continuity import Fact, FactScope, FactSource, FactSubject

    subject_kwargs: dict[str, Any] = {}
    kind = EntityKind(proposal.entity_kind)
    if kind == EntityKind.CHARACTER:
        subject_kwargs["character_ids"] = [proposal.entity_id]
    elif kind == EntityKind.LOCATION:
        subject_kwargs["location_ids"] = [proposal.entity_id]
    elif kind == EntityKind.FACTION:
        subject_kwargs["faction_ids"] = [proposal.entity_id]
    fact = Fact(
        id=f"f_{proposal.entity_id}_{proposal.field}_{source_post_id or 'unknown'}",
        campaign_id=campaign_id,
        text=f"{proposal.entity_id} has {proposal.field}: {proposal.value}",
        established_in_post=source_post_id,
        established_at_in_game=None,
        confidence=proposal.confidence,
        source=FactSource.INFERRED,
        about=FactSubject(scope=FactScope.PUBLIC, **subject_kwargs),
        tags=[proposal.evidence] if proposal.evidence else [],
    )
    try:
        fact_id = await continuity.add_fact(fact, source="transient_state:reinforced")
    except Exception:
        return False
    await transient_state.supersede_with_fact(new_value_id, fact_id, entity_kind=kind)
    return True


async def route_transient_updates(
    *,
    campaign_id: str,
    proposals: list[TransientUpdateProposal],
    transient_state: TransientStateService,
    source_post_id: str | None,
    config: TransientStateConfig | None = None,
    review_enqueuer: ReviewEnqueuer | None = None,
    continuity: Any | None = None,
) -> RoutingSummary:
    """Dispatch each proposal to set / review-queue / discard.

    On ``set`` success the result is checked for conflict — if the newly
    inserted row didn't become current (write lost to a higher-priority
    incumbent), a conflict descriptor is added.
    """
    cfg = config or transient_state.config
    summary = RoutingSummary()
    for proposal in proposals:
        kind = EntityKind(proposal.entity_kind)
        if proposal.confidence >= cfg.auto_apply_threshold:
            value = await transient_state.set(
                campaign_id,
                kind,
                proposal.entity_id,
                proposal.field,
                proposal.value,
                provenance=Provenance.EXTRACTOR_AUTO,
                confidence=proposal.confidence,
                source_post_id=source_post_id,
            )
            summary.auto_applied += 1
            summary.writes.append(
                _write_record(
                    proposal,
                    new_value_id=value.id,
                    provenance=Provenance.EXTRACTOR_AUTO.value,
                )
            )
            current = await transient_state.get(
                campaign_id,
                kind,
                proposal.entity_id,
                proposal.field,
            )
            if current is not None and current.id != value.id:
                summary.conflicts.append(
                    _conflict_record(
                        proposal,
                        current_id=current.id,
                        losing_id=value.id,
                    )
                )
                continue
            if await _should_promote(
                proposal,
                transient_state=transient_state,
                campaign_id=campaign_id,
                cfg=cfg,
            ):
                promoted = await _promote_via_continuity(
                    proposal,
                    new_value_id=value.id,
                    transient_state=transient_state,
                    continuity=continuity,
                    campaign_id=campaign_id,
                    source_post_id=source_post_id,
                )
                if promoted:
                    summary.promoted_to_fact += 1
        elif proposal.confidence >= cfg.review_threshold:
            if review_enqueuer is not None:
                await review_enqueuer(proposal, campaign_id)
            summary.enqueued_for_review += 1
        else:
            summary.discarded += 1
    return summary
