"""World-optional chats: pure play-scope/chat values, payload codec and navigation (Sprint 107).

Pure: no files, clocks, randomness or providers. The store-backed service is
``grimoire.chatstore``. See docs/CHATS.md.
"""

from .codec import CHAT_SCHEMA, SCOPE_SCHEMA, ChatCodecError, chat_from_payload, chat_to_payload
from .codec import scope_from_payload, scope_to_payload
from .model import (
    ACTOR_ROLES, CAMPAIGN_CONTINUITY, MAX_BINDINGS, MAX_ROSTER, NOT_WITNESSED, ROLE_KINDS, BindingLimit,
    ChatModelError, ChatRecord, Launch, NewBinding, PlayScope, RosterEntry, SourceBinding, WorldBinding,
)
from .navigation import ORPHAN_CHAT, UNDECODABLE, NavEntry, NavigationIndex, ScopeEntry, nav_entry, scope_entry

__all__ = [
    "ACTOR_ROLES", "CAMPAIGN_CONTINUITY", "CHAT_SCHEMA", "MAX_BINDINGS", "MAX_ROSTER", "NOT_WITNESSED",
    "ROLE_KINDS", "SCOPE_SCHEMA", "BindingLimit", "ChatCodecError", "ChatModelError", "ChatRecord", "Launch",
    "NavEntry", "NavigationIndex", "NewBinding", "ORPHAN_CHAT", "ScopeEntry", "UNDECODABLE", "PlayScope", "RosterEntry", "SourceBinding", "WorldBinding",
    "chat_from_payload", "chat_to_payload", "nav_entry", "scope_entry", "scope_from_payload", "scope_to_payload",
]
