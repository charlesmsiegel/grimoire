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
  ones reviewed and no turn was posted since: the request carries the reviewed drafts, their etag and the turn
  the review came after (``head``), so a draft corrected, added, dropped or moved, or another turn posted
  meanwhile, refuses ``changed`` and the request posts nothing. A submission retried after its answer was lost
  finds the turn written just after ``head`` holding exactly the posts 058 makes of the reviewed drafts (the same
  characters by the same players, each post's kind and words equal to its draft's as 008 routes and 032 reads
  it), whatever was drafted or played since, and answers ``already_applied`` with that turn's id without
  posting again. Both answers say whether the turn has a take yet (``answered``), so a client asks the table to
  answer it only once.

A combined turn takes no label, framing or effect (058): nothing here offers one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.comms import TransmissionError, Unread, read_message
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.intent import RoutingContext, Utterance, route
from grimoire.intent.text import wrapped
from grimoire.play.drafts import MAX_DRAFT_CHARS, MODES
from grimoire.play.together import MAX_TOGETHER
from grimoire.textapi import ApiRefused
from grimoire.textapi.fields import parse_ref
from grimoire.textapi.table import MAX_DRAFTED_CHATS

_FIELDS = ("actor", "by", "text", "mode")


def etag_of(drafts: Sequence[Mapping[str, object]]) -> str:
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


def _names(scene: Mapping[str, Any]) -> dict[str, InstanceRef]:
    """The cast's display names, for 032's reader, as 058 builds them (two actors sharing a name both stay
    reachable under it: the keys differ only in trailing space, which the reader folds away)."""
    index: dict[str, InstanceRef] = {}
    for member in scene["cast"]:
        name = member.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            actor = InstanceRef.parse(member["actor"])
        except (KeyError, TypeError, ValueError):
            continue
        key = name
        while key in index:
            key += " "
        index[key] = actor
    return index


def _as_posted(draft: Mapping[str, str], names: dict[str, InstanceRef], turn: str) -> tuple[str, str] | None:
    """(kind, words) of the post 058 makes of ``draft`` in turn ``turn``: 008 routes it and 032 reads a spoken
    line's words, exactly as ``read_post`` does; None when it would not be posted at all."""
    try:
        actor = InstanceRef.parse(draft["actor"])
        intent = route(Utterance(draft["text"], draft["mode"]),  # type: ignore[arg-type]  # Utterance checks it
                       RoutingContext(active_pc=actor.instance_id, can_direct=False))
    except ValueError:
        return None
    if intent.kind not in ("pc_speech", "pc_action"):
        return None
    if intent.kind == "pc_speech" and intent.reason == "explicit_mode" and \
            wrapped(draft["text"].strip(), '"', '"') is not None:
        intent = replace(intent, reason="quoted")
    kind = "speech" if intent.kind == "pc_speech" else "action"
    try:
        read = read_message(intent, actor, names, message_id=turn)
    except TransmissionError:
        return None
    if isinstance(read, Unread):
        return (kind, intent.text) if kind == "action" else None
    return kind, read.text


def _posted_as_reviewed(turn: Mapping[str, object] | None, reviewed: list[dict], names: dict[str, InstanceRef]) -> bool:
    """Whether ``turn`` holds exactly the reviewed drafts' posts: the same characters by the same players, in
    order, each post's kind and words the ones 058 makes of its draft (equal, never merely contained)."""
    if turn is None:
        return False
    posts = turn.get("posts") or []
    if len(posts) != len(reviewed):  # type: ignore[arg-type]
        return False
    for post, draft in zip(posts, reviewed):  # type: ignore[call-overload]
        if post["actor"] != draft["actor"] or post["by"] != draft["by"]:
            return False
        if _as_posted(draft, names, str(turn["turn"])) != (post["kind"], post["text"]):
            return False
    return True


def _answered(turn: Mapping[str, object]) -> bool:
    """Whether the table answered ``turn`` already: a take kept or on offer, or the turn passed."""
    return bool(turn.get("kept") or turn.get("offered") or turn.get("passed"))


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
    turns = scene["turns"]
    last = turns[-1]["turn"] if turns else None
    if head == last:
        # nothing was posted since the review: post the drafts only if they are still the ones reviewed
        if etag_of(scene["drafts"]) != expected:
            raise ApiRefused("changed", "the parts changed since they were reviewed; this request posted nothing")
        played = ctx.core("submit", {"chat": str(chat)})
        posted = played["turns"][-1]
        return {"done": {"already_applied": False, "posts": len(reviewed), "turn": posted["turn"],
                         "answered": _answered(posted)},
                "scene": played, "review": _review_of(ctx, chat, played)}
    # a turn was posted after the reviewed head. If it is this review's own turn (an earlier submission of this
    # very request, whose answer was lost), it is answered as done, whatever was drafted or played since
    at = 0 if head is None else next((n + 1 for n, turn in enumerate(turns) if turn["turn"] == head), None)
    after = turns[at] if at is not None and at < len(turns) else None
    if after is not None and _posted_as_reviewed(after, reviewed, _names(scene)):
        return {"done": {"already_applied": True, "posts": len(reviewed), "turn": after["turn"],
                         "answered": _answered(after)},
                "scene": scene, "review": _review_of(ctx, chat, scene)}
    raise ApiRefused("changed", "another turn was posted since the review; this request posted nothing")


ADAPTER = Adapter("table", owner="115", ops={"review": _review, "submit": _submit})

__all__ = ["ADAPTER", "etag_of"]
