"""Issue a turn's planned generation calls, one metered provider call per speaking NPC.

The plan decides which calls exist (``grimoire.generation.plan_generation_turn``); the briefs decide
what each call sees. The runtime only checks that they agree, issues the calls in plan order, lets
each later speaker hear the lines accepted before it, screens every accepted reply and stops at the
first call that does not end with a line. It returns proposals: nothing is committed or persisted.
No text reaches a consumer before it is accepted and screened.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.inference import CallId, InferenceStage, PlannedCall
from grimoire.core.model import ControlAssignment
from grimoire.generation import (
    DirectorBrief, GenerationError, HeardLine, NpcBrief, checked_controls, controller, director_messages,
    decision_messages, decision_options, npc_messages, read_pick, require_director, screen_reply,
)
from grimoire.goals import Choice
from grimoire.providers.adapter import Approvals, CallResult, ProviderAdapter
from grimoire.providers.cancel import CancelToken
from grimoire.providers.config import RouteChoice
from grimoire.providers.meter import TurnMeter
from grimoire.providers.request import ChatMessage, ModelRequest
from grimoire.security import WorkerHandle
from grimoire.voice import ResolvedVoice

from .fence import FenceToken, cancel_when_stale

#: What one planned call came to. Only ``accepted`` produced a line.
CallStatusLabel = Literal["accepted", "rejected", "incomplete", "failed", "cancelled", "refused", "not_issued"]
#: How the turn ended. Only ``complete`` means every planned call produced a line.
TurnStatus = Literal["complete", "halted", "cancelled", "stale"]
#: The display name of the scene-level reply in later speakers' transcripts.
NARRATOR = "Narrator"
DEFAULT_MAX_OUTPUT_TOKENS = 400
#: A pick is one number: a decision call never needs room to write.
DECISION_MAX_OUTPUT_TOKENS = 8
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
    """The turn's lines (none when stale) and every planned call's outcome, in plan order.

    ``meter_token`` is the ``token`` of the meter ``run_turn`` billed these calls to. Two meters on
    one plan have the same call ids, so only it ties the lines to their own bill
    (``record.recorded_turn``). It is a plain string, not the meter, so an outcome pickles and
    keeping one never keeps its meter alive (the adapter refuses a second live meter for a turn).
    """

    status: TurnStatus
    lines: tuple[GeneratedLine, ...]
    calls: tuple[CallOutcome, ...]
    meter_token: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """What one planned ``decision`` call came to (045).

    ``pick`` is the (goal id, approach id) the call chose among the brief's possible approaches, or
    ``None`` when it ended without a usable pick: then the policy's own order decides, and ``reason``
    says why (``unreadable_pick``, a provider refusal, an error kind, ``cancelled`` or ``stale``).
    ``status`` is the call's own status on the meter, and the call is billed either way. ``skipped``
    means no call was made, because there was nothing to offer: ``reason`` is ``no_intention``,
    ``nothing_to_decide``, ``too_many_options`` or ``option_leak``. ``basis`` is the choice the call
    decided on (``None`` for ``no_intention``): ``run_turn`` holds the narration brief to its agenda,
    knowledge and scene, so neither a pick nor the fallback can be rebound to another choice.
    """

    call_id: CallId
    actor: InstanceRef
    status: CallStatusLabel | Literal["skipped"]
    pick: tuple[str, str] | None = None
    reason: str | None = None
    meter_token: str | None = field(default=None, repr=False, compare=False)
    basis: Choice | None = field(default=None, repr=False, compare=False)


def _same_ground(intention: Choice | None, basis: Choice | None) -> bool:
    """Whether a narration brief's choice was made on the very ground a decision call decided on: the
    same agenda, knowledge and scene. Only its selection may differ, and ``run_turn`` checks that apart."""
    if intention is None or basis is None:
        return intention is basis
    return ((intention.agenda, intention.knowledge, intention.present, intention.unrecognised)
            == (basis.agenda, basis.knowledge, basis.present, basis.unrecognised))


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
            controls: tuple[ControlAssignment, ...],
            decided: dict[CallId, DecisionOutcome]) -> list[tuple[PlannedCall, NpcBrief | DirectorBrief]]:
    """Each planned call with its brief, or a refusal before any call is issued.

    A ``decision`` call must already have run (or been skipped) on this meter through ``decide``
    (``decision_not_run``), and the NPC's brief must then carry what it decided: its pick as the
    choice's ``selection``, or no selection when it fell back to the policy (``decision_ignored``). A
    plan with two decision calls about one NPC is ``duplicate_decision``. Narration never re-decides.
    """
    plan = meter.ledger.plan
    by_actor: dict[str, NpcBrief] = {}
    for brief in briefs:
        if brief.actor.campaign_id != plan.campaign_id:
            raise GenerationError("extra_brief", f"{brief.actor} is not in campaign {plan.campaign_id}")
        if brief.actor.instance_id in by_actor:
            raise GenerationError("duplicate_brief", f"{brief.actor} has two briefs")
        if brief.cast is not None and brief.cast != plan.present_actor_ids:
            # 039: the intention was chosen for another scene. An approach may need an actor who has left,
            # and an arrival may switch on a limit that would forbid it: choose again for this turn.
            raise GenerationError("intention_cast_mismatch", f"the intention for {brief.actor} was chosen for "
                                  "another set of present actors than this turn's")
        by_actor[brief.actor.instance_id] = brief
    pairs: list[tuple[PlannedCall, NpcBrief | DirectorBrief]] = []
    used: set[str] = set()
    directed = False
    picks: dict[str, DecisionOutcome] = {}
    for call in plan.calls:
        if call.stage is InferenceStage.DECISION:
            if call.actor_id in picks:
                raise GenerationError("duplicate_decision", f"two decision calls are planned about {call.actor_id}")
            outcome = decided.get(call.call_id)
            ran = outcome is not None and (outcome.status == "skipped") == (meter.status(call.call_id) is None)
            if outcome is None or outcome.meter_token != meter.token or not ran:
                raise GenerationError("decision_not_run", f"{call.call_id} must be decided before the turn runs")
            picks[call.actor_id] = outcome  # type: ignore[index]
            continue
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
        if directed:
            # Narration answers the speakers; it never runs mid-turn for a later NPC to build on.
            raise GenerationError("director_not_last", f"{call.call_id} is planned after the director reply")
        brief = by_actor.get(call.actor_id)  # type: ignore[arg-type]
        if brief is None:
            raise GenerationError("missing_brief", f"{call.call_id} has no brief")
        used.add(call.actor_id)  # type: ignore[arg-type]
        pairs.append((call, brief))
    for actor_id, brief in by_actor.items():
        selection = brief.intention.selection if brief.intention is not None else None
        # Without a decision call on this meter a selection is a reroll's replayed record (045), which
        # ``choose`` held to what is possible and permitted, as it holds any selection.
        decided = picks.get(actor_id)
        # A brief with no choice is fine only when there was none to decide on (``no_intention``).
        if decided is not None and (selection != decided.pick or not _same_ground(brief.intention, decided.basis)):
            raise GenerationError("decision_ignored", f"the brief for {brief.actor} does not carry what a "
                                  "decision call on this meter picked")
    extra = sorted(set(by_actor) - used)
    if extra:
        raise GenerationError("extra_brief", f"briefs for actors with no planned call: {extra}")
    if director is not None and not directed:
        raise GenerationError("director_mismatch", "a director brief was given but no director reply is planned")
    for actor_id in used:
        # A hand-built plan or brief cannot voice a player character.
        require_director(plan.campaign_id, actor_id, controls)
    return pairs


#: The longest fallback reason a decision record keeps (``grimoire.decisions``).
_MAX_REASON = 64
#: ``decision_messages``' refusals that mean there is nothing to offer: no call is made, the policy decides.
_NOTHING_TO_OFFER = frozenset({"no_intention", "nothing_to_decide", "too_many_options", "option_leak"})


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
        # Per meter: two meters on one plan share call ids, and a finished turn's outcomes go with it.
        self._decided: weakref.WeakKeyDictionary[TurnMeter, dict[CallId, DecisionOutcome]] = \
            weakref.WeakKeyDictionary()

    def _request(self, messages: tuple[tuple[str, str], ...], max_output_tokens: int | None = None) -> ModelRequest:
        return ModelRequest(tuple(ChatMessage(role, content) for role, content in messages),
                            max_output_tokens=max_output_tokens or self._max_output_tokens,
                            temperature=self._temperature)

    def decide(self, meter: TurnMeter, call: PlannedCall, brief: NpcBrief, *, controls: Iterable[ControlAssignment],
               token: FenceToken | None = None, cancel: CancelToken | None = None) -> DecisionOutcome:
        """Issue one planned ``decision`` call (045): the NPC picks among its brief's possible approaches.

        Refused before the call: a call that is not a planned ``decision`` about ``brief``'s actor
        (``decision_mismatch``) or that already ran or was skipped on ``meter`` (``call_already_run``), a
        brief whose choice was made for another set of present actors (``intention_cast_mismatch``) or
        already carries a selection (``already_selected``), an actor that is not director-controlled now,
        and whatever the adapter refuses before sending (it raises, and nothing is recorded). With nothing
        to offer (no choice on the brief, fewer than two possible approaches, too many, or an option that
        would state a secret) no call is made: the outcome is ``skipped`` with that reason, and the
        policy's order decides. Otherwise the call is billed to ``meter`` and the outcome's ``pick`` is the
        option it named, or ``None`` (the policy's order decides) when the reply names none, the call
        failed or was cancelled, or the run went stale. Build the NPC's generation brief with
        ``choose(..., selected=pick)`` when there is a pick; ``run_turn`` on this runtime and meter then
        checks it does.
        """
        if not isinstance(meter, TurnMeter):
            raise GenerationError("bad_meter", "decide needs the turn's TurnMeter")
        if not isinstance(brief, NpcBrief):
            raise GenerationError("bad_brief", "decide needs the NPC's NpcBrief")
        if token is not None and not isinstance(token, FenceToken):
            raise GenerationError("bad_token", "token must be a FenceToken from RunFence.claim")
        if cancel is not None and not isinstance(cancel, CancelToken):
            raise GenerationError("bad_cancel", "cancel must be a CancelToken")
        plan = meter.ledger.plan
        if (not isinstance(call, PlannedCall) or call not in plan.calls or call.stage is not InferenceStage.DECISION
                or call.actor_id != brief.actor.instance_id or plan.campaign_id != brief.actor.campaign_id):
            raise GenerationError("decision_mismatch", f"no planned decision call about {brief.actor} on this meter")
        decided = self._decided.setdefault(meter, {})
        if meter.status(call.call_id) is not None or call.call_id in decided:
            raise GenerationError("call_already_run", f"{call.call_id} already ran on this meter")
        if brief.intention is not None and brief.cast != plan.present_actor_ids:
            raise GenerationError("intention_cast_mismatch", f"the choice for {brief.actor} was made for another "
                                  "set of present actors than this turn's")
        if brief.intention is not None and brief.intention.selection is not None:
            raise GenerationError("already_selected", f"the choice for {brief.actor} was already selected")
        require_director(plan.campaign_id, brief.actor.instance_id, checked_controls(controls))

        def ended(status: CallStatusLabel | Literal["skipped"], pick: tuple[str, str] | None = None,
                  reason: str | None = None) -> DecisionOutcome:
            if token is not None and token.stale and status != "skipped":
                pick, reason = None, "stale"
            reason = reason[:_MAX_REASON] if reason is not None else None  # a record keeps a short reason
            outcome = DecisionOutcome(call.call_id, brief.actor, status, pick, reason, meter_token=meter.token,
                                      basis=brief.intention)
            decided[call.call_id] = outcome
            return outcome

        try:
            options = decision_options(brief)
            messages = decision_messages(brief)
        except GenerationError as error:
            if error.reason in _NOTHING_TO_OFFER:
                return ended("skipped", reason=error.reason)
            raise
        cancel = cancel if cancel is not None else CancelToken()
        unwatch = cancel_when_stale(token, cancel)
        try:
            try:
                result = self._adapter.execute(meter, call, self._request(messages, DECISION_MAX_OUTPUT_TOKENS),
                                               self._routes, self._handle, approvals=self._approvals, cancel=cancel)
            except Exception as error:
                if meter.status(call.call_id) is None:
                    raise  # refused before anything was sent: nothing is billed, and nothing is decided
                return ended(meter.status(call.call_id), reason=type(error).__name__)  # type: ignore[arg-type]
            if result.status != "accepted":
                return ended(result.status, reason=_reason(result) or result.status)
            pick = read_pick(result.reply, options)
            if pick is None:
                return ended("accepted", reason="unreadable_pick")
            return ended("accepted", pick)
        finally:
            unwatch()

    def run_turn(self, meter: TurnMeter, briefs: Iterable[NpcBrief], *, controls: Iterable[ControlAssignment],
                 director: DirectorBrief | None = None, token: FenceToken | None = None,
                 cancel: CancelToken | None = None, scene_voices: Iterable[ResolvedVoice] = ()) -> TurnOutcome:
        """Issue every planned call in order; see the module docstring and docs/GENERATION.md.

        Refused before any call: a ``decision`` call ``decide`` has not run on ``meter``
        (``decision_not_run``) or whose pick the NPC's brief does not carry (``decision_ignored``), a plan
        with any other stage, a call without its brief or a brief
        without its call, a director brief without a planned director reply (or the reverse), a call
        that already ran on ``meter``, any brief actor that is not director-controlled now, and a brief
        whose 039 intention was chosen for another set of present actors than this plan's.
        ``token`` (from ``RunFence.claim``) makes the run stale when a newer run claims the same key:
        a stale run returns no lines. ``cancel`` cancels the in-flight call; nothing runs after it.
        ``scene_voices`` are the other voices present (silent NPCs): a line reproducing one of their
        authored examples is rejected as ``copied_example`` too, as 014 asks for every scene voice.
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
        pairs = _assign(meter, briefs, director, checked_controls(controls), self._decided.get(meter, {}))
        scene_voices = tuple(scene_voices)
        if not all(isinstance(voice, ResolvedVoice) for voice in scene_voices):
            raise GenerationError("bad_voice", "scene_voices must be ResolvedVoice values")
        voices = tuple(brief.voice for brief in briefs) + scene_voices
        cancel = cancel if cancel is not None else CancelToken()
        unwatch = cancel_when_stale(token, cancel)
        outcomes: list[CallOutcome] = []
        lines: list[GeneratedLine] = []

        def ended(status: TurnStatus) -> TurnOutcome:
            issued = {outcome.call_id for outcome in outcomes}
            rest = [CallOutcome(call.call_id, "not_issued") for call, _brief in pairs if call.call_id not in issued]
            if token is not None and token.stale:
                status = "stale"
            return TurnOutcome(status, () if status == "stale" else tuple(lines), tuple(outcomes + rest),
                               meter_token=meter.token)

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
                try:
                    result = self._adapter.execute(meter, call, self._request(messages), self._routes,
                                                   self._handle, approvals=self._approvals, cancel=cancel)
                except Exception as error:  # an approvals callback or adapter bug: keep the lines so far
                    outcomes.append(CallOutcome(call.call_id, "failed", type(error).__name__))
                    return ended("halted")
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
