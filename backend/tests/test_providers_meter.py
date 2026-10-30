"""TurnMeter: the one booking path for a turn's provider attempts."""

import pytest

from grimoire.core.inference import (
    CallAttempt, InferencePolicy, Measurement, OptionalRequest, PlannedCall, TokenUsage,
    TurnUsageLedger, UnplannedCallError, plan_turn,
)
from grimoire.core.inference.plan import CallId
from grimoire.providers import CallRefused
from grimoire.providers.meter import AttemptReport, TurnMeter

DIGEST = "d" * 64


def _meter(attempts=2, **hooks):
    plan = plan_turn("camp", "t1", present_actor_ids=["a", "b", "c"], speaking_npc_ids=["a", "b"],
                     optional_requests=[OptionalRequest("critique", None, "review")],
                     policy=InferencePolicy(max_attempts_per_call=attempts))
    return TurnMeter(TurnUsageLedger(plan), **hooks), plan


def _book(meter, call, n, outcome="failed", usage=TokenUsage(None, None), sent=True):
    def build():
        attempt = CallAttempt(call.call_id, n, usage, Measurement.SYNTHETIC, outcome, "p@h/m")
        return attempt, AttemptReport(call.call_id, n, "p@h/m", outcome, usage,
                                      Measurement.SYNTHETIC)
    return meter._book(call.call_id, n, "p@h/m", sent=sent, measurement=Measurement.SYNTHETIC,
                       error_kind=None, build=build)


def _run(meter, call, outcome="completed", status="accepted", n=None):
    remaining = meter._begin(call, DIGEST, False, 1)
    try:
        number = n or meter.next_attempt(call.call_id)
        _book(meter, call, number, outcome, TokenUsage(10, 2))
    finally:
        meter._end(call.call_id, status)
    return remaining


def test_unplanned_declined_other_turn_and_forged_purpose_are_refused():
    meter, plan = _meter()
    call = plan.calls[0]
    other = plan_turn("camp", "t2", present_actor_ids=["a"], speaking_npc_ids=["a"]).calls[0]
    forged = PlannedCall(call.call_id, "secretly plan everything")
    fabricated = PlannedCall(CallId("camp", "t1", "main", "npc_generation", "c", 1), "x")
    declined = PlannedCall(CallId("camp", "t1", "main", "critique", None, 1), "review")
    for bad in (other, forged, fabricated, declined, "not a call"):
        with pytest.raises(UnplannedCallError):
            meter._begin(bad, DIGEST, False, 1)
    assert meter.ledger.attempts == ()
    assert meter._begin(call, DIGEST, False, 1) == 2  # the lock was released by each refusal
    meter._end(call.call_id, "refused")


def test_completed_exhausted_poisoned_and_busy_calls_are_refused():
    meter, plan = _meter(attempts=1)
    first, second = plan.calls
    _run(meter, first)
    with pytest.raises(CallRefused, match="completed"):
        meter._begin(first, DIGEST, False, 1)
    _run(meter, second, outcome="failed", status="failed")
    with pytest.raises(CallRefused, match="exhausted"):
        meter._begin(second, DIGEST, False, 1)

    meter, plan = _meter()
    call = plan.calls[0]
    meter._begin(call, DIGEST, False, 1)
    with pytest.raises(CallRefused, match="busy"):
        meter._begin(plan.calls[1], DIGEST, False, 1)
    meter._end(call.call_id, "refused")


def test_request_is_fixed_only_after_a_booked_attempt():
    meter, plan = _meter()
    call = plan.calls[0]
    meter._begin(call, DIGEST, False, 1)
    meter._end(call.call_id, "refused")  # nothing booked: the prompt may still change
    meter._begin(call, "e" * 64, False, 1)
    _book(meter, call, 1)
    meter._end(call.call_id, "failed")
    with pytest.raises(CallRefused, match="request_changed"):
        meter._begin(call, DIGEST, False, 1)
    assert meter._begin(call, "e" * 64, False, 1) == 1
    meter._end(call.call_id, "failed")


def test_delivered_call_needs_explicit_redo():
    meter, plan = _meter(attempts=3)
    call = plan.calls[0]
    meter._begin(call, DIGEST, False, 1)
    meter._delivered(call.call_id)
    _book(meter, call, 1)
    meter._end(call.call_id, "failed")
    with pytest.raises(CallRefused, match="delivered"):
        meter._begin(call, DIGEST, False, 1)
    assert meter._begin(call, DIGEST, True, 1) == 2
    assert meter.delivered(call.call_id) is False  # the redo starts undelivered
    meter._end(call.call_id, "failed")


def test_routes_beyond_remaining_attempts_raise_valueerror():
    meter, plan = _meter(attempts=2)
    with pytest.raises(ValueError):
        meter._begin(plan.calls[0], DIGEST, False, 3)
    assert meter._begin(plan.calls[0], DIGEST, False, 2) == 2
    meter._end(plan.calls[0].call_id, "refused")


def test_precise_build_failure_books_fallback_not_poisoned():
    meter, plan = _meter(_fail_booking=lambda stage: stage == "precise")
    call = plan.calls[0]
    meter._begin(call, DIGEST, False, 1)
    assert _book(meter, call, 1, "completed", TokenUsage(10, 2)) == "fallback"
    meter._end(call.call_id, "failed")
    (attempt,) = meter.ledger.attempts
    assert (attempt.outcome, attempt.usage) == ("failed", TokenUsage(None, None))
    assert meter.status(call.call_id) == "failed"
    (report,) = meter.reports(call.call_id)
    assert report.usage_note == "booking fell back to unknown usage"

    meter, plan = _meter(_fail_booking=lambda stage: stage == "precise")
    meter._begin(plan.calls[0], DIGEST, False, 1)
    assert _book(meter, plan.calls[0], 1, sent=False) == "fallback"
    meter._end(plan.calls[0].call_id, "failed")
    (attempt,) = meter.ledger.attempts
    assert (attempt.outcome, attempt.usage, attempt.measurement) == (
        "not_sent", TokenUsage(0, 0), Measurement.ESTIMATED)


def test_fallback_failure_poisons():
    meter, plan = _meter(_fail_booking=lambda stage: True)
    call = plan.calls[0]
    meter._begin(call, DIGEST, False, 1)
    assert _book(meter, call, 1, "completed") == "poisoned"
    meter._end(call.call_id, "failed")
    assert meter.status(call.call_id) == "poisoned"
    with pytest.raises(CallRefused, match="poisoned"):
        meter._begin(call, DIGEST, False, 1)


def test_interrupt_during_booking_leaves_the_call_poisoned():
    meter, plan = _meter()
    call = plan.calls[0]
    meter._begin(call, DIGEST, False, 1)

    def build():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        meter._book(call.call_id, 1, "p@h/m", sent=True, measurement=Measurement.SYNTHETIC,
                    error_kind=None, build=build)
    meter._end(call.call_id, "failed")
    assert meter.status(call.call_id) == "poisoned"
    with pytest.raises(CallRefused, match="poisoned"):
        meter._begin(call, DIGEST, False, 1)


def test_all_accepted_and_refused_status():
    meter, plan = _meter()
    first, second = plan.calls
    assert meter.all_accepted is False
    _run(meter, first)
    meter._begin(second, DIGEST, False, 1)
    meter._end(second.call_id, "refused")
    assert meter.status(second.call_id) == "refused" and meter.all_accepted is False
    summary = meter.summary()
    assert second.call_id in summary.unmetered_call_ids  # a refusal is not billed
    _run(meter, second)
    assert meter.all_accepted is True


def test_add_phase_keeps_attempts_and_reports():
    meter, plan = _meter()
    _run(meter, plan.calls[0])
    meter.add_phase("after", speaking_npc_ids=["c"])
    assert len(meter.ledger.plan.calls) == 3 and len(meter.ledger.attempts) == 1
    assert len(meter.reports(plan.calls[0].call_id)) == 1
    assert meter.all_accepted is False
    assert "camp" in repr(meter) and DIGEST not in repr(meter)


def test_a_refused_redo_keeps_the_delivered_gate():
    meter, plan = _meter(attempts=3)
    call = plan.calls[0]
    meter._begin(call, DIGEST, False, 1)
    meter._delivered(call.call_id)
    _book(meter, call, 1)
    meter._end(call.call_id, "failed")
    with pytest.raises(ValueError):
        meter._begin(call, DIGEST, True, 3)  # too many routes: refused before any change
    with pytest.raises(CallRefused, match="delivered"):
        meter._begin(call, DIGEST, False, 1)


def test_phases_cannot_be_added_while_a_call_runs():
    meter, plan = _meter()
    meter._begin(plan.calls[0], DIGEST, False, 1)
    with pytest.raises(CallRefused, match="busy"):
        meter.add_phase("later", speaking_npc_ids=["c"])
    meter._end(plan.calls[0].call_id, "refused")
    meter.add_phase("later", speaking_npc_ids=["c"])
