"""One proof of source-safe, perspective-safe character assembly."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .model import ActorView, Belief, CampaignActor, CharacterDefinition, Observation, SourceRef


class MissingDefinition(LookupError):
    """A source is missing: never silently substitute a same-named record."""


def actor_view(
    actor: CampaignActor,
    definitions: Mapping[SourceRef, CharacterDefinition],
    observations: Iterable[Observation] = (),
    beliefs: Iterable[Belief] = (),
) -> ActorView:
    """Build only this actor's perspective in this campaign.

    Events, other actors' observations, control assignments and narrator-only
    knowledge are intentionally not parameters. This isn't a prompt renderer.
    """
    definition = None
    if actor.source is not None:
        definition = definitions.get(actor.source)
        if definition is None:
            raise MissingDefinition(f"missing exact source: {actor.source!r}")
    name = actor.local_name or (definition.name if definition else "")
    return ActorView(
        campaign_id=actor.campaign_id,
        actor_id=actor.actor_id,
        name=name,
        description=definition.description if definition else "",
        observations=tuple(
            observation for observation in observations
            if observation.campaign_id == actor.campaign_id and observation.actor_id == actor.actor_id
        ),
        beliefs=tuple(
            belief for belief in beliefs
            if belief.campaign_id == actor.campaign_id and belief.actor_id == actor.actor_id
        ),
    )
