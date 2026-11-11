"""What a trace's model calls cost, per model role: tokens, retries, provider time and (when priced) money.

A *role* is one inference stage for one actor (``npc_generation`` for Mara, ``director_generation`` for the
scene). Every state a number can be in is said, never papered over:

- **Tokens** are ``UsageTotals`` (004): a count the provider did not report is counted as unreported, never
  as zero, and a sum is a lower bound while any count of its field is unreported.
- **Provider time** is ``measured`` (every sent attempt timed on the real clock against a provider-reported
  attempt), ``synthetic`` (a test or scripted clock), ``partial`` (some attempts timed, some not, or a mix of
  measured and synthetic) or ``unreported`` (none timed). Only ``measured`` is evidence of real latency.
- **Provider seconds** are ``None`` when no attempt was timed; **first-byte seconds** are ``None`` unless every
  timed attempt reported its first byte, so a sum is never quietly missing some of its parts.
- **Money** needs a ``PriceTable`` the caller trusts (no prices ship: 013). Without a price for every route a
  role used, its cost is ``unpriced`` and ``None``; priced from any count the provider did not report (a
  test's or a scripted transport's, or an estimate) it is ``estimated``, never evidence of a bill; with
  prices but an unreported count, or a lost call (sent, booking failed), it is ``partial`` and a lower bound;
  otherwise ``priced``. A role that sent nothing costs nothing (``priced``, zero).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from grimoire.core.inference import CallAttempt, Measurement, UsageTotals

from .trace import CallSample, Trace, TraceError

CostState = Literal["priced", "estimated", "partial", "unpriced"]
LatencyState = Literal["measured", "synthetic", "partial", "unreported"]
_MILLION = Decimal(1_000_000)


def _price(value: object, what: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise TraceError(f"{what} is a non-negative Decimal per million tokens")
    return value


@dataclass(frozen=True, slots=True)
class Price:
    """One route's price per million tokens. A cached prompt token is charged at ``cached`` (the prompt price
    when ``None``); reasoning is part of the completion and never charged twice."""

    prompt: Decimal
    completion: Decimal
    cached: Decimal | None = None

    def __post_init__(self) -> None:
        _price(self.prompt, "prompt")
        _price(self.completion, "completion")
        if self.cached is not None:
            _price(self.cached, "cached")


@dataclass(frozen=True)
class PriceTable:
    """Prices by route label (``CallAttempt.route``, the provider and model a call used)."""

    prices: Mapping[str, Price]

    def __post_init__(self) -> None:
        checked = {}
        for route, price in dict(self.prices).items():
            if type(route) is not str or not route or not isinstance(price, Price):
                raise TraceError("a price table maps route labels to Price values")
            checked[route] = price
        object.__setattr__(self, "prices", MappingProxyType(checked))


@dataclass(frozen=True)
class RoleCost:
    """One role's calls in a trace. ``retries`` are attempts beyond each call's first; ``failed`` are attempts
    that did not complete (failed or cancelled), not counting ``not_sent`` ones, which used nothing, nor
    ``lost`` ones, sent calls whose booking failed, so whether they completed is unknown."""

    stage: str
    actor_id: str | None
    calls: int
    attempts: int
    retries: int
    failed: int
    lost: int
    usage: UsageTotals
    provider_seconds: float | None
    first_byte_seconds: float | None
    latency: LatencyState
    cost: Decimal | None
    cost_state: CostState


@dataclass(frozen=True)
class CallCosts:
    """Every role of a trace (in call order of first appearance) and the trace's total over all of them."""

    roles: tuple[RoleCost, ...]
    total: RoleCost

    def role(self, stage: str, actor_id: str | None) -> RoleCost | None:
        return next((each for each in self.roles if each.stage == stage and each.actor_id == actor_id), None)


def _attempt_cost(sample: CallSample, prices: PriceTable | None) -> tuple[Decimal | None, bool]:
    """``(cost, exact)``: ``None`` when unpriced; a lower bound (``exact`` false) when a needed count is missing."""
    attempt = sample.attempt
    if attempt.outcome == "not_sent":
        return Decimal(0), True
    if sample.lost:  # sent, and nothing is known of what it used: at least nothing, never exactly nothing
        return Decimal(0), False
    price = None if prices is None or attempt.route is None else prices.prices.get(attempt.route)
    if price is None:
        return None, False
    usage = attempt.usage
    exact = usage.input_tokens is not None and usage.output_tokens is not None
    cache_price = price.prompt if price.cached is None else price.cached
    if usage.cache_read_tokens is None:
        # How much of the prompt was cached is unknown: charge all of it at the cheaper rate, so the cost stays
        # a lower bound, and say so unless the two rates are the same.
        rate = min(price.prompt, cache_price)
        exact = exact and rate == price.prompt == cache_price
        prompt = (usage.input_tokens or 0) * rate
    else:
        cached = usage.cache_read_tokens
        prompt = max(0, (usage.input_tokens or 0) - cached) * price.prompt + cached * cache_price
    return (prompt + (usage.output_tokens or 0) * price.completion) / _MILLION, exact


def _latency(samples: Iterable[CallSample]) -> LatencyState:
    sent = [sample for sample in samples if sample.attempt.outcome != "not_sent"]
    kinds = {sample.latency for sample in sent}
    if not sent or kinds == {"unreported"}:
        return "unreported"
    if kinds == {"measured"}:
        return "measured"
    if kinds == {"synthetic"}:
        return "synthetic"
    return "partial"


def _role(stage: str, actor_id: str | None, samples: list[CallSample], prices: PriceTable | None) -> RoleCost:
    calls = {sample.attempt.call_id for sample in samples}
    costs = [_attempt_cost(sample, prices) for sample in samples]
    if any(cost is None for cost, _ in costs):
        cost, state = None, "unpriced"
    else:
        cost = sum((each for each, _ in costs), Decimal(0))
        if any(sample.attempt.measurement is not Measurement.PROVIDER_REPORTED for sample in samples
               if sample.attempt.outcome != "not_sent" and not sample.lost):
            state = "estimated"  # not evidence of a bill, whether or not every count is there
        elif not all(exact for _, exact in costs):
            state = "partial"
        else:
            state = "priced"
    timed = [sample for sample in samples if sample.total_s is not None]
    provider = sum((sample.total_s for sample in timed), 0.0) if timed else None
    first_byte = (sum((sample.first_byte_s for sample in timed), 0.0)
                  if timed and all(sample.first_byte_s is not None for sample in timed) else None)
    lost = sum(1 for sample in samples if sample.lost)
    return RoleCost(
        stage, actor_id, len(calls), len(samples), len(samples) - len(calls),
        sum(1 for sample in samples if not sample.lost and sample.attempt.outcome in ("failed", "cancelled")),
        lost, UsageTotals.of(sample.attempt for sample in samples), provider, first_byte,
        _latency(samples), cost, state)  # type: ignore[arg-type]


def call_costs(trace: Trace, prices: PriceTable | None = None) -> CallCosts:
    """Each model role's tokens, retries, provider time and cost in ``trace``, and their total."""
    if not isinstance(trace, Trace):
        raise TraceError("call_costs reads a Trace")
    if prices is not None and not isinstance(prices, PriceTable):
        raise TraceError("prices is a PriceTable")
    groups: dict[tuple[str, str | None], list[CallSample]] = {}
    for sample in trace.calls:
        groups.setdefault((sample.stage, sample.actor_id), []).append(sample)
    roles = tuple(_role(stage, actor, samples, prices) for (stage, actor), samples in groups.items())
    return CallCosts(roles, _role("all", None, list(trace.calls), prices))


def usage_cost(attempts: Iterable[CallAttempt], prices: PriceTable | None) -> tuple[Decimal | None, CostState, bool]:
    """``(cost, state, exact)`` of booked attempts taken from a ledger, not a trace (112's usage screens): the
    same sums and states as a role's. ``exact`` is false while the cost is a lower bound (a needed count went
    unreported), whatever the state, so an ``estimated`` cost can say so too."""
    if prices is not None and not isinstance(prices, PriceTable):
        raise TraceError("prices is a PriceTable")
    samples = [CallSample(attempt, "all", None, "unreported") for attempt in attempts]
    role = _role("all", None, samples, prices)
    exact = role.cost is not None and all(_attempt_cost(sample, prices)[1] for sample in samples)
    return role.cost, role.cost_state, exact
