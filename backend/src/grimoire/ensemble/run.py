"""Execute a scheduled exchange through 027; coordination never commits an event or belief."""
from __future__ import annotations

import threading
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.generation import GenerationError, NpcBrief, checked_controls, screen_reply
from grimoire.generation.delivery import ContributionMode
from grimoire.providers.meter import TurnMeter
from grimoire.providers.cancel import CancelToken
from grimoire.runtime.fence import FenceToken
from grimoire.runtime import GenerationRuntime, TurnOutcome

from .model import ConversationBid, ConversationPlan, SPEAKING, refs
from .plan import owners_of

_CLAIMS = weakref.WeakKeyDictionary()
_CLAIM_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ConversationOutcome:
    """Director-private planning trace and unreviewed proposals. Neither is canonical fiction."""
    plan: ConversationPlan = field(repr=False)
    generation: TurnOutcome
    quiet: tuple[ConversationBid, ...] = field(repr=False)


def run_conversation(plan: ConversationPlan, runtime: GenerationRuntime, meter: TurnMeter, briefs, *,
                     controls, present, token=None, cancel=None) -> ConversationOutcome:
    """Require the same cast/authority and validate every bidder's own grounding before I/O.

    Briefs include quiet bidders so a silence cannot smuggle an unlearned private rationale.
    Only the speaking briefs enter generation. Audiences apply to the new lines, while historic
    heard lines must already have been projected by their owning communication/knowledge services.
    """
    if not isinstance(plan, ConversationPlan) or not isinstance(meter, TurnMeter):
        raise GenerationError('bad_plan', 'execution needs a scheduled plan and its meter')
    if not isinstance(runtime, GenerationRuntime):
        raise GenerationError('bad_runtime', 'execution needs GenerationRuntime')
    if token is not None and not isinstance(token, FenceToken):
        raise GenerationError('bad_token', 'token must be a FenceToken')
    if cancel is not None and not isinstance(cancel, CancelToken):
        raise GenerationError('bad_cancel', 'cancel must be a CancelToken')
    present = refs(present, 'present')
    controls = checked_controls(controls)
    if set(present) != set(plan.present) or dict(owners_of(plan.inference.campaign_id, present, controls)) != dict(plan.owners):
        raise GenerationError('stale_cast', 'presence or control changed; schedule a new exchange')
    if meter.ledger.plan != plan.inference:
        raise GenerationError('plan_mismatch', 'the meter belongs to another conversation plan')
    if isinstance(briefs, (str, bytes, Mapping)):
        raise GenerationError('bad_briefs', 'briefs must be a collection of NpcBrief')
    try:
        briefs = tuple(briefs)
    except TypeError:
        raise GenerationError('bad_briefs', 'briefs must be a collection of NpcBrief') from None
    if not all(isinstance(brief, NpcBrief) for brief in briefs):
        raise GenerationError('bad_briefs', 'briefs must be NpcBrief values')
    by_actor = {brief.actor: brief for brief in briefs}
    if len(by_actor) != len(briefs) or set(by_actor) != {bid.actor for bid in plan.bids}:
        raise GenerationError('brief_mismatch', 'exactly one own brief is required for every bidder')
    for bid in plan.bids:
        brief = by_actor[bid.actor]
        if brief.cast is not None and brief.cast != plan.inference.present_actor_ids:
            raise GenerationError('stale_cast', 'an intention was formed for another cast')
        if bid.grounding == 'heard' and bid.heard_index >= len(brief.heard):
            raise GenerationError('bad_grounding', 'the cited heard line is absent from the own brief')
        if bid.grounding == 'intention' and brief.intention is None:
            raise GenerationError('bad_grounding', 'the own brief has no intention')
        if bid.grounding == 'addressed' and not any(line.actor == plan.initiator for line in brief.heard):
            raise GenerationError('bad_grounding', 'the addressed actor has not heard the initiating speaker')
        for proposal in (bid.reason, bid.nonverbal):
            if proposal is not None and screen_reply(brief, proposal) is not None:
                raise GenerationError('private_reason', 'a bid contains unsupported private content')
    # Claim even an empty plan: held/silent exchanges are executions too. Weak keys avoid a
    # process-lifetime registry of old turns, and the lock closes concurrent claim/replay races.
    with _CLAIM_LOCK:
        if meter in _CLAIMS or any(meter.status(call.call_id) is not None for call in plan.inference.calls):
            raise GenerationError('already_run', 'this conversation meter already ran')
        _CLAIMS[meter] = True
    audiences = {bid.actor: frozenset(bid.audience) for bid in plan.bids}
    modes = {bid.actor: ContributionMode(bid.kind) for bid in plan.bids if bid.actor in plan.speakers}
    generation = runtime.run_turn(
        meter, [by_actor[actor] for actor in plan.speakers], controls=controls, token=token, cancel=cancel,
        modes=modes, scene_voices=[brief.voice for brief in briefs if brief.actor not in plan.speakers],
        hears=lambda listener, speaker: listener in audiences.get(speaker, ()))
    if generation.status == 'complete' and cancel is not None and cancel.cancelled:
        generation = TurnOutcome('cancelled', generation.lines, generation.calls, generation.meter_token)
    quiet = tuple(bid for bid in plan.bids if bid.kind not in SPEAKING) if (
        generation.status == 'complete' and not plan.player_speaking) else ()
    return ConversationOutcome(plan, generation, quiet)
