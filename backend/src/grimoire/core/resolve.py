"""One proof of source-safe, perspective-safe actor assembly."""

from __future__ import annotations

from .entity import (
    EntityDefinition, EntityError, EntityInstance, effective_entity, instance_from_actor, is_agent,
)
from .index import DefinitionIndex, MissingDefinition
from .knowledge import KnowledgeError
from .model import ActorView, CampaignActor
from .perspective import ActorKnowledge

__all__ = ["MissingDefinition", "actor_view"]


def actor_view(
    actor: CampaignActor | EntityInstance,
    definitions: DefinitionIndex,
    knowledge: ActorKnowledge | None = None,
) -> ActorView:
    """Build only this actor's perspective in this campaign.

    An unpinned actor source follows the head revision; ``resolved_source``
    records exactly which revision this view used. A missing source raises
    ``MissingDefinition`` and is never replaced by a same-named record.
    An entity instance, or a legacy actor whose source is an entity
    definition, must be an effective agent: a plain sword has no perspective.
    ``knowledge`` must be this actor's own ``perspective`` projection; a
    director projection, any other value, or another actor's view is refused.
    Events, other actors' records, control assignments and narrator-only
    knowledge are intentionally not parameters. This isn't a prompt renderer.
    """
    if knowledge is not None:
        if type(knowledge) is not ActorKnowledge:
            raise KnowledgeError("not_actor_knowledge")
        if knowledge.actor != actor.ref:
            raise KnowledgeError("actor_mismatch")
    definition = None
    if isinstance(actor, CampaignActor) and actor.source is not None:
        definition = definitions.resolve(actor.source)
        if isinstance(definition, EntityDefinition):
            actor = instance_from_actor(actor)
    if isinstance(actor, EntityInstance):
        entity = effective_entity(actor, definitions)
        if not is_agent(entity):
            raise EntityError(f"{actor.ref} is not an agent and has no perspective", "not_agent")
        campaign_id, actor_id = actor.ref.campaign_id, actor.ref.instance_id
        name, description, resolved = entity.name, entity.description, entity.resolved_source
    else:
        campaign_id, actor_id = actor.campaign_id, actor.actor_id
        name = actor.local_name or (definition.name if definition else "")
        description = getattr(definition, "description", "")
        resolved = definition.ref if definition else None
    return ActorView(
        campaign_id=campaign_id,
        actor_id=actor_id,
        name=name,
        description=description,
        resolved_source=resolved,
        knowledge=knowledge,
    )
