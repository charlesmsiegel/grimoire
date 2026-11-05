"""Character, scenario and lorebook cards (Sprint 037): parse, map to records, classify roles. Pure.

Bytes are decoded by the store-backed importer (``grimoire.cardstore``); this package reads the
decoded document. A card is interchange data, not native storage: it becomes source records whose
texts keep their authored roles, and nothing it says is ever an accepted event. See docs/CARDS.md.
"""

from .mentions import LinkSuggestion, Suggestions, suggest_links
from .model import (
    CCV3, CHARACTER, FORMATS, LOREBOOK, LOREBOOK_V3, SCENARIO, SCENARIO_DOC, Asset, Authorship, CardError,
    CastRef, CharacterCard, Greeting, LoreEntry, Lorebook, LorebookCard, Loss, ParsedCard, ScenarioCard, key_path,
)
from .parse import detect, parse_card, parse_lorebook_document
from .records import (
    ENTITY_SCHEMA, IMPORT_ID, IMPORT_KIND, IMPORT_SCHEMA, LOREBOOK_SCHEMA, ROLES, SCENARIO_SCHEMA, Piece,
    character_payload, lorebook_payload, mention_texts, pieces, scenario_payload, section_ok, slugify,
)

__all__ = [
    "Asset", "Authorship", "CCV3", "CHARACTER", "CardError", "CastRef", "CharacterCard", "ENTITY_SCHEMA", "FORMATS",
    "Greeting", "IMPORT_ID", "IMPORT_KIND", "IMPORT_SCHEMA", "LOREBOOK", "LOREBOOK_SCHEMA", "LOREBOOK_V3",
    "LinkSuggestion", "LoreEntry", "Lorebook", "LorebookCard", "Loss", "ParsedCard", "Piece", "ROLES", "SCENARIO",
    "SCENARIO_DOC", "SCENARIO_SCHEMA", "ScenarioCard", "Suggestions", "character_payload", "detect", "key_path",
    "lorebook_payload", "mention_texts", "parse_card", "parse_lorebook_document", "pieces", "scenario_payload",
    "section_ok", "slugify", "suggest_links",
]
