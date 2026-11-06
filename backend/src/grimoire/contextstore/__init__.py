"""Context packing over one loaded library (Sprint 031): an adapter over the pure ``grimoire.context``.

Joins 022's ``chat_snapshot``, 020's director ledger read and the store's envelopes, so that a
world-based or world-less chat's actor gets a bounded, audience-safe ``ContextPack``. See
docs/CONTEXT.md.
"""

from .library import DEFAULT_READERS, ChatContext, ChatLore, actor_context, chat_lore, stored_record

__all__ = ["DEFAULT_READERS", "ChatContext", "ChatLore", "actor_context", "chat_lore", "stored_record"]
