"""Transcript/event safety metrics, never a claim about acoustic recognition.

A test-owned corpus provides expectations independently of the system under test.
No transcripts, actor ids or per-speaker profiles are copied to the aggregate report.
"""
import math
from dataclasses import dataclass, field
from statistics import mean, median
from time import perf_counter
from typing import Callable

from grimoire.intent import Command, RoutingContext

from .control import ControlResult, Recognition, route_recognition

CATEGORIES = frozenset({"clean", "quiet", "disfluency", "speech_variant", "recognizer_error",
                        "noise", "background", "simultaneous", "interruption", "ambiguous"})
KINDS = frozenset({"blocked", "uncertain", "reject", "ui_command", "pc_speech", "pc_action", "director_instruction"})


@dataclass(frozen=True)
class Case:
    id: str
    event: Recognition = field(repr=False)
    context: RoutingContext = field(repr=False)
    expected_kind: str
    expected_command: Command | None = None
    expected_actor: str | None = field(default=None, repr=False)
    category: str = "clean"

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip() or len(self.id) > 128:
            raise ValueError("invalid case id")
        if not isinstance(self.event, Recognition) or not isinstance(self.context, RoutingContext):
            raise ValueError("invalid case input")
        if self.expected_kind not in KINDS or self.category not in CATEGORIES:
            raise ValueError("invalid case expectation")
        if (self.expected_kind == "ui_command") != isinstance(self.expected_command, Command):
            raise ValueError("command expectation must match kind")
        if self.expected_command is not None and not isinstance(self.expected_command, Command):
            raise ValueError("invalid command expectation")
        pc = self.expected_kind in {"pc_speech", "pc_action"}
        if pc:
            if not isinstance(self.expected_actor, str) or not self.expected_actor.strip():
                raise ValueError("PC expectation requires actor")
        elif self.expected_actor is not None:
            raise ValueError("only PC expectations carry an actor")


@dataclass(frozen=True)
class Report:
    cases: int
    completed: int
    negative_cases: int
    unintended_commands: int
    wrong_commands: int
    unattributed_cases: int
    misattributions: int
    foreground_cases: int
    wrong_actors: int
    latency_mean: float | None
    latency_p50: float | None
    latency_p95: float | None
    latency_max: float | None
    evidence: str = "transcript_event"
    acoustic_tested: bool = False

    @property
    def unintended_command_rate(self):
        return self.unintended_commands / self.negative_cases if self.negative_cases else None

    @property
    def misattribution_rate(self):
        return self.misattributions / self.unattributed_cases if self.unattributed_cases else None

    @property
    def wrong_actor_rate(self):
        return self.wrong_actors / self.foreground_cases if self.foreground_cases else None

    @property
    def task_completion_rate(self):
        return self.completed / self.cases if self.cases else None

    @property
    def passed(self):
        return (self.cases > 0 and self.completed == self.cases and not self.unintended_commands
                and not self.wrong_commands and not self.misattributions and not self.wrong_actors)


def _tick(clock: Callable[[], float]) -> float:
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid routing clock")
    return value


def evaluate(cases: tuple[Case, ...], *,
             router: Callable[[Recognition, RoutingContext], ControlResult] = route_recognition,
             clock: Callable[[], float] = perf_counter) -> Report:
    if not isinstance(cases, tuple) or len(cases) > 10000 or not all(isinstance(c, Case) for c in cases):
        raise ValueError("expected bounded case tuple")
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("duplicate case id")
    completed = negatives = unintended = wrong_commands = unattributed = misattributions = foreground = wrong_actors = 0
    latencies = []
    for case in cases:
        start = _tick(clock)
        result = router(case.event, case.context)
        elapsed = _tick(clock) - start
        if elapsed < 0 or not math.isfinite(elapsed):
            raise ValueError("non-monotonic routing clock")
        latencies.append(elapsed)
        if not isinstance(result, ControlResult) or result.status not in {"blocked", "routed"}:
            raise ValueError("invalid router result")
        if (result.status == "routed") != (result.intent is not None):
            raise ValueError("incoherent router result")
        intent = result.intent
        kind = intent.kind if intent else "blocked"
        command = intent.command if intent else None
        actor = intent.actor_id if intent else None
        completed += (kind, command, actor) == (case.expected_kind, case.expected_command, case.expected_actor)
        if case.expected_command is None:
            negatives += 1
            unintended += command is not None
        elif command is not None and command != case.expected_command:
            wrong_commands += 1
        if case.event.attribution != "foreground":
            unattributed += 1
            misattributions += intent is not None
        else:
            foreground += 1
            wrong_actors += actor is not None and actor != case.expected_actor
    ordered = sorted(latencies)
    return Report(len(cases), completed, negatives, unintended, wrong_commands, unattributed,
                  misattributions, foreground, wrong_actors,
                  mean(ordered) if ordered else None, median(ordered) if ordered else None,
                  ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None,
                  max(ordered) if ordered else None)
