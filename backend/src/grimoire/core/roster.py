"""One campaign's instances, bridging ``InstanceRef`` and segment-keyed control.

Authority and intent name an actor by its campaign-local segment
(``ControlAssignment.actor_id``), while an instance's identity is the full
``campaign:<c>/<kind>/<id>`` reference. Instance ids are unique within a
campaign across kinds, so the segment is unambiguous; this roster is the one
explicit conversion and refuses anything "close enough" (another campaign, a
different kind, reference text where a segment belongs).

Facets never grant control. ``assign_control`` only validates a *new*
assignment (the actor must be an effective agent); an existing assignment keeps
working through the authority policy, and later problems are reported by
``check_controls`` rather than revoked here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .authority import AuthorityError
from .entity import EntityError, EntityInstance, effective_entity, is_agent
from .identity import IdentityConflict, InstanceRef, validate_segment
from .index import DefinitionIndex, MissingDefinition
from .model import ControlAssignment


class UnknownInstance(LookupError):
    """No instance in this campaign has exactly this reference."""

    def __init__(self, ref_text: str) -> None:
        super().__init__(f"no such instance in this campaign: {ref_text}")
        self.ref_text = ref_text

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.ref_text,))


@dataclass(frozen=True, slots=True)
class ControlProblem:
    """Why an existing assignment no longer fits; reported, never auto-revoked."""

    assignment: ControlAssignment
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CampaignRoster:
    """Immutable index of one campaign's instances, keyed by unique segment."""

    campaign_id: str
    instances: tuple[EntityInstance, ...]
    _by_id: dict[str, EntityInstance] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        by_id: dict[str, EntityInstance] = {}
        for instance in self.instances:
            if not isinstance(instance, EntityInstance):
                raise ValueError(f"a roster holds EntityInstance values, not {type(instance).__name__}")
            if instance.ref.campaign_id != self.campaign_id:
                raise ValueError(f"{instance.ref} is not in campaign {self.campaign_id}")
            if instance.ref.instance_id in by_id:
                raise IdentityConflict(
                    f"instance id {instance.ref.instance_id!r} is used by both "
                    f"{by_id[instance.ref.instance_id].ref} and {instance.ref}; "
                    "ids are unique within a campaign across kinds")
            by_id[instance.ref.instance_id] = instance
        object.__setattr__(self, "instances", tuple(by_id[key] for key in sorted(by_id)))
        object.__setattr__(self, "_by_id", by_id)

    def ref_for(self, actor_id: str) -> InstanceRef:
        """The instance a campaign-local segment names; reference text is refused."""
        try:
            validate_segment(actor_id, "actor_id")
        except ValueError as error:
            raise ValueError(f"{error}; convert reference text with segment_for") from None
        instance = self._by_id.get(actor_id)
        if instance is None:
            raise UnknownInstance(actor_id)
        return instance.ref

    def segment_for(self, ref: InstanceRef | str) -> str:
        """The authority segment for exactly this instance reference."""
        return self.instance(ref).ref.instance_id

    def instance(self, ref: InstanceRef | str) -> EntityInstance:
        if isinstance(ref, str):
            ref = InstanceRef.parse(ref)
        if not isinstance(ref, InstanceRef):
            raise TypeError(f"expected an instance reference, not {type(ref).__name__}")
        found = self._by_id.get(ref.instance_id) if ref.campaign_id == self.campaign_id else None
        if found is None or found.ref != ref:
            raise UnknownInstance(str(ref))
        return found

    def assign_control(self, actor: InstanceRef, controller_id: str,
                       index: DefinitionIndex) -> ControlAssignment:
        """A new assignment, only to an effective agent; resolution errors propagate."""
        if not isinstance(actor, InstanceRef):
            raise TypeError(f"only a campaign instance can be controlled, not {actor!r}")
        entity = effective_entity(self.instance(actor), index)
        if not is_agent(entity):
            raise AuthorityError(f"{actor} is not an agent and cannot be controlled")
        return ControlAssignment(self.campaign_id, actor.instance_id, controller_id)

    def check_controls(self, controls: Iterable[ControlAssignment],
                       index: DefinitionIndex) -> tuple[ControlProblem, ...]:
        problems = []
        for control in controls:
            if not isinstance(control, ControlAssignment):
                raise TypeError(f"expected a ControlAssignment, not {type(control).__name__}")
            if control.campaign_id != self.campaign_id:
                problems.append(ControlProblem(control, "other_campaign"))
                continue
            instance = self._by_id.get(control.actor_id)
            if instance is None:
                problems.append(ControlProblem(control, "unknown_instance"))
                continue
            try:
                entity = effective_entity(instance, index)
            except (MissingDefinition, EntityError) as error:
                problems.append(ControlProblem(control, "unresolved", error.reason))
                continue
            if not is_agent(entity):
                problems.append(ControlProblem(control, "not_agent"))
        return tuple(problems)
