"""A bounded floor: interjections, the addressed floor holder, then other replies; never a PC voice."""
from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.authority import DIRECTOR
from grimoire.generation import GenerationError, checked_controls, controller, plan_generation_turn, require_director

from .model import ConversationBid, ConversationPlan, SPEAKING, _SEAL, refs


def owners_of(campaign_id, present, controls):
    controls = checked_controls(controls)
    owners = tuple((ref, controller(campaign_id, ref.instance_id, controls)) for ref in present)
    if any(owner is None for _, owner in owners):
        raise GenerationError('unassigned_actor', 'every present actor needs one controller')
    return owners


def schedule(campaign_id, turn_id, *, present, controls, initiator, addressees, floor=None,
             player_speaking=False, bids=(), policy=None) -> ConversationPlan:
    """Plan, not enact, explicit actor-scoped bids. No inference, files or canonical changes.

    Interruption is an interjection before an NPC yields its reserved floor, not editing a line
    already generated. Multiple interjections retain bid order. A player never loses the floor.
    """
    present = refs(present, 'present')
    addressees = refs(addressees, 'addressees')
    if (any(ref.campaign_id != campaign_id for ref in present)
            or len({ref.instance_id for ref in present}) != len(present)):
        raise GenerationError('bad_cast', 'cast must be campaign-local with unique instance ids')
    if initiator not in present or (floor is not None and floor not in present) or not set(addressees) <= set(present):
        raise GenerationError('absent_actor', 'initiator, floor and addressees must be present')
    if type(player_speaking) is not bool:
        raise GenerationError('bad_floor', 'player_speaking must be boolean')
    controls = checked_controls(controls)
    owners = owners_of(campaign_id, present, controls)
    if player_speaking and (floor != initiator or dict(owners)[initiator] == DIRECTOR):
        raise GenerationError('bad_floor', 'unfinished player speech needs that player to hold the floor')
    if isinstance(bids, (str, bytes, Mapping)):
        raise GenerationError('bad_bids', 'bids must be a collection')
    try:
        bids = tuple(bids)
    except TypeError:
        raise GenerationError('bad_bids', 'bids must be a collection') from None
    if not all(isinstance(bid, ConversationBid) for bid in bids):
        raise GenerationError('bad_bids', 'bids must be ConversationBid values')
    if len({bid.actor for bid in bids}) != len(bids):
        raise GenerationError('duplicate_bid', 'only one contribution per actor in an exchange')
    for bid in bids:
        if not {bid.actor, *bid.addressees, *bid.audience, *bid.attention} <= set(present):
            raise GenerationError('absent_actor', 'a bid references an absent actor')
        require_director(campaign_id, bid.actor.instance_id, controls)
        if bid.grounding == 'addressed' and bid.actor not in addressees:
            raise GenerationError('bad_grounding', 'only an addressed actor can answer as addressed')
        if bid.kind == 'interject' and not player_speaking and (floor is None or dict(owners)[floor] != DIRECTOR or floor == bid.actor):
            raise GenerationError('bad_floor', 'interjections require another NPC holding the floor')
    speaking = [bid for bid in bids if bid.kind in SPEAKING]
    speaking.sort(key=lambda bid: 0 if bid.kind == 'interject' else 1 if bid.actor == floor and bid.kind == 'answer' else 2)
    speakers = () if player_speaking else tuple(bid.actor for bid in speaking)
    inference = plan_generation_turn(campaign_id, turn_id, present=[ref.instance_id for ref in present],
                                     speakers=[ref.instance_id for ref in speakers], controls=controls, policy=policy)
    return ConversationPlan(inference, present, owners, initiator, addressees, floor, player_speaking,
                            bids, speakers, _SEAL)
