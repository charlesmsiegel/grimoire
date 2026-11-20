"""World-optional chats: pure play-scope/chat values, payload codec and navigation (Sprint 107).

Pure: no files, clocks, randomness or providers. The store-backed service is
``grimoire.chatstore``. See docs/CHATS.md.
"""

from .codec import CHAT_SCHEMA, SCOPE_SCHEMA, ChatCodecError, chat_from_payload, chat_to_payload
from .codec import scope_from_payload, scope_to_payload
from .model import (
    ACTOR_ROLES, CAMPAIGN_CONTINUITY, EXISTING, MAX_BINDINGS, MAX_ROSTER, NO_ACTOR, NOT_WITNESSED, PER_SOURCE,
    ROLE_KINDS, ROLES, BindingLimit, ChatModelError, ChatRecord, Launch, NewBinding, PlayScope, RoleRule,
    RosterEntry, SourceBinding, WorldBinding, role_rule,
)
from .navigation import ORPHAN_CHAT, UNDECODABLE, NavEntry, NavigationIndex, ScopeEntry, nav_entry, scope_entry

__all__ = [
    "ACTOR_ROLES", "BindingLimit", "CAMPAIGN_CONTINUITY", "CHAT_SCHEMA", "ChatCodecError", "ChatModelError",
    "ChatRecord", "EXISTING", "Launch", "MAX_BINDINGS", "MAX_ROSTER", "NOT_WITNESSED", "NO_ACTOR", "NavEntry",
    "NavigationIndex", "NewBinding", "ORPHAN_CHAT", "PER_SOURCE", "PlayScope", "ROLES", "ROLE_KINDS", "RoleRule",
    "RosterEntry", "SCOPE_SCHEMA", "ScopeEntry", "SourceBinding", "UNDECODABLE", "WorldBinding",
    "chat_from_payload", "chat_to_payload", "nav_entry", "role_rule", "scope_entry", "scope_from_payload",
    "scope_to_payload",
]
