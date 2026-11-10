"""The library-source ops (109): what the app's Library pages call, as JSON ops over ``SourceAuthoring``.

Like 044's ``TextApi``, ``SourceApi`` is trusted application code over one open library, acting for its owner.
Every op takes a JSON object and returns one, and every refusal is 044's ``ApiRefused`` with a
machine-readable reason (``changed`` for a stale write: nothing was written and the client keeps its draft).
Every ref is written in its own string form, so a client hands it back unchanged. Op names start with
``sources.``. See docs/SOURCE_AUTHORING.md.
"""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from collections.abc import Callable, Mapping

from grimoire.cards import IMPORT_KIND
from grimoire.chats import ChatModelError
from grimoire.chatstore import ChatLibrary, InvalidBinding
from grimoire.core.identity import ChatRef, SourceRef
from grimoire.core.index import MissingDefinition
from grimoire.core.portrayal import PortrayalError
from grimoire.store import LibraryStore, LibraryView, RecordNotFound, StaleWrite, StoreError, StoredDefinition
from grimoire.store.jsonfmt import is_etag, thaw
from grimoire.textapi import ApiRefused

from .authoring import (
    ENTITY,
    LOREBOOK,
    NATURES,
    OTHER,
    PORTRAYAL_EDITS,
    SCENARIO,
    AuthoringRefused,
    SourceAuthoring,
    Written,
    facet_value,
    family_of_kind,
    family_of_schema,
)

MAX_LIMIT = 1000
#: the chat role a definition of each kind is attached under (107); other kinds are not attachable
ATTACH_ROLE = {"character": "character", "scenario": "scenario", "lorebook": "lorebook"}
#: what this slice cannot do, and why; shown, never faked
UNAVAILABLE = {
    "delete": "Deleting a source or a revision is not supported: the V2 store keeps every revision, so chats "
              "that pin one never change.",
    "art": "Pictures are placed in the gallery (Sprint 114).",
    "import": "Cards and V0.2.1 libraries are imported from the import page (Sprint 110).",
    "relations": "Typed relations between actors are campaign state (017); a source declares only derivation "
                 "links here.",
    "abilities": "Abilities and other facets with fields are edited in the raw editor.",
    "rebase": "Moving a chat to a newer revision is a reviewed rebase (042), done from the chat.",
    "voice": "A character made here speaks in play only once a voice profile is authored for it (014, 027); "
             "voice profiles are not authored in the library yet. Imported characters speak from their own "
             "imported text.",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _reason_of(error: Exception) -> str:
    """A store refusal's class name as a snake_case reason (``InvalidEnvelope`` -> ``invalid_envelope``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", type(error).__name__).lower()


def _str(value: object) -> str | None:
    return None if value is None else str(value)


def _field(args: Mapping[str, object], key: str, kind: type, *, required: bool = True, default=None):  # type: ignore[no-untyped-def]
    value = args.get(key, default)
    if value is None and not required:
        return default
    if type(value) is not kind:
        raise ApiRefused("invalid_field", f"{key} must be a {kind.__name__}")
    return value


def _ref(args: Mapping[str, object], key: str = "ref") -> SourceRef:
    text = _field(args, key, str)
    try:
        return SourceRef.parse(text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", f"{key} is not a definition reference") from None


def _etag(args: Mapping[str, object], key: str = "expected") -> str:
    value = _field(args, key, str)
    if not is_etag(value):
        raise ApiRefused("invalid_field", f"{key} must be an etag")
    return value


def _written(written: Written) -> dict:
    return {"ref": _str(written.ref), "source": written.source, "manifest_etag": written.manifest_etag}


class SourceApi:
    """The Library pages' ops over one open library, for its owner. Calls are serialized."""

    def __init__(self, store: LibraryStore, authority, *, owner: str,  # type: ignore[no-untyped-def]
                 clock: Callable[[], str] = _utc_now, new_id: Callable[[str], str] = _new_id) -> None:
        handle = authority.handle_for(owner, trusted=True)
        self._setup(store, handle, ChatLibrary(store, handle, clock=clock, new_id=new_id, owner=owner), owner)

    @classmethod
    def over(cls, store: LibraryStore, handle, chats: ChatLibrary, *, owner: str) -> "SourceApi":  # type: ignore[no-untyped-def]
        """The ops over a handle and chat library that already exist: the app's adapter (108) builds one per call
        over the text API's own, so it never asks the authority for a handle of its own."""
        api = cls.__new__(cls)
        api._setup(store, handle, chats, owner)
        return api

    def _setup(self, store: LibraryStore, handle, chats: ChatLibrary, owner: str) -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.owner = owner
        self.handle = handle
        self.authoring = SourceAuthoring(store, handle, owner=owner)
        self.chats = chats
        self._lock = threading.RLock()
        self._ops: dict[str, Callable[[Mapping[str, object]], dict]] = {
            "sources.catalog": self._catalog, "sources.read": self._read, "sources.create_source": self._create_source,
            "sources.rename_source": self._rename_source, "sources.create": self._create, "sources.save": self._save,
            "sources.save_raw": self._save_raw, "sources.derive": self._derive, "sources.portrayal": self._portrayal,
            "sources.chats": self._attach_targets, "sources.attach": self._attach,
        }

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(sorted(self._ops))

    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        handler = self._ops.get(op) if isinstance(op, str) else None
        if handler is None:
            raise ApiRefused("unknown_op", f"no op {op!r}")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ApiRefused("invalid_field", "the arguments must be a JSON object")
        with self._lock:
            try:
                return handler(args)
            except ApiRefused:
                raise
            except AuthoringRefused as error:
                raise ApiRefused(error.reason, str(error)) from None
            except (InvalidBinding, ChatModelError) as error:  # a binding the chat refuses, its limits included
                raise ApiRefused(getattr(error, "reason", "invalid_binding") or "invalid_binding", str(error)) from None
            except StaleWrite as error:
                raise ApiRefused("changed", f"the library changed meanwhile; nothing was written ({error})") from None
            except RecordNotFound as error:
                raise ApiRefused("missing", str(error), 404) from None
            except StoreError as error:  # any other refusal of the store: nothing was written
                raise ApiRefused(_reason_of(error), str(error)) from None

    # -- reading -----------------------------------------------------------------------------------------
    def _catalog(self, args: Mapping[str, object]) -> dict:
        """Every source and its entities, newest name first within a source; ``query`` matches names and
        refs (case-insensitive), ``kind`` and ``source`` filter. Same-named entities of different sources
        are listed apart, each with its qualified ref: a name is never an identity."""
        query = _field(args, "query", str, required=False, default="")
        kind = _field(args, "kind", str, required=False)
        only = _field(args, "source", str, required=False)
        limit = _field(args, "limit", int, required=False, default=200)
        if not 1 <= limit <= MAX_LIMIT:
            raise ApiRefused("invalid_field", f"limit must be 1 to {MAX_LIMIT}")
        needle = query.strip().casefold()
        view = self.store.load()
        dropped = {ref.unpinned() for ref in view.dropped}
        sources, entries = [], []
        total = 0
        for source_id in sorted(view.sources):
            if only is not None and source_id != only:
                continue
            stored = view.sources[source_id]
            entities = self.store.source_entities(source_id)
            sources.append({"source": source_id, "title": stored.manifest.title, "owner": stored.manifest.owner,
                            "namespace": source_id.split(":", 1)[0], "etag": stored.etag,
                            "entities": sum(1 for key in entities if key[0] != IMPORT_KIND)})
            for (entity_kind, entity_id), revisions in sorted(entities.items()):
                if entity_kind == IMPORT_KIND or (kind is not None and entity_kind != kind):
                    continue
                ref = SourceRef(source_id, entity_kind, entity_id)
                entry = self._entry(view, ref, revisions, dropped)
                if needle and needle not in (entry["name"] or "").casefold() and needle not in entry["ref"].casefold():
                    continue
                total += 1
                if len(entries) < limit:
                    entries.append(entry)
        excluded = [{"source": source_id, "reason": "excluded_source"} for source_id in sorted(view.excluded)
                    if only is None or only == source_id]
        names: dict[str, list[str]] = {}
        for entry in entries:
            if entry["name"]:
                names.setdefault(entry["name"].casefold(), []).append(entry["ref"])
        for entry in entries:  # a namesake elsewhere is shown, so a person never picks one by name alone
            entry["namesakes"] = [ref for ref in names.get((entry["name"] or "").casefold(), ()) if ref != entry["ref"]]
        return {"sources": sources, "entries": entries, "total": total, "truncated": total > len(entries),
                "excluded": excluded, "kinds": sorted({entry["kind"] for entry in entries})}

    def _entry(self, view: LibraryView, ref: SourceRef, revisions: tuple[str, ...], dropped: set) -> dict:  # type: ignore[type-arg]
        entry = {"ref": str(ref), "source": ref.source, "kind": ref.kind, "entity": ref.entity_id,
                 "revisions": len(revisions), "head": None, "name": None, "family": family_of_kind(ref.kind),
                 "derived": False, "problem": None}
        try:
            head = view.index.resolve(ref)
        except MissingDefinition as error:
            entry["problem"] = "entity_dropped" if ref in dropped else error.reason
            return entry
        payload = head.envelope.payload if isinstance(head, StoredDefinition) else None
        entry.update(head=head.ref.revision, name=head.name or None,
                     family=family_of_schema(head.envelope.extra.get("schema")) if isinstance(head, StoredDefinition)
                     else OTHER,
                     derived=isinstance(payload, Mapping) and "derivation" in payload)
        return entry

    def _read(self, args: Mapping[str, object]) -> dict:
        """One revision (``ref`` pinned) or an entity's head (unpinned): its exact record, what it composes
        to with every field's provenance, its links and portrayals, the chats that use it, and what can be
        done to it (or why not)."""
        ref = _ref(args)
        view = self.store.load()
        stored = self.authoring.head(view, ref)
        head = self.authoring.head(view, ref.unpinned())
        source = view.sources[ref.source]
        schema = stored.envelope.extra.get("schema")
        family = family_of_schema(schema)
        payload = thaw(stored.envelope.payload)
        derived = family == ENTITY and isinstance(payload, dict) and "derivation" in payload
        detail: dict = {
            "ref": str(stored.ref), "entity": str(ref.unpinned()), "source": ref.source,
            "source_title": source.manifest.title, "kind": ref.kind, "id": ref.entity_id,
            "revision": stored.ref.revision, "head": head.ref.revision, "is_head": stored.ref == head.ref,
            "revisions": list(self.authoring.revisions(ref)), "schema": schema, "family": family,
            "name": stored.name or None, "etag": stored.etag, "manifest_etag": source.etag, "payload": payload,
            "envelope": {"owner": stored.envelope.owner, "extra": {key: thaw(value) for key, value
                                                                   in stored.envelope.extra.items()}},
            "derived": derived, "base": None, "effective": None, "composition_error": None,
        }
        if family == ENTITY:
            try:
                inspection, composed = self.authoring.inspect(view, stored.ref)
            except (PortrayalError, MissingDefinition) as error:
                detail["composition_error"] = {"reason": getattr(error, "reason", "missing"),
                                               "ref": _str(getattr(error, "ref", None)),
                                               "detail": getattr(error, "detail", "") or ""}
            else:
                detail["base"] = _str(inspection.base)
                detail["effective"] = _effective(inspection, composed)
        detail["links"] = [{"subject": str(link.subject), "relation": link.relation, "object": str(link.object),
                            "declared_by": getattr(link, "declared_by", None)}
                           for link in view.index.links_for(ref.unpinned())]
        detail["portrayals"] = sorted({str(link.subject.unpinned()) for link in view.index.links_for(ref.unpinned())
                                       if link.relation == "derived_variant"
                                       and link.object.entity_key == ref.entity_key
                                       and link.subject.entity_key != ref.entity_key})
        detail["namesakes"] = [str(other) for other in view.index.find_by_name(stored.name, ref.kind)
                               if other.entity_key != ref.entity_key] if stored.name else []
        detail["chats"] = self._linked_chats(ref)
        detail["actions"] = _actions(ref, family, derived, stored.ref == head.ref)
        detail["unavailable"] = UNAVAILABLE
        return detail

    def _linked_chats(self, ref: SourceRef) -> list[dict]:
        index = self.chats.navigation()
        found = []
        for chat in index.by_source(ref.unpinned(), include_detached=True):
            entry = index.entry(chat)
            revisions = sorted({revision or "" for _role, key, revision, active in entry.mentions
                                if key == ref.entity_key and active})
            found.append({"chat": str(chat), "label": entry.label, "last_activity": entry.last_activity,
                          "revisions": [revision or None for revision in revisions],
                          "active": bool(revisions), "problem": entry.problem})
        return found

    def _attach_targets(self, args: Mapping[str, object]) -> dict:
        """Recent chats with the etag an ``attach`` must name."""
        limit = _field(args, "limit", int, required=False, default=50)
        if not 1 <= limit <= MAX_LIMIT:
            raise ApiRefused("invalid_field", f"limit must be 1 to {MAX_LIMIT}")
        index = self.chats.navigation()
        chats = []
        for chat in index.recent(limit):
            entry = index.entry(chat)
            if entry.problem is not None:
                continue
            try:
                _record, etag = self.chats.read_chat(chat)
            except (RecordNotFound, StoreError, ValueError):
                continue
            chats.append({"chat": str(chat), "label": entry.label, "last_activity": entry.last_activity,
                          "etag": etag})
        return {"chats": chats}

    # -- writing -----------------------------------------------------------------------------------------
    def _create_source(self, args: Mapping[str, object]) -> dict:
        return _written(self.authoring.create_source(_field(args, "source", str),
                                                     _field(args, "title", str, required=False)))

    def _rename_source(self, args: Mapping[str, object]) -> dict:
        return _written(self.authoring.rename_source(_field(args, "source", str),
                                                     _field(args, "title", str, required=False),
                                                     expected=_etag(args)))

    def _create(self, args: Mapping[str, object]) -> dict:
        return _written(self.authoring.create(_field(args, "source", str), _field(args, "kind", str),
                                              _field(args, "fields", dict), expected=_etag(args),
                                              entity_id=_field(args, "id", str, required=False)))

    def _save(self, args: Mapping[str, object]) -> dict:
        return _written(self.authoring.save(_ref(args), _field(args, "fields", dict), expected=_etag(args),
                                            head=_field(args, "head", str), etag=_etag(args, "etag")))

    def _save_raw(self, args: Mapping[str, object]) -> dict:
        return _written(self.authoring.save_raw(_ref(args), _field(args, "payload", dict), expected=_etag(args),
                                                head=_field(args, "head", str), etag=_etag(args, "etag")))

    def _derive(self, args: Mapping[str, object]) -> dict:
        return _written(self.authoring.derive(_ref(args, "base"), _field(args, "source", str), expected=_etag(args),
                                              entity_id=_field(args, "id", str, required=False),
                                              name=_field(args, "name", str, required=False)))

    def _portrayal(self, args: Mapping[str, object]) -> dict:
        return _written(self.authoring.edit_portrayal(_ref(args), _field(args, "edit", str), expected=_etag(args),
                                                      head=_field(args, "head", str), etag=_etag(args, "etag"),
                                                      name=_field(args, "name", str, required=False),
                                                      text=_field(args, "text", str, required=False)))

    def _attach(self, args: Mapping[str, object]) -> dict:
        """Bind exactly the pinned ``ref`` to ``chat`` (107): a character becomes a new actor that has
        witnessed nothing; a scenario or lorebook creates none. The chat must still be at ``expected``."""
        ref = _ref(args)
        if not ref.pinned:
            raise ApiRefused("invalid_field", "attach names one exact revision")
        role = ATTACH_ROLE.get(ref.kind)
        if role is None:
            raise ApiRefused("not_attachable", f"a {ref.kind} cannot be attached to a chat")
        try:
            chat = ChatRef.parse(_field(args, "chat", str))
        except (ValueError, TypeError):
            raise ApiRefused("invalid_field", "chat is not a chat reference") from None
        expected = _etag(args)
        try:
            self.chats.read_chat(chat)
        except RecordNotFound:
            raise ApiRefused("no_chat", f"no chat {chat}") from None
        change = self.chats.attach_source(chat, ref, role, expected=expected)
        change.commit()
        _record, etag = self.chats.read_chat(chat)
        actors = change.new_actors
        return {"chat": str(chat), "ref": str(ref), "role": role, "etag": etag,
                "actors": [str(actor) for actor in actors]}


def _effective(inspection, composed) -> dict:  # type: ignore[no-untyped-def]
    values: dict[str, object] = {"name": composed.name}
    if composed.description.strip():
        values["description"] = composed.description
    values.update({f"section:{name}": text for name, text in composed.sections})
    values.update({f"facet:{facet.name}": facet_value(facet) for facet in composed.traits})
    return {
        "nature": composed.nature,
        "chain": [{"ref": str(node.ref), "etag": node.etag} for node in composed.chain],
        "base": _str(inspection.base), "base_head": _str(inspection.base_head),
        "fields": [{"field": view.field, "state": view.state, "source": str(view.source), "upstream": view.upstream,
                    "value": values.get(view.field)} for view in inspection.fields],
        "notices": [{"code": notice.code, "ref": str(notice.ref), "detail": notice.detail}
                    for notice in inspection.notices],
        "uncomposed": [{"ref": str(ref), "field": key} for ref, key in composed.uncomposed],
    }


def _actions(ref: SourceRef, family: str, derived: bool, is_head: bool) -> dict:
    """What the record page offers: each action available, or the reason it is not."""
    actions: dict[str, str | None] = {}
    edit_reason = None if is_head else "Only the head revision is edited; open the head to change this entity."
    actions["save"] = edit_reason if family in (ENTITY, SCENARIO, LOREBOOK) else \
        "This record has no structured editor; use the raw editor."
    actions["save_raw"] = edit_reason
    actions["derive"] = None if family == ENTITY else "Only entities have portrayals."
    actions["portrayal"] = (edit_reason if derived else "Only a derived portrayal overrides or clears inherited "
                                                        "fields.")
    actions["attach"] = None if ref.kind in ATTACH_ROLE else f"A {ref.kind} cannot be attached to a chat."
    actions["delete"] = UNAVAILABLE["delete"]
    return actions


__all__ = ["ATTACH_ROLE", "NATURES", "PORTRAYAL_EDITS", "UNAVAILABLE", "SourceApi"]
