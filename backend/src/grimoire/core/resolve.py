"""One proof of source-safe, perspective-safe character assembly."""

from __future__ import annotations

from collections.abc import Iterable

from .index import DefinitionIndex, MissingDefinition
from .model import ActorView, Belief, CampaignActor, Observation

__all__ = ["MissingDefinition", "actor_view"]


def actor_view(
    actor: CampaignActor,
    definitions: DefinitionIndex,
    observations: Iterable[Observation] = (),
    beliefs: Iterable[Belief] = (),
) -> ActorView:
    """Build only this actor's perspective in this campaign.

    An unpinned actor source follows the head revision; ``resolved_source``
    records exactly which revision this view used. A missing source raises
    ``MissingDefinition`` and is never replaced by a same-named record.
    Events, other actors' observations, control assignments and narrator-only
    knowledge are intentionally not parameters. This isn't a prompt renderer.
    """
    definition = None
    if actor.source is not None:
        definition = definitions.resolve(actor.source)
    name = actor.local_name or (definition.name if definition else "")
    return ActorView(
        campaign_id=actor.campaign_id,
        actor_id=actor.actor_id,
        name=name,
        description=getattr(definition, "description", ""),
        observations=tuple(
            observation for observation in observations
            if observation.campaign_id == actor.campaign_id and observation.actor_id == actor.actor_id
        ),
        beliefs=tuple(
            belief for belief in beliefs
            if belief.campaign_id == actor.campaign_id and belief.actor_id == actor.actor_id
        ),
        resolved_source=definition.ref if definition else None,
    )
