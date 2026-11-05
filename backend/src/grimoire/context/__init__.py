"""Audience-aware context retrieval and packing (Sprint 031): pure values, triggering and the packer.

For one actor at one moment, ``pack_actor_context`` chooses only what that actor may see (its own
composed portrayal, its own 012 knowledge, and ``common`` lore from the chat's bound lorebooks and
scenarios that what the actor perceived triggers), ranks it deterministically and fits it into an
input-token budget, reporting everything it shed. Nothing here reads files, writes, or calls a
model; ``grimoire.contextstore`` joins it to the store. See docs/CONTEXT.md.
"""

from .actor import (
    DEFAULT_EXCLUDED_SECTIONS, ENTITY_SCHEMA_FAMILY, LORE_ROLES, MEASUREMENTS, Unpack, pack_actor_context,
)
from .model import (
    AUDIENCES, FORMAT, LANES, MAX_ENTRIES, MAX_ENTRY_TEXT, MAX_KEY_TEXT, MAX_KEYS, MAX_PRESENT, MAX_SCENE_TEXT,
    MAX_TOKENS, Budget, ClaimReading, ContextError, ContextPack, EntryReader, LoreEntry, Omission, Origin,
    PackedItem, SourceProblem, TokenCounter, estimate_tokens,
)
from .pack import Candidate, pack
from .trigger import SceneWords, words

__all__ = [
    "AUDIENCES", "DEFAULT_EXCLUDED_SECTIONS", "ENTITY_SCHEMA_FAMILY", "FORMAT", "LANES", "LORE_ROLES", "MAX_ENTRIES",
    "MAX_ENTRY_TEXT", "MAX_KEYS", "MAX_KEY_TEXT", "MAX_PRESENT", "MAX_SCENE_TEXT", "MAX_TOKENS", "MEASUREMENTS",
    "Budget", "Candidate", "ClaimReading", "ContextError", "ContextPack", "EntryReader", "LoreEntry", "Omission",
    "Origin", "PackedItem", "SceneWords", "SourceProblem", "TokenCounter", "Unpack", "estimate_tokens", "pack",
    "pack_actor_context", "words",
]
