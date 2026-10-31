"""Source definitions, campaign instances and their effective capabilities.

An ``EntityDefinition`` is one revision of reusable authored traits; an
``EntityInstance`` is one individual in one campaign, with capabilities it
gained there, explicit trait replacements and campaign state. Neither is a
Character/Item/Creature subtype: ``kind`` is an identity namespace and
capabilities are facets. Control (PC/NPC) is a separate ``ControlAssignment``.

``effective_entity`` validates an instance against the exact definition its
source resolves to and returns a disposable view. Validation runs at
resolution time, so a changed head can make an unpinned instance invalid; that
is a named error, never a silent repair, and pinning is the remedy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import ClassVar, Literal

from .facets import Agent, Communicative, Durable, Facet, FacetBundle, FacetError, FacetSet, Portable
from .identity import InstanceRef, SourceRef
from .index import DefinitionIndex
from .json_value import JsonObject, JsonValueError, freeze_object
from .model import CampaignActor, CharacterDefinition

Nature = Literal["individual", "template"]
Origin = Literal["trait", "replaced", "added"]
NATURES = frozenset({"individual", "template"})
#: Keys an ``extras`` mapping may never use: the envelope owns the address and schema.
ADDRESS_KEYS = frozenset({"ref", "address", "source_id", "campaign_id", "schema",
                          "kind", "entity_id", "instance_id", "revision"})


class EntityError(ValueError):
    """An invalid definition, instance or composition; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str) -> EntityError:
    return EntityError(message, "invalid_field")


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{label} must be nonempty text")


def _extras(value: object, reserved: frozenset[str]) -> JsonObject:
    try:
        extras = freeze_object(value)
    except JsonValueError as error:
        raise _invalid(f"extras: {error}") from None
    clashes = sorted(set(extras) & (reserved | ADDRESS_KEYS))
    if clashes:
        raise _invalid(f"extras may not use field or address keys: {clashes}")
    return extras


def _bundle(value: object, label: str) -> FacetBundle:
    if not isinstance(value, FacetBundle):
        raise _invalid(f"{label} must be a FacetBundle, not {type(value).__name__}")
    return value if type(value) is FacetBundle else FacetBundle(value.facets)


@dataclass(frozen=True, slots=True)
class EntityDefinition:
    """One revision of a reusable authored entity: an individual or a template."""

    ref: SourceRef
    name: str
    nature: Nature
    traits: FacetSet
    description: str = ""
    extras: JsonObject = JsonObject()

    FIELDS: ClassVar[frozenset[str]] = frozenset({"name", "nature", "traits", "description"})

    def __post_init__(self) -> None:
        if not isinstance(self.ref, SourceRef) or not self.ref.pinned:
            raise _invalid("a definition is one revision: its ref must be a pinned SourceRef")
        _text(self.name, "name")
        if not isinstance(self.nature, str) or self.nature not in NATURES:
            raise _invalid(f"nature must be one of {sorted(NATURES)}: {self.nature!r}")
        if not isinstance(self.traits, FacetSet):
            raise _invalid("traits must be a FacetSet")
        if not isinstance(self.description, str):
            raise _invalid("description must be text")
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))


@dataclass(frozen=True, slots=True)
class Integrity:
    """Current integrity of a durable entity; campaign state, not a trait."""

    facet: ClassVar[str] = "durable"
    current: int

    def __post_init__(self) -> None:
        if not isinstance(self.current, int) or isinstance(self.current, bool) or self.current < 0:
            raise _invalid(f"integrity must be a whole number >= 0: {self.current!r}")


StateValue = Integrity
STATE_TYPES: dict[str, type] = {"integrity": Integrity}


@dataclass(frozen=True, slots=True)
class EntityInstance:
    """One individual in one campaign, whoever controls it."""

    ref: InstanceRef
    source: SourceRef | None = None
    local_name: str | None = None
    added: FacetBundle = FacetBundle()
    replaced: FacetBundle = FacetBundle()
    state: tuple[StateValue, ...] = ()
    extras: JsonObject = JsonObject()

    FIELDS: ClassVar[frozenset[str]] = frozenset({"source", "local_name", "added", "replaced", "state"})

    def __post_init__(self) -> None:
        if not isinstance(self.ref, InstanceRef):
            raise _invalid("an instance ref must be an InstanceRef")
        if self.source is not None and not isinstance(self.source, SourceRef):
            raise _invalid("an instance source must be a SourceRef or None")
        if self.local_name is not None:
            _text(self.local_name, "local_name")
        elif self.source is None:
            raise EntityError("a campaign-original entity needs a local_name",
                              "campaign_original_needs_name")
        added, replaced = _bundle(self.added, "added"), _bundle(self.replaced, "replaced")
        both = sorted(set(added.names) & set(replaced.names))
        if both:
            raise EntityError(f"facets both added and replaced: {both}", "added_shadows_trait")
        object.__setattr__(self, "added", added)
        object.__setattr__(self, "replaced", replaced)
        object.__setattr__(self, "state", _state(self.state))
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))


def _state(values: object) -> tuple[StateValue, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise _invalid("state must be a collection of state values")
    names = {cls: name for name, cls in STATE_TYPES.items()}
    by_type: dict[str, StateValue] = {}
    for value in values:
        name = names.get(type(value))
        if name is None:
            raise _invalid(f"not a state value: {value!r}")
        if name in by_type:
            raise EntityError(f"two {name} values", "duplicate_state")
        by_type[name] = value
    return tuple(by_type[name] for name in sorted(by_type))


@dataclass(frozen=True, slots=True)
class EffectiveEntity:
    """Disposable validated view of an instance; never a canonical record."""

    ref: InstanceRef
    resolved_source: SourceRef | None
    name: str
    facets: FacetSet
    origins: tuple[tuple[str, Origin], ...]
    state: tuple[StateValue, ...]
    description: str = ""


def _definition(instance: EntityInstance, index: DefinitionIndex) -> EntityDefinition | None:
    if instance.source is None:
        return None
    definition = index.resolve(instance.source)
    if not isinstance(definition, EntityDefinition):
        raise EntityError(f"{instance.source} is a {type(definition).__name__}; convert it explicitly",
                          "not_entity_definition")
    if definition.nature == "individual" and instance.ref.kind != definition.ref.kind:
        raise EntityError(f"an instance of individual {definition.ref} must have kind "
                          f"{definition.ref.kind!r}, not {instance.ref.kind!r}", "kind_mismatch")
    return definition


def _compose(traits: Iterable[Facet], instance: EntityInstance) -> tuple[FacetSet, tuple]:
    chosen: dict[str, tuple[Facet, Origin]] = {facet.name: (facet, "trait") for facet in traits}
    for facet in instance.replaced:
        if facet.name not in chosen:
            raise EntityError(f"replaced facet {facet.name!r} is not a trait", "replaced_without_trait")
        chosen[facet.name] = (facet, "replaced")
    for facet in instance.added:
        if facet.name in chosen:
            raise EntityError(f"added facet {facet.name!r} is already a trait; replace it explicitly",
                              "added_shadows_trait")
        chosen[facet.name] = (facet, "added")
    try:
        facets = FacetSet(tuple(facet for facet, _ in chosen.values()))
    except FacetError as error:
        raise EntityError(str(error), error.reason) from None
    return facets, tuple((name, chosen[name][1]) for name in sorted(chosen))


def effective_entity(instance: EntityInstance, index: DefinitionIndex) -> EffectiveEntity:
    """Validate an instance against its exact source revision and compose its facets."""
    if not isinstance(instance, EntityInstance):
        raise TypeError(f"expected an EntityInstance, not {type(instance).__name__}")
    definition = _definition(instance, index)
    facets, origins = _compose(definition.traits if definition else (), instance)
    for value in instance.state:
        if value.facet not in facets:
            raise EntityError(f"{type(value).__name__} needs an effective {value.facet} facet",
                              "state_without_facet")
        durable = facets.get(Durable)
        if value.current > durable.max_integrity:
            raise EntityError(f"integrity {value.current} exceeds {durable.max_integrity}", "invalid_state")
    return EffectiveEntity(
        ref=instance.ref,
        resolved_source=definition.ref if definition else None,
        name=instance.local_name or definition.name,
        facets=facets,
        origins=origins,
        state=instance.state,
        description=definition.description if definition else "",
    )


# --- capabilities -------------------------------------------------------------

def is_agent(entity: EffectiveEntity) -> bool:
    return entity.facets.has(Agent)


def can_be_carried(entity: EffectiveEntity) -> bool:
    return entity.facets.has(Portable)


def can_be_damaged(entity: EffectiveEntity) -> bool:
    return entity.facets.has(Durable)


def can_be_heard(entity: EffectiveEntity) -> bool:
    """Perceivable expression on any channel (speech, telepathy, sign or writing)."""
    voice = entity.facets.get(Communicative)
    return bool(voice and voice.speaks)


def can_be_addressed(entity: EffectiveEntity) -> bool:
    voice = entity.facets.get(Communicative)
    return bool(voice and voice.understands)


def integrity(entity: EffectiveEntity) -> int | None:
    """Current integrity, full when undamaged; ``None`` for a non-durable entity."""
    durable = entity.facets.get(Durable)
    if durable is None:
        return None
    return next((value.current for value in entity.state if isinstance(value, Integrity)),
                durable.max_integrity)


def propose_damage(instance: EntityInstance, amount: int, index: DefinitionIndex) -> EntityInstance:
    """The instance as it would be after damage: a proposal, not a committed event."""
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
        raise _invalid(f"damage must be a whole number >= 1: {amount!r}")
    current = integrity(effective_entity(instance, index))
    if current is None:
        raise EntityError("only a durable entity can be damaged", "state_without_facet")
    others = tuple(value for value in instance.state if not isinstance(value, Integrity))
    return replace(instance, state=(*others, Integrity(max(0, current - amount))))


# --- legacy first-slice bridge -----------------------------------------------

def entity_from_character(definition: CharacterDefinition) -> EntityDefinition:
    """A Wave 1 character as an entity that can act; physicality is not invented."""
    return EntityDefinition(definition.ref, definition.name, "individual", FacetSet([Agent()]),
                            definition.description)


def instance_from_actor(actor: CampaignActor) -> EntityInstance:
    """A Wave 1 actor as an instance with the same segment, so its control still applies.

    A sourced actor's agency comes from its (converted) definition; a campaign
    original has no definition, so it is given ``Agent`` here.
    """
    added = FacetBundle([Agent()]) if actor.source is None else FacetBundle()
    return EntityInstance(actor.ref, actor.source, actor.local_name, added)
