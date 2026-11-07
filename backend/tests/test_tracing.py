"""048's pure tracing: spans and their breakdown, call samples, and cost per model role with every missing state."""

from __future__ import annotations

from decimal import Decimal

import pytest

from grimoire.core.inference import CallAttempt, CallId, Measurement, TokenUsage
from grimoire.tracing import (
    OK, CallSample, Price, PriceTable, Span, Trace, TraceBuilder, TraceError, TraceLog, call_costs, merge_counts,
)


class Clock:
    """A clock that moves only when told, so every duration is exact."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


class Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def attempt(actor="mara", attempt_no=1, *, ordinal=1, stage="npc_generation", outcome="completed",
            usage=TokenUsage(1000, 200, 400), route="test/route",
            measurement=Measurement.PROVIDER_REPORTED) -> CallAttempt:
    if outcome == "not_sent":
        usage = TokenUsage(0, 0)
    return CallAttempt(CallId("camp", "turn-1", "main", stage, actor, ordinal), attempt_no, usage,
                       measurement, outcome, route)


def sample(each: CallAttempt, latency="synthetic", first=0.1, total=0.5) -> CallSample:
    if latency == "unreported":
        return CallSample(each, str(each.call_id.stage), each.call_id.actor_id, "unreported")
    return CallSample(each, str(each.call_id.stage), each.call_id.actor_id, latency, first, total)


def built(clock: Clock) -> Trace:
    builder = TraceBuilder(clock)
    with builder.span("generate"):
        clock.tick(1.0)
        with builder.span("read.turns", files=3) as reads:
            clock.tick(2.0)
            reads.count(decoded=1, files=2)
        with builder.span("brief.npc"):
            with builder.span("pack", items=4):
                clock.tick(0.5)
            clock.tick(0.25)
        with builder.span("brief.npc"):
            with builder.span("pack", items=2):
                clock.tick(0.5)
        clock.tick(0.75)
    return builder.trace


def test_breakdown_self_times_add_up_to_the_root_and_count_nothing_twice():
    trace = built(Clock())
    assert trace.operation == "generate" and trace.status == OK and trace.seconds == pytest.approx(5.0)
    times = {each.operation: each for each in trace.breakdown()}
    assert times["read.turns"].self_seconds == pytest.approx(2.0)
    assert times["brief.npc"].count == 2 and times["brief.npc"].seconds == pytest.approx(1.25)
    assert times["brief.npc"].self_seconds == pytest.approx(0.25)
    assert times["pack"].self_seconds == pytest.approx(1.0)
    assert times["generate"].self_seconds == pytest.approx(1.75)
    assert sum(each.self_seconds for each in trace.breakdown()) == pytest.approx(trace.seconds)
    assert [each.operation for each in trace.breakdown()][0] == "read.turns"  # slowest self time first


def test_counts_sum_over_spans_and_traces():
    trace = built(Clock())
    assert dict(trace.counts("read.turns")) == {"decoded": 1, "files": 5}
    assert dict(trace.counts("pack")) == {"items": 6}
    assert dict(trace.counts("absent")) == {}
    assert dict(merge_counts([trace, built(Clock())], "pack")) == {"items": 12}
    assert [trace.spans[i].operation for i in trace.children(0)] == ["read.turns", "brief.npc", "brief.npc"]


def test_a_refusal_is_the_span_status_and_any_other_error_its_type_name():
    clock = Clock()
    builder = TraceBuilder(clock)
    with pytest.raises(Refused):
        with builder.span("post"):
            with builder.span("write.turn"):
                raise Refused("stale_base")
    assert builder.trace.status == "stale_base" and builder.trace.spans[1].status == "stale_base"
    builder = TraceBuilder(clock)
    with pytest.raises(KeyError):
        with builder.span("open"):
            raise KeyError("x")
    assert builder.trace.status == "KeyError"
    builder = TraceBuilder(clock)
    with pytest.raises(Refused):
        with builder.span("open"):
            raise Refused("not a short name, with spaces and a very long tail " * 3)
    assert builder.trace.status == "Refused"  # a reason that is not a short name is never copied in
    builder = TraceBuilder(clock)
    with builder.span("open") as root:
        root.fail("declined")
    assert builder.trace.status == "declined"


def test_a_builder_refuses_misuse():
    clock = Clock()
    builder = TraceBuilder(clock)
    with builder.span("open"):
        pass
    with pytest.raises(TraceError):
        builder.span("again")  # finished
    builder = TraceBuilder(clock)
    with pytest.raises(TraceError):
        builder.span("has a space")
    with pytest.raises(TraceError):
        builder.span("x" * 65)
    with pytest.raises(TraceError):
        with builder.span("open", files=-1):
            pass
    with pytest.raises(TraceError):
        TraceBuilder(lambda: "now").span("open")
    with pytest.raises(TraceError):
        Trace((Span("child", 0, 0.0, 1.0),))
    with pytest.raises(TraceError):
        Trace((Span("root", None, 0.0, 1.0), Span("orphan", 5, 0.0, 1.0)))


def test_a_clock_that_goes_backwards_never_makes_a_negative_duration():
    times = iter([10.0, 9.0, 8.0])
    builder = TraceBuilder(lambda: next(times))
    with builder.span("open"):
        pass
    assert builder.trace.seconds == 0.0


def test_a_clock_may_give_any_real_number_but_not_a_flag_or_nan():
    class Seconds(float):
        pass

    times = iter([Seconds(1.0), Seconds(3.5)])
    builder = TraceBuilder(lambda: next(times))
    with builder.span("open"):
        pass
    assert builder.trace.seconds == 2.5
    for bad in (True, float("nan"), "1.0"):
        with pytest.raises(TraceError):
            TraceBuilder(lambda bad=bad: bad).span("open")


def test_a_call_sample_says_how_its_latency_is_known():
    each = attempt()
    assert sample(each).call == "main:npc_generation:mara:1"
    assert sample(attempt(actor=None, stage="director_generation")).call == "main:director_generation:scene:1"
    with pytest.raises(TraceError):
        CallSample(each, "npc_generation", "mara", "unreported", None, 0.5)  # unreported has no timings
    with pytest.raises(TraceError):
        CallSample(each, "npc_generation", "mara", "measured")  # a timed attempt has a total
    with pytest.raises(TraceError):
        CallSample(each, "npc_generation", "mara", "measured", 0.6, 0.5)  # first byte after the end
    with pytest.raises(TraceError):
        CallSample(each, "npc_generation", "mara", "guessed", 0.1, 0.5)
    with pytest.raises(TraceError):
        CallSample(each, "npc_generation", "mara", "measured", None, float("nan"))
    with pytest.raises(TraceError):
        CallSample("not an attempt", "npc_generation", "mara", "unreported")
    with pytest.raises(TraceError):
        CallSample(each, "npc_generation", "mara", "measured", 0.1, 0.5, lost=True)  # a lost call was not timed


def with_calls(*samples: CallSample) -> Trace:
    builder = TraceBuilder(Clock())
    with builder.span("generate"):
        for each in samples:
            builder.sample(each)
    return builder.trace


PRICES = PriceTable({"test/route": Price(Decimal("3"), Decimal("15"), Decimal("0.3"))})


def test_a_priced_role_charges_cached_and_uncached_prompt_tokens_apart():
    costs = call_costs(with_calls(sample(attempt())), PRICES)
    role = costs.role("npc_generation", "mara")
    # 600 uncached at 3, 400 cached at 0.3, 200 completion at 15, per million.
    assert role.cost == (600 * Decimal(3) + 400 * Decimal("0.3") + 200 * Decimal(15)) / 1_000_000
    assert role.cost_state == "priced" and role.latency == "synthetic"
    assert role.calls == 1 and role.attempts == 1 and role.retries == 0 and role.failed == 0 and role.lost == 0
    assert role.provider_seconds == pytest.approx(0.5) and role.first_byte_seconds == pytest.approx(0.1)
    assert costs.total.cost == role.cost


def test_a_cost_from_counts_the_provider_did_not_report_is_estimated_never_priced():
    for measurement in (Measurement.SYNTHETIC, Measurement.ESTIMATED):
        made_up = sample(attempt(measurement=measurement))
        role = call_costs(with_calls(made_up), PRICES).total
        assert role.cost is not None and role.cost_state == "estimated"
        mixed = call_costs(with_calls(sample(attempt(attempt_no=2)), made_up), PRICES).total
        assert mixed.cost_state == "estimated"  # one made-up count makes the whole sum no evidence of a bill
    unsent = sample(attempt("seraphine", outcome="not_sent", measurement=Measurement.SYNTHETIC), "unreported")
    assert call_costs(with_calls(sample(attempt()), unsent), PRICES).total.cost_state == "priced"


def test_a_lost_call_is_counted_and_makes_the_cost_a_lower_bound():
    lost = CallSample(attempt(outcome="failed", usage=TokenUsage(None, None), measurement=Measurement.ESTIMATED),
                      "npc_generation", "mara", "unreported", lost=True)
    costs = call_costs(with_calls(sample(attempt("seraphine")), lost), PRICES)
    mara = costs.role("npc_generation", "mara")
    assert mara.lost == 1 and mara.failed == 0 and mara.calls == 1  # sent: whether it completed is unknown
    assert mara.cost == 0 and mara.cost_state == "partial" and mara.latency == "unreported"
    assert mara.usage.unreported.get("input_tokens") == 1
    assert costs.total.cost_state == "partial" and costs.total.latency == "partial"


def test_provider_time_is_unknown_rather_than_zero_when_it_was_not_timed():
    lost = call_costs(with_calls(sample(attempt(), "unreported"))).total
    assert lost.provider_seconds is None and lost.first_byte_seconds is None
    one_first_byte = call_costs(with_calls(sample(attempt(), "measured", 0.1, 0.5),
                                           sample(attempt(attempt_no=2), "measured", None, 0.7))).total
    assert one_first_byte.provider_seconds == pytest.approx(1.2)
    assert one_first_byte.first_byte_seconds is None  # a sum missing one of its parts is no sum


def test_without_a_price_every_role_is_unpriced_never_zero():
    costs = call_costs(with_calls(sample(attempt())))
    assert costs.total.cost is None and costs.total.cost_state == "unpriced"
    other = PriceTable({"another/route": Price(Decimal(1), Decimal(1))})
    assert call_costs(with_calls(sample(attempt())), other).total.cost_state == "unpriced"
    assert call_costs(with_calls(sample(attempt(route=None))), PRICES).total.cost_state == "unpriced"


def test_an_unreported_count_makes_the_cost_a_lower_bound():
    unreported_output = attempt(usage=TokenUsage(1000, None, 0))
    role = call_costs(with_calls(sample(unreported_output)), PRICES).total
    assert role.cost_state == "partial" and role.cost == 1000 * Decimal(3) / 1_000_000
    assert role.usage.unreported.get("output_tokens") == 1
    # How much was cached is unknown: the whole prompt is charged at the cheaper rate, so it stays a bound.
    unknown_cache = attempt(usage=TokenUsage(1000, 200, None))
    role = call_costs(with_calls(sample(unknown_cache)), PRICES).total
    assert role.cost_state == "partial"
    assert role.cost == (1000 * Decimal("0.3") + 200 * Decimal(15)) / 1_000_000
    flat = PriceTable({"test/route": Price(Decimal(2), Decimal(4))})
    assert call_costs(with_calls(sample(unknown_cache)), flat).total.cost_state == "priced"


def test_retries_failures_and_unsent_attempts_are_counted_as_what_they_are():
    first = sample(attempt(outcome="failed", usage=TokenUsage(1000, 0, 0)))
    retry = sample(attempt(attempt_no=2))
    unsent = sample(attempt("seraphine", outcome="not_sent"), "unreported")
    costs = call_costs(with_calls(first, retry, unsent), PRICES)
    mara = costs.role("npc_generation", "mara")
    assert mara.calls == 1 and mara.attempts == 2 and mara.retries == 1 and mara.failed == 1
    seraphine = costs.role("npc_generation", "seraphine")
    assert seraphine.cost == 0 and seraphine.cost_state == "priced" and seraphine.latency == "unreported"
    assert seraphine.provider_seconds is None
    assert seraphine.failed == 0  # not sent is not a failure: it used nothing
    assert [role.actor_id for role in costs.roles] == ["mara", "seraphine"]
    assert costs.total.calls == 2 and costs.total.attempts == 3
    assert costs.total.latency == "synthetic"  # the unsent attempt has no say in how latency is known


def test_latency_states_never_pass_a_mix_off_as_measured():
    measured = sample(attempt(), "measured")
    synthetic = sample(attempt(attempt_no=2), "synthetic")
    lost = sample(attempt(attempt_no=3), "unreported")
    assert call_costs(with_calls(measured)).total.latency == "measured"
    assert call_costs(with_calls(measured, synthetic)).total.latency == "partial"
    assert call_costs(with_calls(measured, lost)).total.latency == "partial"
    assert call_costs(with_calls(lost)).total.latency == "unreported"
    assert call_costs(with_calls()).total.latency == "unreported"
    assert call_costs(with_calls()).total.cost == 0  # nothing sent costs nothing


def test_prices_are_checked():
    with pytest.raises(TraceError):
        Price(3, Decimal(1))  # a float or int is not a price: only an exact Decimal
    with pytest.raises(TraceError):
        Price(Decimal(-1), Decimal(1))
    with pytest.raises(TraceError):
        Price(Decimal("NaN"), Decimal(1))
    with pytest.raises(TraceError):
        PriceTable({"": Price(Decimal(1), Decimal(1))})
    with pytest.raises(TraceError):
        call_costs("not a trace")
    with pytest.raises(TraceError):
        call_costs(with_calls(), {"test/route": Price(Decimal(1), Decimal(1))})


def test_a_trace_log_keeps_the_newest():
    log = TraceLog(keep=2)
    for operation in ("open", "post", "generate"):
        builder = TraceBuilder(Clock())
        with builder.span(operation):
            pass
        log.record(builder.trace)
    assert [each.operation for each in log.traces] == ["post", "generate"]
    assert [each.operation for each in log.of("post")] == ["post"]
    with pytest.raises(TraceError):
        log.record("not a trace")
    with pytest.raises(TraceError):
        TraceLog(keep=0)
