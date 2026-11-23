"""075's recorded rolls on the Rules page (116): ``rules.rolls``, ``rules.roll_plan`` and ``rules.roll``.

Everything is ``grimoire.rollstore.RollLibrary`` over the open library's durable authority. Its roll key is the
app's one trusted handle (``ctx.handle``, the owner's: an adapter never asks the authority for another), and its
draw is the operating system's randomness unless whoever builds the app gave this library another (``configure``,
for tests): a request never chooses the draw. Nothing is kept beside the roll ledger.

**The selected module.** No campaign or chat records which ruleset module it plays under (062 keeps no module
record; 075 takes the caller's selection). As on the Prep page, a roll is made under 062's reference profile for the
family the person picks (``reference:062/ruleset/<family>@<edition>``), and the answer says so. A season of Ars study
rolls no die (075's ``no_roll``). The action's DC, modifier and pool are the person's: 075 takes them as given.

**Who may roll.** The actor must be in the chat's cast. An NPC (the director controls it) is rolled in the director's
view only, backed by nothing, under 075's attempt ``director:<request id>``. A character the owner controls is rolled
backed by the owner's own ``action`` contribution, whose id is the request id and whose text is what the player typed,
given through the owner's own handle; its attempt is the request id. Anyone else's character is never rolled here.
075 checks control itself, again, when it records.

**Order.** ``roll_plan`` is the review: it builds the request and its backing, runs every check 075 makes before a
die is drawn (the review's own draw refuses to draw, so nothing is drawn or written) and answers what would be rolled,
under which attempt, with a digest of exactly that request. ``roll`` rebuilds the request; one that is not what was
reviewed is refused ``changed``. 075 then draws once and records before answering. The same request asked again
reads the same roll back and draws no die; another request under the same attempt is ``already_rolled``.

**What it is not.** Narration is not yet reviewed against a roll in a scene (075 milestone 3: 035's runner and 029
are not joined to these resolutions), and a roll applies no effect. The table lists only the rolls its own actions
back; the director's rolls are the director's.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from threading import Lock

from grimoire.adjudication.model import PROSE_MAX
from grimoire.app.adapter import AdapterContext, Handler
from grimoire.campaignapi import parse
from grimoire.core import DIRECTOR
from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.rolls import RecordedRoll, RollError, attempt_for, dice_for, request_to_payload
from grimoire.rolls.codec import contribution_to_payload
from grimoire.rollstore import RollLibrary
from grimoire.rollstore.library import Draw, ledger_for, system_draw
from grimoire.rulesets import ActionRequest
from grimoire.rulesets.families import ACTION_TYPES
from grimoire.store.errors import LedgerInvalid
from grimoire.textapi import ApiRefused, TextApi

from ..campaign import hub_of
from ..prep.common import audience_of, campaign_of, chat_of, segment
from ..prep.rulesets import CONTRACTS, NOT_PINNED, reference_module
from .modules import plain

#: The families a die decides. Ars's season of study is arithmetic over the student's record (075: ``no_roll``).
ROLLED = ("d20", "wod", "pbta")
NO_ROLL = "A season of Ars Magica study rolls no die: 075 refuses it (no_roll). Its arithmetic is checked on Prep."
MODULE_NOTE = (NOT_PINNED + " A roll here is made under the reference profile of the family you pick, the same "
               "module Prep checks against, and the roll records that module.")
#: What a recorded roll does not do yet, each with its reason (shown as unavailable, never as a control).
UNAVAILABLE = (
    {"what": "Narration reviewed against this roll in a scene",
     "reason": "075 milestone 3: 035's scene runner and 029's adjudication are not yet joined to recorded rolls, "
               "so no take is checked against one here."},
    {"what": "Applying a roll's effect", "reason": "075 records a result and applies no damage, condition, resource "
                                                   "or time; effects stay with their own reviewed owner."},
    {"what": "Choosing a partial result's cost, or an opposed roll", "reason": "075 milestone 2."},
)
_ATTRIBUTE = "_rules_draw"
_MADE = Lock()


def configure(api: TextApi, draw: Draw) -> None:
    """Give ``api``'s library this source of faces instead of the operating system's (tests). Called once, before
    the app serves ``api``; never reachable from a request."""
    if not callable(draw):
        raise TypeError("draw must be callable")
    with _MADE:
        if getattr(api, _ATTRIBUTE, None) is not None:
            raise ValueError("this library's dice are already configured")
        setattr(api, _ATTRIBUTE, draw)


def _draw(ctx: AdapterContext) -> Draw:
    with _MADE:
        return getattr(ctx.api, _ATTRIBUTE, None) or system_draw


class _Reviewed(Exception):
    """Raised by the review's draw: every check 075 makes before drawing has passed, and nothing is drawn."""


def _no_draw(sides: int) -> int:
    raise _Reviewed()


def _library(ctx: AdapterContext, draw: Draw) -> RollLibrary:
    return RollLibrary(ctx.store, ctx.authority, ctx.handle, draw=draw)


def guarded(handler: Handler) -> Handler:
    """075's refusals answered with their own reasons, never a server error."""
    def run(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            return handler(ctx, args)
        except RollError as error:
            raise ApiRefused(error.reason, str(error)) from None
        except LedgerInvalid as error:
            raise ApiRefused("rolls_unreadable", f"the campaign's roll ledger cannot be read; nothing was written "
                                                 f"({error})") from None
    run.__name__ = handler.__name__
    return run


# -- views -----------------------------------------------------------------------------------------------------
def _fields(value: object) -> dict:
    return {item.name: list(getattr(value, item.name)) if isinstance(getattr(value, item.name), tuple)
            else getattr(value, item.name) for item in dataclasses.fields(value)}  # type: ignore[arg-type]


def _whose(roll: RecordedRoll, owner: str) -> str:
    if roll.contribution is None:
        return "director"
    return "you" if roll.contribution.submitted_by == owner else "player"


def roll_view(roll: RecordedRoll, owner: str, names: Mapping[InstanceRef, str]) -> dict:
    request = roll.request
    binding = request.binding
    contribution = roll.contribution
    resolution = roll.resolution()
    return {"attempt": roll.attempt_id, "request": request.request_id, "actor": str(request.actor),
            "name": names.get(request.actor), "family": binding.family, "edition": binding.edition,
            "module": str(request.module.ref), "action": _fields(request.action),
            "dice": list(dice_for(request.action)), "faces": list(roll.faces), "result": _fields(roll.result),
            "outcome": roll.outcome, "whose": _whose(roll, owner), "recorded_by": roll.recorded_by,
            "backing": None if contribution is None else {"text": contribution.text, "turn": contribution.turn_id,
                                                         "chat": contribution.scene_id},
            "resolution": {"campaign": resolution.campaign_id, "attempt": resolution.attempt_id,
                           "result": resolution.result}}


def _actors(ctx: AdapterContext, scene, audience: str) -> list[dict]:  # type: ignore[no-untyped-def]
    """The cast members this view may roll for: the owner's own characters, and in the director's view the NPCs."""
    found = []
    for actor in scene.cast:
        controller = scene.controller(actor)
        if controller == ctx.owner or (controller == DIRECTOR and audience == "director"):
            found.append({"actor": str(actor), "name": scene.names.get(actor),
                          "role": "npc" if controller == DIRECTOR else "player", "yours": controller == ctx.owner})
    return found


def _profiles() -> list[dict]:
    return [{"family": family, "edition": CONTRACTS[family]["edition"], "title": CONTRACTS[family]["title"],
             "module": str(reference_module(family).ref), "action": CONTRACTS[family]["action"],
             "example": CONTRACTS[family]["example"]["action"], "dice": list(dice_for(
                 ACTION_TYPES[family](**CONTRACTS[family]["example"]["action"])))} for family in ROLLED]


# -- ops -------------------------------------------------------------------------------------------------------
def _rolls(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The campaign's recorded rolls this view is shown (the director: all; the table: those its own actions back),
    oldest first, and, for a chat, whom this view may roll for."""
    campaign = campaign_of(args)
    audience = audience_of(args)
    chat = None if args.get("chat") is None else chat_of(args, campaign)
    scene = None if chat is None else hub_of(ctx).runner.open(chat)
    names = {} if scene is None else dict(scene.names)
    state = _library(ctx, _no_draw).state(campaign.campaign_id)
    shown = [roll_view(roll, ctx.owner, names) for roll in state.rolls.values()
             if audience == "director" or _whose(roll, ctx.owner) == "you"]
    return {"campaign": campaign.campaign_id, "chat": None if chat is None else str(chat), "audience": audience,
            "rolls": shown, "actors": None if scene is None else _actors(ctx, scene, audience),
            "profiles": _profiles(), "no_roll": NO_ROLL, "module_note": MODULE_NOTE, "pinned": None,
            "ledger": f"authority/{ledger_for(campaign.campaign_id)}/", "unavailable": [dict(item) for item in UNAVAILABLE]}


def _values(kind: type, raw: object) -> dict:
    if not isinstance(raw, Mapping):
        raise ApiRefused("invalid_field", "action must be an object")
    names = [item.name for item in dataclasses.fields(kind)]
    if set(raw) != set(names):
        raise ApiRefused("invalid_field", f"the action takes exactly {', '.join(names)}")
    plain(raw, "the action")
    return dict(raw)


def _recorded_by(ctx: AdapterContext, campaign: str, actor: object, request_id: str, audience: str) -> str | None:
    """Whose recorded roll of ``actor`` under ``request_id`` this view may read back: "player" for one this player's
    own words backed, "npc" for an NPC's roll in the director's view, or None."""
    for roll in _library(ctx, _no_draw).state(campaign).rolls.values():
        if roll.request.request_id != request_id or roll.request.actor != actor:
            continue
        if roll.contribution is None:
            if audience == "director":
                return "npc"
        elif roll.contribution.submitted_by == ctx.owner:
            return "player"
    return None


def _request(ctx: AdapterContext, args: Mapping[str, object], *, reviewing: bool):  # type: ignore[no-untyped-def]
    """The chat, its scene, the actor's name, the 062 request and the 003 contribution backing it (or None), and the
    digest naming exactly that pair."""
    campaign = campaign_of(args)
    chat: ChatRef = chat_of(args, campaign)
    audience = audience_of(args)
    scene = hub_of(ctx).runner.open(chat)
    actor = parse.actor_of(args.get("actor"), "actor", campaign.campaign_id)
    if actor not in scene.cast:
        raise ApiRefused("no_actor", f"{actor} is not in this chat's cast")
    request_id = segment(args, "request_id")
    controller = scene.controller(actor)
    if controller == DIRECTOR and audience == "director":
        whose = "npc"
    elif controller == ctx.owner:
        whose = "player"
    else:
        # A roll already recorded under this request id for this actor, by this view, is read back as asked
        # again, even if control changed since: 075 returns an identical recorded request before it checks control,
        # so a lost answer is recovered. Anything else is a new roll, and only the current controller rolls.
        whose = _recorded_by(ctx, campaign.campaign_id, actor, request_id, audience)
        if whose is None:
            if controller == DIRECTOR:
                raise ApiRefused("director_only", "an NPC's rolls are the director's")
            if controller is None:
                raise ApiRefused("no_controller", "nobody controls this character, so nothing rolls for it here")
            raise ApiRefused("not_your_character", "only the character's own player rolls for it")
    family = args.get("family")
    if family == "ars":
        raise ApiRefused("no_roll", NO_ROLL)
    if family not in ROLLED:
        raise ApiRefused("invalid_field", "family must be d20, wod or pbta")
    module = reference_module(family)  # type: ignore[arg-type]
    kind = ACTION_TYPES[family]  # type: ignore[index]
    try:
        request = ActionRequest(request_id, actor, module, module.bindings[0], kind(**_values(kind, args.get("action"))))
    except ApiRefused:
        raise
    except (ValueError, TypeError) as error:
        raise ApiRefused("invalid_action", str(error) or type(error).__name__) from None
    if not dice_for(request.action):
        raise ApiRefused("invalid_action", "this action rolls no die, so there is nothing to record")
    contribution = None
    if whose == "player":
        text = args.get("text")
        if type(text) is not str or not text.strip() or len(text) > PROSE_MAX:
            raise ApiRefused("invalid_field", f"say what your character does, in at most {PROSE_MAX} characters")
        plain(text, "what your character does")
        turns = [record.turn_id for record in scene.records]
        turn = args.get("turn")
        plain(turn, "turn")
        if turn is None and reviewing:  # the action is given in the turn being played now
            if not turns:
                raise ApiRefused("no_turn", "a player's action is given in a turn of play; this chat has none yet")
            turn = turns[-1]
        if type(turn) is not str or turn not in turns:
            raise ApiRefused("review_required" if turn is None else "invalid_field",
                             "the action's turn must be a turn of this chat, as reviewed")
        try:
            contribution = PlayerContribution(campaign.campaign_id, request_id, chat.chat_id, turn, actor.instance_id,
                                              ctx.owner, "action", text)
        except (ValueError, TypeError) as error:
            raise ApiRefused("invalid_field", str(error)) from None
    elif args.get("text") is not None:
        raise ApiRefused("invalid_field", "an NPC's roll is backed by no player's words")
    payload = json.dumps({"request": request_to_payload(request), "contribution": contribution_to_payload(contribution)},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return chat, audience, scene, request, contribution, digest


def _roll_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What would be rolled, under which attempt, with every check 075 makes before drawing already made. Nothing is
    drawn or written. A request already rolled exactly so answers its roll, which ``roll`` would read back."""
    chat, audience, scene, request, contribution, digest = _request(ctx, args, reviewing=True)
    library = _library(ctx, _no_draw)
    attempt = attempt_for(request, contribution)
    campaign = request.actor.campaign_id
    before = library.rolled(campaign, attempt)
    already = None
    try:
        found = library.roll(request.module, request, contribution=contribution,
                             author=None if contribution is None else ctx.handle)
    except _Reviewed:  # every check before the draw passed: the review's draw draws nothing
        pass
    else:
        if before is None:  # never reached: an action with no die is refused first, so the review always draws
            raise RuntimeError("the review of a roll recorded one")
        already = roll_view(found, ctx.owner, scene.names)
    # 075 does not limit how many attempts an actor makes in a turn (ROLLS.md), so the review shows the earlier ones
    earlier = [] if contribution is None else [
        roll_view(roll, ctx.owner, scene.names) for roll in library.state(campaign).rolls.values()
        if roll.attempt_id != attempt and roll.request.actor == request.actor and roll.contribution is not None
        and roll.contribution.scene_id == contribution.scene_id and roll.contribution.turn_id == contribution.turn_id
        and (audience == "director" or _whose(roll, ctx.owner) == "you")]
    return {"plan": {"chat": str(chat), "audience": audience, "attempt": attempt_for(request, contribution),
                     "request": request.request_id, "actor": str(request.actor), "name": scene.names.get(request.actor),
                     "family": request.binding.family, "edition": request.binding.edition,
                     "module": str(request.module.ref), "action": _fields(request.action),
                     "dice": list(dice_for(request.action)),
                     "backing": None if contribution is None else {"text": contribution.text,
                                                                  "turn": contribution.turn_id},
                     "turn": None if contribution is None else contribution.turn_id,
                     "review": digest, "already": already, "earlier": earlier,
                     "files": [f"authority/{ledger_for(campaign)}/"]},
            "module_note": MODULE_NOTE}


def _roll(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The reviewed request, rolled once and recorded before it is answered; asked again, read back."""
    chat, audience, scene, request, contribution, digest = _request(ctx, args, reviewing=False)
    reviewed = args.get("review")
    if type(reviewed) is not str:
        raise ApiRefused("review_required", "review the roll before rolling it")
    if reviewed != digest:
        raise ApiRefused("changed", "this is not the roll that was reviewed; review it again")
    library = _library(ctx, _draw(ctx))
    attempt = attempt_for(request, contribution)
    before = library.rolled(request.actor.campaign_id, attempt)
    roll = library.roll(request.module, request, contribution=contribution,
                        author=None if contribution is None else ctx.handle)
    return {"done": {"attempt": roll.attempt_id, "drawn": before is None}, "roll": roll_view(roll, ctx.owner,
                                                                                          scene.names)}


OPS = {"rolls": guarded(_rolls), "roll_plan": guarded(_roll_plan), "roll": guarded(_roll)}

__all__ = ["MODULE_NOTE", "NO_ROLL", "OPS", "ROLLED", "UNAVAILABLE", "configure", "roll_view"]
