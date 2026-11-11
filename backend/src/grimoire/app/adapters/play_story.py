"""The play adapter's story ops: the "Last time" recap, the Story timeline and a chat's bookmarks.

Registered by ``play.py`` as ``play.story``, ``play.bookmark_add``, ``play.bookmark_rename`` and
``play.bookmark_remove``. Nothing here calls a model: the recap is the last lines the table's player read,
verbatim, taken from the same player-facing view of each turn the chat page draws (``textapi.views.turn_view``),
so an unkept take, a struck or withdrawn line and anything only the director sees are never in it.

Where the last sitting ended is not in the library: a turn records no time, and a pause records no history. The
client names it (``since``: the id of the newest closed turn when this device last left the chat, which the chat
page keeps in the browser), and it is used only when it is a turn on this chat's line. Without it the recap is
the latest lines before the newest post, and the timeline has no "Last time you played" entry.

A chat records no scene changes (no scene has a name or a boundary in ``grimoire.scene``), so ``scenes`` is
always empty: the timeline holds bookmarks and the last-time entry only. Bookmarks are ``scenestore.bookmarks``
records, written only through ``BookmarkLibrary``. See docs/CHAT_PLAY.md.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core.identity import ChatRef
from grimoire.scene.bookmarks import MAX_BOOKMARK_LABEL
from grimoire.scenestore.bookmarks import BookmarkLibrary
from grimoire.store.jsonfmt import is_etag
from grimoire.textapi import ApiRefused, parse_ref
from grimoire.textapi.views import turn_view

#: How many lines the recap quotes.
RECAP_LINES = 4
MAX_ID = 300


def _chat(args: Mapping[str, object]) -> ChatRef:
    return parse_ref(ChatRef.parse, args, "chat")


def _id(args: Mapping[str, object], key: str, *, required: bool = True) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if type(value) is not str or not value or len(value) > MAX_ID:
        raise ApiRefused("invalid_field", f"{key} must be an id as the chat gave it")
    return value


def _label(args: Mapping[str, object]) -> str | None:
    value = args.get("label")
    if value is None:
        return None
    if type(value) is not str or len(value) > 4 * MAX_BOOKMARK_LABEL:
        raise ApiRefused("invalid_field", f"label must be text of at most {MAX_BOOKMARK_LABEL} characters")
    return value


def _library(ctx: AdapterContext) -> BookmarkLibrary:
    return BookmarkLibrary(ctx.runner.scenes)


def said(turns: list[dict], persona: str | None) -> list[dict]:
    """What the table's player read, in order, from the player-facing view of each turn: a post that was said
    or done in the open (a private or withheld one is left out), then the kept take's lines. Passed turns give
    their post only; a take on offer gives nothing."""
    out: list[dict] = []
    for turn in turns:
        where = {"turn": turn["turn"], "number": turn["number"]}
        post = turn["post"]
        if post is not None and not post["private"] and not post["withheld"]:
            out.append({**where, "speaker": post["speaker"], "text": post["text"], "you": post["actor"] == persona})
        if turn["kept"] is not None:
            for line in turn["kept"]["lines"]:
                out.append({**where, "speaker": line["speaker"] or "Narrator", "text": line["text"], "you": False})
    return out


def recap_of(turns: list[dict], persona: str | None, since: str | None) -> tuple[dict | None, dict | None]:
    """``(recap, last_time)``. With ``since`` a turn of ``turns``: the last ``RECAP_LINES`` things said up to
    and including it (``basis: "sitting"``), and that turn as ``last_time``. Otherwise the last ``RECAP_LINES``
    said before the turn of the newest post (``basis: "latest"``; everything said, when nobody has posted) and
    no ``last_time``. ``recap`` is ``None`` when there is nothing to quote."""
    mark = next((turn for turn in turns if turn["turn"] == since), None) if since is not None else None
    lines = said(turns, persona)
    if mark is not None:
        upto = turns.index(mark)
        basis = "sitting"
    else:
        posts = [index for index, turn in enumerate(turns) if turn["post"] is not None]
        upto = (posts[-1] - 1) if posts else len(turns) - 1
        basis = "latest"
    within = {turn["turn"] for turn in turns[:upto + 1]}
    quoted = [line for line in lines if line["turn"] in within][-RECAP_LINES:]
    last_time = None if mark is None else {"turn": mark["turn"], "number": mark["number"]}
    if not quoted:
        return None, last_time
    return {"basis": basis, "lines": quoted}, last_time


def _story(ctx: AdapterContext, chat: ChatRef, since: str | None) -> dict:
    scene = ctx.runner.open(chat)
    names = scene.names
    turns = [turn_view(stored, names) for stored in scene.turns.turns]
    persona = None if scene.persona is None else str(scene.persona)
    recap, last_time = recap_of(turns, persona, since)
    numbers = {turn["turn"]: turn["number"] for turn in turns}
    found = _library(ctx).bookmarks(chat)
    bookmarks = [{"id": item.bookmark.bookmark_id, "turn": item.bookmark.turn, "label": item.bookmark.label,
                  "etag": item.etag, "number": numbers.get(item.bookmark.turn),
                  "missing": item.bookmark.turn not in numbers} for item in found.bookmarks]
    # in the order of the story; one whose turn is gone goes last, by id
    bookmarks.sort(key=lambda item: (item["missing"], item["number"] or 0, item["id"]))
    return {"chat": str(chat), "recap": recap, "last_time": last_time, "scenes": [], "bookmarks": bookmarks,
            "problems": [{"path": path, "code": code} for path, code in found.problems]}


def story(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The recap, the timeline's entries and the bookmarks of one chat. Reads only."""
    return _story(ctx, _chat(args), _id(args, "since", required=False))


def bookmark_add(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """A new bookmark on ``turn`` (a turn id of this chat's line) with an optional ``label``. The story."""
    chat = _chat(args)
    turn = _id(args, "turn")
    _library(ctx).add(chat, turn, _label(args))
    return _story(ctx, chat, _id(args, "since", required=False))


def _expected(args: Mapping[str, object]) -> str:
    value = args.get("expected")
    if type(value) is not str or not is_etag(value):
        raise ApiRefused("invalid_field", "expected must be the bookmark's etag as the story gave it")
    return value


def bookmark_rename(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Set a bookmark's ``label`` (``null`` or blank clears it), over the ``expected`` etag. The story."""
    chat = _chat(args)
    bookmark = _id(args, "bookmark")
    _library(ctx).rename(chat, bookmark, _label(args), expected=_expected(args))
    return _story(ctx, chat, _id(args, "since", required=False))


def bookmark_remove(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Remove a bookmark, over the ``expected`` etag. The story."""
    chat = _chat(args)
    bookmark = _id(args, "bookmark")
    _library(ctx).remove(chat, bookmark, expected=_expected(args))
    return _story(ctx, chat, _id(args, "since", required=False))


OPS = {"story": story, "bookmark_add": bookmark_add, "bookmark_rename": bookmark_rename,
       "bookmark_remove": bookmark_remove}

__all__ = ["OPS", "RECAP_LINES", "recap_of", "said"]
