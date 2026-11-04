"""Making one generic template instance a persistent individual (sprint 024).

Three goblins are three ``EntityInstance``s of the goblin *template*. When one
becomes noteworthy, an explicit ``IndividualizationDecision`` promotes it **in
place**: it keeps its ``InstanceRef``, so everything keyed by that reference
(the evidence it holds, control assignments, later relations and image
placements) stays with it. Nothing is copied or re-keyed, and its siblings and
the species definition are untouched. The template keeps supplying inherited
traits through the instance's source, with the usual pin semantics.

Trust boundary: a decision comes only from a human, out-of-character channel
(the table owner's command or a confirmed intent, or the actor's controlling
player), never from narrator or model output. ``decided_by`` is asserted by
that trusted caller; this module checks it against current control.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from .authority import DIRECTOR, AuthorityError, controller_of
from .entity import (
    EntityDefinition, EntityError, EntityInstance, Individuality, effective_entity, segments,
)
from .identity import InstanceRef, validate_segment
from .index import DefinitionIndex
from .model import ControlAssignment
from .roster import CampaignRoster


def _invalid(message: str) -> EntityError:
    return EntityError(message, "invalid_field")


def _segment(value: object, label: str) -> None:
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise _invalid(str(error)) from None


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{label} must be nonempty text")


@dataclass(frozen=True, slots=True)
class IndividualizationDecision:
    """An explicit, human decision to make one template instance an individual."""

    campaign_id: str
    decision_id: str
    instance: InstanceRef
    name: str
    decided_by: str
    scene_id: str
    event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _segment(self.campaign_id, "campaign_id")
        _segment(self.decision_id, "decision_id")
        if type(self.instance) is not InstanceRef:
            raise _invalid("a decision names an InstanceRef")
        if self.instance.campaign_id != self.campaign_id:
            raise _invalid(f"{self.instance} is not in campaign {self.campaign_id}")
        for label in ("name", "decided_by", "scene_id"):
            _text(getattr(self, label), label)
        object.__setattr__(self, "event_ids", segments(self.event_ids, "event_ids"))


@dataclass(frozen=True, slots=True)
class Individualization:
    """The outcome: the new roster, the individual, and whether anything changed."""

    roster: CampaignRoster
    instance: EntityInstance
    applied: bool


def _records(individuality: Individuality, decision: IndividualizationDecision) -> bool:
    return (individuality.decision_id == decision.decision_id
            and individuality.given_name == decision.name
            and individuality.decided_by == decision.decided_by
            and individuality.scene_id == decision.scene_id
            and individuality.event_ids == decision.event_ids)


def _template(instance: EntityInstance, index: DefinitionIndex) -> EntityDefinition:
    if instance.source is None:
        raise EntityError(f"{instance.ref} is a campaign original and already an individual",
                          "not_template")
    definition = index.resolve(instance.source)
    if not isinstance(definition, EntityDefinition):
        raise EntityError(f"{instance.source} is a {type(definition).__name__}; convert it explicitly",
                          "not_entity_definition")
    if definition.nature != "template":
        raise EntityError(f"{instance.ref} is an instance of individual {definition.ref}, "
                          "already an individual", "not_template")
    return definition


def individualize(roster: CampaignRoster, decision: IndividualizationDecision,
                  index: DefinitionIndex,
                  controls: Iterable[ControlAssignment]) -> Individualization:
    """Promote one generic template instance in place; replaying a decision is a no-op.

    ``controls`` is required, as in every authority API: an omitted argument
    must never read as "nobody controls this actor". Pass ``()`` only when the
    campaign truly has no assignments. An existing assignment keeps its
    authority even if ``CampaignRoster.check_controls`` reports it (for
    example ``not_agent`` after a source change), because control is never
    silently revoked.

    A replay (the same decision already recorded) returns ``applied=False``
    without re-validating the instance against the current index; call
    ``effective_entity`` to check that it still resolves.
    """
    if not isinstance(roster, CampaignRoster):
        raise TypeError(f"expected a CampaignRoster, not {type(roster).__name__}")
    if not isinstance(decision, IndividualizationDecision):
        raise TypeError(f"expected an IndividualizationDecision, not {type(decision).__name__}")
    if not isinstance(index, DefinitionIndex):
        raise TypeError(f"expected a DefinitionIndex, not {type(index).__name__}")
    controls = tuple(controls)
    for control in controls:
        if not isinstance(control, ControlAssignment):
            raise TypeError(f"expected a ControlAssignment, not {type(control).__name__}")
    if decision.campaign_id != roster.campaign_id:
        raise ValueError(f"decision {decision.decision_id} is for campaign {decision.campaign_id}, "
                         f"not {roster.campaign_id}")
    for other in roster.instances:
        if (other.ref != decision.instance and other.individuality is not None
                and other.individuality.decision_id == decision.decision_id):
            raise EntityError(f"decision {decision.decision_id} already individualized {other.ref}",
                              "decision_conflict")
    instance = roster.instance(decision.instance)
    if instance.individuality is not None:
        if _records(instance.individuality, decision):
            return Individualization(roster, instance, applied=False)
        if instance.individuality.decision_id == decision.decision_id:
            raise EntityError(f"decision {decision.decision_id} was recorded with different content",
                              "decision_conflict")
        raise EntityError(f"{instance.ref} was already individualized by decision "
                          f"{instance.individuality.decision_id}", "already_individualized")
    template = _template(instance, index)
    allowed = controller_of(roster.campaign_id, instance.ref.instance_id, controls) or DIRECTOR
    if decision.decided_by != allowed:
        raise AuthorityError(f"only {allowed!r} may individualize {instance.ref}, "
                             f"not {decision.decided_by!r}")
    individual = replace(instance, local_name=decision.name, individuality=Individuality(
        decision.decision_id, decision.name, decision.decided_by, decision.scene_id, template.ref,
        decision.event_ids))
    effective_entity(individual, index)
    others = tuple(other for other in roster.instances if other.ref != instance.ref)
    return Individualization(CampaignRoster(roster.campaign_id, (*others, individual)), individual,
                             applied=True)


def is_generic(instance: EntityInstance, index: DefinitionIndex) -> bool:
    """A not-yet-individualized instance of a template; ``MissingDefinition`` propagates.

    This only classifies the instance. It does not run ``effective_entity``,
    so it can answer ``True`` for an instance that still fails resolution
    (for example ``added_shadows_trait`` after a head move) and that
    ``individualize`` will refuse. Check ``effective_entity`` before offering
    promotion.
    """
    if not isinstance(instance, EntityInstance):
        raise TypeError(f"expected an EntityInstance, not {type(instance).__name__}")
    if not isinstance(index, DefinitionIndex):
        raise TypeError(f"expected a DefinitionIndex, not {type(index).__name__}")
    if instance.individuality is not None or instance.source is None:
        return False
    definition = index.resolve(instance.source)
    return isinstance(definition, EntityDefinition) and definition.nature == "template"


__all__ = ["Individualization", "IndividualizationDecision", "individualize", "is_generic"]
