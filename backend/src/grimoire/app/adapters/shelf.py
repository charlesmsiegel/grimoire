"""The shelf's adapter ops: tags and pins on library entries, pins on chats and saved filters. Called as
``shelf.<op>``; see docs/SHELF.md and docs/APP_SHELL.md.

Every op is ``grimoire.shelfstore.ShelfStore``'s, over the open library's own folder and under the text API's one
writer lock. The shelf is the person's notes *about* entries and chats: no op here writes a source, a revision or
a chat record. A new tag or pin is given only to an entry or chat the library holds now; taking one away is
always allowed, and a note about something no longer here is kept (it may come back with a sync) and listed as it
is, for the page to leave out.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping

from grimoire.app.adapter import Adapter, AdapterContext, Handler
from grimoire.core.identity import ChatRef, SourceRef
from grimoire.shelf import (
    MAX_FILTER_NAME, MAX_FILTERS, MAX_QUERY, MAX_TAG_CHARS, MAX_TAGS, SavedFilter, ShelfError, respell, tag_key,
    vocabulary,
)
from grimoire.shelfstore import ShelfStore, Stored
from grimoire.store import RecordNotFound, StoreError
from grimoire.textapi import ApiRefused

#: A refusal that is the request's own fault (400) or names something that is not there (404); the rest are 409.
_BAD = frozenset({"invalid_tag", "too_many_tags", "invalid_name", "invalid_filter", "invalid_ref", "invalid_field"})
_MISSING = frozenset({"no_filter", "no_entry"})
_ABSENT = object()


def _shelf(ctx: AdapterContext) -> ShelfStore:
    return ShelfStore(ctx.store.root)


def _refuse(error: ShelfError) -> ApiRefused:
    status = 400 if error.reason in _BAD else 404 if error.reason in _MISSING else 409
    return ApiRefused(error.reason, str(error), status)


def _writable(ctx: AdapterContext) -> None:
    if not ctx.store.writable:
        raise ApiRefused("read_only", "this library is open read-only")


def _expected(args: Mapping[str, object]) -> str | None:
    """The etag the client read, or ``None`` when it read no record. The key itself is required, so a client
    that never looked cannot write over what is there."""
    value = args.get("expected", _ABSENT)
    if value is _ABSENT or (value is not None and type(value) is not str):
        raise ApiRefused("invalid_field", "expected must be the etag shelf.read gave, or null when it gave none")
    return value  # type: ignore[return-value]


def _flag(args: Mapping[str, object], key: str) -> bool:
    value = args.get(key)
    if type(value) is not bool:
        raise ApiRefused("invalid_field", f"{key} must be true or false")
    return value


def _names(args: Mapping[str, object], key: str, *, required: bool = True) -> list[object]:
    value = args.get(key, _ABSENT)
    if value is _ABSENT and not required:
        return []
    if not isinstance(value, list):
        raise ApiRefused("invalid_field", f"{key} must be a list of tag names")
    return value


def _text(args: Mapping[str, object], key: str, default: str = "") -> str:
    value = args.get(key, default)
    if value is None:
        return default
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be text")
    return value


def _entry_ref(args: Mapping[str, object]) -> SourceRef:
    text = args.get("ref")
    try:
        ref = SourceRef.parse(text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", "ref is not a definition reference") from None
    if ref.pinned:
        raise ApiRefused("invalid_ref", "tags and pins belong to the entry, not to one revision: name it unpinned", 400)
    return ref


def _chat_ref(args: Mapping[str, object]) -> ChatRef:
    try:
        return ChatRef.parse(args.get("chat"))
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", "chat is not a chat reference") from None


def _require_entry(ctx: AdapterContext, ref: SourceRef) -> None:
    """Refuse ``no_entry`` unless the library holds this entity now (at any revision, playable or not). An entry
    that resolves is found without listing its source; only one that does not is looked for among the files."""
    try:
        if not isinstance(ctx.store.check_definition(ref), str):
            return
        held = (ref.kind, ref.entity_id) in ctx.store.source_entities(ref.source)
    except (StoreError, ValueError, OSError):
        held = False
    if not held:
        raise ApiRefused("no_entry", f"no entry {ref} is in this library", 404)


def _require_chat(ctx: AdapterContext, chat: ChatRef) -> None:
    try:
        ctx.store.read(chat)
    except RecordNotFound:
        raise ApiRefused("no_chat", f"no chat {chat} is in this library") from None
    except StoreError:
        pass  # it is there, if not readable: the pin is about the chat, not its content


def _entry_view(ref: SourceRef, stored: Stored | None, known: Mapping[str, str] | None = None) -> dict:  # type: ignore[type-arg]
    if stored is None:
        return {"ref": str(ref), "tags": [], "pinned": False, "etag": None}
    tags = stored.value.tags if known is None else respell(stored.value.tags, known)
    return {"ref": str(ref), "tags": list(tags), "pinned": stored.value.pinned, "etag": stored.etag}


def _chat_view(chat: ChatRef, stored: Stored | None) -> dict:  # type: ignore[type-arg]
    return {"chat": str(chat), "pinned": bool(stored and stored.value.pinned), "etag": stored.etag if stored else None}


def _filter_view(stored: Stored, known: Mapping[str, str] | None = None) -> dict:  # type: ignore[type-arg]
    saved: SavedFilter = stored.value
    tags = saved.tags if known is None else respell(saved.tags, known)
    return {"id": saved.id, "name": saved.name, "role": saved.role, "shelf": saved.shelf, "source": saved.source,
            "tags": list(tags), "query": saved.query, "sort": saved.sort, "etag": stored.etag}


# -- the ops -----------------------------------------------------------------------------------------------
def _read(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Everything on the shelf. Each tag shows in one spelling everywhere (``grimoire.shelf.vocabulary``), so a
    page compares tags as exact text. ``problems`` names every file that is not a readable record."""
    view = _shelf(ctx).view()
    known = vocabulary(stored.value.tags for stored in view.entries.values())
    tags: dict[str, int] = {}
    for stored in view.entries.values():
        for name in stored.value.tags:
            tags[known[tag_key(name)]] = tags.get(known[tag_key(name)], 0) + 1
    return {
        "entries": [_entry_view(ref, view.entries[ref], known) for ref in sorted(view.entries, key=str)],
        "chats": [_chat_view(chat, view.chats[chat]) for chat in sorted(view.chats, key=str)],
        "filters": [_filter_view(stored, known) for stored in sorted(
            view.filters.values(), key=lambda stored: (tag_key(stored.value.name), stored.value.id))],
        "tags": [{"tag": name, "entries": tags[name]} for name in sorted(tags, key=lambda name: (tag_key(name), name))],
        "problems": [{"path": problem.path, "reason": problem.reason, "detail": problem.detail}
                     for problem in view.problems],
        "limits": {"tag_chars": MAX_TAG_CHARS, "tags": MAX_TAGS, "filter_name_chars": MAX_FILTER_NAME,
                   "query_chars": MAX_QUERY, "filters": MAX_FILTERS},
    }


def _set_tags(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Give one entry exactly ``tags`` (an empty list clears them)."""
    ref, tags, expected = _entry_ref(args), _names(args, "tags"), _expected(args)
    if tags:
        _require_entry(ctx, ref)
    return _entry_view(ref, _shelf(ctx).set_tags(ref, tags, expected))


def _edit_tags(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Add ``add`` to one entry's tags and take ``remove`` away, leaving its other tags (what a bulk Tag does)."""
    ref, expected = _entry_ref(args), _expected(args)
    add, remove = _names(args, "add", required=False), _names(args, "remove", required=False)
    if not add and not remove:
        raise ApiRefused("invalid_field", "name at least one tag to add or remove")
    if add:
        _require_entry(ctx, ref)
    return _entry_view(ref, _shelf(ctx).edit_tags(ref, add, remove, expected))


def _pin_entry(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    ref, pinned, expected = _entry_ref(args), _flag(args, "pinned"), _expected(args)
    if pinned:
        _require_entry(ctx, ref)
    return _entry_view(ref, _shelf(ctx).pin_entry(ref, pinned, expected))


def _pin_chat(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat, pinned, expected = _chat_ref(args), _flag(args, "pinned"), _expected(args)
    if pinned:
        _require_chat(ctx, chat)
    return _chat_view(chat, _shelf(ctx).pin_chat(chat, pinned, expected))


def _save_filter(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Keep a new saved filter: a name for one role, shelf (or source), set of tags, search and order."""
    source = args.get("source")
    if source is not None and type(source) is not str:
        raise ApiRefused("invalid_field", "source must be a source id")
    fields = dict(name=args.get("name"), role=_text(args, "role"), shelf=_text(args, "shelf"), source=source or None,
                  tags=tuple(_names(args, "tags", required=False)), query=_text(args, "query"), sort=_text(args, "sort"))
    shelf = _shelf(ctx)
    for _attempt in range(4):  # an id is random; one already used is tried again
        saved = SavedFilter(id="f-" + secrets.token_hex(6), **fields)  # type: ignore[arg-type]
        try:
            return _filter_view(shelf.save_filter(saved))
        except ShelfError as error:
            if error.reason != "filter_exists":
                raise
    raise ApiRefused("filter_exists", "no free id for a saved filter; try again")


def _rename_filter(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    return _filter_view(_shelf(ctx).rename_filter(_text(args, "id"), args.get("name"), _expected(args)))


def _delete_filter(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    filter_id = _text(args, "id")
    _shelf(ctx).delete_filter(filter_id, _expected(args))
    return {"id": filter_id, "deleted": True}


def _op(work: Callable[[AdapterContext, Mapping[str, object]], dict], *, writes: bool) -> Handler:
    def handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        if writes:
            _writable(ctx)
        try:
            return work(ctx, args)
        except ShelfError as error:
            raise _refuse(error) from None
        except OSError as error:
            raise ApiRefused("shelf_unwritable" if writes else "shelf_unreadable",
                             f"the shelf's files cannot be {'written' if writes else 'read'} "
                             f"({error.strerror or type(error).__name__})") from None

    handler.__name__ = "shelf" + work.__name__
    return handler


#: The ops that change the shelf; ``read`` is the only other one.
WRITES = {"set_tags": _set_tags, "edit_tags": _edit_tags, "pin_entry": _pin_entry, "pin_chat": _pin_chat,
          "save_filter": _save_filter, "rename_filter": _rename_filter, "delete_filter": _delete_filter}

ADAPTER = Adapter("shelf", owner="109", ops={
    "read": _op(_read, writes=False), **{name: _op(work, writes=True) for name, work in WRITES.items()}})

__all__ = ["ADAPTER", "WRITES"]
