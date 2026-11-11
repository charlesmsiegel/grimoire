"""A person's bookmarks on the moments of one chat: a turn, marked, with an optional short label. Pure.

A bookmark is a note about the transcript and never part of it: it changes no turn, is heard by no one and is
read by no generation. It names its chat and the turn by the turn's stable id (never its display number, which
a fork choice can change), so it means the same moment on every device. One bookmark is one record, so two
devices that each add one never write the same file. A removed bookmark stays as a record marked ``removed``
(a record of this family is never deleted), which no list shows. See docs/CHAT_PLAY.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from grimoire.core.identity import BookmarkRef, ChatRef, validate_segment

from .model import SceneRefused, parse_turn_id

BOOKMARK_SCHEMA = "grimoire.chat-bookmark/1"
#: A label is a few words beside a dot on a timeline.
MAX_BOOKMARK_LABEL = 120
#: Bookmarks one chat holds, removed ones included: each is a file read whenever the chat's timeline is.
MAX_BOOKMARKS = 500
_KEYS = frozenset({"chat", "turn", "label", "removed"})


def _fail(reason: str, message: str = "") -> SceneRefused:
    return SceneRefused(reason, message)


def clean_label(value: object) -> str | None:
    """A label as stored: ``None`` for none or blank, else its text trimmed, on one line, of at most
    ``MAX_BOOKMARK_LABEL`` characters. Anything else is refused ``invalid_field``."""
    if value is None:
        return None
    if type(value) is not str:
        raise _fail("invalid_field", "a bookmark's label is text")
    text = " ".join(value.split())
    if len(text) > MAX_BOOKMARK_LABEL:
        raise _fail("invalid_field", f"a bookmark's label is at most {MAX_BOOKMARK_LABEL} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise _fail("invalid_field", "a bookmark's label holds no control characters")
    return text or None


def bookmark_chat_id(bookmark_id: object) -> str | None:
    """The chat id a bookmark id belongs to (``<chat_id>.<suffix>``, the suffix without a dot), or ``None``."""
    if type(bookmark_id) is not str:
        return None
    chat_id, dot, suffix = bookmark_id.rpartition(".")
    return chat_id if dot and chat_id and suffix else None


def new_bookmark_id(chat: ChatRef, device: str | None, unique: str) -> str:
    """``<chat_id>.bm[-<device>]-<unique>``: ``device`` is the library's device tag (eight hex digits, or
    ``None`` with no identity) and ``unique`` text drawn fresh, so two devices never write one id."""
    suffix = "bm" + ("" if device is None else f"-{device}") + f"-{unique}"
    if "." in suffix:
        raise _fail("invalid_field", "a bookmark id's own part holds no dot")
    return f"{chat.chat_id}.{suffix}"


@dataclass(frozen=True, slots=True)
class Bookmark:
    """One bookmark: ``turn`` is the id of a turn of ``chat``; ``label`` is ``None`` or its cleaned text."""

    chat: ChatRef
    bookmark_id: str
    turn: str
    label: str | None = None
    removed: bool = False

    def __post_init__(self) -> None:
        if type(self.chat) is not ChatRef:
            raise _fail("invalid_field", "a bookmark's chat is a ChatRef")
        try:
            validate_segment(self.bookmark_id, "bookmark_id")
            BookmarkRef(self.chat.campaign_id, self.bookmark_id)
        except (ValueError, TypeError) as error:
            raise _fail("invalid_field", str(error)[:200]) from None
        if bookmark_chat_id(self.bookmark_id) != self.chat.chat_id:
            raise _fail("invalid_field", "a bookmark's id starts with its chat's id")
        found = parse_turn_id(self.turn) if type(self.turn) is str else None
        if found is None or found[0] != self.chat.chat_id:
            raise _fail("invalid_field", "a bookmark names a turn of its own chat by id")
        if self.label is not None and (type(self.label) is not str or self.label != clean_label(self.label)):
            raise _fail("invalid_field", "a bookmark's label is trimmed text on one line, or none")
        if type(self.removed) is not bool:
            raise _fail("invalid_field", "removed is true or false")

    @property
    def ref(self) -> BookmarkRef:
        return BookmarkRef(self.chat.campaign_id, self.bookmark_id)

    def relabelled(self, label: object) -> Bookmark:
        return replace(self, label=clean_label(label))


def bookmark_to_payload(bookmark: Bookmark) -> dict[str, object]:
    return {"chat": str(bookmark.chat), "turn": bookmark.turn, "label": bookmark.label, "removed": bookmark.removed}


def bookmark_from_payload(ref: BookmarkRef, schema: object, payload: object) -> Bookmark:
    """The bookmark stored at ``ref``. Refused (``SceneRefused``, with a reason) for another schema, a payload
    that is not exactly a bookmark's, or one that names another campaign's or another chat's turn."""
    if schema != BOOKMARK_SCHEMA:
        raise _fail("unsupported_schema", f"not a {BOOKMARK_SCHEMA} payload")
    if not isinstance(payload, Mapping) or set(payload) != _KEYS:
        raise _fail("malformed", "a bookmark holds exactly chat, turn, label and removed")
    try:
        chat = ChatRef.parse(payload["chat"])
    except (ValueError, TypeError):
        raise _fail("malformed", "a bookmark's chat is a chat reference") from None
    if chat.campaign_id != ref.campaign_id:
        raise _fail("malformed", "a bookmark belongs to its chat's campaign")
    try:
        return Bookmark(chat, ref.bookmark_id, payload["turn"], payload["label"], payload["removed"])  # type: ignore[arg-type]
    except SceneRefused as error:
        raise _fail("malformed", str(error)[:200]) from None


__all__ = ["BOOKMARK_SCHEMA", "Bookmark", "MAX_BOOKMARKS", "MAX_BOOKMARK_LABEL", "bookmark_chat_id",
           "bookmark_from_payload", "bookmark_to_payload", "clean_label", "new_bookmark_id"]
