"""Exhaustive provider fault exploration, and proof that each invariant can fail."""

import pytest

from grimoire.providers import CallRefused, ProviderEndpoint
from grimoire.providers.adapter import ProviderAdapter
from grimoire.testing.providers import (
    EXPLORER_KEY, PROVIDER_INVARIANTS, ScriptedTransport, explore_provider_faults,
)

INDEXED = ("read_timeout", "read_reset", "truncate", "cancel", "garbage", "interrupt")
UNINDEXED = ("connect_timeout", "connect_refused", "write_fail", "status_401", "status_429",
             "status_503", "finish_length", "no_usage")
FIRSTS = ("connect_timeout", "read_timeout@0", "truncate@0", "status_503", "status_429",
          "garbage@0")
CONSUMERS = ("consumer", "no_consumer")
LINES = 6  # the reference reply: three content chunks, finish, usage, [DONE]


def expected_points():
    singles = [f"{kind}@{index}" for kind in INDEXED for index in range(LINES + 1)]
    singles += list(UNINDEXED)
    points = {f"d1:{fault}:{mode}" for fault in singles for mode in CONSUMERS}
    points |= {f"d2:{first}>{second}:{mode}" for first in FIRSTS
               for second in [*singles, "deny"] for mode in CONSUMERS}
    points |= {"booking:precise", "booking:fallback"}
    points |= {f"retry_after:{value}" for value in ("2", "9999", "-1", "soon")}
    return points


def test_adapter_survives_every_fault_point():
    report = explore_provider_faults(seed=7)
    report.assert_ok()
    assert set(report.points) == expected_points()
    assert report.runs == len(expected_points()) == 718


def test_explorer_is_deterministic():
    one = explore_provider_faults(seed=3)
    two = explore_provider_faults(seed=3)
    assert one.traces == two.traces and one.seed == 3


# -- each invariant can fail -------------------------------------------------


class RetriesAfterDelivery(ProviderAdapter):
    def _retryable(self, kind, delivered):
        return super()._retryable(kind, False)


class AcceptsAnyFinish(ProviderAdapter):
    def _accept(self, state):
        return state.finish_reason is not None


class InventsZeroUsage(ProviderAdapter):
    def _book(self, meter, call, number, label, attempt, sent, *rest):
        if sent and attempt.state.usage_block is None:
            attempt.state.usage_block = {"prompt_tokens": 0, "completion_tokens": 0}
        return super()._book(meter, call, number, label, attempt, sent, *rest)


class ResendsWhenRefused(ProviderAdapter):
    def execute(self, meter, call, request, routes, handle, **options):
        try:
            return super().execute(meter, call, request, routes, handle, **options)
        except CallRefused:
            self._transport.connect(self._registry.get(routes[0].provider_id).completions_url,
                                    self._registry.get(routes[0].provider_id).timeouts,
                                    options.get("cancel"))
            raise


class LeaksKey(ProviderAdapter):
    def _run(self, *args):
        result = super()._run(*args)
        if result.error is not None:
            result.error.error_code = EXPLORER_KEY
        return result


class DoubleConnects(ScriptedTransport):
    def connect(self, url, timeouts, cancel):
        self.connects.append(url)
        return super().connect(url, timeouts, cancel)


class UnbookedSends(ScriptedTransport):
    def connect(self, url, timeouts, cancel):
        connection = super().connect(url, timeouts, cancel)
        original = connection.send

        def send(body, headers, key):
            self.sends.append(self.sends[-1]) if self.sends else None
            return original(body, headers, key)
        connection.send = send
        return connection


_runs = [0]


class Drifting(ScriptedTransport):
    def __init__(self, steps, **kw):
        _runs[0] += 1
        super().__init__(steps, peer_loopback=_runs[0] % 2 == 0, **kw)
        if _runs[0] % 2:
            self.connects.append("drift")


MUTANTS = {
    "no unaccepted reply": dict(adapter_factory=AcceptsAnyFinish),
    "every send booked": dict(transport_factory=UnbookedSends),
    "single acceptance": dict(adapter_factory=ResendsWhenRefused),
    "bounded attempts": dict(transport_factory=DoubleConnects),
    "no retry after delivery": dict(adapter_factory=RetriesAfterDelivery),
    "unknown is not zero": dict(adapter_factory=InventsZeroUsage),
    "secret never leaks": dict(adapter_factory=LeaksKey),
    "deterministic": dict(transport_factory=Drifting),
    "no resend after poisoning": dict(adapter_factory=ResendsWhenRefused),
    "bounded exploration": dict(max_runs=1),
}


def test_every_invariant_has_a_mutant():
    assert set(MUTANTS) == set(PROVIDER_INVARIANTS)


@pytest.mark.parametrize("invariant", sorted(MUTANTS))
def test_explorer_detects_each_invariant(invariant):
    report = explore_provider_faults(**MUTANTS[invariant])
    named = {violation.invariant for violation in report.violations}
    assert invariant in named, sorted(named)
    with pytest.raises(AssertionError, match=invariant):
        report.assert_ok()
