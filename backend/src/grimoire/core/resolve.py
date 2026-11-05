"""One proof of source-safe, perspective-safe actor assembly."""

from __future__ import annotations

from .entity import (
    EffectiveEntity, EntityDefinition, EntityError, EntityInstance, effective_entity, instance_from_actor,
    is_agent,
)
from .identity import InstanceRef
from .index import DefinitionIndex, MissingDefinition
from .knowledge import KnowledgeError
from .model import ActorView, CampaignActor
from .perspective import ActorKnowledge
from .portrayal import PortrayalError
from .snapshot import EffectiveSnapshot

__all__ = ["MissingDefinition", "actor_view", "snapshot_actor_view"]


def _check_knowledge(actor: InstanceRef, knowledge: ActorKnowledge | None) -> None:
    if knowledge is not None:
        if type(knowledge) is not ActorKnowledge:
            raise KnowledgeError("not_actor_knowledge")
        if knowledge.actor != actor:
            raise KnowledgeError("actor_mismatch")


def _entity_view(entity: EffectiveEntity, knowledge: ActorKnowledge | None) -> ActorView:
    """The one place an effective entity becomes a perspective: both paths expose exactly this."""
    if not is_agent(entity):
        raise EntityError(f"{entity.ref} is not an agent and has no perspective", "not_agent")
    return ActorView(
        campaign_id=entity.ref.campaign_id,
        actor_id=entity.ref.instance_id,
        name=entity.name,
        description=entity.description,
        resolved_source=entity.resolved_source,
        knowledge=knowledge,
    )


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

    A derived portrayal (022) is refused with ``derived_definition``: this
    index path would read its delta as a complete card. Use
    ``snapshot_actor_view`` for it.
    """
    _check_knowledge(actor.ref, knowledge)
    definition = None
    if isinstance(actor, CampaignActor) and actor.source is not None:
        definition = definitions.resolve(actor.source)
        if isinstance(definition, EntityDefinition):
            actor = instance_from_actor(actor)
    if isinstance(actor, EntityInstance):
        return _entity_view(effective_entity(actor, definitions), knowledge)
    return ActorView(
        campaign_id=actor.campaign_id,
        actor_id=actor.actor_id,
        name=actor.local_name or (definition.name if definition else ""),
        description=getattr(definition, "description", ""),
        resolved_source=definition.ref if definition else None,
        knowledge=knowledge,
    )


def snapshot_actor_view(
    instance: EntityInstance,
    snapshot: EffectiveSnapshot,
    knowledge: ActorKnowledge | None = None,
) -> ActorView:
    """``actor_view`` for an instance captured in an effective snapshot (022), derived portrayals included.

    The actor is composed by ``snapshot.entity``: over its selection's effective definition (a
    derived portrayal composed over its pinned base), so it follows the actor's binding, not the
    instance's own ``source`` text. ``instance`` must equal the value the snapshot captured
    (``instance_mismatch`` otherwise), so a caller cannot get a view of an instance the snapshot
    never saw. The view has exactly the fields, and the same knowledge and agent rules, as the
    index path: ``resolved_source`` is the leaf pinned revision, and sections, ancestry, notices
    and provenance stay in the snapshot. Snapshot refusals (``unknown_instance``, ``not_selected``,
    ``actor_source_mismatch``, a portrayal's composition reasons, ``MissingDefinition``) propagate.
    """
    if not isinstance(instance, EntityInstance):
        raise TypeError(f"expected an EntityInstance, not {type(instance).__name__}")
    if not isinstance(snapshot, EffectiveSnapshot):
        raise TypeError(f"expected an EffectiveSnapshot, not {type(snapshot).__name__}")
    _check_knowledge(instance.ref, knowledge)
    if snapshot.instance(instance.ref) != instance:
        raise PortrayalError(f"{instance.ref} is not the value this snapshot captured", "instance_mismatch",
                             instance.source)
    return _entity_view(snapshot.entity(instance.ref), knowledge)
