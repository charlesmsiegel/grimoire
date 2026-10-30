"""Plan a turn's model calls before making any of them.

A speaking NPC contribution gets exactly one generation call. Silent or absent
actors get nothing. Optional calls happen only when explicitly requested and
the policy enables the stage with turn capacity left; otherwise the request is
declined as data, so a UI can say what was skipped and why.

One plan covers a whole turn. Phases (say, a ``decision`` call choosing who
answers, then the answers) are added to that plan, so a phase cannot bring its
own policy or reset a cap.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from .policy import InferencePolicy, InferenceStage, coerce_stage, count, identifier

DeclineReason = Literal["disabled", "cap_reached"]
DECLINE_REASONS: frozenset[str] = frozenset({"disabled", "cap_reached"})
GENERATION_PURPOSE = "speaking NPC contribution"


class InferencePlanError(ValueError):
    """The caller asked for something no plan may contain; nothing was planned."""


def _optional_id(value: object, label: str) -> str | None:
    return None if value is None else identifier(value, label)


@dataclass(frozen=True, slots=True)
class OptionalRequest:
    """A request for one optional call, about one actor or the scene (None)."""

    stage: InferenceStage
    actor_id: str | None
    purpose: str

    def __post_init__(self) -> None:
        stage = coerce_stage(self.stage)
        if stage.is_baseline:
            raise InferencePlanError(f"{stage} is planned from speakers, not requested")
        object.__setattr__(self, "stage", stage)
        _optional_id(self.actor_id, "actor_id")
        identifier(self.purpose, "purpose")


@dataclass(frozen=True, slots=True)
class CallId:
    """Structured and turn-scoped, so no two planned calls can be confused."""

    campaign_id: str
    turn_id: str
    phase: str
    stage: InferenceStage
    actor_id: str | None
    ordinal: int

    def __post_init__(self) -> None:
        for label in ("campaign_id", "turn_id", "phase"):
            identifier(getattr(self, label), label)
        object.__setattr__(self, "stage", coerce_stage(self.stage))
        _optional_id(self.actor_id, "actor_id")
        count(self.ordinal, "ordinal", minimum=1)


@dataclass(frozen=True, slots=True)
class PlannedCall:
    call_id: CallId
    purpose: str

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, CallId):
            raise ValueError("a planned call needs a CallId")
        identifier(self.purpose, "purpose")

    @property
    def stage(self) -> InferenceStage:
        return self.call_id.stage

    @property
    def actor_id(self) -> str | None:
        return self.call_id.actor_id


@dataclass(frozen=True, slots=True)
class DeclinedRequest:
    phase: str
    request: OptionalRequest
    reason: DeclineReason

    def __post_init__(self) -> None:
        identifier(self.phase, "phase")
        if not isinstance(self.request, OptionalRequest):
            raise ValueError("a declined request needs an OptionalRequest")
        if self.reason not in DECLINE_REASONS:
            raise ValueError(f"unknown decline reason: {self.reason!r}")


@dataclass(frozen=True, slots=True)
class TurnInferencePlan:
    """Every model call one turn may make, and the optional ones it declined.

    Validated on construction, so a hand-built plan cannot hold a call that
    ``plan_turn``/``add_phase`` would have refused or declined.
    """

    campaign_id: str
    turn_id: str
    policy: InferencePolicy = field(default_factory=InferencePolicy)
    present_actor_ids: frozenset[str] = frozenset()
    player_controlled_ids: frozenset[str] = frozenset()
    phases: tuple[str, ...] = ()
    calls: tuple[PlannedCall, ...] = ()
    declined: tuple[DeclinedRequest, ...] = ()

    def __post_init__(self) -> None:
        for name, kind in (("present_actor_ids", frozenset), ("player_controlled_ids", frozenset),
                           ("phases", tuple), ("calls", tuple), ("declined", tuple)):
            object.__setattr__(self, name, kind(getattr(self, name)))
        try:
            self._validate()
        except InferencePlanError:
            raise
        except ValueError as error:
            raise InferencePlanError(str(error)) from error

    def _validate(self) -> None:
        identifier(self.campaign_id, "campaign_id")
        identifier(self.turn_id, "turn_id")
        if not isinstance(self.policy, InferencePolicy):
            raise InferencePlanError("policy must be an InferencePolicy")
        for actor in self.present_actor_ids | self.player_controlled_ids:
            identifier(actor, "actor id")
        for phase in self.phases:
            identifier(phase, "phase")
        if len(set(self.phases)) != len(self.phases):
            raise InferencePlanError(f"repeated phase in {self.phases!r}")
        if len(self.call_ids) != len(self.calls):
            raise InferencePlanError("duplicate call id")

        ordinals: Counter[tuple[str, InferenceStage, str | None]] = Counter()
        generated: set[str] = set()
        for call in self.calls:
            if not isinstance(call, PlannedCall):
                raise InferencePlanError(f"not a planned call: {call!r}")
            cid = call.call_id
            if (cid.campaign_id, cid.turn_id) != (self.campaign_id, self.turn_id):
                raise InferencePlanError(f"{cid!r} belongs to another turn")
            if cid.phase not in self.phases:
                raise InferencePlanError(f"{cid!r} names an unlisted phase")
            key = (cid.phase, cid.stage, cid.actor_id)
            ordinals[key] += 1
            if cid.ordinal != ordinals[key]:
                raise InferencePlanError(f"{cid!r} breaks ordinal order")
            if cid.stage.is_baseline:
                if cid.actor_id is None or cid.actor_id in generated:
                    raise InferencePlanError(f"one generation per speaking NPC: {cid!r}")
                generated.add(cid.actor_id)
                self._check_actor(cid.actor_id)
            else:
                if not self.policy.enabled(cid.stage):
                    raise InferencePlanError(f"{cid.stage} is disabled by the policy")
                if cid.actor_id is not None:
                    self._check_actor(cid.actor_id)
        used = self._optional_use()
        for stage, calls in used.items():
            if calls > self.policy.caps[stage]:
                raise InferencePlanError(f"{calls} {stage} calls exceed the turn cap")

        for declined in self.declined:
            if not isinstance(declined, DeclinedRequest):
                raise InferencePlanError(f"not a declined request: {declined!r}")
            if declined.phase not in self.phases:
                raise InferencePlanError(f"decline names an unlisted phase: {declined!r}")
            request = declined.request
            if request.actor_id is not None:
                self._check_actor(request.actor_id)
            cap = self.policy.cap_for(request.stage)
            if declined.reason == "disabled" and cap is not None:
                raise InferencePlanError(f"{request.stage} is enabled, not disabled")
            if declined.reason == "cap_reached" and (cap is None or used[request.stage] < cap):
                raise InferencePlanError(f"{request.stage} has turn capacity left")

    def _check_actor(self, actor_id: str) -> None:
        if actor_id not in self.present_actor_ids:
            raise InferencePlanError(f"{actor_id!r} is not present; absent actors get no calls")
        if actor_id in self.player_controlled_ids:
            raise InferencePlanError(f"{actor_id!r} is player-controlled; no model call decides for it")

    def _optional_use(self) -> Counter[InferenceStage]:
        return Counter(call.stage for call in self.calls if not call.stage.is_baseline)

    @property
    def baseline_calls(self) -> tuple[PlannedCall, ...]:
        return tuple(call for call in self.calls if call.stage.is_baseline)

    @property
    def optional_calls(self) -> tuple[PlannedCall, ...]:
        return tuple(call for call in self.calls if not call.stage.is_baseline)

    @property
    def call_ids(self) -> frozenset[CallId]:
        return frozenset(call.call_id for call in self.calls)

    @property
    def generated_actor_ids(self) -> frozenset[str]:
        return frozenset(call.actor_id for call in self.baseline_calls if call.actor_id)

    @property
    def worst_case_attempts(self) -> int:
        return len(self.calls) * self.policy.max_attempts_per_call

    def call(self, call_id: CallId) -> PlannedCall:
        for call in self.calls:
            if call.call_id == call_id:
                return call
        raise KeyError(call_id)

    def add_phase(
        self,
        phase: str,
        *,
        speaking_npc_ids: Iterable[str] = (),
        optional_requests: Iterable[OptionalRequest] = (),
    ) -> TurnInferencePlan:
        """Plan another step of this turn under the same policy and caps."""
        identifier(phase, "phase", InferencePlanError)
        if phase in self.phases:
            raise InferencePlanError(f"phase {phase!r} was already planned")
        speakers = tuple(speaking_npc_ids)
        requests = tuple(optional_requests)

        # Validate everything before consulting the policy.
        seen: set[str] = set(self.generated_actor_ids)
        for speaker in speakers:
            identifier(speaker, "speaker id", InferencePlanError)
            if speaker in seen:
                raise InferencePlanError(f"{speaker!r} already has a contribution this turn")
            seen.add(speaker)
            self._check_actor(speaker)
        for request in requests:
            if not isinstance(request, OptionalRequest):
                raise InferencePlanError(f"not an optional request: {request!r}")
            if request.actor_id is not None:
                self._check_actor(request.actor_id)

        def call_id(stage: InferenceStage, actor_id: str | None) -> CallId:
            ordinal = 1 + sum(1 for c in calls if c.call_id.phase == phase
                              and c.stage is stage and c.actor_id == actor_id)
            return CallId(self.campaign_id, self.turn_id, phase, stage, actor_id, ordinal)

        calls = list(self.calls)
        declined = list(self.declined)
        for speaker in speakers:
            calls.append(PlannedCall(call_id(InferenceStage.NPC_GENERATION, speaker),
                                     GENERATION_PURPOSE))
        used = self._optional_use()
        for request in requests:
            cap = self.policy.cap_for(request.stage)
            if cap is None:
                declined.append(DeclinedRequest(phase, request, "disabled"))
            elif used[request.stage] >= cap:
                declined.append(DeclinedRequest(phase, request, "cap_reached"))
            else:
                used[request.stage] += 1
                calls.append(PlannedCall(call_id(request.stage, request.actor_id), request.purpose))
        return TurnInferencePlan(
            self.campaign_id, self.turn_id, self.policy, self.present_actor_ids,
            self.player_controlled_ids, self.phases + (phase,), tuple(calls), tuple(declined),
        )


def plan_turn(
    campaign_id: str,
    turn_id: str,
    *,
    present_actor_ids: Iterable[str],
    speaking_npc_ids: Iterable[str] = (),
    player_controlled_ids: Iterable[str] = (),
    optional_requests: Iterable[OptionalRequest] = (),
    phase: str = "main",
    policy: InferencePolicy | None = None,
) -> TurnInferencePlan:
    """Plan a turn's first phase: one generation per speaking NPC, nothing implicit.

    Speakers are decided by the player, director or scene rules (or by a
    planned ``decision`` call in an earlier phase), never by this function.
    """
    present = tuple(present_actor_ids)
    if len(set(present)) != len(present):
        raise InferencePlanError(f"duplicate present actor in {present!r}")
    empty = TurnInferencePlan(
        campaign_id, turn_id, policy if policy is not None else InferencePolicy(),
        frozenset(present), frozenset(player_controlled_ids),
    )
    return empty.add_phase(phase, speaking_npc_ids=speaking_npc_ids,
                           optional_requests=optional_requests)
