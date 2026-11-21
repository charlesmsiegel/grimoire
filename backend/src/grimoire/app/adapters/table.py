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
  meanwhile, refuses ``changed`` and the request posts nothing now. The moment a submission posts, this module
  keeps a receipt of it, by chat, etag and head, in memory beside the drafts. A submission retried after its
  answer was lost finds that receipt and answers ``already_applied`` with the posted turn's id, whatever was
  drafted or played since, without posting again; a turn that merely holds the same words (posted through the
  composer, say) is never taken for it. The receipt does not survive an app restart (nor do the drafts): a retry
  after one refuses ``changed``. Both answers say whether the turn has a take yet (``answered``), so a client
  asks the table to answer it only once.

A combined turn takes no label, framing or effect (058): nothing here offers one.
"""

from __future__ import annotations

import hashlib
import json
import threading
import weakref
from collections import OrderedDict
from collections.abc import Mapping, Sequence

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.core.identity import ChatRef
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


class _Receipts:
    """The turns this app posted from a review, by (chat, the review's etag, its head): written the moment the core
    ``submit`` succeeds, read by a submission retried after its answer was lost. Kept in memory with the drafts
    themselves (058 holds those in memory too), a bounded number per text API; an app restart loses both."""

    LIMIT = 256

    def __init__(self) -> None:
        self.lock = threading.Lock()  # guards the receipts themselves; what serializes a check and its post is the
        # text API's own lock, which every op not marked unlocked runs under (two tabs never post one review twice)
        self._by_api: weakref.WeakKeyDictionary[object, OrderedDict[tuple[str, str, str | None], str]] = (
            weakref.WeakKeyDictionary())

    def _of(self, api: object) -> OrderedDict[tuple[str, str, str | None], str]:
        found = self._by_api.get(api)
        if found is None:
            found = self._by_api[api] = OrderedDict()
        return found

    def get(self, api: object, key: tuple[str, str, str | None]) -> str | None:
        return self._of(api).get(key)

    def put(self, api: object, key: tuple[str, str, str | None], turn: str) -> None:
        held = self._of(api)
        held[key] = turn
        held.move_to_end(key)
        while len(held) > self.LIMIT:
            held.popitem(last=False)


_RECEIPTS = _Receipts()


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
    key = (str(chat), expected, head)
    with _RECEIPTS.lock:
        scene = _open(ctx, chat)
        turns = scene["turns"]
        posted_turn = _RECEIPTS.get(ctx.api, key)
        if posted_turn is not None:
            # this very review was posted already (an earlier submission, whose answer was lost): say so, whatever
            # was drafted or played since, and post nothing again
            posted = next((turn for turn in turns if turn["turn"] == posted_turn), None)
            return {"done": {"already_applied": True, "posts": len(reviewed), "turn": posted_turn,
                             # a turn no longer in the chat (rewound since) has nothing left to answer
                             "answered": True if posted is None else _answered(posted)},
                    "scene": scene, "review": _review_of(ctx, chat, scene)}
        last = turns[-1]["turn"] if turns else None
        if head != last:
            raise ApiRefused("changed", "another turn was posted since the review, so it was not posted now")
        if etag_of(scene["drafts"]) != expected:
            raise ApiRefused("changed", "the parts changed since they were reviewed, so they were not posted now")
        played = ctx.core("submit", {"chat": str(chat)})
        posted = played["turns"][-1]
        _RECEIPTS.put(ctx.api, key, posted["turn"])
    return {"done": {"already_applied": False, "posts": len(reviewed), "turn": posted["turn"],
                     "answered": _answered(posted)},
            "scene": played, "review": _review_of(ctx, chat, played)}


ADAPTER = Adapter("table", owner="115", ops={"review": _review, "submit": _submit})

__all__ = ["ADAPTER", "etag_of"]
