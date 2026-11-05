"""Issue a turn's planned generation calls, one metered provider call per speaking NPC.

The plan decides which calls exist (``grimoire.generation.plan_generation_turn``); the briefs decide
what each call sees. The runtime only checks that they agree, issues the calls in plan order, lets
each later speaker hear the lines accepted before it, screens every accepted reply and stops at the
first call that does not end with a line. It returns proposals: nothing is committed or persisted.
No text reaches a consumer before it is accepted and screened.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.inference import CallId, InferenceStage, PlannedCall
from grimoire.core.model import ControlAssignment
from grimoire.generation import (
    DirectorBrief, GenerationError, HeardLine, NpcBrief, checked_controls, controller, director_messages,
    npc_messages, require_director, screen_reply,
)
from grimoire.providers.adapter import Approvals, CallResult, ProviderAdapter
from grimoire.providers.cancel import CancelToken
from grimoire.providers.config import RouteChoice
from grimoire.providers.meter import TurnMeter
from grimoire.providers.request import ChatMessage, ModelRequest
from grimoire.security import WorkerHandle

from .fence import FenceToken, cancel_when_stale

#: What one planned call came to. Only ``accepted`` produced a line.
CallStatusLabel = Literal["accepted", "rejected", "incomplete", "failed", "cancelled", "refused", "not_issued"]
#: How the turn ended. Only ``complete`` means every planned call produced a line.
TurnStatus = Literal["complete", "halted", "cancelled", "stale"]
#: The display name of the scene-level reply in later speakers' transcripts.
NARRATOR = "Narrator"
DEFAULT_MAX_OUTPUT_TOKENS = 400
_SUPPORTED = frozenset({InferenceStage.NPC_GENERATION, InferenceStage.DIRECTOR_GENERATION})


@dataclass(frozen=True, slots=True)
class GeneratedLine:
    """One accepted, screened, unreviewed proposal. ``actor`` is ``None`` for the director reply."""

    call_id: CallId
    actor: InstanceRef | None
    speaker: str
    actor_revision: SourceRef | None  # the actor's own selected revision (a derived portrayal, say)
    voice_profile: SourceRef | None  # the profile revision its voice came from (maybe an ancestor's)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """One planned call's end. ``reason`` is a rejection, refusal, error kind or finish reason."""

    call_id: CallId
    status: CallStatusLabel
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """The turn's lines (none when stale) and every planned call's outcome, in plan order."""

    status: TurnStatus
    lines: tuple[GeneratedLine, ...]
    calls: tuple[CallOutcome, ...]


def _briefs(briefs: object) -> tuple[NpcBrief, ...]:
    if isinstance(briefs, (str, bytes)):
        raise GenerationError("bad_brief", "briefs must be a sequence of NpcBrief values")
    try:
        briefs = tuple(briefs)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_brief", "briefs must be a sequence of NpcBrief values") from None
    if not all(isinstance(brief, NpcBrief) for brief in briefs):
        raise GenerationError("bad_brief", "briefs must be NpcBrief values")
    return briefs


def _assign(meter: TurnMeter, briefs: tuple[NpcBrief, ...], director: DirectorBrief | None,
            controls: tuple[ControlAssignment, ...]) -> list[tuple[PlannedCall, NpcBrief | DirectorBrief]]:
    """Each planned call with its brief, or a refusal before any call is issued."""
    plan = meter.ledger.plan
    by_actor: dict[str, NpcBrief] = {}
    for brief in briefs:
        if brief.actor.campaign_id != plan.campaign_id:
            raise GenerationError("extra_brief", f"{brief.actor} is not in campaign {plan.campaign_id}")
        if brief.actor.instance_id in by_actor:
            raise GenerationError("duplicate_brief", f"{brief.actor} has two briefs")
        by_actor[brief.actor.instance_id] = brief
    pairs: list[tuple[PlannedCall, NpcBrief | DirectorBrief]] = []
    used: set[str] = set()
    directed = False
    for call in plan.calls:
        if call.stage not in _SUPPORTED:
            raise GenerationError("unsupported_stage", f"{call.call_id} is a {call.stage} call; this "
                                  "runtime issues only npc_generation and director_generation")
        if meter.status(call.call_id) is not None:
            raise GenerationError("call_already_run", f"{call.call_id} already ran on this meter")
        if call.stage is InferenceStage.DIRECTOR_GENERATION:
            if director is None:
                raise GenerationError("director_mismatch", "a director reply is planned but no director brief")
            owners = {actor_id: controller(plan.campaign_id, actor_id, controls)
                      for actor_id in plan.present_actor_ids}
            if None in owners.values():
                raise GenerationError("unassigned_actor", "every present actor needs a controller for a director reply")
            players = frozenset(actor_id for actor_id, owner in owners.items() if owner != DIRECTOR)
            # Both the plan's player characters and control now must agree with the brief.
            if (director.campaign_id != plan.campaign_id or director.players != players
                    or director.players != frozenset(plan.player_controlled_ids)):
                raise GenerationError("director_mismatch",
                                      "the director brief names other player characters than control does now")
            directed = True
            pairs.append((call, director))
            continue
        brief = by_actor.get(call.actor_id)  # type: ignore[arg-type]
        if brief is None:
            raise GenerationError("missing_brief", f"{call.call_id} has no brief")
        used.add(call.actor_id)  # type: ignore[arg-type]
        pairs.append((call, brief))
    extra = sorted(set(by_actor) - used)
    if extra:
        raise GenerationError("extra_brief", f"briefs for actors with no planned call: {extra}")
    if director is not None and not directed:
        raise GenerationError("director_mismatch", "a director brief was given but no director reply is planned")
    for actor_id in used:
        # A hand-built plan or brief cannot voice a player character.
        require_director(plan.campaign_id, actor_id, controls)
    return pairs


def _reason(result: CallResult) -> str | None:
    if result.status == "refused":
        return result.refusal_reason
    if result.status == "incomplete":
        return result.finish_reason or "incomplete"
    return result.error.kind if result.error is not None else None


class GenerationRuntime:
    """Fixed provider routing for a turn's generation calls; the handle and approvals pass through."""

    def __init__(self, adapter: ProviderAdapter, routes: Sequence[RouteChoice], handle: WorkerHandle, *,
                 approvals: Approvals | None = None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                 temperature: float | None = None) -> None:
        routes = tuple(routes)
        if not routes or not all(isinstance(route, RouteChoice) for route in routes):
            raise ValueError("routes must be a nonempty sequence of RouteChoice")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be an int >= 1")
        self._adapter = adapter
        self._routes = routes
        self._handle = handle
        self._approvals = approvals
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    def _request(self, messages: tuple[tuple[str, str], ...]) -> ModelRequest:
        return ModelRequest(tuple(ChatMessage(role, content) for role, content in messages),
                            max_output_tokens=self._max_output_tokens, temperature=self._temperature)

    def run_turn(self, meter: TurnMeter, briefs: Iterable[NpcBrief], *, controls: Iterable[ControlAssignment],
                 director: DirectorBrief | None = None, token: FenceToken | None = None,
                 cancel: CancelToken | None = None) -> TurnOutcome:
        """Issue every planned call in order; see the module docstring and docs/GENERATION.md.

        Refused before any call: a plan with any other stage, a call without its brief or a brief
        without its call, a director brief without a planned director reply (or the reverse), a call
        that already ran on ``meter``, and any brief actor that is not director-controlled now.
        ``token`` (from ``RunFence.claim``) makes the run stale when a newer run claims the same key:
        a stale run returns no lines. ``cancel`` cancels the in-flight call; nothing runs after it.
        """
        if not isinstance(meter, TurnMeter):
            raise GenerationError("bad_meter", "run_turn needs the turn's TurnMeter")
        if director is not None and not isinstance(director, DirectorBrief):
            raise GenerationError("bad_brief", "director must be a DirectorBrief")
        if token is not None and not isinstance(token, FenceToken):
            raise GenerationError("bad_token", "token must be a FenceToken from RunFence.claim")
        if cancel is not None and not isinstance(cancel, CancelToken):
            raise GenerationError("bad_cancel", "cancel must be a CancelToken")
        briefs = _briefs(briefs)
        pairs = _assign(meter, briefs, director, checked_controls(controls))
        voices = tuple(brief.voice for brief in briefs)
        cancel = cancel if cancel is not None else CancelToken()
        unwatch = cancel_when_stale(token, cancel)
        outcomes: list[CallOutcome] = []
        lines: list[GeneratedLine] = []

        def ended(status: TurnStatus) -> TurnOutcome:
            issued = {outcome.call_id for outcome in outcomes}
            rest = [CallOutcome(call.call_id, "not_issued") for call, _brief in pairs if call.call_id not in issued]
            if token is not None and token.stale:
                status = "stale"
            return TurnOutcome(status, () if status == "stale" else tuple(lines), tuple(outcomes + rest))

        try:
            for call, brief in pairs:
                if token is not None and token.stale:
                    return ended("stale")
                if cancel.cancelled:
                    return ended("cancelled")
                spoken = tuple(HeardLine(line.speaker, line.text, line.actor) for line in lines)
                if isinstance(brief, NpcBrief):
                    messages = npc_messages(brief, spoken)
                else:
                    messages = director_messages(brief, spoken)
                # No on_delta: nothing is shown before acceptance, and a consumer would block retries.
                result = self._adapter.execute(meter, call, self._request(messages), self._routes, self._handle,
                                               approvals=self._approvals, cancel=cancel)
                if result.status != "accepted":
                    outcomes.append(CallOutcome(call.call_id, result.status, _reason(result)))
                    return ended("cancelled" if result.status == "cancelled" else "halted")
                rejected = screen_reply(brief, result.reply or "", spoken=spoken, voices=voices)
                if rejected is not None:
                    outcomes.append(CallOutcome(call.call_id, "rejected", rejected))
                    return ended("halted")
                outcomes.append(CallOutcome(call.call_id, "accepted"))
                if isinstance(brief, NpcBrief):
                    lines.append(GeneratedLine(call.call_id, brief.actor, brief.name, brief.voice.actor_revision,
                                               brief.voice.portrayal, result.reply.strip()))  # type: ignore[union-attr]
                else:
                    lines.append(GeneratedLine(call.call_id, None, NARRATOR, None, None,
                                               result.reply.strip()))  # type: ignore[union-attr]
            return ended("complete")
        finally:
            unwatch()
