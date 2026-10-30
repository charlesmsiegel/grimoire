"""One proof of source-safe, perspective-safe character assembly."""

from __future__ import annotations

from .index import DefinitionIndex, MissingDefinition
from .knowledge import KnowledgeError
from .model import ActorView, CampaignActor
from .perspective import ActorKnowledge

__all__ = ["MissingDefinition", "actor_view"]


def actor_view(
    actor: CampaignActor,
    definitions: DefinitionIndex,
    knowledge: ActorKnowledge | None = None,
) -> ActorView:
    """Build only this actor's perspective in this campaign.

    An unpinned actor source follows the head revision; ``resolved_source``
    records exactly which revision this view used. A missing source raises
    ``MissingDefinition`` and is never replaced by a same-named record.
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
    if actor.source is not None:
        definition = definitions.resolve(actor.source)
    name = actor.local_name or (definition.name if definition else "")
    return ActorView(
        campaign_id=actor.campaign_id,
        actor_id=actor.actor_id,
        name=name,
        description=getattr(definition, "description", ""),
        resolved_source=definition.ref if definition else None,
        knowledge=knowledge,
    )
