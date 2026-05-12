"""Character-specific types layered over Setting storage."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .common import CharacterRef, Json
from .state import CharacterState


class CharacterRole(StrEnum):
    PC = "pc"
    MAJOR_NPC = "major_npc"
    MINOR_NPC = "minor_npc"
    ENSEMBLE = "ensemble"
    NAMED_FLAVOR = "named_flavor"


class VoiceAnchor(BaseModel):
    summary: str = ""
    voice_register: str = ""  # 'formal', 'casual', 'archaic', 'low', 'technical', ...
    samples: list[str] = Field(default_factory=list)
    speech_patterns: list[str] = Field(default_factory=list)
    address_terms: dict[str, str] = Field(default_factory=dict)
    dos: list[str] = Field(default_factory=list)
    donts: list[str] = Field(default_factory=list)


class ImagePromptTemplate(BaseModel):
    base_prompt: str = ""
    negative_prompt: str = ""
    canonical_seed: int | None = None
    extra: Json = Field(default_factory=dict)


class StructuralRelationship(BaseModel):
    """Library-level relationship recorded on the character card."""

    to_ref: CharacterRef
    kind: str  # 'spouse', 'sibling', 'mentor', etc.
    note: str = ""


class Character(BaseModel):
    """Library character read from a markdown file."""

    id: str
    name: str
    role: CharacterRole
    setting_id: str | None = None  # None if campaign-local emergent
    aliases: list[str] = Field(default_factory=list)
    age: str | None = None
    tags: list[str] = Field(default_factory=list)
    voice: VoiceAnchor = Field(default_factory=VoiceAnchor)
    image: ImagePromptTemplate | None = None
    structural_relationships: list[StructuralRelationship] = Field(default_factory=list)
    description: str = ""
    body: str = ""
    file_path: str = ""
    file_mtime: datetime | None = None
    version: int = 0


class CharacterData(BaseModel):
    """Payload for create/import flows; lighter than `Character` (no file metadata)."""

    id: str
    name: str
    role: CharacterRole
    aliases: list[str] = Field(default_factory=list)
    age: str | None = None
    tags: list[str] = Field(default_factory=list)
    voice: VoiceAnchor = Field(default_factory=VoiceAnchor)
    image: ImagePromptTemplate | None = None
    description: str = ""
    body: str = ""


class ResolvedCharacter(BaseModel):
    """Character after the cascade has been applied, with state and capabilities."""

    character: Character
    current_state: CharacterState
    capabilities: list[Json] = Field(default_factory=list)  # Capability at runtime
    source_chain: list[Json] = Field(default_factory=list)  # ResolutionSource at runtime
    overrides_applied: list[str] = Field(default_factory=list)


class PCEntry(BaseModel):
    character_ref: CharacterRef
    name: str
    owner: str  # 'local' in v1; account id in v2
    active: bool = True


class CharacterFilter(BaseModel):
    roles: list[CharacterRole] | None = None
    tags: list[str] | None = None
    setting_ids: list[str] | None = None
    name_contains: str | None = None


class AwarenessState(StrEnum):
    UNKNOWN = "unknown"
    AWARE = "aware"
    INTIMATE = "intimate"
    ESTRANGED = "estranged"


class RelationshipState(BaseModel):
    affection: int = 0
    trust: int = 0
    dominance: int = 0
    intimacy: int = 0
    awareness: AwarenessState = AwarenessState.UNKNOWN
    custom: Json = Field(default_factory=dict)


class RelationshipEvent(BaseModel):
    in_post: str | None
    summary: str
    delta: Json = Field(default_factory=dict)


class DriftReport(BaseModel):
    character_ref: CharacterRef
    window: int
    drift_score: float
    evidence: list[str] = Field(default_factory=list)
    corrective_context: str = ""


class ImportResult(BaseModel):
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
