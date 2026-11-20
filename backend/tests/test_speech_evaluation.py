from dataclasses import replace

import pytest

from grimoire.intent import Command, RoutingContext, Utterance, route
from grimoire.speech import ControlResult, route_recognition
from grimoire.speech.evaluation import evaluate
from speech_corpus import CORPUS


def test_synthetic_corpus_gate_and_honest_metric_labels():
    report = evaluate(CORPUS)
    assert report.passed
    assert report.completed == len(CORPUS)
    assert report.unintended_commands == report.misattributions == report.wrong_actors == 0
    assert report.negative_cases > 0 and report.unattributed_cases > 0
    assert report.task_completion_rate == 1
    assert report.evidence == "transcript_event" and report.acoustic_tested is False
    assert 0 <= report.latency_p50 <= report.latency_p95 <= report.latency_max


def test_unsafe_router_and_reject_everything_cannot_pass():
    def unsafe(event, context):
        return ControlResult("routed", "unsafe", route(event.utterance, context))
    report = evaluate(CORPUS, router=unsafe)
    assert not report.passed and report.unintended_commands > 0 and report.misattributions > 0
    blocked = evaluate(CORPUS, router=lambda e, c: ControlResult("blocked", "test"))
    assert blocked.unintended_commands == 0
    assert not blocked.passed and blocked.task_completion_rate < 1


def test_rates_use_matching_denominators_and_empty_is_not_a_pass():
    empty = evaluate(())
    assert not empty.passed
    assert empty.task_completion_rate is empty.unintended_command_rate is empty.misattribution_rate is None
    assert empty.latency_mean is None
    positive = evaluate(CORPUS[:1])
    assert positive.unintended_command_rate is None and positive.misattribution_rate is None
    assert positive.passed
    negative = evaluate((CORPUS[9],), router=lambda e, c: ControlResult("routed", "unsafe", route(e.utterance, c)))
    assert negative.unintended_command_rate == negative.misattribution_rate == 1


def test_wrong_actor_and_wrong_command_arguments_fail_exact_task_match():
    actor = evaluate((CORPUS[2],), router=lambda e, c: route_recognition(e, RoutingContext("pc-2", True)))
    assert actor.wrong_actors == 1 and actor.wrong_actor_rate == 1
    assert actor.misattribution_rate is None and not actor.passed
    wrong = evaluate(CORPUS[:1], router=lambda e, c: ControlResult("routed", "test", route(Utterance("open map"), c)))
    assert wrong.wrong_commands == 1 and wrong.completed == 0 and not wrong.passed


def test_latency_is_measured_routing_only():
    times = iter((1., 1.25, 2., 2.75))
    report = evaluate(CORPUS[:2], clock=lambda: next(times))
    assert report.latency_mean == .5 and report.latency_max == .75


@pytest.mark.parametrize("times", [(1., 0.), (0., float("nan")), (0., float("inf")), (False, 1.)])
def test_invalid_clock_is_not_a_measurement(times):
    tick = iter(times)
    with pytest.raises(ValueError):
        evaluate(CORPUS[:1], clock=lambda: next(tick))


def test_duplicate_or_incoherent_expectations_are_rejected():
    with pytest.raises(ValueError):
        evaluate((CORPUS[0], CORPUS[0]))
    for changes in (dict(expected_kind="bad"), dict(expected_kind="blocked"),
                    dict(expected_command=Command("pause"), expected_kind="pc_speech"),
                    dict(category="invented-metric"), dict(expected_actor="pc-1")):
        with pytest.raises(ValueError):
            replace(CORPUS[0], **changes)
