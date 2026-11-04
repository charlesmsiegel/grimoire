"""Fictional abilities: what a powers-capable entity may and may not do (sprint 025).

An ability is authored, human-editable fictional fact, not a rules module. It
lists permitted effects (each with open-vocabulary tags, prose range and a
reliability), limits that exclude tags outright, prerequisites and costs.
Nothing here is numeric or mechanical, and nothing here decides an outcome:
``ability_use`` assesses a labelled attempt against these values, and
fiction-first adjudication (029) consumes that assessment.

``Abilities`` is the facet that carries them. It requires nothing, so an
object may describe powers, but only an agent can attempt one. Unknown
authored fields are kept verbatim as ``extras`` and grant and restrict
nothing here; the assessment fails closed on them (``needs_upgrade``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal

from .facet_errors import FacetError
from .identity import validate_segment
from .json_value import JsonObject, JsonValueError, freeze_object

Certainty = Literal["reliable", "uncertain"]
CERTAINTIES = frozenset({"reliable", "uncertain"})
PROSE_MAX = 4000
MAX_ABILITIES = 64
MAX_PARTS = 64
MAX_TAGS = 32


def _invalid(message: str) -> FacetError:
    return FacetError(message, "invalid_field")


def _segment(value: object, label: str) -> None:
    if type(value) is not str:  # a str subclass could redefine equality
        raise _invalid(f"{label} must be plain text: {value!r}")
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise _invalid(str(error)) from None


def _prose(value: object, label: str, *, blank: bool = False) -> None:
    if type(value) is not str or (not blank and not value.strip()):
        raise _invalid(f"{label} must be {'' if blank else 'nonempty '}plain text")
    if len(value) > PROSE_MAX:
        raise _invalid(f"{label} is longer than {PROSE_MAX} characters")


def _collection(value: object, label: str) -> list:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable) or isinstance(value, Mapping):
        raise _invalid(f"{label} must be a collection: {value!r}")
    return list(value)


def _names(value: object, label: str, *, nonempty: bool = False, limit: int = MAX_TAGS) -> frozenset[str]:
    """A set of id segments; constructors deduplicate (the codec refuses duplicates)."""
    entries = _collection(value, label)
    for name in entries:
        _segment(name, f"{label} entry")
    names = frozenset(entries)
    if nonempty and not names:
        raise _invalid(f"{label} needs at least one entry")
    if len(names) > limit:
        raise _invalid(f"{label} has more than {limit} entries")
    return names


def _extras(value: object, fields: frozenset[str]) -> JsonObject:
    try:
        extras = freeze_object(value)
    except JsonValueError as error:
        raise _invalid(f"extras: {error}") from None
    clashes = sorted(set(extras) & fields)
    if clashes:
        raise _invalid(f"extras may not use field names: {clashes}")
    return extras


def _parts(value: object, cls: type, label: str) -> tuple:
    parts = _collection(value, label)
    for part in parts:
        if type(part) is not cls:  # a subclass could change what a part means
            raise _invalid(f"{label} must hold {cls.__name__} values, not {part!r}")
    if len(parts) > MAX_PARTS:
        raise _invalid(f"{label} has more than {MAX_PARTS} entries")
    return tuple(sorted(parts, key=lambda part: part.id))


@dataclass(frozen=True, slots=True)
class Effect:
    """Something the ability permits, described by tags and prose."""

    id: str
    text: str
    tags: frozenset[str]
    range: str | None = None
    certainty: Certainty = "reliable"
    extras: JsonObject = JsonObject()

    FIELDS: ClassVar[frozenset[str]] = frozenset({"id", "text", "tags", "range", "certainty"})

    def __post_init__(self) -> None:
        _segment(self.id, "effect id")
        _prose(self.text, "effect text")
        object.__setattr__(self, "tags", _names(self.tags, "effect tags", nonempty=True))
        if self.range is not None:
            _prose(self.range, "effect range")
        if type(self.certainty) is not str or self.certainty not in CERTAINTIES:
            raise _invalid(f"certainty must be one of {sorted(CERTAINTIES)}: {self.certainty!r}")
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))


@dataclass(frozen=True, slots=True)
class Limit:
    """What the ability never does: tags it excludes outright."""

    id: str
    text: str
    excludes: frozenset[str]
    extras: JsonObject = JsonObject()

    FIELDS: ClassVar[frozenset[str]] = frozenset({"id", "text", "excludes"})

    def __post_init__(self) -> None:
        _segment(self.id, "limit id")
        _prose(self.text, "limit text")
        object.__setattr__(self, "excludes", _names(self.excludes, "limit excludes", nonempty=True))
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))


@dataclass(frozen=True, slots=True)
class Cost:
    """A price a use would pay; ``applies_to`` names effects, empty meaning every use."""

    id: str
    text: str
    applies_to: frozenset[str] = frozenset()
    extras: JsonObject = JsonObject()

    FIELDS: ClassVar[frozenset[str]] = frozenset({"id", "text", "applies_to"})

    def __post_init__(self) -> None:
        _segment(self.id, "cost id")
        _prose(self.text, "cost text")
        object.__setattr__(self, "applies_to", _names(self.applies_to, "cost applies_to", limit=MAX_PARTS))
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))


@dataclass(frozen=True, slots=True)
class Prerequisite:
    """A condition a use needs; ``applies_to`` names effects, empty meaning every use."""

    id: str
    text: str
    applies_to: frozenset[str] = frozenset()
    extras: JsonObject = JsonObject()

    FIELDS: ClassVar[frozenset[str]] = frozenset({"id", "text", "applies_to"})

    def __post_init__(self) -> None:
        _segment(self.id, "prerequisite id")
        _prose(self.text, "prerequisite text")
        object.__setattr__(self, "applies_to",
                           _names(self.applies_to, "prerequisite applies_to", limit=MAX_PARTS))
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))


@dataclass(frozen=True, slots=True)
class Ability:
    """One authored power, technology, spell or extraordinary trait."""

    id: str
    name: str
    effects: tuple[Effect, ...]
    limits: tuple[Limit, ...] = ()
    costs: tuple[Cost, ...] = ()
    prerequisites: tuple[Prerequisite, ...] = ()
    description: str = ""
    extras: JsonObject = JsonObject()

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"id", "name", "effects", "limits", "costs", "prerequisites", "description"})

    def __post_init__(self) -> None:
        _segment(self.id, "ability id")
        _prose(self.name, "ability name")
        _prose(self.description, "ability description", blank=True)
        for label, cls in (("effects", Effect), ("limits", Limit), ("costs", Cost),
                           ("prerequisites", Prerequisite)):
            object.__setattr__(self, label, _parts(getattr(self, label), cls, f"ability {label}"))
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))
        if not self.effects:
            raise FacetError(f"ability {self.id!r} permits no effect", "empty_ability")
        seen: set[str] = set()
        for part in (*self.effects, *self.limits, *self.costs, *self.prerequisites):
            if part.id in seen:
                raise FacetError(f"ability {self.id!r} uses the id {part.id!r} twice",
                                 "duplicate_ability_part")
            seen.add(part.id)
        effect_ids = {effect.id for effect in self.effects}
        for part in (*self.costs, *self.prerequisites):
            missing = sorted(part.applies_to - effect_ids)
            if missing:
                raise FacetError(f"{part.id!r} applies to unknown effects {missing}", "unknown_applies_to")
        for limit in self.limits:
            for effect in self.effects:
                both = sorted(limit.excludes & effect.tags)
                if both:
                    raise FacetError(f"limit {limit.id!r} excludes {both}, which effect {effect.id!r} "
                                     "declares", "contradictory_ability")

    def effect(self, effect_id: str) -> Effect | None:
        return next((effect for effect in self.effects if effect.id == effect_id), None)

    def prerequisite(self, prerequisite_id: str) -> Prerequisite | None:
        return next((p for p in self.prerequisites if p.id == prerequisite_id), None)


@dataclass(frozen=True, slots=True)
class Abilities:
    """The facet listing an entity's abilities; an empty facet means no powers."""

    name: ClassVar[str] = "abilities"
    requires: ClassVar[frozenset[str]] = frozenset()
    abilities: tuple[Ability, ...] = ()
    extras: JsonObject = JsonObject()

    #: ``facet`` is the codec's discriminator, so facet extras may not use it either.
    FIELDS: ClassVar[frozenset[str]] = frozenset({"abilities", "facet"})

    def __post_init__(self) -> None:
        abilities = _collection(self.abilities, "abilities")
        for ability in abilities:
            if type(ability) is not Ability:
                raise _invalid(f"abilities must hold Ability values, not {ability!r}")
        if len(abilities) > MAX_ABILITIES:
            raise _invalid(f"more than {MAX_ABILITIES} abilities")
        ids = [ability.id for ability in abilities]
        if len(set(ids)) != len(ids):
            raise FacetError("an ability id appears twice", "duplicate_ability")
        object.__setattr__(self, "abilities", tuple(sorted(abilities, key=lambda a: a.id)))
        object.__setattr__(self, "extras", _extras(self.extras, self.FIELDS))

    def by_id(self, ability_id: str) -> Ability | None:
        return next((ability for ability in self.abilities if ability.id == ability_id), None)


__all__ = [
    "CERTAINTIES", "MAX_ABILITIES", "MAX_PARTS", "MAX_TAGS", "PROSE_MAX",
    "Abilities", "Ability", "Cost", "Effect", "Limit", "Prerequisite",
]
