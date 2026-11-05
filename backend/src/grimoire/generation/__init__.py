"""Per-NPC generation (sprint 027): plan, voice, brief and screen each speaker's own call.

Pure and deterministic: no model calls, clock, I/O or persistence. ``grimoire.runtime`` issues
the planned calls through the provider adapter. See docs/GENERATION.md for the contract.
"""

from .errors import GenerationError
from .plan import DIRECTOR_PURPOSE, checked_controls, controller, plan_generation_turn, require_director
from .voice import VOICE_SECTION, resolve_snapshot_voice
from .brief import (
    CONTEXT_CHARS, KNOWLEDGE_CHARS, MAX_HEARD, MAX_LINE_CHARS, MIN_LORE_MARKER_WORDS, SCENARIO_CHARS,
    DirectorBrief, HeardLine, NpcBrief, director_brief, director_messages, known_ids, npc_brief, npc_messages,
)
from .screen import REJECTIONS, screen_reply

__all__ = [
    "GenerationError",
    "DIRECTOR_PURPOSE", "checked_controls", "controller", "plan_generation_turn", "require_director",
    "VOICE_SECTION", "resolve_snapshot_voice",
    "CONTEXT_CHARS", "KNOWLEDGE_CHARS", "MAX_HEARD", "MAX_LINE_CHARS", "MIN_LORE_MARKER_WORDS", "SCENARIO_CHARS",
    "DirectorBrief", "HeardLine", "NpcBrief", "director_brief", "director_messages", "known_ids", "npc_brief",
    "npc_messages",
    "REJECTIONS", "screen_reply",
]
