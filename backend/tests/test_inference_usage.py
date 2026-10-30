"""Usage is booked only against planned calls; missing data never reads as zero."""

from dataclasses import fields, replace

import pytest

from grimoire.core.inference import (
    TOKEN_FIELDS, CallAttempt, CallId, InferencePolicy, InferenceStage, Measurement,
    OptionalRequest, SessionUsage, TokenUsage, TurnUsageLedger, UnplannedCallError,
    UsageTotals, plan_turn,
)

PRESENT = ("npc-a", "npc-b", "npc-c", "npc-d")
SYNTHETIC = Measurement.SYNTHETIC
PROVIDER = Measurement.PROVIDER_REPORTED


def two_speakers(turn_id="turn-1", **overrides):
    args = dict(present_actor_ids=PRESENT, speaking_npc_ids=("npc-b", "npc-d"))
    args.update(overrides)
    return plan_turn("campaign-1", turn_id, **args)


def attempt(call, input_tokens, output_tokens, *, n=1, measurement=SYNTHETIC,
            outcome="completed", route=None, **extra):
    return CallAttempt(call.call_id, n, TokenUsage(input_tokens, output_tokens, **extra),
                       measurement, outcome, route)


def booked(plan, *attempts):
    ledger = TurnUsageLedger(plan)
    for each in attempts:
        ledger = ledger.record(each)
    return ledger


# --- Token usage and attempts -------------------------------------------------

def test_unreported_counts_stay_none():
    usage = TokenUsage(None, 7)
    assert usage.input_tokens is None
    assert usage.cache_read_tokens is None and usage.reasoning_tokens is None


@pytest.mark.parametrize("value", [-1, True, 1.0, "12"])
def test_token_counts_reject_negative_bool_and_float(value):
    with pytest.raises(ValueError):
        TokenUsage(value, 0)
    with pytest.raises(ValueError):
        TokenUsage(0, 0, cache_write_tokens=value)


def test_cache_tokens_are_slices_of_input():
    assert TokenUsage(11, 5, cache_read_tokens=6, cache_write_tokens=5).input_tokens == 11
    with pytest.raises(ValueError):
        TokenUsage(10, 5, cache_read_tokens=6, cache_write_tokens=5)
    assert TokenUsage(None, 5, cache_read_tokens=6).cache_read_tokens == 6


def test_reasoning_is_a_slice_of_output():
    assert TokenUsage(10, 4, reasoning_tokens=4).reasoning_tokens == 4
    with pytest.raises(ValueError):
        TokenUsage(10, 3, reasoning_tokens=4)


def test_not_sent_attempt_requires_zero_usage():
    call = two_speakers().calls[0]
    assert attempt(call, 0, 0, outcome="not_sent").outcome == "not_sent"
    for bad in ((1, 0, {}), (None, 0, {}), (0, 0, {"cache_read_tokens": 1})):
        with pytest.raises(ValueError):
            attempt(call, bad[0], bad[1], outcome="not_sent", **bad[2])


def test_attempt_validation():
    call = two_speakers().calls[0]
    with pytest.raises(ValueError):
        attempt(call, 1, 1, n=0)
    with pytest.raises(ValueError):
        attempt(call, 1, 1, n=True)
    with pytest.raises(ValueError):
        attempt(call, 1, 1, route=" ")
    with pytest.raises(ValueError):
        attempt(call, 1, 1, outcome="maybe")
    with pytest.raises(ValueError):
        attempt(call, 1, 1, measurement="guessed")
    with pytest.raises(ValueError):
        CallAttempt("not-a-call-id", 1, TokenUsage(1, 1), SYNTHETIC, "completed")
    assert attempt(call, 1, 1, measurement="estimated").measurement is Measurement.ESTIMATED


def test_usage_records_hold_no_free_text():
    assert [f.name for f in fields(TokenUsage)] == [
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens"]
    assert [f.name for f in fields(CallAttempt)] == [
        "call_id", "attempt", "usage", "measurement", "outcome", "route"]


# --- Turn ledger --------------------------------------------------------------

def test_proving_scenario_usage_is_baseline_only():
    plan = two_speakers()
    summary = booked(plan, attempt(plan.calls[0], 100, 20), attempt(plan.calls[1], 120, 30)).summary()
    assert summary.baseline.sums["input_tokens"] == 220
    assert summary.baseline.sums["output_tokens"] == 50
    assert summary.baseline.attempts == 2
    assert summary.optional.attempts == 0
    assert summary.total == summary.baseline
    assert summary.complete
    assert not summary.provider_reported_only
    assert summary.measurements == {SYNTHETIC}


def test_unplanned_declined_and_foreign_calls_cannot_be_booked():
    plan = two_speakers(optional_requests=[OptionalRequest("critique", None, "check")])
    ledger = TurnUsageLedger(plan)
    declined = plan.declined[0].request
    fabricated = CallId("campaign-1", "turn-1", "main", InferenceStage.NPC_GENERATION, "npc-a", 1)
    from_declined = CallId("campaign-1", "turn-1", "main", declined.stage, declined.actor_id, 1)
    other_turn = two_speakers("turn-2").calls[0].call_id
    for call_id in (fabricated, from_declined, other_turn):
        with pytest.raises(UnplannedCallError):
            ledger.record(CallAttempt(call_id, 1, TokenUsage(1, 1), SYNTHETIC, "completed"))


def test_retries_are_bounded_gapless_and_summed():
    plan = two_speakers(policy=InferencePolicy(max_attempts_per_call=2))
    first = plan.calls[0]
    ledger = booked(plan, attempt(first, 50, 0, outcome="failed", route="r1"),
                    attempt(first, 60, 10, n=2, route="r2"))
    totals = ledger.summary().by_actor["npc-b"]
    assert (totals.attempts, totals.sums["input_tokens"], totals.sums["output_tokens"]) == (2, 110, 10)
    assert [a.route for a in ledger.attempts] == ["r1", "r2"]
    with pytest.raises(ValueError):  # over the attempt limit
        ledger.record(attempt(first, 1, 1, n=3))
    with pytest.raises(ValueError):  # a gap
        TurnUsageLedger(plan).record(attempt(first, 1, 1, n=2))
    with pytest.raises(ValueError):  # a duplicate
        booked(plan, attempt(first, 1, 1, outcome="failed")).record(
            attempt(first, 1, 1, outcome="failed"))
    roomy = two_speakers(policy=InferencePolicy(max_attempts_per_call=3))
    with pytest.raises(ValueError):  # retrying a completed call
        booked(roomy, attempt(roomy.calls[0], 1, 1)).record(attempt(roomy.calls[0], 1, 1, n=2))
    cancelled_then_unsent = booked(roomy, attempt(roomy.calls[0], 5, None, outcome="cancelled"),
                                   attempt(roomy.calls[0], 0, 0, n=2, outcome="not_sent"))
    assert len(cancelled_then_unsent.attempts) == 2


def test_missing_usage_is_not_zero():
    plan = two_speakers()
    summary = booked(plan, attempt(plan.calls[0], 100, 20)).summary()
    assert summary.unmetered_call_ids == {plan.calls[1].call_id}
    assert not summary.complete
    unreported = booked(plan, attempt(plan.calls[0], 100, None),
                        attempt(plan.calls[1], 90, 10)).summary()
    assert unreported.total.unreported["output_tokens"] == 1
    assert unreported.total.sums["output_tokens"] == 10
    assert unreported.total.unreported["cache_read_tokens"] == 2
    assert not unreported.complete


def test_provider_reported_only_requires_complete_provider_data():
    plan = two_speakers()
    provider = booked(plan, attempt(plan.calls[0], 1, 1, measurement=PROVIDER),
                      attempt(plan.calls[1], 1, 1, measurement=PROVIDER)).summary()
    assert provider.provider_reported_only
    mixed = booked(plan, attempt(plan.calls[0], 1, 1, measurement=PROVIDER),
                   attempt(plan.calls[1], 1, 1, measurement="estimated")).summary()
    assert not mixed.provider_reported_only
    partial = booked(plan, attempt(plan.calls[0], 1, 1, measurement=PROVIDER)).summary()
    assert not partial.provider_reported_only
    empty = TurnUsageLedger(two_speakers(speaking_npc_ids=())).summary()
    assert empty.complete and not empty.provider_reported_only


def test_by_stage_and_by_actor_breakdowns():
    plan = two_speakers(optional_requests=[OptionalRequest("critique", None, "check"),
                                           OptionalRequest("planning", "npc-a", "plan")],
                        policy=InferencePolicy({"critique": 1, "planning": 1}))
    gen_b, gen_d, critique, planning = plan.calls
    summary = booked(plan, attempt(gen_b, 10, 1), attempt(gen_d, 20, 2),
                     attempt(critique, 30, 3), attempt(planning, 40, 4)).summary()
    assert summary.by_stage[InferenceStage.NPC_GENERATION].sums["input_tokens"] == 30
    assert summary.by_stage[InferenceStage.CRITIQUE].sums["input_tokens"] == 30
    assert summary.optional.sums["input_tokens"] == 70
    assert summary.by_actor[None].sums["output_tokens"] == 3
    assert summary.by_actor["npc-a"].sums["output_tokens"] == 4
    assert summary.total.sums["input_tokens"] == 100


def test_ledger_constructor_replays_booking_checks():
    plan = two_speakers()
    foreign = attempt(two_speakers("turn-2").calls[0], 1, 1)
    with pytest.raises(UnplannedCallError):
        TurnUsageLedger(plan, (foreign,))
    with pytest.raises(ValueError):
        TurnUsageLedger(plan, (attempt(plan.calls[0], 1, 1, n=2),))


def test_ledger_extends_to_later_phase():
    decide = OptionalRequest("decision", None, "choose who answers")
    select = two_speakers(phase="select", speaking_npc_ids=(), optional_requests=[decide],
                          policy=InferencePolicy({"decision": 1}))
    ledger = booked(select, attempt(select.calls[0], 30, 5))
    main = select.add_phase("main", speaking_npc_ids=("npc-b", "npc-d"))
    extended = ledger.extend(main)
    assert extended.attempts == ledger.attempts
    assert extended.summary().unmetered_call_ids == {c.call_id for c in main.baseline_calls}
    with pytest.raises(ValueError):
        ledger.extend(two_speakers())  # not this turn's continuation
    with pytest.raises(ValueError):
        ledger.extend(two_speakers(phase="select", speaking_npc_ids=(),
                                   optional_requests=[decide],
                                   policy=InferencePolicy({"decision": 2})))


def test_record_returns_new_ledger():
    plan = two_speakers()
    empty = TurnUsageLedger(plan)
    one = empty.record(attempt(plan.calls[0], 1, 1))
    assert empty.attempts == () and len(one.attempts) == 1
    assert empty.summary().unmetered_call_ids == plan.call_ids


# --- Session ------------------------------------------------------------------

def full_turn(turn_id, input_tokens):
    plan = two_speakers(turn_id)
    return booked(plan, *(attempt(c, input_tokens, 1) for c in plan.calls)).summary()


def test_session_sums_turns_and_phases():
    decide = OptionalRequest("decision", None, "choose who answers")
    select = two_speakers("turn-1", phase="select", speaking_npc_ids=(),
                          optional_requests=[decide], policy=InferencePolicy({"decision": 1}))
    main = select.add_phase("main", speaking_npc_ids=("npc-b", "npc-d"))
    turn_one = booked(select, attempt(select.calls[0], 30, 5)).extend(main)
    turn_one = turn_one.record(attempt(main.calls[1], 100, 10)).record(attempt(main.calls[2], 100, 10))
    session = SessionUsage("campaign-1").add(turn_one.summary()).add(full_turn("turn-2", 50))
    assert turn_one.summary().phases == ("select", "main")
    assert session.baseline.sums["input_tokens"] == 300
    assert session.optional.sums["input_tokens"] == 30
    assert session.total.sums["input_tokens"] == 330
    assert session.by_stage[InferenceStage.DECISION].attempts == 1
    assert session.by_actor["npc-b"].sums["input_tokens"] == 150
    assert session.complete


def test_session_rejects_other_campaign_and_duplicate_turn():
    session = SessionUsage("campaign-1").add(full_turn("turn-1", 1))
    with pytest.raises(ValueError):
        session.add(full_turn("turn-1", 2))
    other = TurnUsageLedger(plan_turn("campaign-2", "turn-9", present_actor_ids=PRESENT)).summary()
    with pytest.raises(ValueError):
        session.add(other)
    with pytest.raises(ValueError):
        SessionUsage("campaign-1", (full_turn("turn-1", 1), full_turn("turn-1", 2)))
    with pytest.raises(ValueError):
        SessionUsage("campaign-1", (other,))


def test_session_with_unmetered_turn_is_incomplete():
    plan = two_speakers("turn-2")
    partial = booked(plan, attempt(plan.calls[0], 1, 1)).summary()
    session = SessionUsage("campaign-1").add(full_turn("turn-1", 1)).add(partial)
    assert not session.complete
    assert session.unmetered_call_ids == {plan.calls[1].call_id}


def test_empty_session_is_complete_but_not_provider_reported():
    session = SessionUsage("campaign-1")
    assert session.complete and not session.provider_reported_only
    assert session.total.attempts == 0


def test_ledger_extends_only_to_a_later_phase():
    plan = two_speakers(speaking_npc_ids=("npc-b",))
    ledger = booked(plan, attempt(plan.calls[0], 1, 1))
    with pytest.raises(ValueError):  # same plan again: not a later phase
        ledger.extend(plan)
    replanned = plan_turn("campaign-1", "turn-1", present_actor_ids=PRESENT,
                          speaking_npc_ids=("npc-b", "npc-d"))
    with pytest.raises(ValueError):  # re-planned "main", not a new phase
        ledger.extend(replanned)
    later = ledger.add_phase("after", speaking_npc_ids=("npc-d",))
    assert later.plan.phases == ("main", "after")
    assert later.summary().unmetered_call_ids == {later.plan.calls[1].call_id}


def test_usage_values_are_hashable_and_do_not_alias_inputs():
    plan = two_speakers()
    summary = booked(plan, attempt(plan.calls[0], 1, 1), attempt(plan.calls[1], 2, 2)).summary()
    session = SessionUsage("campaign-1").add(summary)
    assert len({summary, summary}) == 1 and hash(session) == hash(session)
    sums = {name: 0 for name in TOKEN_FIELDS}
    unreported = dict(sums)
    totals = UsageTotals(1, sums, unreported, {SYNTHETIC})
    unreported["input_tokens"] = 1
    assert totals.fully_reported
    with pytest.raises(TypeError):
        totals.sums["input_tokens"] = 5  # type: ignore[index]


def test_hand_built_totals_and_summaries_are_validated():
    zeros = {name: 0 for name in TOKEN_FIELDS}
    for bad in ((0, {"input_tokens": -5}, zeros, ()),  # missing and negative counts
                (1, zeros, {**zeros, "input_tokens": 2}, ()),  # more unreported than attempts
                (0, zeros, zeros, {PROVIDER}),  # measured nothing
                (1, zeros, zeros, {"guessed"})):
        with pytest.raises(ValueError):
            UsageTotals(*bad)
    plan = two_speakers()
    real = booked(plan, attempt(plan.calls[0], 5, 5, measurement=PROVIDER)).summary()
    inflated = UsageTotals(3, zeros, zeros, {PROVIDER})
    with pytest.raises(ValueError):  # breakdowns no longer add up to the total
        replace(real, total=inflated)
    with pytest.raises(ValueError):  # unmetered calls from another turn
        replace(real, unmetered_call_ids=frozenset(two_speakers("turn-2").call_ids))
    assert not real.complete  # npc-d is unmetered, whatever the summary is told


def test_unsent_attempts_do_not_taint_measurements():
    plan = two_speakers(policy=InferencePolicy(max_attempts_per_call=2))
    summary = booked(
        plan,
        attempt(plan.calls[0], 0, 0, outcome="not_sent", measurement=SYNTHETIC),
        attempt(plan.calls[0], 10, 2, n=2, measurement=PROVIDER),
        attempt(plan.calls[1], 10, 2, measurement=PROVIDER),
    ).summary()
    assert summary.total.attempts == 3
    assert summary.provider_reported_only
    session = SessionUsage("campaign-1").add(summary)
    assert session.measurements == {PROVIDER} and session.provider_reported_only


def test_hand_built_summaries_keep_baseline_and_optional_apart():
    plan = two_speakers(optional_requests=[OptionalRequest("critique", None, "check")],
                        policy=InferencePolicy({"critique": 1}))
    real = booked(plan, attempt(plan.calls[0], 10, 1), attempt(plan.calls[1], 10, 1),
                  attempt(plan.calls[2], 900, 9)).summary()
    with pytest.raises(ValueError):  # optional tokens passed off as baseline
        replace(real, baseline=real.optional, optional=real.baseline)
    with pytest.raises(ValueError):
        replace(real, by_stage={"bogus": real.total})
    with pytest.raises(ValueError):
        replace(real, phases=(1, 2))
    with pytest.raises(ValueError):
        replace(real, declined=("junk",))
