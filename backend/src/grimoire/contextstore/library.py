"""One chat actor's context pack over one loaded library: the join of 022, 020 and the store (031).

An adapter, not part of the pure package: it knows the store's ``StoredDefinition`` and
``LibraryView`` and 020's ``CampaignKnowledge``. Everything comes from **one** ``LibraryView`` and
**one** director ledger read, which the caller holds for the whole turn. ``actor_context`` packs for an
NPC, ``player_context`` for a player character's present controller. Nothing here writes, reads
files itself, or calls a model.

The library's own lore formats are read by ``DEFAULT_READERS``: 037's card lorebooks and scenarios,
whose entries are ``common`` lore (anyone in the scene may know them).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from grimoire.cards import LORE_READERS
from grimoire.context import (
    DEFAULT_EXCLUDED_SECTIONS, Budget, ContextError, ContextPack, EntryReader, LorePack, TokenCounter,
    director_lore, estimate_tokens, pack_actor_context, pack_player_context,
)
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.model import ControlAssignment
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import CampaignKnowledge
from grimoire.store import LibraryView, StoredDefinition

__all__ = [
    "DEFAULT_READERS", "ChatContext", "ChatLore", "actor_context", "chat_lore", "player_context", "stored_record",
]

#: The lore readers a store-backed pack uses when its caller passes none: every stored lore format the
#: library itself writes. A caller with its own formats passes ``{**DEFAULT_READERS, **its_own}``.
DEFAULT_READERS: Mapping[str, EntryReader] = LORE_READERS


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
    can tell a pack built before an attach, detach or pin from one built after. A head-following
    binding can move without changing either etag; that shows as ``binding_drift`` in the pack's
    ``problems``. ``problems`` here are the chat snapshot's own (``orphan_chat``,
    ``missing_actor:<ref>`` ...), never a failure.
    """

    pack: ContextPack
    chat: ChatRef
    chat_etag: str
    scope_etag: str | None
    problems: tuple[str, ...]


def _captured(view: LibraryView, chat: ChatRef, ledger: CampaignKnowledge):
    if not isinstance(ledger, CampaignKnowledge):
        raise TypeError(f"expected a CampaignKnowledge read, not {type(ledger).__name__}")
    if not isinstance(chat, ChatRef):
        raise TypeError(f"expected a ChatRef, not {type(chat).__name__}")
    if ledger.knowledge.campaign_id != chat.campaign_id:
        raise ContextError("the ledger belongs to another campaign than the chat", "campaign_mismatch")
    return chat_snapshot(view, chat)


def _context(pack: ContextPack, ledger: CampaignKnowledge, chat: ChatRef, captured) -> ChatContext:
    if ledger.quarantined:
        pack = replace(pack, notes=(*pack.notes, "knowledge_quarantined"))
    return ChatContext(pack, chat, captured.chat_etag, captured.scope_etag, captured.problems)


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

    ``readers`` defaults to ``DEFAULT_READERS``, so a bound card lorebook reaches the pack without
    the caller knowing its format; ``{}`` reads no lore format at all. An NPC's pack: a player
    character's is ``player_context``'s.
    """
    captured = _captured(view, chat, ledger)
    pack = pack_actor_context(captured.snapshot, ledger.knowledge, actor, as_of=as_of, budget=budget,
                              present=present, scene_text=scene_text, claims=ledger.claims,
                              readers=DEFAULT_READERS if readers is None else readers,
                              unpack=stored_record, count=count, measurement=measurement,
                              exclude_sections=exclude_sections)
    return _context(pack, ledger, chat, captured)


def player_context(view: LibraryView, chat: ChatRef, ledger: CampaignKnowledge, actor: InstanceRef, *,
                   controller: str, controls: Iterable[ControlAssignment], as_of: int, budget: Budget,
                   present: Iterable[InstanceRef] = (), scene_text: str = "",
                   readers: Mapping[str, EntryReader] | None = None, count: TokenCounter = estimate_tokens,
                   measurement: str = "estimated",
                   exclude_sections: Iterable[str] = DEFAULT_EXCLUDED_SECTIONS) -> ChatContext:
    """``pack_player_context`` for player character ``actor`` of ``chat``, packed for its present
    ``controller``, over ``view`` and a director ``ledger`` read, as ``actor_context`` is for an NPC.

    ``controls`` are 003's control assignments of the campaign as read for this call (the sovereignty
    ledger's), the same the caller decided ``controller`` from. Everything else is ``actor_context``'s.
    """
    captured = _captured(view, chat, ledger)
    pack = pack_player_context(captured.snapshot, ledger.knowledge, actor, controller=controller, controls=controls,
                               as_of=as_of, budget=budget, present=present, scene_text=scene_text,
                               claims=ledger.claims, readers=DEFAULT_READERS if readers is None else readers,
                               unpack=stored_record, count=count, measurement=measurement,
                               exclude_sections=exclude_sections)
    return _context(pack, ledger, chat, captured)


@dataclass(frozen=True)
class ChatLore:
    """The director's lore for a chat's scene and what it was built from (see ``ChatContext``).

    ``problems`` are the chat snapshot's own, such as ``invalid_scenario_lorebook:<ref>``: lore the
    chat should have had but could not select, which ``lore``'s own problems cannot show.
    """

    lore: LorePack
    chat: ChatRef
    chat_etag: str
    scope_etag: str | None
    problems: tuple[str, ...]


def chat_lore(view: LibraryView, chat: ChatRef, *, table_text: str, budget: Budget,
              readers: Mapping[str, EntryReader] | None = None, count: TokenCounter = estimate_tokens,
              measurement: str = "estimated") -> ChatLore:
    """``director_lore`` for ``chat``'s scene over ``view``, with ``actor_context``'s default readers.

    The caller obligation on ``table_text`` applies unchanged. ``RecordNotFound`` and ``ChatCodecError``
    propagate from 022's ``chat_snapshot``.
    """
    if not isinstance(chat, ChatRef):
        raise TypeError(f"expected a ChatRef, not {type(chat).__name__}")
    captured = chat_snapshot(view, chat)
    lore = director_lore(captured.snapshot, campaign_id=chat.campaign_id, table_text=table_text, budget=budget,
                         readers=DEFAULT_READERS if readers is None else readers, unpack=stored_record,
                         count=count, measurement=measurement)
    return ChatLore(lore, chat, captured.chat_etag, captured.scope_etag, captured.problems)
