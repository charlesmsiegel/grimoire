"""Audience-aware context retrieval and packing (Sprint 031): pure values, triggering and the packer.

For one actor at one moment, ``pack_actor_context`` chooses only what that actor may see (its own
composed portrayal, its own 012 knowledge, and ``common`` lore from the chat's bound lorebooks and
scenarios that what the actor perceived triggers), ranks it deterministically and fits it into an
input-token budget, reporting everything it shed; ``pack_player_context`` does the same for a player
character, packed for its present controller (only the beliefs that controller authored). ``director_lore``
packs the scene-level director reply's lore, triggered by table-visible text only. Nothing here reads
files, writes, or calls a model; ``grimoire.contextstore`` joins it to the store and 027's briefs render
it. See docs/CONTEXT.md.
"""

from .actor import (
    DEFAULT_EXCLUDED_SECTIONS, MEASUREMENTS, controller_projection, pack_actor_context, pack_player_context,
)
from .basis import knowledge_digest, selection_basis
from .director import director_lore
from .lore import ENTITY_SCHEMA_FAMILY, LORE_ROLES, Unpack
from .model import (
    AUDIENCES, FORMAT, LANES, MAX_ENTRIES, MAX_ENTRY_TEXT, MAX_KEY_TEXT, MAX_KEYS, MAX_ORDER, MAX_PRESENT,
    MAX_SCENE_TEXT, MAX_TOKENS, SECONDARY_LOGICS, Budget, ClaimReading, ContextError, ContextPack, EntryReader,
    LoreEntry, LorePack, Omission, Origin, PackedItem, SelectionBasis, SkippedEntry, SourceProblem, TokenCounter,
    estimate_tokens, safe_text, valid_key,
)
from .pack import Candidate, pack
from .trigger import SceneWords, words

__all__ = [
    "AUDIENCES", "DEFAULT_EXCLUDED_SECTIONS", "ENTITY_SCHEMA_FAMILY", "FORMAT", "LANES", "LORE_ROLES", "MAX_ENTRIES",
    "MAX_ENTRY_TEXT", "MAX_KEYS", "MAX_KEY_TEXT", "MAX_ORDER", "MAX_PRESENT", "MAX_SCENE_TEXT", "MAX_TOKENS",
    "MEASUREMENTS", "SECONDARY_LOGICS", "Budget", "Candidate", "ClaimReading", "ContextError", "ContextPack",
    "EntryReader", "LoreEntry", "LorePack", "Omission", "Origin", "PackedItem", "SceneWords", "SelectionBasis",
    "SkippedEntry", "SourceProblem", "TokenCounter", "Unpack", "controller_projection", "director_lore",
    "estimate_tokens", "knowledge_digest", "pack", "pack_actor_context", "pack_player_context", "safe_text",
    "selection_basis", "valid_key", "words",
]
