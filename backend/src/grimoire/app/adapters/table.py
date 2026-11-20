"""Sprint 115's local table ops (058): two or more players at one screen draft their characters' parts of one turn,
and the turn they reviewed is posted once. Called as ``table.<op>``. See docs/TABLE.md.

Only Sprint 115 edits this module. It keeps no record of its own. The drafts are 058's: the text API holds them in
memory (``TextApi.drafts``, written by the core ``draft`` and ``drop_draft`` ops, lost when the app stops: 058
milestone 2), and a character is handed to a player at this device by the core ``seat`` op. This module adds the
review a combined turn is posted from:

- ``review`` answers the chat's drafts in posting order, each with its character, who wrote it, whether that
  writer still controls the character (a draft whose character changed hands is refused on submission), and the
  revision (``etag``) of exactly those drafts; and the players this device was started for.
- ``submit`` posts the drafts as one turn (the core ``submit``: 058's ``post_together``) only if they are still the
  ones reviewed: the request carries the reviewed drafts and their etag, so anything changed since (a draft
  corrected, added, dropped or moved) refuses ``changed`` and nothing is posted. A submission retried after its
  answer was lost finds the drafts gone and the turn written just after the reviewed one, holding the reviewed
  characters' posts by the same players, and answers ``already_applied`` without posting again.

A combined turn takes no label, framing or effect (058): nothing here offers one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.core.identity import ChatRef
from grimoire.play.drafts import MAX_DRAFT_CHARS, MODES
from grimoire.play.together import MAX_TOGETHER
from grimoire.textapi import ApiRefused
from grimoire.textapi.fields import parse_ref
from grimoire.textapi.table import MAX_DRAFTED_CHATS

_FIELDS = ("actor", "by", "text", "mode")


def etag_of(drafts: list[Mapping[str, object]]) -> str:
    """The revision of one chat's drafts: every draft's character, writer, words and mode, in posting order."""
    raw = json.dumps([[str(item.get(name)) for name in _FIELDS] for item in drafts], ensure_ascii=True,
                     separators=(",", ":"))
    return "d-" + hashlib.sha256(raw.encode("ascii")).hexdigest()[:32]


def _chat(args: Mapping[str, object]) -> ChatRef:
    return parse_ref(ChatRef.parse, args, "chat")


def _open(ctx: AdapterContext, chat: ChatRef) -> dict:
    return ctx.core("open", {"chat": str(chat)})


def _review_of(ctx: AdapterContext, chat: ChatRef, scene: dict) -> dict:
    cast = {member["actor"]: member for member in scene["cast"]}
    drafts = []
    for item in scene["drafts"]:
        member = cast.get(item["actor"])
        controller = None if member is None else member.get("controller")
        drafts.append({**{name: item[name] for name in _FIELDS}, "name": item["name"], "controller": controller,
                       # a draft whose character is no longer its writer's is refused when the turn is submitted
                       "ready": controller == item["by"]})
    turns = scene["turns"]
    return {"chat": str(chat), "owner": ctx.owner, "players": list(ctx.api.seats), "drafts": drafts,
            "etag": etag_of(scene["drafts"]), "head": turns[-1]["turn"] if turns else None,
            "open_turn": scene["state"]["open_turn"], "kept_in": "memory",
            "limits": {"drafts": MAX_TOGETHER, "chars": MAX_DRAFT_CHARS, "chats": MAX_DRAFTED_CHATS,
                       "modes": list(MODES)}}


def _review(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat = _chat(args)
    return _review_of(ctx, chat, _open(ctx, chat))


def _reviewed(args: Mapping[str, object]) -> list[dict]:
    """The drafts the person reviewed, as the commit carries them."""
    value = args.get("reviewed")
    if type(value) is not list or not 1 <= len(value) <= MAX_TOGETHER:
        raise ApiRefused("review_required", "review the turn before posting it")
    found = []
    for item in value:
        if not isinstance(item, Mapping) or not all(type(item.get(name)) is str for name in _FIELDS):
            raise ApiRefused("invalid_field", "each reviewed draft names its actor, by, text and mode")
        found.append({name: item[name] for name in _FIELDS})
    return found


def _posted_as_reviewed(turn: Mapping[str, object] | None, reviewed: list[dict]) -> bool:
    """Whether ``turn`` holds the reviewed drafts' posts: the same characters by the same players, in order, each
    post's words taken from its draft (008 may take a line's quotes off, never add words)."""
    if turn is None:
        return False
    posts = turn.get("posts") or []
    return len(posts) == len(reviewed) and all(
        post["actor"] == draft["actor"] and post["by"] == draft["by"] and str(post["text"]).strip()
        and str(post["text"]) in draft["text"] for post, draft in zip(posts, reviewed))


def _submit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Post the drafts reviewed (``reviewed``, at revision ``expected``, after the turn ``head``) as one turn."""
    chat = _chat(args)
    expected = args.get("expected")
    if type(expected) is not str:
        raise ApiRefused("review_required", "review the turn before posting it")
    reviewed = _reviewed(args)
    if etag_of(reviewed) != expected:
        raise ApiRefused("invalid_field", "the reviewed drafts are not the ones at that revision")
    head = args.get("head")
    if head is not None and type(head) is not str:
        raise ApiRefused("invalid_field", "head is the id of the turn the review came after")
    scene = _open(ctx, chat)
    if etag_of(scene["drafts"]) == expected:
        played = ctx.core("submit", {"chat": str(chat)})
        return {"done": {"already_applied": False, "posts": len(reviewed)}, "scene": played,
                "review": _review_of(ctx, chat, played)}
    turns = scene["turns"]
    if not scene["drafts"] and turns:
        # the answer to an earlier submission of this very review was lost: its turn is the one after ``head``
        at = next((n for n, turn in enumerate(turns) if turn["turn"] == head), -1) if head is not None else -1
        after = turns[at + 1] if at + 1 < len(turns) else None
        if after is turns[-1] and _posted_as_reviewed(after, reviewed):
            return {"done": {"already_applied": True, "posts": len(reviewed)}, "scene": scene,
                    "review": _review_of(ctx, chat, scene)}
    raise ApiRefused("changed", "the drafts changed since they were reviewed; nothing was posted")


ADAPTER = Adapter("table", owner="115", ops={"review": _review, "submit": _submit})

__all__ = ["ADAPTER", "etag_of"]
