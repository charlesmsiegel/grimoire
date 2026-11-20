"""Safety metrics: transcript/event cases (``evaluate``) and synthetic-audio cases (``evaluate_audio``).

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


# -- Acoustic evaluation (067): real audio through a real recognizer, capture analysis and session gates ------

OUTCOMES = frozenset({"none", "shown", "draft", "review", "act"})


@dataclass(frozen=True)
class Window:
    """One step: WAV bytes captured in a gesture-opened window, or typing. ``answer=True`` types a reply to the
    reviewed proposal (its Yes control); otherwise typed text goes through the ordinary keyboard router."""
    audio: bytes | None = field(default=None, repr=False)
    typed: str | None = field(default=None, repr=False)
    trigger: str = "keyboard"
    answer: bool = False

    def __post_init__(self):
        if (self.audio is None) == (self.typed is None):
            raise ValueError("a window is audio or a typed answer")
        if self.audio is not None and (not isinstance(self.audio, bytes) or not self.audio):
            raise ValueError("invalid window audio")
        if self.typed is not None and (not isinstance(self.typed, str) or not self.typed.strip()):
            raise ValueError("invalid typed answer")
        if type(self.answer) is not bool or (self.answer and self.typed is None):
            raise ValueError("only typing answers a proposal by its control")


@dataclass(frozen=True)
class AudioCase:
    """``windows``: the first opens the case; a later one answers a pending proposal. ``expected``: the
    acceptable final outcomes, each ``(outcome, command)``. ``bystander``: no speech in the case is the person's."""
    id: str
    windows: tuple[Window, ...] = field(repr=False)
    expected: frozenset = field(repr=False)
    category: str = "clean"
    bystander: bool = False

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip() or len(self.id) > 128:
            raise ValueError("invalid case id")
        if (not isinstance(self.windows, tuple) or not 1 <= len(self.windows) <= 4
                or not all(isinstance(w, Window) for w in self.windows) or self.windows[0].audio is None):
            raise ValueError("a case opens with one audio window and at most three answers")
        if not isinstance(self.expected, frozenset) or not self.expected:
            raise ValueError("expected outcomes required")
        for outcome, command in self.expected:
            if outcome not in OUTCOMES or (outcome in ("act", "review")) != isinstance(command, Command):
                raise ValueError("invalid expected outcome")
        if self.category not in AUDIO_CATEGORIES or type(self.bystander) is not bool:
            raise ValueError("invalid case label")

    @property
    def negative(self) -> bool:
        return not any(outcome == "act" for outcome, _ in self.expected)


AUDIO_CATEGORIES = frozenset({"clean", "quiet", "atypical", "rate", "disfluency", "noise", "background",
                              "simultaneous", "interruption", "confirmation", "recovery"})


@dataclass(frozen=True)
class AudioReport:
    cases: int
    completed: int
    negative_cases: int
    unintended_actions: int
    command_hypotheses_on_negatives: int
    wrong_actions: int
    wrong_command_hypotheses: int
    bystander_cases: int
    bystanders_recognized: int
    bystander_actions: int
    windows: int
    recognized_windows: int
    latency_mean: float | None
    latency_p50: float | None
    latency_p95: float | None
    latency_max: float | None
    backend: str
    evidence: str = "synthetic_audio"
    acoustic_tested: bool = True

    @property
    def task_completion_rate(self):
        return self.completed / self.cases if self.cases else None

    @property
    def unintended_action_rate(self):
        return self.unintended_actions / self.negative_cases if self.negative_cases else None

    @property
    def unintended_hypothesis_rate(self):
        return self.command_hypotheses_on_negatives / self.negative_cases if self.negative_cases else None

    @property
    def bystander_recognition_rate(self):
        return self.bystanders_recognized / self.bystander_cases if self.bystander_cases else None

    @property
    def safe(self):
        """No case did something it should not have; recognizer misses that fail safely are not unsafe."""
        return self.cases > 0 and not (self.unintended_actions or self.wrong_actions or self.bystander_actions)


def _outcome(voice_action, intent, pending):
    action = voice_action(intent)
    if action == "act":
        return ("act", intent.command)
    if pending is not None:
        return ("review", pending.command)
    return (action if action == "draft" else "shown", None)


def evaluate_audio(cases: tuple[AudioCase, ...], backend, context: RoutingContext, *,
                   clock: Callable[[], float] = perf_counter) -> AudioReport:
    """Run each case in a fresh local-only session over ``backend``: analysis, recognition and routing are
    real; latency is measured per audio window. A case never posts, deletes or dispatches anything; "act"
    means what ``voice_action`` would let the UI do."""
    from grimoire.intent import Utterance

    from .capture import Capture, hear, voice_action
    from .model import Policy, Selection
    from .session import SpeechSession

    if not isinstance(cases, tuple) or not 0 < len(cases) <= 2000 or not all(isinstance(c, AudioCase) for c in cases):
        raise ValueError("expected bounded audio case tuple")
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("duplicate case id")
    info = backend.info
    if info.locality != "local" or info.network is not False or info.billing != "free":
        raise ValueError("acoustic evaluation runs local, offline, free recognizers only")
    counts = dict(completed=0, negatives=0, unintended=0, hypotheses=0, wrong=0, wrong_hypotheses=0,
                  bystanders=0, recognized_bystanders=0, bystander_actions=0, windows=0, recognized=0)
    latencies = []
    for case in cases:
        session = SpeechSession((backend,), Policy(stt=Selection("local_only", info.id), recording=True), context)
        outcome, hypothesis, sequence, first_recognized = ("none", None), None, 0, False
        for index, window in enumerate(case.windows):
            pending = session.pending if outcome[0] == "review" else None
            if window.typed is not None:
                if window.answer:  # the reviewed proposal's typed Yes; with nothing pending it confirms nothing
                    if pending is None:
                        outcome = ("none", None)
                        continue
                    intent = session.confirm(Utterance(window.typed), token=pending.token)
                    outcome = ("act", intent.command) if intent is not None else ("none", None)
                else:  # back to typed controls: the same router, no recording needed
                    outcome = _outcome(voice_action, session.keyboard(Utterance(window.typed)), None)
                continue
            sequence += 1
            capture = Capture(window.audio, window.trigger, session.capture_token,
                              pending.token if pending is not None else None)
            start = _tick(clock)
            heard = hear(session, sequence, capture)
            latencies.append(_tick(clock) - start)
            counts["windows"] += 1
            recognized = heard.transcript is not None
            counts["recognized"] += recognized
            if not index:
                first_recognized = recognized
                hypothesis = heard.intent.command if heard.intent is not None else None
            if heard.status in ("confirmed", "not_confirmed"):
                outcome = ("act", heard.intent.command) if heard.intent is not None else ("none", None)
            elif heard.status != "routed":
                outcome = ("none", None)
            else:
                outcome = _outcome(voice_action, heard.intent, session.pending)
        session.close()
        counts["completed"] += outcome in case.expected
        acted = outcome[0] == "act"
        if case.negative:
            counts["negatives"] += 1
            counts["unintended"] += acted
            counts["hypotheses"] += hypothesis is not None
        else:
            wanted = {command for kind, command in case.expected if kind in ("act", "review")}
            counts["wrong"] += acted and outcome[1] not in wanted
            counts["wrong_hypotheses"] += hypothesis is not None and hypothesis not in wanted
        if case.bystander:
            counts["bystanders"] += 1
            counts["recognized_bystanders"] += first_recognized
            counts["bystander_actions"] += acted
    ordered = sorted(latencies)
    return AudioReport(len(cases), counts["completed"], counts["negatives"], counts["unintended"],
                       counts["hypotheses"], counts["wrong"], counts["wrong_hypotheses"], counts["bystanders"],
                       counts["recognized_bystanders"], counts["bystander_actions"], counts["windows"],
                       counts["recognized"], mean(ordered) if ordered else None,
                       median(ordered) if ordered else None,
                       ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None,
                       max(ordered) if ordered else None, info.id)
