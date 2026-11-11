"""The ``bookmarks/`` family over one open ``LibraryStore``: a chat's bookmarks, one small record each.

``campaigns/<campaign>/bookmarks/<chat_id>.bm[-<device>]-<unique>.json`` holds one ``grimoire.scene.bookmarks``
record. It lies beside the chat's turns, so it syncs with the chat (docs/SYNC.md: an ordinary mutable row that
names its chat), and it is never a turn file: nothing here writes ``turns/``. A bookmark is created once under
an id no other device draws, so two devices that each add one write two files and both survive a sync; it is
then changed only by compare-and-swap against the etag it was read at (a rename, or a removal, which marks the
record ``removed`` because a record of this family is never deleted). Every write is one store transaction,
crash-safe as every record write is.

A file that is not a bookmark of this version is reported in ``ChatBookmarks.problems`` and never written
over or left out silently. See docs/CHAT_PLAY.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from grimoire.core.identity import BookmarkRef, ChatRef
from grimoire.scene import SceneRefused
from grimoire.scene.bookmarks import (
    BOOKMARK_SCHEMA, MAX_BOOKMARKS, Bookmark, bookmark_chat_id, bookmark_from_payload, bookmark_to_payload,
    clean_label, new_bookmark_id,
)
from grimoire.store import ScopeRecord, StaleWrite, StoredScopeRecord
from grimoire.store.layout import address_parts

from .library import SceneLibrary


def _unique() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class StoredBookmark:
    """A bookmark as stored, with the etag it was read at (what a rename or a removal must name)."""

    bookmark: Bookmark
    etag: str


@dataclass(frozen=True)
class ChatBookmarks:
    """One chat's bookmarks: the live ones by id, how many records are marked removed, and each file of the
    folder that could not be read as this chat's bookmark, as ``(path, code)``."""

    chat: ChatRef
    bookmarks: tuple[StoredBookmark, ...]
    removed: int = 0
    problems: tuple[tuple[str, str], ...] = ()

    def find(self, bookmark_id: str) -> StoredBookmark | None:
        return next((item for item in self.bookmarks if item.bookmark.bookmark_id == bookmark_id), None)


def _another_chats(name: str, chat: ChatRef) -> bool:
    """Whether a file name in ``bookmarks/`` spells another chat's bookmark: never read for ``chat``. A name
    that spells no chat's bookmark is read, and reported for every chat."""
    if not name.endswith(".json"):
        return False
    owner = bookmark_chat_id(name[:-len(".json")])
    return owner is not None and owner != chat.chat_id


class BookmarkLibrary:
    """Bookmarks of the chats of one open library, beside ``scenes`` (whose handle and owner it writes with).
    ``unique`` draws the fresh part of a new id."""

    def __init__(self, scenes: SceneLibrary, *, unique: Callable[[], str] = _unique) -> None:
        self.scenes = scenes
        self.store = scenes.store
        self._unique = unique

    # -- reading -----------------------------------------------------------------------------------------
    def _read(self, chat: ChatRef) -> tuple[dict[str, tuple[Bookmark, StoredScopeRecord]], list[tuple[str, str]]]:
        found = self.store.campaign_bookmarks(chat.campaign_id, skip=lambda name: _another_chats(name, chat))
        problems = [(problem.path, problem.code) for problem in found.problems]
        read: dict[str, tuple[Bookmark, StoredScopeRecord]] = {}
        for ref, stored in found.records.items():
            path = "/".join(address_parts(ref))
            try:
                bookmark = bookmark_from_payload(ref, stored.record.schema, stored.record.payload)  # type: ignore[arg-type]
            except SceneRefused as error:
                problems.append((path, error.reason))
                continue
            if bookmark.chat != chat:  # the id says this chat, the record another: neither chat's
                problems.append((path, "malformed"))
                continue
            read[bookmark.bookmark_id] = (bookmark, stored)
        return read, problems

    def bookmarks(self, chat: ChatRef) -> ChatBookmarks:
        read, problems = self._read(chat)
        live = tuple(StoredBookmark(bookmark, stored.etag) for _id, (bookmark, stored) in sorted(read.items())
                     if not bookmark.removed)
        return ChatBookmarks(chat, live, len(read) - len(live), tuple(sorted(problems)))

    # -- writing -----------------------------------------------------------------------------------------
    def _put(self, bookmark: Bookmark, base: StoredScopeRecord | None) -> StoredBookmark:
        payload = bookmark_to_payload(bookmark)
        if base is None:
            envelope = ScopeRecord(bookmark.ref, BOOKMARK_SCHEMA, payload, owner=self.scenes.owner)
        else:
            envelope = ScopeRecord(bookmark.ref, BOOKMARK_SCHEMA, payload, owner=base.record.owner,
                                   extra=base.record.extra)
        txn = self.store.transaction()
        txn.put(envelope, None if base is None else base.etag)
        result = txn.commit(self.scenes.handle)
        return StoredBookmark(bookmark, result.etags["/".join(address_parts(bookmark.ref))])

    def add(self, chat: ChatRef, turn: str, label: object = None) -> StoredBookmark:
        """A new bookmark on ``turn`` (the id of a turn on this chat's line: ``unknown_turn`` otherwise), with
        ``label`` cleaned. Refused ``too_many_bookmarks`` at ``MAX_BOOKMARKS`` records for the chat."""
        cleaned = clean_label(label)
        with self.store.lock:
            line = {stored.record.turn_id for stored in self.scenes.turns(chat).turns}
            if type(turn) is not str or turn not in line:
                raise SceneRefused("unknown_turn", "that turn is not on this chat's line")
            read, _problems = self._read(chat)
            if len(read) >= MAX_BOOKMARKS:
                raise SceneRefused("too_many_bookmarks", f"a chat holds at most {MAX_BOOKMARKS} bookmarks")
            for _attempt in range(8):
                bookmark_id = new_bookmark_id(chat, self.scenes.device(), self._unique())
                if bookmark_id in read:
                    continue
                try:
                    BookmarkRef(chat.campaign_id, bookmark_id)
                except ValueError as error:
                    raise SceneRefused("invalid_field", str(error)[:200]) from None
                try:
                    return self._put(Bookmark(chat, bookmark_id, turn, cleaned), None)
                except StaleWrite:  # a file of that name the read above could not see as a bookmark: another id
                    continue
            raise SceneRefused("changed", "no fresh bookmark id could be drawn; try again")

    def _current(self, chat: ChatRef, bookmark_id: object, expected: object) -> tuple[Bookmark, StoredScopeRecord]:
        read, _problems = self._read(chat)
        found = read.get(bookmark_id) if type(bookmark_id) is str else None
        if found is None or found[0].removed:
            raise SceneRefused("unknown_bookmark", "this chat has no such bookmark")
        if found[1].etag != expected:
            raise SceneRefused("changed", "the bookmark changed since it was read; read the chat's story again")
        return found

    def rename(self, chat: ChatRef, bookmark_id: str, label: object, *, expected: str) -> StoredBookmark:
        """Set (or, with ``None`` or blank, clear) a bookmark's label, over the etag it was read at."""
        cleaned = clean_label(label)
        with self.store.lock:
            bookmark, stored = self._current(chat, bookmark_id, expected)
            if bookmark.label == cleaned:
                return StoredBookmark(bookmark, stored.etag)
            return self._put(bookmark.relabelled(cleaned), stored)

    def remove(self, chat: ChatRef, bookmark_id: str, *, expected: str) -> StoredBookmark:
        """Mark a bookmark removed, over the etag it was read at: no list shows it again."""
        with self.store.lock:
            bookmark, stored = self._current(chat, bookmark_id, expected)
            return self._put(Bookmark(bookmark.chat, bookmark.bookmark_id, bookmark.turn, bookmark.label, True), stored)


__all__ = ["BookmarkLibrary", "ChatBookmarks", "StoredBookmark"]
