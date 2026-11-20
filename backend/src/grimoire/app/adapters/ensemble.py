"""Sprint 115's ensemble ops (051): the director previews how several NPCs would answer one moment of a chat.
Called as ``ensemble.<op>``. See docs/TABLE.md.

Only Sprint 115 edits this module. It keeps no record of its own and calls no model. 051's ``schedule`` is pure:
the director authors one bid per NPC (answer, interject, aside, silence or a nonverbal beat), each with its
addressees, audience, private reason and grounding, and ``preview`` answers the floor 051 would give them (who
interjects first, who answers, who adds a remark after, who stays silent and why) as an **unaccepted proposal**.
Nothing is written, nothing is said in the chat and no generation call is made.

- **The director's.** The full plan is director-private (docs/ENSEMBLE.md): every op refuses ``director_only``
  unless ``audience`` is ``director``. Beside the plan, the preview answers each present character's own
  projection (``ConversationPlan.for_actor``): its own reason, and only the public coordination of the bids whose
  audience includes it, so the director sees that an aside never reaches a character outside its audience.
- **Read from the chat.** The cast is the chat's characters on stage, control as recorded (an NPC is the
  director's; a player character is never voiced), named by reference, never by name.
- **Not available.** Running the exchange (one separately voiced generation call per speaking NPC, 051's
  ``run_conversation``) has no app path yet: the chat's own ``generate`` asks NPCs in its own way, with no
  interjection or side-remark modes and no per-line audience. Accepting an exchange's lines or quiet beats into the
  story has no owner yet either. Both are said plainly and never offered.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.campaignapi import parse
from grimoire.core import DIRECTOR
from grimoire.core.identity import InstanceRef
from grimoire.ensemble import ConversationBid, schedule
from grimoire.ensemble.model import GROUNDINGS, KINDS, SPEAKING
from grimoire.generation import GenerationError
from grimoire.textapi import ApiRefused

from .campaign import hub_of

#: At most this many bids in one preview (a scene's NPCs, not a crowd).
MAX_BIDS = 16
#: The turn id a preview is planned under: a preview is no turn of the chat.
PREVIEW_TURN = "ensemble-preview"
#: What the page says instead of a control, for each step 051 has no app path for.
NOT_AVAILABLE = {
    "run": "051 runs an exchange headlessly; the chat's play does not run one yet (its replies have no "
           "interjection or side-remark modes and no per-line audience), so no model is asked here",
    "accept": "an exchange's lines and quiet beats are unaccepted proposals; no review owner takes them into the "
              "story yet",
}


def _audience(args: Mapping[str, object]) -> None:
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", "an exchange's plan is the director's")


def _scene(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    _audience(args)
    chat = parse.chat_of(args)
    return chat, hub_of(ctx).runner.open(chat)


def _who(scene, actor: InstanceRef) -> dict:  # type: ignore[no-untyped-def]
    controller = scene.controller(actor)
    return {"actor": str(actor), "name": scene.names.get(actor),
            "role": "unseated" if controller is None else "npc" if controller == DIRECTOR else "player"}


def _present(scene) -> tuple[InstanceRef, ...]:  # type: ignore[no-untyped-def]
    """Who is in the exchange: the chat's cast on stage that someone controls (051 needs one controller each)."""
    return tuple(actor for actor in scene.on_stage if scene.controller(actor) is not None)


def _exchange(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat, scene = _scene(ctx, args)
    return {"chat": str(chat), "present": [_who(scene, actor) for actor in _present(scene)],
            "kinds": sorted(KINDS), "speaking": sorted(SPEAKING), "groundings": sorted(GROUNDINGS),
            "max_bids": MAX_BIDS, "not_available": dict(NOT_AVAILABLE)}


def _actors(args: Mapping[str, object], key: str, campaign: str, *, maximum: int = 64) -> tuple[InstanceRef, ...]:
    value = args.get(key, [])
    if type(value) is not list or len(value) > maximum:
        raise ApiRefused("invalid_field", f"{key} must be a list of actor references")
    return tuple(parse.actor_of(item, key, campaign) for item in value)


def _bid(item: object, campaign: str) -> ConversationBid:
    if not isinstance(item, Mapping):
        raise ApiRefused("invalid_field", "each bid is an object")
    heard = item.get("heard_index")
    if heard is not None and type(heard) is not int:
        raise ApiRefused("invalid_field", "heard_index must be a whole number")
    nonverbal = item.get("nonverbal")
    if nonverbal is not None and type(nonverbal) is not str:
        raise ApiRefused("invalid_field", "nonverbal is the proposed beat's text")
    for key in ("kind", "reason", "grounding"):
        if type(item.get(key)) is not str:
            raise ApiRefused("invalid_field", f"a bid's {key} is text")
    return ConversationBid(parse.actor_of(item.get("actor"), "actor", campaign), item["kind"],
                           _actors(item, "addressees", campaign), _actors(item, "audience", campaign),
                           item["reason"], item["grounding"], heard_index=heard, nonverbal=nonverbal)


def _decision(scene, bid: ConversationBid, *, private: bool) -> dict:  # type: ignore[no-untyped-def]
    found = {**_who(scene, bid.actor), "kind": bid.kind, "addressees": [_who(scene, a) for a in bid.addressees],
             "audience": [_who(scene, a) for a in bid.audience], "grounding": bid.grounding,
             "heard_index": bid.heard_index}
    if private:
        found["reason"] = bid.reason
        found["beat"] = bid.nonverbal
    return found


def _preview(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """051's floor for the bids given, as an unaccepted proposal; nothing is written and no model is asked."""
    chat, scene = _scene(ctx, args)
    campaign = chat.campaign_id
    present = _present(scene)
    initiator = parse.actor_of(args.get("initiator"), "initiator", campaign)
    floor = None if args.get("floor") is None else parse.actor_of(args.get("floor"), "floor", campaign)
    addressees = _actors(args, "addressees", campaign)
    player_speaking = parse.field(args, "player_speaking", bool, required=False, default=False)
    bids = args.get("bids", [])
    if type(bids) is not list or len(bids) > MAX_BIDS:
        raise ApiRefused("invalid_field", f"bids is a list of at most {MAX_BIDS} bids")
    try:
        authored = tuple(_bid(item, campaign) for item in bids)
        plan = schedule(campaign, PREVIEW_TURN, present=present, controls=scene.controls, initiator=initiator,
                        addressees=addressees, floor=floor, player_speaking=player_speaking, bids=authored)
    except GenerationError as error:
        raise ApiRefused(error.reason, error.message) from None
    by_actor = {bid.actor: bid for bid in plan.bids}
    order = [{**_decision(scene, by_actor[actor], private=True), "position": n}
             for n, actor in enumerate(plan.speakers, 1)]
    # while a player is still speaking every NPC's bid is held, a quiet one too: nobody interrupts them (051)
    quiet = [] if plan.player_speaking else [_decision(scene, bid, private=True) for bid in plan.bids
                                             if bid.kind not in SPEAKING]
    held = [_decision(scene, bid, private=True) for bid in plan.bids if plan.player_speaking]
    views = []
    for actor in plan.present:
        seen = plan.for_actor(actor)
        views.append({**_who(scene, actor), "sees": [
            {**_who(scene, item.actor), "kind": item.kind, "addressees": [_who(scene, a) for a in item.addressees],
             "held": item.held, "reason": item.reason} for item in seen]})
    return {"chat": str(chat), "proposal": True, "accepted": False,
            "initiator": _who(scene, initiator), "floor": None if floor is None else _who(scene, floor),
            "addressees": [_who(scene, actor) for actor in addressees], "player_speaking": player_speaking,
            "order": order, "quiet": quiet, "held": held, "calls": len(plan.inference.calls), "views": views,
            "not_available": dict(NOT_AVAILABLE)}


ADAPTER = Adapter("ensemble", owner="115", ops={"exchange": _exchange, "preview": _preview},
                  refusals={GenerationError: "ensemble_refused"})

__all__ = ["ADAPTER", "MAX_BIDS", "NOT_AVAILABLE"]
