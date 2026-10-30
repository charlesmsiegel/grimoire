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
from types import MappingProxyType
from typing import Literal

from .plan import CallId, DeclinedRequest, TurnInferencePlan
from .policy import InferenceStage, count, identifier

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
        if not isinstance(self.measurement, str):
            raise ValueError(f"unknown measurement: {self.measurement!r}")
        try:
            object.__setattr__(self, "measurement", Measurement(self.measurement))
        except ValueError:
            raise ValueError(f"unknown measurement: {self.measurement!r}") from None
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {self.outcome!r}")
        if self.route is not None:
            identifier(self.route, "route")
        if self.outcome == "not_sent":
            usage = self.usage
            if (usage.input_tokens, usage.output_tokens) != (0, 0) or any(
                    getattr(usage, name) not in (0, None) for name in TOKEN_FIELDS[2:]):
                raise ValueError("an attempt that was never sent used no tokens")


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """Sums of reported counts, and how many attempts left each count unreported."""

    attempts: int
    sums: Mapping[str, int]
    unreported: Mapping[str, int]
    measurements: frozenset[Measurement]

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
        return cls(len(attempts), MappingProxyType(sums), MappingProxyType(unreported),
                   frozenset(each.measurement for each in attempts))

    def __add__(self, other: UsageTotals) -> UsageTotals:
        return UsageTotals(
            self.attempts + other.attempts,
            MappingProxyType({n: self.sums[n] + other.sums[n] for n in TOKEN_FIELDS}),
            MappingProxyType({n: self.unreported[n] + other.unreported[n] for n in TOKEN_FIELDS}),
            self.measurements | other.measurements,
        )

    @property
    def fully_reported(self) -> bool:
        """Every attempt reported its input and output counts."""
        return self.unreported["input_tokens"] == self.unreported["output_tokens"] == 0


EMPTY_TOTALS = UsageTotals.of(())


def _provider_reported_only(complete: bool, measurements: frozenset[Measurement]) -> bool:
    return complete and measurements == {Measurement.PROVIDER_REPORTED}


@dataclass(frozen=True, slots=True)
class TurnUsageSummary:
    """A turn's usage by stage and actor, with its missing data called out.

    ``complete`` and ``provider_reported_only`` are derived, so a summary
    cannot claim completeness its own figures contradict.
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

    Bind the ledger to the turn's final plan (``extend`` after ``add_phase``)
    before summarising, or the later phases' calls are not accounted for.
    """

    plan: TurnInferencePlan
    attempts: tuple[CallAttempt, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.plan, TurnInferencePlan):
            raise ValueError("a ledger needs a TurnInferencePlan")
        attempts = tuple(self.attempts)
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

    def extend(self, plan: TurnInferencePlan) -> TurnUsageLedger:
        """Rebind to this turn's plan after later phases were added to it."""
        old = self.plan
        if not isinstance(plan, TurnInferencePlan) or (
                (plan.campaign_id, plan.turn_id, plan.policy, plan.present_actor_ids,
                 plan.player_controlled_ids)
                != (old.campaign_id, old.turn_id, old.policy, old.present_actor_ids,
                    old.player_controlled_ids)
                or plan.phases[:len(old.phases)] != old.phases
                or plan.calls[:len(old.calls)] != old.calls
                or plan.declined[:len(old.declined)] != old.declined):
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
            by_stage=MappingProxyType({
                stage: totals(lambda cid, stage=stage: cid.stage is stage)
                for stage in dict.fromkeys(call.stage for call in plan.calls)}),
            by_actor=MappingProxyType({
                actor: totals(lambda cid, actor=actor: cid.actor_id == actor)
                for actor in dict.fromkeys(call.actor_id for call in plan.calls)}),
            unmetered_call_ids=plan.call_ids - attempted,
            declined=plan.declined,
        )


def _merge(parts: Iterable[Mapping]) -> Mapping:
    merged: dict = {}
    for part in parts:
        for key, value in part.items():
            merged[key] = merged[key] + value if key in merged else value
    return MappingProxyType(merged)


@dataclass(frozen=True, slots=True)
class SessionUsage:
    """Turn summaries of one campaign session, each turn counted once."""

    campaign_id: str
    summaries: tuple[TurnUsageSummary, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.campaign_id, "campaign_id")
        summaries = tuple(self.summaries)
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

    def _sum(self, name: str) -> UsageTotals:
        return sum((getattr(s, name) for s in self.summaries), EMPTY_TOTALS)

    @property
    def total(self) -> UsageTotals:
        return self._sum("total")

    @property
    def baseline(self) -> UsageTotals:
        return self._sum("baseline")

    @property
    def optional(self) -> UsageTotals:
        return self._sum("optional")

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
    def complete(self) -> bool:
        return all(s.complete for s in self.summaries)

    @property
    def provider_reported_only(self) -> bool:
        return _provider_reported_only(self.complete, self.total.measurements)
