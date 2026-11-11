"""Character, scenario and lorebook cards (Sprint 037): parse, map to records, classify roles. Pure.

Bytes are decoded by the store-backed importer (``grimoire.cardstore``); this package reads the
decoded document. A card is interchange data, not native storage: it becomes source records whose
texts keep their authored roles, and nothing it says is ever an accepted event. See docs/CARDS.md.
"""

from .lore import CARD_LORE_AUDIENCE, LORE_READERS, read_lorebook, read_scenario
from .mentions import LinkSuggestion, Suggestions, suggest_links
from .model import (
    CCV2, CCV3, CHARACTER, CHARACTER_FORMATS, FORMATS, LOREBOOK, LOREBOOK_V3, SCENARIO, SCENARIO_DOC, Asset, Authorship, CardError,
    CastRef, CharacterCard, Greeting, LoreEntry, Lorebook, LorebookCard, Loss, ParsedCard, ScenarioCard, key_path,
)
from .parse import detect, parse_card, parse_lorebook_document
from .records import (
    ENTITY_SCHEMA, IMPORT_ID, IMPORT_KIND, IMPORT_SCHEMA, LOREBOOK_SCHEMA, ROLES, SCENARIO_SCHEMA, Piece,
    character_payload, lorebook_payload, mention_texts, pieces, scenario_lorebook, scenario_payload, section_ok,
    slugify,
)

__all__ = [
    "Asset", "Authorship", "CARD_LORE_AUDIENCE", "CCV2", "CCV3", "CHARACTER", "CHARACTER_FORMATS",
    "CardError", "CastRef", "CharacterCard",
    "ENTITY_SCHEMA", "FORMATS", "Greeting", "IMPORT_ID", "IMPORT_KIND", "IMPORT_SCHEMA", "LOREBOOK", "LOREBOOK_SCHEMA",
    "LOREBOOK_V3", "LORE_READERS", "LinkSuggestion", "LoreEntry", "Lorebook", "LorebookCard", "Loss", "ParsedCard",
    "Piece", "ROLES", "SCENARIO", "SCENARIO_DOC", "SCENARIO_SCHEMA", "ScenarioCard", "Suggestions",
    "character_payload", "detect", "key_path", "lorebook_payload", "mention_texts", "parse_card",
    "parse_lorebook_document", "pieces", "read_lorebook", "read_scenario", "scenario_lorebook", "scenario_payload",
    "section_ok", "slugify", "suggest_links",
]
