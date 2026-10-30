"""Book actual model usage against a turn plan, and say plainly what is missing.

Counts a provider did not report are ``None``, never zero; a planned call with
no recorded attempt is *unmetered*, not free; estimated or synthetic counts are
never presented as provider-reported. Every attempt, including a failed retry,
is summed, because every attempt can be billed. Nothing here holds prompt or
reply text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from ._values import FrozenMap, collection, count, identifier
from .plan import CallId, DeclinedRequest, OptionalRequest, TurnInferencePlan
from .policy import InferenceStage

TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens",
)
Outcome = Literal["completed", "failed", "cancelled", "not_sent"]
OUTCOMES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "not_sent"})


class UnplannedCallError(LookupError):
    """Usage for a call the turn plan does not contain: a hidden model call."""


class Measurement(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """One attempt's token counts; ``None`` means the provider did not say.

    ``input_tokens`` is the whole prompt, cached parts included (adapters
    normalise providers that report only the uncached part). Cache reads and
    writes are slices of it, and reasoning is a slice of the output, never
    additions to them.
    """

    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in TOKEN_FIELDS:
            value = getattr(self, name)
            if value is not None:
                count(value, name)
        cached = (self.cache_read_tokens or 0) + (self.cache_write_tokens or 0)
        if self.input_tokens is not None and cached > self.input_tokens:
            raise ValueError("cache reads and writes are slices of input_tokens")
        if (self.output_tokens is not None and self.reasoning_tokens is not None
                and self.reasoning_tokens > self.output_tokens):
            raise ValueError("reasoning_tokens is a slice of output_tokens")


@dataclass(frozen=True, slots=True)
class CallAttempt:
    """One try at a planned call. ``route`` names the provider/model, no secrets."""

    call_id: CallId
    attempt: int
    usage: TokenUsage
    measurement: Measurement
    outcome: Outcome
    route: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, CallId):
            raise ValueError("an attempt needs the CallId of a planned call")
        count(self.attempt, "attempt", minimum=1)
        if not isinstance(self.usage, TokenUsage):
            raise ValueError("an attempt needs a TokenUsage")
        object.__setattr__(self, "measurement", _measurement(self.measurement))
        if not isinstance(self.outcome, str) or self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {self.outcome!r}")
        object.__setattr__(self, "outcome", str(self.outcome))
        if self.route is not None:
            identifier(self.route, "route")
        if self.outcome == "not_sent":
            usage = self.usage
            if (usage.input_tokens, usage.output_tokens) != (0, 0) or any(
                    getattr(usage, name) not in (0, None) for name in TOKEN_FIELDS[2:]):
                raise ValueError("an attempt that was never sent used no tokens")


def _measurement(value: object) -> Measurement:
    if isinstance(value, str):
        try:
            return Measurement(value)
        except ValueError:
            pass
    raise ValueError(f"unknown measurement: {value!r}")


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """Sums of reported counts, and how many attempts left each count unreported.

    ``measurements`` covers attempts that were sent; a ``not_sent`` attempt
    used nothing, so how it was measured says nothing about the totals.
    """

    attempts: int
    sums: Mapping[str, int]
    unreported: Mapping[str, int]
    measurements: frozenset[Measurement]

    def __post_init__(self) -> None:
        count(self.attempts, "attempts")
        for name in ("sums", "unreported"):
            values = getattr(self, name)
            if not isinstance(values, Mapping) or set(values) != set(TOKEN_FIELDS):
                raise ValueError(f"{name} must count exactly {TOKEN_FIELDS}")
            for field_name in TOKEN_FIELDS:
                count(values[field_name], f"{name}[{field_name}]")
            object.__setattr__(self, name, FrozenMap(values))
        if any(n > self.attempts for n in self.unreported.values()):
            raise ValueError("more unreported counts than attempts")
        measurements = frozenset(_measurement(m) for m in collection(
            self.measurements, "measurements"))
        if self.attempts == 0 and measurements:
            raise ValueError("no attempts, so nothing was measured")
        object.__setattr__(self, "measurements", measurements)

    @classmethod
    def of(cls, attempts: Iterable[CallAttempt]) -> UsageTotals:
        attempts = tuple(attempts)
        sums = {name: 0 for name in TOKEN_FIELDS}
        unreported = {name: 0 for name in TOKEN_FIELDS}
        for each in attempts:
            for name in TOKEN_FIELDS:
                value = getattr(each.usage, name)
                if value is None:
                    unreported[name] += 1
                else:
                    sums[name] += value
        return cls(len(attempts), sums, unreported, frozenset(
            each.measurement for each in attempts if each.outcome != "not_sent"))

    def __add__(self, other: UsageTotals) -> UsageTotals:
        return UsageTotals(
            self.attempts + other.attempts,
            {n: self.sums[n] + other.sums[n] for n in TOKEN_FIELDS},
            {n: self.unreported[n] + other.unreported[n] for n in TOKEN_FIELDS},
            self.measurements | other.measurements,
        )

    @property
    def fully_reported(self) -> bool:
        """Every attempt reported its input and output counts."""
        return self.unreported["input_tokens"] == self.unreported["output_tokens"] == 0


EMPTY_TOTALS = UsageTotals.of(())


def _sum(parts: Iterable[UsageTotals]) -> UsageTotals:
    return sum(parts, EMPTY_TOTALS)


def _provider_reported_only(complete: bool, measurements: frozenset[Measurement]) -> bool:
    return complete and measurements == {Measurement.PROVIDER_REPORTED}


@dataclass(frozen=True, slots=True)
class TurnUsageSummary:
    """A turn's usage by stage and actor, with its missing data called out.

    Validated on construction (the breakdowns must add up to the total), and
    ``complete`` and ``provider_reported_only`` are derived, so a summary cannot
    claim completeness its own figures contradict.
    """

    campaign_id: str
    turn_id: str
    phases: tuple[str, ...]
    total: UsageTotals
    baseline: UsageTotals
    optional: UsageTotals
    by_stage: Mapping[InferenceStage, UsageTotals]
    by_actor: Mapping[str | None, UsageTotals]
    unmetered_call_ids: frozenset[CallId]
    declined: tuple[DeclinedRequest, ...]

    def __post_init__(self) -> None:
        identifier(self.campaign_id, "campaign_id")
        identifier(self.turn_id, "turn_id")
        object.__setattr__(self, "phases", collection(self.phases, "phases"))
        object.__setattr__(self, "declined", collection(self.declined, "declined"))
        object.__setattr__(self, "unmetered_call_ids",
                           frozenset(collection(self.unmetered_call_ids, "unmetered_call_ids")))
        for name in ("by_stage", "by_actor"):
            if not isinstance(getattr(self, name), Mapping):
                raise ValueError(f"{name} must be a mapping")
            object.__setattr__(self, name, FrozenMap(getattr(self, name)))
        for name in ("total", "baseline", "optional"):
            if not isinstance(getattr(self, name), UsageTotals):
                raise ValueError(f"{name} must be UsageTotals")
        for label, parts in (("baseline + optional", (self.baseline, self.optional)),
                             ("by_stage", self.by_stage.values()),
                             ("by_actor", self.by_actor.values())):
            if _sum(parts) != self.total:
                raise ValueError(f"{label} does not add up to the total")
        for cid in self.unmetered_call_ids:
            if not isinstance(cid, CallId) or (cid.campaign_id, cid.turn_id) != (
                    self.campaign_id, self.turn_id):
                raise ValueError(f"unmetered call {cid!r} is not from this turn")

    @property
    def complete(self) -> bool:
        return not self.unmetered_call_ids and self.total.fully_reported

    @property
    def measurements(self) -> frozenset[Measurement]:
        return self.total.measurements

    @property
    def provider_reported_only(self) -> bool:
        return _provider_reported_only(self.complete, self.measurements)


@dataclass(frozen=True, slots=True)
class TurnUsageLedger:
    """Attempts booked against one turn's plan.

    A summary accounts only for the plan the ledger is bound to. Add later
    phases through ``add_phase`` here (or ``extend`` to the turn's final plan)
    so their calls are counted, unmetered or not.
    """

    plan: TurnInferencePlan
    attempts: tuple[CallAttempt, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.plan, TurnInferencePlan):
            raise ValueError("a ledger needs a TurnInferencePlan")
        attempts = collection(self.attempts, "attempts")
        object.__setattr__(self, "attempts", attempts)
        for index, each in enumerate(attempts):
            self._check(each, attempts[:index])

    def _check(self, new: CallAttempt, earlier: tuple[CallAttempt, ...]) -> None:
        if not isinstance(new, CallAttempt):
            raise ValueError(f"not a call attempt: {new!r}")
        if new.call_id not in self.plan.call_ids:
            raise UnplannedCallError(f"no planned call {new.call_id!r} in this turn")
        limit = self.plan.policy.max_attempts_per_call
        if new.attempt > limit:
            raise ValueError(f"attempt {new.attempt} exceeds the limit of {limit}")
        previous = [each for each in earlier if each.call_id == new.call_id]
        if new.attempt != len(previous) + 1:
            raise ValueError(f"attempt {new.attempt} of {new.call_id!r} is out of order")
        if any(each.outcome == "completed" for each in previous):
            raise ValueError(f"{new.call_id!r} already completed")

    def record(self, attempt: CallAttempt) -> TurnUsageLedger:
        self._check(attempt, self.attempts)
        return TurnUsageLedger(self.plan, self.attempts + (attempt,))

    def add_phase(
        self,
        phase: str,
        *,
        speaking_npc_ids: Iterable[str] = (),
        optional_requests: Iterable[OptionalRequest] = (),
    ) -> TurnUsageLedger:
        """Plan another phase of this turn and keep accounting for all of it."""
        return self.extend(self.plan.add_phase(
            phase, speaking_npc_ids=speaking_npc_ids, optional_requests=optional_requests))

    def extend(self, plan: TurnInferencePlan) -> TurnUsageLedger:
        """Rebind to this turn's plan after one or more later phases were added."""
        old = self.plan
        if not isinstance(plan, TurnInferencePlan):
            raise ValueError("a ledger extends only to a TurnInferencePlan")
        new_phases = plan.phases[len(old.phases):]
        if ((plan.campaign_id, plan.turn_id, plan.policy, plan.present_actor_ids,
             plan.player_controlled_ids)
                != (old.campaign_id, old.turn_id, old.policy, old.present_actor_ids,
                    old.player_controlled_ids)
                or plan.phases[:len(old.phases)] != old.phases or not new_phases
                or plan.calls[:len(old.calls)] != old.calls
                or plan.declined[:len(old.declined)] != old.declined
                or any(call.call_id.phase not in new_phases
                       for call in plan.calls[len(old.calls):])
                or any(declined.phase not in new_phases
                       for declined in plan.declined[len(old.declined):])):
            raise ValueError("a ledger extends only to a later phase of the same turn plan")
        return TurnUsageLedger(plan, self.attempts)

    def summary(self) -> TurnUsageSummary:
        plan = self.plan
        stage_of = {call.call_id: call.stage for call in plan.calls}

        def totals(keep) -> UsageTotals:
            return UsageTotals.of(each for each in self.attempts if keep(each.call_id))

        attempted = {each.call_id for each in self.attempts}
        return TurnUsageSummary(
            campaign_id=plan.campaign_id,
            turn_id=plan.turn_id,
            phases=plan.phases,
            total=totals(lambda cid: True),
            baseline=totals(lambda cid: stage_of[cid].is_baseline),
            optional=totals(lambda cid: not stage_of[cid].is_baseline),
            by_stage={stage: totals(lambda cid, stage=stage: cid.stage is stage)
                      for stage in dict.fromkeys(call.stage for call in plan.calls)},
            by_actor={actor: totals(lambda cid, actor=actor: cid.actor_id == actor)
                      for actor in dict.fromkeys(call.actor_id for call in plan.calls)},
            unmetered_call_ids=plan.call_ids - attempted,
            declined=plan.declined,
        )


def _merge(parts: Iterable[Mapping]) -> Mapping:
    merged: dict = {}
    for part in parts:
        for key, value in part.items():
            merged[key] = merged[key] + value if key in merged else value
    return FrozenMap(merged)


@dataclass(frozen=True, slots=True)
class SessionUsage:
    """Turn summaries of one campaign session, each turn counted once."""

    campaign_id: str
    summaries: tuple[TurnUsageSummary, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.campaign_id, "campaign_id")
        summaries = collection(self.summaries, "summaries")
        object.__setattr__(self, "summaries", summaries)
        turns: set[str] = set()
        for summary in summaries:
            if not isinstance(summary, TurnUsageSummary):
                raise ValueError(f"not a turn usage summary: {summary!r}")
            if summary.campaign_id != self.campaign_id:
                raise ValueError(f"turn {summary.turn_id!r} belongs to another campaign")
            if summary.turn_id in turns:
                raise ValueError(f"turn {summary.turn_id!r} is already counted")
            turns.add(summary.turn_id)

    def add(self, summary: TurnUsageSummary) -> SessionUsage:
        return SessionUsage(self.campaign_id, self.summaries + (summary,))

    @property
    def total(self) -> UsageTotals:
        return _sum(s.total for s in self.summaries)

    @property
    def baseline(self) -> UsageTotals:
        return _sum(s.baseline for s in self.summaries)

    @property
    def optional(self) -> UsageTotals:
        return _sum(s.optional for s in self.summaries)

    @property
    def by_stage(self) -> Mapping[InferenceStage, UsageTotals]:
        return _merge(s.by_stage for s in self.summaries)

    @property
    def by_actor(self) -> Mapping[str | None, UsageTotals]:
        return _merge(s.by_actor for s in self.summaries)

    @property
    def unmetered_call_ids(self) -> frozenset[CallId]:
        return frozenset().union(*(s.unmetered_call_ids for s in self.summaries))

    @property
    def measurements(self) -> frozenset[Measurement]:
        return self.total.measurements

    @property
    def complete(self) -> bool:
        return all(s.complete for s in self.summaries)

    @property
    def provider_reported_only(self) -> bool:
        return _provider_reported_only(self.complete, self.measurements)
