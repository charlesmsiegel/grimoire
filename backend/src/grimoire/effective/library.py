"""Snapshots over one loaded library: the join of 010 envelopes, 009 entities and 107 chats (022).

An adapter, not part of the pure core: it knows the store's ``StoredDefinition``/``LibraryView``
and the chat codec, and hands the pure ``EffectiveSnapshot`` a decoder. Everything a snapshot
answers comes from **one** ``LibraryView``: the caller loads once and holds the view for the whole
turn. Nothing here writes, reads files itself, or calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.chats import ChatRecord, PlayScope, chat_from_payload, scope_from_payload
from grimoire.chats import ChatCodecError
from grimoire.core.entity import EntityDefinition, EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, CodecError, definition_from_payload, instance_from_payload
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef
from grimoire.core.portrayal import Explain, PortrayalError
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection
from grimoire.store import LibraryView, RecordNotFound, StoredDefinition
from grimoire.store.layout import classify

ENTITY_FAMILY = ENTITY_SCHEMA.rsplit("/", 1)[0] + "/"  # grimoire.entity/<version>

__all__ = ["ChatSnapshot", "chat_selection", "chat_snapshot", "entity_decoder", "explainer", "selection_snapshot"]


def entity_decoder(definition: object) -> EntityDefinition:
    """A stored definition revision as a 009 ``EntityDefinition``; other records are refused, not guessed."""
    if isinstance(definition, EntityDefinition):
        return definition
    if not isinstance(definition, StoredDefinition):
        raise TypeError(f"expected a StoredDefinition, not {type(definition).__name__}")
    envelope = definition.envelope
    schema = envelope.extra.get("schema")
    if not (isinstance(schema, str) and schema.startswith(ENTITY_FAMILY)):
        raise PortrayalError(f"{definition.ref} is not an entity record (schema {schema!r})",
                             "not_entity_definition", definition.ref)
    # another grimoire.entity/N version is an entity this version cannot read, never a lorebook:
    # the codec refuses it as unsupported_schema, which surfaces as undecodable
    try:
        return definition_from_payload(definition.ref, schema, envelope.payload)
    except ValueError as error:  # CodecError, FacetError and EntityError all carry a reason
        detail = getattr(error, "reason", "invalid_field")
        raise PortrayalError(f"{definition.ref}: {error}", "undecodable", definition.ref, detail) from None


def explainer(view: LibraryView) -> Explain:
    """The store's reason a base does not resolve, or ``None``.

    ``excluded_source`` or ``entity_dropped`` when the load left it out, ``unreadable_revision``
    when its file exists but could not be read (fix the file, do not restore it).

    The most specific reason wins. An excluded source is fixed at its manifest, so it comes first.
    An unreadable base file comes before ``entity_dropped`` only when the drop is that file's own
    consequence (``_drop_is_unreadable_base``): then repairing the file is the whole fix. When the
    loader dropped the entity for another reason as well (``kind_reuse``, ``missing_head``, a head
    naming another revision), repairing the file would leave it dropped, so ``entity_dropped`` wins.
    """
    excluded, dropped = set(view.excluded), {ref.entity_key for ref in view.dropped}
    unreadable = _unreadable(view)

    def explain(ref: SourceRef) -> str | None:
        if ref.source in excluded:
            return "excluded_source"
        if ref in unreadable and (ref.entity_key not in dropped or _drop_is_unreadable_base(view, ref)):
            return "unreadable_revision"
        return "entity_dropped" if ref.entity_key in dropped else None
    return explain


def _drop_is_unreadable_base(view: LibraryView, ref: SourceRef) -> bool:
    """Whether the loader dropped ``ref``'s entity only because ``ref``'s own file is unreadable.

    This mirrors the store loader's ``evaluate``, which drops an entity for exactly four causes:
    ``kind_reuse`` (its id under two kinds), ``bad_head`` (its declared head names a missing or
    unreadable revision), ``missing_head`` (several revisions, no head) and, silently, no readable
    revision at all. The drop is the base's own consequence when no ``kind_reuse`` is involved and
    either the declared head *is* this unreadable revision, or no head is declared and the source
    reports no ``missing_head`` (so this was the entity's only revision: with a second one the
    loader would have reported ``missing_head``). A ``missing_head`` for another entity of the same
    source cannot be told apart from one for this entity without parsing its prose, so it is read
    conservatively as ``entity_dropped``, which is still true, only less specific.
    """
    if any(other.source == ref.source and other.entity_id == ref.entity_id and other.kind != ref.kind
           for other in view.dropped):  # kind_reuse drops every kind sharing the id
        return False
    stored = view.sources.get(ref.source)
    if stored is None:
        return False
    try:
        head = stored.manifest.head_refs().get(ref.unpinned())
    except ValueError:  # a manifest with a bad head key: the loader's own reading decides, not ours
        return False
    if head is not None:
        return head == ref.revision
    return not any(problem.code == "missing_head" and problem.source == ref.source for problem in view.problems)


def selection_snapshot(view: LibraryView, selection: SourceSelection) -> EffectiveSnapshot:
    """A snapshot of an explicit, in-memory selection over one loaded library (no instances captured)."""
    return EffectiveSnapshot(view.index, selection, decode=entity_decoder, explain=explainer(view))


def chat_selection(chat: ChatRecord, scope: PlayScope | None) -> SourceSelection:
    """A chat's **active** bindings with their roles, introduced revisions and actors.

    The launch is provenance only: a detached launch binding is not selected. The world is the
    scope's world, or ``None``.
    """
    sources = tuple(SelectedSource(binding.ref, binding.role, binding.resolved_revision, binding.actor)
                    for binding in chat.active_bindings())
    world = scope.world.source if scope is not None and scope.world is not None else None
    return SourceSelection(world, sources)


@dataclass(frozen=True)
class ChatSnapshot:
    """One chat, its scope and a snapshot of what it selected, all from the same loaded library."""

    chat: ChatRecord
    chat_etag: str
    scope: PlayScope | None
    scope_etag: str | None
    snapshot: EffectiveSnapshot
    problems: tuple[str, ...] = ()


def _unreadable(view: LibraryView) -> set[object]:
    """Records whose files exist but whose envelopes the store could not read."""
    found: set[object] = set()
    for problem in view.problems:
        record = classify(tuple(problem.path.split("/")))
        if record is not None:
            found.add(record[1])
    return found


def chat_snapshot(view: LibraryView, chat_ref: ChatRef) -> ChatSnapshot:
    """Capture a chat, its scope and its roster's instances from ``view`` and freeze them together.

    ``RecordNotFound`` if the view holds no readable chat at that ref; ``ChatCodecError`` if this
    version cannot interpret the chat. With an ``undecodable_scope`` problem, ``world`` is ``None``
    because it is unknown, not because the chat has no world. Problems (never failures): ``orphan_chat``,
    ``undecodable_scope``, ``missing_actor:<ref>``, ``undecodable_actor:<ref>``.
    """
    stored = view.chats.get(chat_ref)
    if stored is None:
        raise RecordNotFound(str(chat_ref))
    chat = chat_from_payload(chat_ref, stored.record.schema, stored.record.payload)
    unreadable = _unreadable(view)
    problems: list[str] = []
    scope: PlayScope | None = None
    scope_etag: str | None = None  # only for a scope this version understood
    found = view.scopes.get(chat_ref.scope)
    if found is None:
        problems.append("undecodable_scope" if chat_ref.scope in unreadable else "orphan_chat")
    else:
        try:
            scope = scope_from_payload(chat_ref.scope, found.record.schema, found.record.payload)
            scope_etag = found.etag
        except ChatCodecError:
            problems.append("undecodable_scope")
    instances: list[tuple[EntityInstance, str | None]] = []
    for entry in chat.roster:
        captured = _instance(view, entry.actor, unreadable)
        if isinstance(captured, str):
            problems.append(f"{captured}:{entry.actor}")
        else:
            instances.append(captured)
    snapshot = EffectiveSnapshot(view.index, chat_selection(chat, scope), decode=entity_decoder,
                                 explain=explainer(view), instances=instances)
    return ChatSnapshot(chat, stored.etag, scope, scope_etag, snapshot, tuple(problems))


def _instance(view: LibraryView, ref: InstanceRef, unreadable: set[object]) -> tuple[EntityInstance, str] | str:
    stored = view.instances.get(ref)
    if stored is None:
        return "undecodable_actor" if ref in unreadable else "missing_actor"
    envelope = stored.envelope
    schema = envelope.extra.get("schema")
    try:
        return instance_from_payload(ref, schema if isinstance(schema, str) else "", envelope.payload), stored.etag
    except (CodecError, ValueError):
        return "undecodable_actor"
