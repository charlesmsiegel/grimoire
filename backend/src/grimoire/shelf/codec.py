"""The three shelf records as JSON documents, read strictly. Pure: a document is an already-decoded mapping.

Each record is one small object with a ``format`` that names its version. Reading refuses anything that is not
exactly a record of that version, with a reason (``unsupported_format``, ``malformed``): a file from a newer
app, a hand edit gone wrong or half of two versions is reported and left alone, never guessed at. Writing is
deterministic (no time, no device, tags in key order), so two devices that make the same change write the same
bytes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from grimoire.core.identity import ChatRef, SourceRef

from .model import ChatMarks, EntryMarks, SavedFilter, ShelfError, tag_key, tag_name

ENTRY_FORMAT = "grimoire.shelf.entry/1"
CHAT_FORMAT = "grimoire.shelf.chat/1"
FILTER_FORMAT = "grimoire.shelf.filter/1"

_ENTRY_KEYS = frozenset({"format", "ref", "tags", "pinned"})
_CHAT_KEYS = frozenset({"format", "chat", "pinned"})
_FILTER_KEYS = frozenset({"format", "id", "name", "role", "shelf", "source", "tags", "query", "sort"})


def record_name(ref: SourceRef | ChatRef) -> str:
    """The file stem of the record about ``ref``: a digest of its exact text. A ref holds ``:`` and ``/`` and
    may be long, so it is never a file name; the record names its ref inside, and the two must agree."""
    return hashlib.sha256(str(ref).encode("utf-8")).hexdigest()[:32]


def _shape(document: object, format_id: str, keys: frozenset[str], what: str) -> Mapping[str, object]:
    if not isinstance(document, Mapping):
        raise ShelfError("malformed", f"{what} must be a JSON object")
    found = document.get("format")
    if found != format_id:
        family = format_id.rsplit("/", 1)[0] + "/"
        if type(found) is str and found.startswith(family):
            raise ShelfError("unsupported_format", f"{what} is {found}; this app reads {format_id}")
        raise ShelfError("malformed", f"{what} does not say it is {format_id}")
    if set(document) != keys:
        extra, missing = sorted(set(document) - keys), sorted(keys - set(document))
        raise ShelfError("malformed", f"{what} has " + "; ".join(
            part for part in (extra and "unknown " + ", ".join(extra), missing and "no " + ", ".join(missing)) if part))
    return document


def _tags(value: object, what: str) -> tuple[str, ...]:
    """Tags exactly as a writer leaves them: valid names, one per key. Their order is not checked."""
    if not isinstance(value, (list, tuple)):
        raise ShelfError("malformed", f"{what}: tags must be a list")
    seen: set[str] = set()
    for item in value:
        try:
            name = tag_name(item)
        except ShelfError as error:
            raise ShelfError("malformed", f"{what}: {error}") from None
        if name != item:
            raise ShelfError("malformed", f"{what}: the tag {item!r} is not trimmed and normalized")
        if tag_key(name) in seen:
            raise ShelfError("malformed", f"{what}: the tag {name!r} is listed twice")
        seen.add(tag_key(name))
    return tuple(value)


def _bool(value: object, what: str) -> bool:
    if type(value) is not bool:
        raise ShelfError("malformed", f"{what}: pinned must be true or false")
    return value


def _made(build, what: str):  # type: ignore[no-untyped-def]
    try:
        return build()
    except ShelfError as error:
        raise ShelfError("malformed", f"{what}: {error}") from None


def entry_to_document(marks: EntryMarks) -> dict[str, object]:
    return {"format": ENTRY_FORMAT, "ref": str(marks.ref), "tags": list(marks.tags), "pinned": marks.pinned}


def entry_from_document(document: object) -> EntryMarks:
    found = _shape(document, ENTRY_FORMAT, _ENTRY_KEYS, "an entry's marks")
    try:
        ref = SourceRef.parse(found["ref"])
    except (ValueError, TypeError):
        raise ShelfError("malformed", "an entry's marks: ref is not a definition reference") from None
    tags, pinned = _tags(found["tags"], "an entry's marks"), _bool(found["pinned"], "an entry's marks")
    return _made(lambda: EntryMarks(ref, tags, pinned), "an entry's marks")


def chat_to_document(marks: ChatMarks) -> dict[str, object]:
    return {"format": CHAT_FORMAT, "chat": str(marks.chat), "pinned": marks.pinned}


def chat_from_document(document: object) -> ChatMarks:
    found = _shape(document, CHAT_FORMAT, _CHAT_KEYS, "a chat's marks")
    try:
        chat = ChatRef.parse(found["chat"])
    except (ValueError, TypeError):
        raise ShelfError("malformed", "a chat's marks: chat is not a chat reference") from None
    return ChatMarks(chat, _bool(found["pinned"], "a chat's marks"))


def filter_to_document(saved: SavedFilter) -> dict[str, object]:
    return {"format": FILTER_FORMAT, "id": saved.id, "name": saved.name, "role": saved.role, "shelf": saved.shelf,
            "source": saved.source, "tags": list(saved.tags), "query": saved.query, "sort": saved.sort}


def filter_from_document(document: object) -> SavedFilter:
    found = _shape(document, FILTER_FORMAT, _FILTER_KEYS, "a saved filter")
    tags = _tags(found["tags"], "a saved filter")
    source = found["source"]
    if source is not None and type(source) is not str:
        raise ShelfError("malformed", "a saved filter: source must be a source id or null")
    saved = _made(lambda: SavedFilter(found["id"], found["name"], found["role"], found["shelf"], source, tags,  # type: ignore[arg-type]
                                      found["query"], found["sort"]), "a saved filter")  # type: ignore[arg-type]
    if saved.name != found["name"] or saved.query != found["query"]:
        raise ShelfError("malformed", "a saved filter: its name or search is not trimmed and normalized")
    return saved


__all__ = ["CHAT_FORMAT", "ENTRY_FORMAT", "FILTER_FORMAT", "chat_from_document", "chat_to_document",
           "entry_from_document", "entry_to_document", "filter_from_document", "filter_to_document", "record_name"]
