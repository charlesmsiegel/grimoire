"""Inclusion-reason vocabulary for context sources.

Each ``ContextSource`` that the Context Builder emits carries a list of
``InclusionReason`` values explaining *why* it ended up in the prompt. The
list is composable — a character can be both ``PRESENT_IN_SCENE`` and
``COMMITMENT_OPEN_TO_PC`` simultaneously.

Adding a new reason is a deliberate change: extend the enum here and emit
it from the relevant assembly step in ``grimoire.context.builder``.
"""

from __future__ import annotations

from enum import StrEnum


class InclusionReason(StrEnum):
    PRESENT_IN_SCENE = "present_in_scene"
    MENTIONED_IN_RECENT_POSTS = "mentioned_in_recent_posts"
    COMMITMENT_OPEN_TO_PC = "commitment_open_to_pc"
    KEYWORD_TRIGGERED = "keyword_triggered"
    RELATIONSHIP_TO_PRESENT = "relationship_to_present"
    PINNED_BY_USER = "pinned_by_user"
    SCENE_ANCHOR = "scene_anchor"
    MECHANICS_RELEVANT = "mechanics_relevant"
    STYLE_GUIDE_ACTIVE = "style_guide_active"
    PC_CARD = "pc_card"
    COMPOSITION_DEFAULT = "composition_default"
    EXTRAS_PINNED_TO_HUD = "extras_pinned_to_hud"
    EXTRAS_DEFAULT_VISIBLE = "extras_default_visible"
    LORE_BEFORE_CAST = "lore_before_cast"
    LORE_AFTER_CAST = "lore_after_cast"
    LORE_AT_DEPTH = "lore_at_depth"
    LORE_ARCHIVE = "lore_archive"
    TRANSIENT_STATE_ACTIVE = "transient_state_active"
    SYSTEM_PROMPT = "system_prompt"
    SCENE_HEADER = "scene_header"
    VERBATIM_RECENT = "verbatim_recent"
    PLAYER_INPUT = "player_input"
    RESPONSE_FORMAT = "response_format"


__all__ = ["InclusionReason"]
