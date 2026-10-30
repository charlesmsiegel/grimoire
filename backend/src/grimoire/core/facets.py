"""Typed, composable entity capabilities.

A facet is an immutable capability contract with its own invariants, not a
freeform flag. Entities combine facets instead of inheriting from exclusive
Character/Item/Creature classes, so a sentient sword is simply physical,
portable, durable, an agent and communicative.

Facets never confer control: PC/NPC is a ``ControlAssignment``, and no
authority code reads facets. Reserved names are extra defence, not the
guarantee. A facet from a newer version is kept as an opaque ``UnknownFacet``
that grants no capability and satisfies no requirement.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import ClassVar, Literal, Union

from .identity import validate_segment
from .json_value import JsonObject, JsonValueError, freeze_object

Size = Literal["tiny", "small", "medium", "large", "huge"]
Bulk = Literal["light", "normal", "heavy"]
Channel = Literal["speech", "telepathy", "sign", "writing"]

SIZES = frozenset({"tiny", "small", "medium", "large", "huge"})
BULKS = frozenset({"light", "normal", "heavy"})
CHANNELS = frozenset({"speech", "telepathy", "sign", "writing"})
RESERVED_FACET_NAMES = frozenset({"control", "controller", "pc", "npc", "player", "director"})


class FacetError(ValueError):
    """An invalid facet or facet combination; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _choice(value: object, options: frozenset[str], label: str) -> None:
    if not isinstance(value, str) or value not in options:
        raise FacetError(f"{label} must be one of {sorted(options)}: {value!r}", "invalid_field")


def _channels(value: object, label: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise FacetError(f"{label} must be a collection of channels: {value!r}", "invalid_field")
    channels = frozenset(value)
    for channel in channels:
        _choice(channel, CHANNELS, f"{label} channel")
    return channels


@dataclass(frozen=True, slots=True)
class Physical:
    """Occupies space and can be touched."""

    name: ClassVar[str] = "physical"
    requires: ClassVar[frozenset[str]] = frozenset()
    material: str | None = None
    size: Size | None = None

    def __post_init__(self) -> None:
        if self.material is not None and (not isinstance(self.material, str) or not self.material.strip()):
            raise FacetError("material must be nonempty text or None", "invalid_field")
        if self.size is not None:
            _choice(self.size, SIZES, "size")


@dataclass(frozen=True, slots=True)
class Portable:
    """Can be picked up and carried; who carries it is a relation (017)."""

    name: ClassVar[str] = "portable"
    requires: ClassVar[frozenset[str]] = frozenset({"physical"})
    bulk: Bulk = "normal"

    def __post_init__(self) -> None:
        _choice(self.bulk, BULKS, "bulk")


@dataclass(frozen=True, slots=True)
class Durable:
    """Can be damaged. Current integrity is campaign state, not this trait."""

    name: ClassVar[str] = "durable"
    requires: ClassVar[frozenset[str]] = frozenset({"physical"})
    max_integrity: int

    def __post_init__(self) -> None:
        if not _whole(self.max_integrity) or self.max_integrity < 1:
            raise FacetError(f"max_integrity must be a whole number >= 1: {self.max_integrity!r}",
                             "invalid_field")


@dataclass(frozen=True, slots=True)
class Biological:
    """A living body. Species membership is a relation (017/024), not a field."""

    name: ClassVar[str] = "biological"
    requires: ClassVar[frozenset[str]] = frozenset({"physical"})


@dataclass(frozen=True, slots=True)
class Agent:
    """Can hold intentions and act; the only facet that makes control possible."""

    name: ClassVar[str] = "agent"
    requires: ClassVar[frozenset[str]] = frozenset()


@dataclass(frozen=True, slots=True)
class Communicative:
    """Expresses itself (``speaks``: can be heard) and/or can be addressed."""

    name: ClassVar[str] = "communicative"
    requires: ClassVar[frozenset[str]] = frozenset({"agent"})
    speaks: frozenset[Channel] = frozenset()
    understands: frozenset[Channel] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "speaks", _channels(self.speaks, "speaks"))
        object.__setattr__(self, "understands", _channels(self.understands, "understands"))
        if not self.speaks and not self.understands:
            raise FacetError("a communicative facet needs a channel to speak or understand",
                             "invalid_field")


@dataclass(frozen=True, slots=True)
class Spatial:
    """A place other entities can be in; what is in it is a relation (017)."""

    name: ClassVar[str] = "spatial"
    requires: ClassVar[frozenset[str]] = frozenset()


KnownFacet = Union[Physical, Portable, Durable, Biological, Agent, Communicative, Spatial]
KNOWN_FACETS: dict[str, type] = {
    cls.name: cls for cls in (Physical, Portable, Durable, Biological, Agent, Communicative, Spatial)
}


def _check_name(name: object) -> None:
    try:
        validate_segment(name, "facet name")
    except ValueError as error:
        raise FacetError(str(error), "invalid_field") from None
    if name in RESERVED_FACET_NAMES:
        raise FacetError(f"{name!r} is reserved: control is never a capability", "reserved_name")


@dataclass(frozen=True, slots=True)
class UnknownFacet:
    """A facet this version does not know, kept verbatim and granting nothing."""

    requires: ClassVar[frozenset[str]] = frozenset()
    name: str
    data: JsonObject = JsonObject()

    def __post_init__(self) -> None:
        _check_name(self.name)
        if self.name in KNOWN_FACETS:
            raise FacetError(f"{self.name!r} is a known facet; decode it as one", "known_name_as_unknown")
        try:
            data = freeze_object(self.data)
        except JsonValueError as error:
            raise FacetError(f"unknown facet data: {error}", "invalid_field") from None
        if "facet" in data:
            raise FacetError("unknown facet data may not use the 'facet' key", "invalid_field")
        object.__setattr__(self, "data", data)


Facet = Union[KnownFacet, UnknownFacet]
_FACET_TYPES = (*KNOWN_FACETS.values(), UnknownFacet)


def _normalize_bundle(bundle: FacetBundle) -> None:
    """Shared by ``FacetBundle`` and ``FacetSet``; no ``super()`` in slots dataclasses (3.11)."""
    if isinstance(bundle.facets, (str, bytes)) or not isinstance(bundle.facets, Iterable):
        raise FacetError("facets must be a collection of facet values", "invalid_field")
    named: dict[str, Facet] = {}
    for facet in bundle.facets:
        if not isinstance(facet, _FACET_TYPES):
            raise FacetError(f"not a facet: {facet!r}", "invalid_field")
        if facet.name in named:
            raise FacetError(f"facet {facet.name!r} appears twice", "duplicate_facet")
        named[facet.name] = facet
    object.__setattr__(bundle, "facets", tuple(named[name] for name in sorted(named)))


@dataclass(frozen=True, slots=True)
class FacetBundle:
    """Canonical facets, one per name, with no requirement check (additions, replacements)."""

    facets: tuple[Facet, ...] = ()

    def __post_init__(self) -> None:
        _normalize_bundle(self)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(facet.name for facet in self.facets)

    def by_name(self, name: str) -> Facet | None:
        return next((facet for facet in self.facets if facet.name == name), None)

    def get(self, facet_type: type) -> Facet | None:
        return next((facet for facet in self.facets if type(facet) is facet_type), None)

    def has(self, facet_type: type) -> bool:
        return self.get(facet_type) is not None

    def __contains__(self, item: object) -> bool:
        return item in self.names if isinstance(item, str) else item in self.facets

    def __iter__(self) -> Iterator[Facet]:
        return iter(self.facets)

    def __len__(self) -> int:
        return len(self.facets)


@dataclass(frozen=True, slots=True)
class FacetSet(FacetBundle):
    """A bundle whose known facets' requirements are all present as known facets."""

    def __post_init__(self) -> None:
        _normalize_bundle(self)
        known = {facet.name for facet in self.facets if not isinstance(facet, UnknownFacet)}
        for facet in self.facets:
            missing = facet.requires - known
            if missing:
                raise FacetError(f"{facet.name!r} requires {sorted(missing)}", "missing_requirement")
