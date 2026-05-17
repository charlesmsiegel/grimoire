"""Characters module — behavior layer over the Library's character storage.

Implements spec 08. Builds on :class:`grimoire.library.LibraryService` for
file storage and :class:`grimoire.mechanics.service.MechanicsService` for
capability surfacing.
"""

from grimoire.characters.drift import (
    CallableDriftChecker,
    DriftChecker,
    DriftInput,
    HeuristicDriftChecker,
)
from grimoire.characters.errors import (
    CharacterNotFoundError,
    CharactersError,
    ImportError_,
    PromotionError,
)
from grimoire.characters.imports import (
    parse_charx,
    parse_plaintext,
    parse_sillytavern,
)
from grimoire.characters.ingest import (
    LLMEnrichCallable,
    LLMEnrichment,
    enrich_with_llm,
    extract_relationships_deterministic,
    ingest_character_card_v2,
)
from grimoire.characters.protocols import SheetMigrator
from grimoire.characters.service import (
    CharactersService,
    LLMCapsuleDrafter,
    LLMVoiceAnchorDrafter,
    PostFetcher,
)
from grimoire.characters.views import (
    render_capsule,
    render_compressed,
    render_full,
    render_voice_only,
    rotate_samples,
)

__all__ = [
    "CallableDriftChecker",
    "CharacterNotFoundError",
    "CharactersError",
    "CharactersService",
    "DriftChecker",
    "DriftInput",
    "HeuristicDriftChecker",
    "ImportError_",
    "LLMCapsuleDrafter",
    "LLMEnrichCallable",
    "LLMEnrichment",
    "LLMVoiceAnchorDrafter",
    "PostFetcher",
    "PromotionError",
    "SheetMigrator",
    "enrich_with_llm",
    "extract_relationships_deterministic",
    "ingest_character_card_v2",
    "parse_charx",
    "parse_plaintext",
    "parse_sillytavern",
    "render_capsule",
    "render_compressed",
    "render_full",
    "render_voice_only",
    "rotate_samples",
]
