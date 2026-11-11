"""The person's own organisation of a library: tags and pins on entries, pins on chats, saved filters.

These are annotations *about* an entry or a chat, never part of it: they are keyed by the exact unpinned
``SourceRef`` or the ``ChatRef``, make no revision of a source, change nothing a chat binds, and outlive any
new revision of the entry they name. This package is the pure model and its codec; the files are
``grimoire.shelfstore``'s. See docs/SHELF.md.
"""

from .codec import (
    CHAT_FORMAT, ENTRY_FORMAT, FILTER_FORMAT, chat_from_document, chat_to_document, entry_from_document,
    entry_to_document, filter_from_document, filter_to_document, record_name,
)
from .model import (
    MAX_FILTER_NAME, MAX_FILTERS, MAX_QUERY, MAX_TAG_CHARS, MAX_TAGS, ChatMarks, EntryMarks, SavedFilter,
    ShelfError, check_filter_id, clean_tags, edit_tags, filter_name, respell, tag_key, tag_name, vocabulary,
)

__all__ = [
    "CHAT_FORMAT", "ENTRY_FORMAT", "FILTER_FORMAT", "MAX_FILTERS", "MAX_FILTER_NAME", "MAX_QUERY", "MAX_TAGS",
    "MAX_TAG_CHARS", "ChatMarks", "EntryMarks", "SavedFilter", "ShelfError", "chat_from_document",
    "chat_to_document", "check_filter_id", "clean_tags", "edit_tags", "entry_from_document", "entry_to_document",
    "filter_from_document", "filter_name", "filter_to_document", "record_name", "respell", "tag_key", "tag_name",
    "vocabulary",
]
