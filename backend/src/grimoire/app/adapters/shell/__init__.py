"""Sprint 108's adapter ops: the app shell, onboarding and navigation. Called as ``shell.<op>``.

Only Sprint 108 edits this package (``ADAPTER`` stays at its top). Each op is ``handler(ctx, args) -> dict`` over
an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.

``search_lines`` finds the lines that were said, for the command palette: plain text matching over what the chats'
own views show (``lines``), with no model call. It reads only.
"""

from __future__ import annotations

from collections.abc import Mapping
from weakref import WeakKeyDictionary

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.textapi import ApiRefused, TextApi, field

from .lines import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_QUERY,
    MAX_WORDS,
    MIN_QUERY,
    LineIndex,
    words_of,
)

#: Each open library's line index, for as long as its text API lives. Derived and in memory only: dropping one
#: loses nothing.
_INDEXES: WeakKeyDictionary[TextApi, LineIndex] = WeakKeyDictionary()


def line_index(ctx: AdapterContext) -> LineIndex:
    """The open library's line index, made the first time it is asked for."""
    index = _INDEXES.get(ctx.api)
    if index is None:
        index = _INDEXES[ctx.api] = LineIndex(ctx.store, ctx.handle, owner=ctx.owner)
    return index


def _search_lines(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The lines every word of ``query`` is in (case and accents do not count), the most recent chat first: each with its
    chat's ref and title, its turn's number, who said it and the words around the match. ``complete`` is false
    when a cap stopped the search before every chat was looked at (``stopped`` says which)."""
    query = field(args, "query", str)
    if len(query) > MAX_QUERY:
        raise ApiRefused("invalid_field", f"query is at most {MAX_QUERY} characters")
    words = words_of(query)
    if sum(len(word) for word in words) < MIN_QUERY:
        raise ApiRefused("invalid_field", f"query must be at least {MIN_QUERY} characters")
    if len(words) > MAX_WORDS:
        raise ApiRefused("invalid_field", f"query is at most {MAX_WORDS} words")
    limit = field(args, "limit", int, required=False, default=DEFAULT_LIMIT)
    if not 1 <= limit <= MAX_LIMIT:
        raise ApiRefused("invalid_field", f"limit must be 1 to {MAX_LIMIT}")
    return line_index(ctx).search(query, limit)


ADAPTER = Adapter("shell", owner="108", ops={"search_lines": _search_lines})

__all__ = ["ADAPTER", "line_index"]
