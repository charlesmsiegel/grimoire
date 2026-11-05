"""One chat actor's context pack over one loaded library: the join of 022, 020 and the store (031).

An adapter, not part of the pure package: it knows the store's ``StoredDefinition`` and
``LibraryView`` and 020's ``CampaignKnowledge``. Everything comes from **one** ``LibraryView`` and
**one** director ledger read, which the caller holds for the whole turn. Nothing here writes, reads
files itself, or calls a model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from grimoire.context import (
    DEFAULT_EXCLUDED_SECTIONS, Budget, ContextError, ContextPack, EntryReader, TokenCounter, estimate_tokens,
    pack_actor_context,
)
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import CampaignKnowledge
from grimoire.store import LibraryView, StoredDefinition

__all__ = ["ChatContext", "actor_context", "stored_record"]


def stored_record(definition: object) -> tuple[str | None, Mapping[str, object]] | None:
    """A stored revision's record schema and payload, for a lore reader; ``None`` for anything else."""
    if not isinstance(definition, StoredDefinition):
        return None
    schema = definition.envelope.extra.get("schema")
    return (schema if isinstance(schema, str) else None), definition.envelope.payload


@dataclass(frozen=True)
class ChatContext:
    """A chat actor's pack and what it was built from.

    ``chat_etag`` and ``scope_etag`` name the chat and scope records the snapshot read, so a caller
    can tell a pack built before a binding changed from one built after. ``problems`` are the chat
    snapshot's own (``orphan_chat``, ``missing_actor:<ref>`` ...), never a failure.
    """

    pack: ContextPack
    chat: ChatRef
    chat_etag: str
    scope_etag: str | None
    problems: tuple[str, ...]


def actor_context(view: LibraryView, chat: ChatRef, ledger: CampaignKnowledge, actor: InstanceRef, *, as_of: int,
                  budget: Budget, present: Iterable[InstanceRef] = (), scene_text: str = "",
                  readers: Mapping[str, EntryReader] | None = None, count: TokenCounter = estimate_tokens,
                  measurement: str = "estimated",
                  exclude_sections: Iterable[str] = DEFAULT_EXCLUDED_SECTIONS) -> ChatContext:
    """``pack_actor_context`` for ``actor`` of ``chat``, over ``view`` and a director ``ledger`` read.

    ``ledger`` is ``EvidenceLibrary.read(campaign, requested_by=DIRECTOR)``: trusted, never handed on.
    Only the returned pack goes toward the actor. The caller obligations of ``pack_actor_context``
    (``present``, ``scene_text``) apply unchanged. When the read quarantined damaged records, the pack
    carries the note ``knowledge_quarantined``: the actor's knowledge can only have shrunk (020).
    ``RecordNotFound`` and ``ChatCodecError`` propagate from 022's ``chat_snapshot``.
    """
    if not isinstance(ledger, CampaignKnowledge):
        raise TypeError(f"expected a CampaignKnowledge read, not {type(ledger).__name__}")
    if not isinstance(chat, ChatRef):
        raise TypeError(f"expected a ChatRef, not {type(chat).__name__}")
    if ledger.knowledge.campaign_id != chat.campaign_id:
        raise ContextError("the ledger belongs to another campaign than the chat", "campaign_mismatch")
    captured = chat_snapshot(view, chat)
    pack = pack_actor_context(captured.snapshot, ledger.knowledge, actor, as_of=as_of, budget=budget,
                              present=present, scene_text=scene_text, claims=ledger.claims, readers=readers,
                              unpack=stored_record, count=count, measurement=measurement,
                              exclude_sections=exclude_sections)
    if ledger.quarantined:
        pack = replace(pack, notes=(*pack.notes, "knowledge_quarantined"))
    return ChatContext(pack, chat, captured.chat_etag, captured.scope_etag, captured.problems)
