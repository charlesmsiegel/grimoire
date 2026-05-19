"""Core types for the transient-state subsystem.

See docs/superpowers/specs/2026-05-19-transient-state-design.md.

Public surface:
    EntityKind, ObserverKind, Provenance        - enums
    TransientValue                              - a row of state
    TransientUpdateProposal                     - extractor candidate
    TransientConflict                           - losing-write pairing
    DecayHint                                   - per-field lifetime hint
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class EntityKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    FACTION = "faction"
    SCENE = "scene"


class ObserverKind(StrEnum):
    AUTHOR = "author"
    PC_OWNER = "pc_owner"
    OTHER_PC = "other_pc"
    AUDIENCE = "audience"


class _ProvenanceMechanics:
    """Parametric mechanics:<module-id> provenance value.

    Behaves like Provenance for equality and string conversion but carries
    the module id alongside.
    """

    __slots__ = ("module_id",)

    def __init__(self, module_id: str) -> None:
        self.module_id = module_id

    @property
    def value(self) -> str:
        return f"mechanics:{self.module_id}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _ProvenanceMechanics):
            return self.module_id == other.module_id
        if isinstance(other, str):
            return other == self.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"Provenance.mechanics({self.module_id!r})"

    def __str__(self) -> str:
        return self.value


class Provenance(StrEnum):
    EXTRACTOR_AUTO = "extractor:auto"
    EXTRACTOR_REVIEWED = "extractor:reviewed"
    USER_HUD = "user:hud"
    USER_EDIT = "user:edit"

    @classmethod
    def mechanics(cls, module_id: str) -> _ProvenanceMechanics:
        return _ProvenanceMechanics(module_id)

    @classmethod
    def parse(cls, raw: str) -> Provenance | _ProvenanceMechanics:
        if raw.startswith("mechanics:"):
            return _ProvenanceMechanics(raw.split(":", 1)[1])
        return cls(raw)


@dataclass(frozen=True, slots=True)
class TransientValue:
    id: int
    entity_id: str
    field: str
    value: Any
    provenance: Provenance | _ProvenanceMechanics
    confidence: float
    source_post_id: str | None
    created_at: datetime
    expires_at: datetime | None
    in_game_at: datetime | None
    decayed: bool


@dataclass(frozen=True, slots=True)
class DecayHint:
    posts: int | None = None
    in_game_seconds: int | None = None
    scene_scope: bool = False
    reinforce_extends: bool = False
    promote_to_fact: bool = False


@dataclass(frozen=True, slots=True)
class TransientUpdateProposal:
    entity_kind: EntityKind
    entity_id: str
    field: str
    value: Any
    confidence: float
    evidence: str
    proposed_decay_override: DecayHint | None = None


@dataclass(frozen=True, slots=True)
class TransientConflict:
    current: TransientValue
    losing: TransientValue
