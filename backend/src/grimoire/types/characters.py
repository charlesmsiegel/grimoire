"""Character-specific types layered over Setting storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .common import CharacterRef, Json
from .state import CharacterState


class CharacterRole(StrEnum):
    PC = "pc"
    MAJOR_NPC = "major_npc"
    MINOR_NPC = "minor_npc"
    ENSEMBLE = "ensemble"
    NAMED_FLAVOR = "named_flavor"


@dataclass
class VoiceAnchor:
    summary: str = ""
    register: str = ""
    samples: list[str] = field(default_factory=list)
    speech_patterns: list[str] = field(default_factory=list)
    address_terms: dict[str, str] = field(default_factory=dict)
    dos: list[str] = field(default_factory=list)
    donts: list[str] = field(default_factory=list)


@dataclass
class ImagePromptTemplate:
    base_prompt: str = ""
    negative_prompt: str = ""
    canonical_seed: int | None = None
    extra: Json = field(default_factory=dict)


@dataclass
class StructuralRelationship:
    """Library-level relationship recorded on the character card."""

    to_ref: CharacterRef
    kind: str  # 'spouse', 'sibling', 'mentor', etc.
    note: str = ""


@dataclass
class Character:
    """Library character read from a markdown file."""

    id: str
    name: str
    role: CharacterRole
    setting_id: str | None = None  # None if campaign-local emergent
    aliases: list[str] = field(default_factory=list)
    age: str | None = None
    tags: list[str] = field(default_factory=list)
    voice: VoiceAnchor = field(default_factory=VoiceAnchor)
    image: ImagePromptTemplate | None = None
    structural_relationships: list[StructuralRelationship] = field(default_factory=list)
    description: str = ""
    body: str = ""
    file_path: str = ""
    file_mtime: datetime | None = None
    version: int = 0


@dataclass
class CharacterData:
    """Payload for create/import flows; lighter than `Character` (no file metadata)."""

    id: str
    name: str
    role: CharacterRole
    aliases: list[str] = field(default_factory=list)
    age: str | None = None
    tags: list[str] = field(default_factory=list)
    voice: VoiceAnchor = field(default_factory=VoiceAnchor)
    image: ImagePromptTemplate | None = None
    description: str = ""
    body: str = ""


@dataclass
class ResolvedCharacter:
    """Character after the cascade has been applied, with state and capabilities."""

    character: Character
    current_state: CharacterState
    capabilities: list[Json] = field(default_factory=list)  # Capability at runtime
    source_chain: list[Json] = field(default_factory=list)  # ResolutionSource at runtime
    overrides_applied: list[str] = field(default_factory=list)


@dataclass
class PCEntry:
    character_ref: CharacterRef
    name: str
    owner: str  # 'local' in v1; account id in v2
    active: bool = True


@dataclass
class CharacterFilter:
    roles: list[CharacterRole] | None = None
    tags: list[str] | None = None
    setting_ids: list[str] | None = None
    name_contains: str | None = None


class AwarenessState(StrEnum):
    UNKNOWN = "unknown"
    AWARE = "aware"
    INTIMATE = "intimate"
    ESTRANGED = "estranged"


@dataclass
class RelationshipState:
    affection: int = 0
    trust: int = 0
    dominance: int = 0
    intimacy: int = 0
    awareness: AwarenessState = AwarenessState.UNKNOWN
    custom: Json = field(default_factory=dict)


@dataclass
class RelationshipEvent:
    in_post: str | None
    summary: str
    delta: Json = field(default_factory=dict)


@dataclass
class DriftReport:
    character_ref: CharacterRef
    window: int
    drift_score: float
    evidence: list[str] = field(default_factory=list)
    corrective_context: str = ""


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
