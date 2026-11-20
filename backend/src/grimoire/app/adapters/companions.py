"""Sprint 115's companion ops (050): a campaign's captured companions and the director's reviewed capture.
Called as ``companions.<op>``. See docs/TABLE.md.

Only Sprint 115 edits this module. It keeps no record of its own: a capture is 050's ``CaptureLibrary``, one
accepted-effect transaction over 021's ledger (the ``trained_by`` link, the party or reserve membership, and one
device spent from the trainer's holdings), built over the app's open library with the owner's trusted handle and
the same effect configuration as the campaign hub's. Every op is the director's (``director_only`` otherwise):
capture is an adjudicated decision, and the roster reads 017's relations, which the hub shows the director alone.

- ``roster`` reads 033's party view and the ``trained_by`` links: each trainer's companions, by their own
  instance reference (a second creature of the same species is its own individual), active or in reserve; the
  creatures and trainers a capture may name; each trainer's holdings (the devices it could spend); and the chat's
  turns a capture can be placed at.
- ``capture_plan`` builds 050's request and preview and answers what the commit would do (the placement, the
  device spent, the files written, whose key it needs) and its revision; nothing is written.
- ``capture_commit`` builds the same request again (the same attempt id, at the same campaign sequence the plan
  was reviewed at), and commits it only if the preview's basis is still the one reviewed (``changed`` otherwise).
  An exact retry finds 050's own receipt and writes nothing again (``already_applied``): the one device is spent
  once however often the commit is retried. The same attempt id for another capture refuses ``attempt_conflict``.
- A character a person plays takes that person's own key: the capture rests on that character's post in the
  chosen turn, and the person gives the key knowingly in the request (``keys``); a player who is not at this
  device cannot (``not_at_this_table``).

Nothing else follows from a capture: no NPC speaks for it, nobody's control changes, and no actor learns anything
(docs/COMPANIONS.md).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.campaignapi import parse
from grimoire.companions import CaptureLibrary, CaptureRefused, CaptureRequest
from grimoire.companions.library import recorded
from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError
from grimoire.core.entity_codec import instance_from_payload
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.effects import accepted_from_payload
from grimoire.effectstore import Endorsement, InvalidEffect
from grimoire.graph.codec import relation_from_payload
from grimoire.holdings import HoldingsError
from grimoire.party import PartyRefused
from grimoire.play.accept import next_sequence
from grimoire.play.scene import NOW, post_contributions
from grimoire.store import StoreError
from grimoire.textapi import ApiRefused

from .campaign import hub_of

OUTCOMES = ("success", "failed", "declined")
PLACEMENTS = ("auto", "party", "reserve")
#: The largest active party a capture may be asked to fit (050 hardcodes none: the director names it).
MAX_CAPACITY = 1000
#: The most recent turns of the chat a capture may be placed at.
TURNS = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _director(args: Mapping[str, object]) -> None:
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", "captures and companions are the director's")


def _refused(error: Exception) -> ApiRefused:
    reason = getattr(error, "reason", None)
    if isinstance(error, AuthorityError):
        return ApiRefused("not_authorized", str(error))
    return ApiRefused(reason if isinstance(reason, str) and reason else type(error).__name__, str(error)[:200])


def _guarded(handler):  # type: ignore[no-untyped-def]
    """Every op answers 050's, 021's, 033's and the store's refusals with their reason, never a server error."""
    def guarded(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            return handler(ctx, args)
        except ApiRefused:
            raise
        except (CaptureRefused, InvalidEffect, PartyRefused, HoldingsError, AuthorityError) as error:
            raise _refused(error) from None
    guarded.__name__ = handler.__name__
    return guarded


def _library(ctx: AdapterContext) -> CaptureLibrary:
    return CaptureLibrary(ctx.store, ctx.handle, issued=ctx.authority.issued, clock=_utc_now, new_id=_new_id,
                          owner=ctx.owner)


class _Names:
    """An actor's display name: the chat's cast name, else its instance's own local name; its species (the
    definition it was instantiated from). Presentation only: a reference is never looked up by name."""

    def __init__(self, store, scene) -> None:  # type: ignore[no-untyped-def]
        self.store, self.scene = store, scene
        self.cache: dict = {}

    def _instance(self, actor: InstanceRef):  # type: ignore[no-untyped-def]
        if actor not in self.cache:
            try:
                envelope = self.store.read(actor).envelope
                schema = envelope.extra.get("schema")
                self.cache[actor] = instance_from_payload(actor, schema if isinstance(schema, str) else "",
                                                          envelope.payload)
            except Exception:  # noqa: BLE001 -- a name is presentation only; a missing one shows the reference
                self.cache[actor] = None
        return self.cache[actor]

    def name(self, actor: InstanceRef) -> str | None:
        if actor in self.scene.names:
            return self.scene.names[actor]
        instance = self._instance(actor)
        return None if instance is None else instance.local_name

    def species(self, actor: InstanceRef) -> str | None:
        instance = self._instance(actor)
        if instance is None or instance.source is None:
            return None
        # the definition's own name, read at the revision it is pinned to (or 024's template revision)
        pinned = [ref for ref in (instance.source, getattr(instance.individuality, "template", None))
                  if ref is not None and ref.pinned]
        for ref in pinned:
            try:
                name = self.store.read(ref).envelope.name
            except Exception:  # noqa: BLE001 -- as above
                continue
            if name:
                return name
        return instance.source.entity_id

    def who(self, actor: InstanceRef) -> dict:
        controller = self.scene.controller(actor)
        return {"actor": str(actor), "name": self.name(actor), "kind": actor.kind,
                "controller": controller,
                "role": "unseated" if controller is None else "npc" if controller == DIRECTOR else "player"}


def _scene(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    _director(args)
    chat = parse.chat_of(args)
    hub = hub_of(ctx)
    return chat, hub, hub.runner.open(chat)


def _links(ctx: AdapterContext, campaign: str) -> tuple[list, list]:
    """The live ``trained_by`` relations (creature -> trainer) and the problems reading them."""
    scan = ctx.store.campaign_relations(campaign)
    links, problems = [], [{"code": "relations_unreadable", "detail": str(item.path)} for item in scan.problems]
    for ref, stored in scan.records.items():
        try:
            relation = relation_from_payload(ref, stored.record.schema, stored.record.payload)
        except ValueError:
            problems.append({"code": "relation_unreadable", "detail": str(ref)})
            continue
        if getattr(relation, "predicate", None) == "trained_by" and relation.holds_at(NOW):
            links.append(relation)
    return sorted(links, key=lambda item: (str(item.object), str(item.subject))), problems


def _roster(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat, hub, scene = _scene(ctx, args)
    campaign = chat.campaign_id
    names = _Names(ctx.store, scene)
    view = hub.party.view(campaign, as_of=NOW)
    state = view.state
    links, problems = _links(ctx, campaign)
    problems += [{"code": f"party_{item.code}", "detail": "" if item.member is None else str(item.member)}
                 for item in state.problems]
    trained = {link.subject for link in links}
    trainers: dict[InstanceRef, list] = {}
    for link in links:
        trainers.setdefault(link.object, []).append(link.subject)
    roster = []
    for trainer, creatures in trainers.items():
        active, reserve = state.party(trainer)
        roster.append({**names.who(trainer), "companions": [
            {**names.who(creature), "species": names.species(creature),
             "placement": "active" if creature in active else "reserve" if creature in reserve else "neither",
             "since": next(getattr(link.since, "sequence", None) for link in links if link.subject == creature)}
            for creature in creatures]})
    creatures = [{**names.who(ref), "species": names.species(ref)} for ref in sorted(view.instances, key=str)
                 if ref.kind == "creature" and ref not in trained]
    # who may train: the chat's cast and the campaign's other people, never a creature
    people = sorted({*(ref for ref in scene.cast if ref.kind != "creature"),
                     *(ref for ref in view.instances if ref.kind != "creature")}, key=str)
    holdings = {}
    for person in people:
        try:
            holdings[str(person)] = dict(sorted(hub.holdings.read(person).counts().items()))
        except (ValueError, StoreError, HoldingsError) as error:
            problems.append({"code": "holdings_unreadable", "detail": f"{person}: {type(error).__name__}"})
    # 021 accepts one effect per turn: a turn that already holds one takes no capture (another needs a revocation)
    taken: set[str] = set()
    try:
        for event in hub.effects.continuity(campaign, as_of=NOW, requested_by=DIRECTOR).events:
            if event.chat == chat and event.moment.scene_id == chat.chat_id and event.moment.turn_id:
                taken.add(event.moment.turn_id)
    except (ValueError, StoreError) as error:
        problems.append({"code": "effects_unreadable", "detail": getattr(error, "reason", type(error).__name__)})
    turns = [{"turn": record.turn_id, "number": record.number, "open": record.open,
              "accepted": record.turn_id in taken,
              "posts": [{"actor": str(post.actor), "by": post.submitted_by, "name": names.name(post.actor)}
                        for post in record.posts]} for record in scene.records[-TURNS:]]
    return {"chat": str(chat), "campaign": campaign, "trainers": roster, "creatures": creatures,
            "people": [names.who(ref) for ref in people], "holdings": holdings, "turns": turns,
            "players": list(ctx.api.seats), "problems": problems,
            "outcomes": list(OUTCOMES), "placements": list(PLACEMENTS), "max_capacity": MAX_CAPACITY}


def _segment(args: Mapping[str, object], key: str, *, required: bool = True) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be an id")
    try:
        validate_segment(value, key)
    except ValueError:
        raise ApiRefused("invalid_field", f"{key} is not a valid id") from None
    return value


def _sequence(hub, chat) -> int:  # type: ignore[no-untyped-def]
    """The campaign sequence a capture made now takes (one after every sequence used: 021's order)."""
    with hub.store.lock:
        return next_sequence(hub.runner, chat.campaign_id, hub.runner._knowledge(chat)[1])  # noqa: SLF001


def _made_at(ctx: AdapterContext, campaign: str, attempt: str) -> int | None:
    """The campaign sequence a capture with this attempt id was made at, or ``None`` when none was: a review of a
    capture already made is reviewed where it was made, so it finds 050's receipt (or ``attempt_conflict``)."""
    scan = ctx.store.campaign_accepted(campaign)
    for ref, stored in scan.records.items():
        try:
            saved = recorded(accepted_from_payload(ref, stored.record.schema, stored.record.payload))
        except ValueError:  # 050 itself refuses an unreadable acceptance when it previews
            continue
        if saved is not None and saved.request.attempt_id == attempt:
            return saved.request.at.sequence
    return None


def _built(ctx: AdapterContext, args: Mapping[str, object], sequence: int):  # type: ignore[no-untyped-def]
    """The request the arguments name, at ``sequence``, and what its acceptance needs: (request, contributions,
    endorsers by actor, names, scene)."""
    chat, hub, scene = _scene(ctx, args)
    campaign = chat.campaign_id
    attempt = _segment(args, "attempt_id")
    trainer = parse.actor_of(args.get("trainer"), "trainer", campaign)
    creature = parse.actor_of(args.get("creature"), "creature", campaign)
    if creature.kind != "creature":
        raise ApiRefused("invalid_field", "the one captured is a creature")
    if trainer == creature:
        raise ApiRefused("invalid_field", "a creature does not capture itself")
    outcome = args.get("outcome")
    if outcome not in OUTCOMES:
        raise ApiRefused("invalid_field", "outcome must be success, failed or declined")
    placement = args.get("placement", "auto")
    if placement not in PLACEMENTS:
        raise ApiRefused("invalid_field", "placement must be auto, party or reserve")
    capacity = args.get("capacity")
    if type(capacity) is not int or not 0 <= capacity <= MAX_CAPACITY:
        raise ApiRefused("invalid_field", f"capacity must be a whole number from 0 to {MAX_CAPACITY}")
    device = _segment(args, "device", required=False)
    turn_id = _segment(args, "turn")
    record = next((item for item in scene.records if item.turn_id == turn_id), None)
    if record is None:
        raise ApiRefused("unknown_turn", "this chat has no turn with that id")
    contributions = post_contributions(record)
    backing: dict[InstanceRef, str | None] = {}
    needs: dict[InstanceRef, str] = {}
    for actor in (trainer, creature):
        controller = scene.controller(actor)
        if controller is None or controller == DIRECTOR:
            backing[actor] = None
            continue
        # a character a person plays acts only through that person's own post in the turn, and their key
        post = next((item for item in contributions if item.actor_id == actor.instance_id
                     and item.submitted_by == controller), None)
        if post is None:
            raise ApiRefused("no_backing", f"{names_of(scene, actor)} is played by a person and made no post in "
                                           "the chosen turn for their key to rest on")
        backing[actor] = post.contribution_id
        needs[actor] = controller
    request = CaptureRequest(attempt, chat, Moment(sequence, chat.chat_id, turn_id), trainer, creature,
                             outcome, device, capacity, placement, backing[trainer], backing[creature])
    return request, contributions, needs, _Names(ctx.store, scene), scene


def names_of(scene, actor: InstanceRef) -> str:  # type: ignore[no-untyped-def]
    return scene.names.get(actor) or str(actor)


def _etag(preview, sequence: int) -> str:  # type: ignore[no-untyped-def]
    """The revision a capture was reviewed at: the endpoints' and relations' files as pinned, its placement and
    proposal, and the sequence it takes."""
    raw = json.dumps({"endpoints": [[str(ref), etag] for ref, etag in preview.endpoint_pins],
                      "relations": [[str(ref), etag] for ref, etag in preview.relation_pins],
                      "placement": preview.placement, "sequence": sequence,
                      "proposal": None if preview.proposal is None else preview.proposal.fingerprint},
                     sort_keys=True, separators=(",", ":"))
    return "k-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _plan_view(preview, change, needs, names: _Names, sequence: int) -> dict:  # type: ignore[no-untyped-def]
    request = preview.request
    effect = change.effect
    return {"attempt": request.attempt_id, "outcome": request.outcome, "placement": preview.placement,
            "trainer": names.who(request.trainer), "creature": {**names.who(request.creature),
                                                                "species": names.species(request.creature)},
            "device": request.device, "spends": 1 if request.device is not None and preview.proposal is not None else 0,
            "writes": preview.proposal is not None,
            "already_applied": effect is not None and effect.duplicate_of is not None,
            "files": sorted(change.preview()), "sequence": sequence, "turn": request.at.turn_id,
            "keys": [{**names.who(actor), "player": player} for actor, player in needs.items()],
            "etag": _etag(preview, sequence)}


def _keys(ctx: AdapterContext, args: Mapping[str, object]) -> dict[str, object]:
    """The handles of the players who gave their key in this request (``keys``), each one at this device."""
    value = args.get("keys", [])
    if type(value) is not list or not all(type(item) is str for item in value):
        raise ApiRefused("invalid_field", "keys lists the players giving their key")
    given = {}
    for player in value:
        if player not in ctx.api.seats:
            raise ApiRefused("not_at_this_table", "a key is given by a player at this device")
        given[player] = ctx.api.seats[player]
    return given


def _endorsements(preview, needs, given) -> list:  # type: ignore[no-untyped-def]
    if preview.proposal is None:
        return []
    found = []
    request = preview.request
    for actor, player in needs.items():
        if player in given:
            backing = request.trainer_backing if actor == request.trainer else request.creature_backing
            found.append(Endorsement(backing, given[player], preview.proposal.fingerprint))
    return found


def _accepted(ctx, args, sequence):  # type: ignore[no-untyped-def]
    request, contributions, needs, names, scene = _built(ctx, args, sequence)
    given = _keys(ctx, args)
    library = _library(ctx)
    preview = library.preview(request)
    change = library.accept(preview, controls=scene.controls, contributions=contributions,
                            endorsements=_endorsements(preview, needs, given))
    return preview, change, needs, given, names


def _capture_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What the capture would do, reviewed at the next campaign sequence; nothing is written. A player's key is
    not needed to review, only to make the capture."""
    chat, hub, _ = _scene(ctx, args)
    made = _made_at(ctx, chat.campaign_id, _segment(args, "attempt_id"))
    sequence = _sequence(hub, chat) if made is None else made
    request, contributions, needs, names, scene = _built(ctx, args, sequence)
    library = _library(ctx)
    preview = library.preview(request)
    if needs and preview.proposal is not None:
        # the files a capture with every key would write; without the keys 021 refuses, and the commit asks first
        keys = [Endorsement(request.trainer_backing if actor == request.trainer else request.creature_backing,
                            ctx.api.seats[player], preview.proposal.fingerprint)
                for actor, player in needs.items() if player in ctx.api.seats]
        missing = [player for player in needs.values() if player not in ctx.api.seats]
        if missing:
            raise ApiRefused("not_at_this_table", "a character in this capture is played by someone not at this "
                                                  "device, whose key cannot be given here")
        change = library.accept(preview, controls=scene.controls, contributions=contributions, endorsements=keys)
    else:
        change = library.accept(preview, controls=scene.controls, contributions=contributions)
    return {"plan": _plan_view(preview, change, needs, names, sequence)}


def _capture_commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The capture reviewed with ``capture_plan`` (``sequence``, ``expected``), made once."""
    _, hub, _ = _scene(ctx, args)
    sequence = args.get("sequence")
    expected = args.get("expected")
    if type(sequence) is not int or type(expected) is not str:
        raise ApiRefused("review_required", "review the capture before making it")
    try:
        preview, change, needs, given, names = _accepted(ctx, args, sequence)
    except InvalidEffect as error:
        # 021 refuses a capture missing a player's key: say whose key it is, never a bare refusal
        if error.reason == "not_endorsed":
            raise ApiRefused("player_key_required", "each player whose character takes part gives their own "
                                                    "key; nothing was written") from None
        raise
    except CaptureRefused as error:
        # a membership recorded after the reviewed sequence: the review is stale, not the capture wrong
        if error.reason == "out_of_order" and _sequence(hub, parse.chat_of(args)) != sequence:
            raise ApiRefused("changed", "the party moved on since the review; nothing was written") from None
        raise
    done = _plan_view(preview, change, needs, names, sequence)
    if change.effect is not None and change.effect.duplicate_of is None:
        # a new capture: only as reviewed, at the sequence it was reviewed at, with every player's own key
        if done["etag"] != expected or _sequence(hub, preview.request.chat) != sequence:
            raise ApiRefused("changed", "the party, the creature or the campaign moved on since the review; "
                                        "nothing was written")
        missing = [player for player in needs.values() if player not in given]
        if missing:
            raise ApiRefused("player_key_required", "each player whose character takes part gives their own key")
    change.commit()
    return {"done": done, "roster": _roster(ctx, args)}


ADAPTER = Adapter("companions", owner="115",
                  ops={"roster": _guarded(_roster), "capture_plan": _guarded(_capture_plan),
                       "capture_commit": _guarded(_capture_commit)},
                  refusals={CaptureRefused: "capture_refused", InvalidEffect: "invalid_effect"})

__all__ = ["ADAPTER", "OUTCOMES", "PLACEMENTS"]
