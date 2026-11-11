"""Tags, pins and saved filters as values, and the rules of a tag's name. Pure: no files, no clock.

A **tag** is a short name the person gives. Its identity is its ``tag_key`` (NFC, casefolded), so ``Noir`` and
``noir`` are one tag; the spelling shown is the one most entries use (``vocabulary``). An entry's marks are its
tags and whether it is pinned; a chat's are whether it is pinned. A **saved filter** names one view of
Characters and lore: a role, a shelf, tags (all of them must match), a search and an order. The role, the shelf
and the order are the page's own words, kept here as short tokens and not interpreted.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.identity import ChatRef, SourceRef, validate_source_id

#: The most characters of one tag, and the most tags on one entry or in one saved filter.
MAX_TAG_CHARS = 40
MAX_TAGS = 32
#: The most characters of a saved filter's name and of its search text, and the most saved filters.
MAX_FILTER_NAME = 60
MAX_QUERY = 200
MAX_FILTERS = 200

_SPACES = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_FILTER_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


class ShelfError(ValueError):
    """A tag, pin or saved filter that is refused; nothing was written. ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _unseen(char: str) -> bool:
    """Control (a tab and a line break too), surrogate, private, unassigned and format characters (a zero-width
    joiner, a direction override) and line or paragraph separators: none shows as itself in a name."""
    category = unicodedata.category(char)
    return category.startswith("C") or category in ("Zl", "Zp")


def _plain(text: object, what: str, limit: int, reason: str) -> str:
    """``text`` trimmed, NFC, with each run of spaces as one space; refused when it is not text, is empty, is
    too long or holds a character that does not show."""
    if type(text) is not str:
        raise ShelfError(reason, f"{what} must be text")
    if any(_unseen(char) for char in text):
        raise ShelfError(reason, f"{what} may not hold control or invisible characters")
    cleaned = _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip()
    if not cleaned:
        raise ShelfError(reason, f"{what} may not be empty")
    if len(cleaned) > limit:
        raise ShelfError(reason, f"{what} is at most {limit} characters")
    return cleaned


def tag_name(text: object) -> str:
    """A tag's name as it is kept: trimmed, single-spaced, NFC. Refused ``invalid_tag`` when it is empty, longer
    than ``MAX_TAG_CHARS``, holds a control or invisible character, or holds a comma (tags are listed with
    commas wherever a person types or links several)."""
    name = _plain(text, "a tag", MAX_TAG_CHARS, "invalid_tag")
    if "," in name:
        raise ShelfError("invalid_tag", "a tag may not hold a comma")
    return name


def tag_key(name: str) -> str:
    """What makes two spellings one tag: the name casefolded (after NFC)."""
    return unicodedata.normalize("NFC", name).casefold()


def clean_tags(names: Iterable[object], *, limit: int = MAX_TAGS) -> tuple[str, ...]:
    """Valid tag names, one per key (the first spelling given wins), in key order."""
    if isinstance(names, (str, bytes, Mapping)) or not isinstance(names, Iterable):
        raise ShelfError("invalid_tag", "tags must be a list of names")
    found: dict[str, str] = {}
    for item in names:
        name = tag_name(item)
        found.setdefault(tag_key(name), name)
    if len(found) > limit:
        raise ShelfError("too_many_tags", f"at most {limit} tags")
    return tuple(found[key] for key in sorted(found))


def edit_tags(current: Iterable[str], add: Iterable[object] = (), remove: Iterable[object] = ()) -> tuple[str, ...]:
    """``current`` with ``add`` added and ``remove`` taken away, by key. A tag already there keeps its spelling;
    a tag named in both is refused, since which was meant cannot be known."""
    adding, removing = clean_tags(add), {tag_key(name) for name in clean_tags(remove)}
    if any(tag_key(name) in removing for name in adding):
        raise ShelfError("invalid_tag", "a tag cannot be both added and removed")
    kept = [name for name in current if tag_key(name) not in removing]
    return clean_tags([*kept, *adding])


def vocabulary(tag_sets: Iterable[Iterable[str]]) -> dict[str, str]:
    """Each tag in use, by key, with the one spelling it is shown in: the spelling most sets use, and the
    first in code-point order among equals. Two devices that tagged apart with different capitals therefore
    show one tag, the same one on both."""
    counts: dict[str, dict[str, int]] = {}
    for tags in tag_sets:
        for name in tags:
            spellings = counts.setdefault(tag_key(name), {})
            spellings[name] = spellings.get(name, 0) + 1
    return {key: min(spellings, key=lambda name: (-spellings[name], name)) for key, spellings in counts.items()}


def respell(tags: Iterable[str], known: Mapping[str, str]) -> tuple[str, ...]:
    """``tags`` in the spelling ``known`` (a ``vocabulary``) shows each in; a tag it does not hold stays as it is."""
    return clean_tags(known.get(tag_key(name), name) for name in tags)


def filter_name(text: object) -> str:
    """A saved filter's name as it is kept (trimmed, single-spaced); refused ``invalid_name`` otherwise."""
    return _plain(text, "a saved filter's name", MAX_FILTER_NAME, "invalid_name")


def check_filter_id(value: object) -> str:
    if type(value) is not str or not _FILTER_ID.fullmatch(value):
        raise ShelfError("invalid_filter", "a saved filter's id is lower-case letters, digits and hyphens")
    return value


def _token(value: object, what: str) -> str:
    if type(value) is not str or (value and not _TOKEN.fullmatch(value)):
        raise ShelfError("invalid_filter", f"{what} must be a short lower-case word, or empty")
    return value


@dataclass(frozen=True)
class EntryMarks:
    """What the person noted about one library entry, whatever revision it is at."""

    ref: SourceRef
    tags: tuple[str, ...] = ()
    pinned: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.ref, SourceRef) or self.ref.pinned:
            raise ShelfError("invalid_ref", "tags and pins name an entry by its unpinned ref, never one revision")
        if type(self.pinned) is not bool:
            raise ShelfError("invalid_field", "pinned must be true or false")
        object.__setattr__(self, "tags", clean_tags(self.tags))

    @property
    def empty(self) -> bool:
        return not self.tags and not self.pinned


@dataclass(frozen=True)
class ChatMarks:
    """What the person noted about one chat."""

    chat: ChatRef
    pinned: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.chat, ChatRef):
            raise ShelfError("invalid_ref", "a pin names a chat by its exact ref")
        if type(self.pinned) is not bool:
            raise ShelfError("invalid_field", "pinned must be true or false")

    @property
    def empty(self) -> bool:
        return not self.pinned


@dataclass(frozen=True)
class SavedFilter:
    """One named view of Characters and lore. ``source`` is one source's shelf (then ``shelf`` is empty)."""

    id: str
    name: str
    role: str = ""
    shelf: str = ""
    source: str | None = None
    tags: tuple[str, ...] = ()
    query: str = ""
    sort: str = ""

    def __post_init__(self) -> None:
        check_filter_id(self.id)
        object.__setattr__(self, "name", filter_name(self.name))
        _token(self.role, "role")
        _token(self.shelf, "shelf")
        _token(self.sort, "sort")
        if self.source is not None:
            try:
                validate_source_id(self.source)
            except ValueError:
                raise ShelfError("invalid_filter", "source is not a source id") from None
            if self.shelf:
                raise ShelfError("invalid_filter", "a saved filter names one source or one shelf, not both")
        if type(self.query) is not str:
            raise ShelfError("invalid_filter", "query must be text")
        if self.query:
            object.__setattr__(self, "query", _plain(self.query, "a search", MAX_QUERY, "invalid_filter"))
        object.__setattr__(self, "tags", clean_tags(self.tags))

    def renamed(self, name: object) -> SavedFilter:
        return SavedFilter(self.id, filter_name(name), self.role, self.shelf, self.source, self.tags, self.query,
                           self.sort)


__all__ = ["MAX_FILTERS", "MAX_FILTER_NAME", "MAX_QUERY", "MAX_TAGS", "MAX_TAG_CHARS", "ChatMarks", "EntryMarks",
           "SavedFilter", "ShelfError", "check_filter_id", "clean_tags", "edit_tags", "filter_name", "respell",
           "tag_key", "tag_name", "vocabulary"]
