"""Exhaustive fault exploration of a two-speaker generation turn (sprint 027).

Test infrastructure only. Every point runs one planned turn (Ash speaks, then Brin) through the
real ``ProviderAdapter`` and ``GenerationRuntime`` over a scripted transport, with one fault on one
of the two calls: every provider fault, a reply the screen must reject, a cancel and a newer run
superseding this one, at each byte position that matters, with and without a retry. No real model
is called and nothing is billed. Each point runs twice; the traces must match.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from .providers import (
    REFERENCE_REPLY, CancelAt, ConnectFail, Garbage, ReadFail, Reply, ScriptedClock, ScriptedTransport, Status,
    Step, Supersede, Truncate, WriteFail,
)

#: The generation fault invariants (catalogued in docs/TESTING.md).
GENERATION_INVARIANTS = (
    "no unaccepted line", "stale delivers nothing", "halt after failure", "every send booked",
    "bounded calls", "one line per call", "no leaked line", "deterministic", "bounded exploration",
)
CAMPAIGN, KEY = "c-explore", "chat-1"
SECRET_MARKER = "the duke sold the guild"
ASH_EXAMPLE = "Ledger's shut until the tally is done at dawn."
GOOD = {"ash": "Coin first, talk after.", "brin": "Mind the gate, friend."}
_REPLY_LINES = len(REFERENCE_REPLY.lines())


def _say(text: str, **kw) -> Reply:
    return Reply(deltas=(text,), **kw)


def _faults() -> dict[str, Step]:
    faults: dict[str, Step] = {
        "connect_timeout": ConnectFail("timeout"), "write_fail": WriteFail(), "status_401": Status(401),
        "status_429": Status(429, retry_after="1"), "status_503": Status(503),
        "finish_length": Reply(finish_reason="length"), "silent": _say(" ... "),
        "leak": _say(f"They whisper {SECRET_MARKER}."), "copy": _say(ASH_EXAMPLE),
    }
    for index in range(_REPLY_LINES + 1):
        faults[f"read_timeout@{index}"] = ReadFail(index, "timeout")
        faults[f"truncate@{index}"] = Truncate(index)
        faults[f"garbage@{index}"] = Garbage(index)
        faults[f"cancel@{index}"] = CancelAt(index)
        if index < _REPLY_LINES:  # past the last line the adapter has stopped reading
            faults[f"supersede@{index}"] = Supersede(index)
    return faults


@dataclass(frozen=True)
class _Point:
    name: str
    call: int  # which planned call (0: Ash, 1: Brin) meets the fault
    fault: Step
    attempts: int
    approve: bool = False  # an untrusted handle approved per attempt, instead of a trusted one
    clean: bool = False  # no fault at all


def _points() -> list[_Point]:
    points = []
    for name, fault in _faults().items():
        for call in (0, 1):
            for attempts in (1, 2):
                points.append(_Point(f"{name}/call{call}/x{attempts}", call, fault, attempts))
    for name in ("status_503", "supersede@0"):
        points.append(_Point(f"{name}/call0/x2/approved", 0, _faults()[name], 2, approve=True))
    points.append(_Point("clean/x1", 0, _say(GOOD["ash"]), 1, clean=True))
    points.append(_Point("clean/x1/approved", 0, _say(GOOD["ash"]), 1, approve=True, clean=True))
    return points


@dataclass(frozen=True)
class GenerationFaultReport:
    seed: int
    runs: int
    points: tuple[str, ...]
    traces: Mapping[str, tuple]
    violations: tuple

    def assert_ok(self) -> None:
        if self.violations:
            raise AssertionError("\n".join(v.format() for v in self.violations))


def _cast():
    """Two director NPCs and one player character. Brin saw the secret; Ash did not."""
    from grimoire.core import (
        ControlAssignment, Event, InstanceRef, Knowledge, Moment, Perception, actor_knowledge,
    )
    from grimoire.core.authority import DIRECTOR
    from grimoire.core.identity import SourceRef
    from grimoire.core.model import ActorView
    from grimoire.voice import ResolvedVoice, SecretFact, VoiceAnchor, VoiceExample

    ash, brin = (InstanceRef(CAMPAIGN, "character", name) for name in ("ash", "brin"))
    ledger = Knowledge(CAMPAIGN, events=[Event(CAMPAIGN, "e1", "The duke betrayed the guild", Moment(1),
                                               "director")],
                       evidence=[Perception(CAMPAIGN, "p1", brin, "e1", "hearing",
                                            f"A clerk muttered that {SECRET_MARKER}", Moment(1), "director",
                                            confirmed=True)])
    controls = (ControlAssignment(CAMPAIGN, "ash", DIRECTOR), ControlAssignment(CAMPAIGN, "brin", DIRECTOR),
                ControlAssignment(CAMPAIGN, "wren", "human:alice"))

    def voice(ref, entity, name, anchor, example):
        revision = SourceRef("world:explore", "character", entity, "r1")
        return ResolvedVoice(ref, name, revision, revision, (VoiceAnchor("a1", "cadence", anchor),),
                             (VoiceExample("e1", example),), {"a1": "source", "e1": "source"})

    voices = {"ash": voice(ash, "ash", "Ash", "Clipped counting-house words.", ASH_EXAMPLE),
              "brin": voice(brin, "brin", "Brin", "Slow, gruff gate-guard talk.",
                            "Halt there, papers first, then you pass.")}
    views = {actor.instance_id: ActorView(CAMPAIGN, actor.instance_id, voices[actor.instance_id].name, "",
                                          knowledge=actor_knowledge(ledger, actor, as_of=1))
             for actor in (ash, brin)}
    return controls, voices, views, (SecretFact("duke-betrayal", (SECRET_MARKER,)),)


def _script(point: _Point) -> list[Step]:
    """The fault on its call, a good reply for its retry and for every other call, and spares."""
    good = [_say(GOOD["ash"]), _say(GOOD["brin"])]
    if point.clean:
        return good + [_say(GOOD["brin"])] * 2
    steps: list[Step] = [good[0]] if point.call == 1 else []
    return steps + [point.fault] + good[point.call:] + [_say(GOOD["brin"])] * 2


def _world(point: _Point, runtime_factory=None):
    from grimoire.core.inference import InferencePolicy, TurnUsageLedger
    from grimoire.generation import npc_brief, plan_generation_turn
    from grimoire.providers import ProviderEndpoint, ProviderRegistry, RouteChoice, StaticCredentials
    from grimoire.providers.adapter import ProviderAdapter
    from grimoire.providers.meter import TurnMeter
    from grimoire.runtime import GenerationRuntime, RunFence
    from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest

    controls, voices, views, secrets = _cast()
    authz = Authorizer({"human:owner"})
    principal = "app:worker" if point.approve else "app:runtime"
    authz.add_grant(Grant("g", principal, frozenset({Permission.EXECUTE}), ResourcePath(("providers",)),
                          "human:owner"))
    handle = authz.handle_for(principal, trusted=not point.approve)
    approved = [0]

    def approvals(label: str, attempt: int, payload: bytes) -> str:
        approved[0] += 1
        approval_id = f"ap-{approved[0]}"
        authz.register_approval(Approval(approval_id, principal, Permission.EXECUTE,
                                         ResourcePath(("providers", label.split("@", 1)[0])),
                                         payload_digest(payload), "human:owner"))
        return approval_id

    transport = ScriptedTransport(_script(point))
    fence = RunFence()
    token = fence.claim(KEY)
    transport.on_supersede = lambda: fence.claim(KEY)
    clock = ScriptedClock()
    registry = ProviderRegistry([ProviderEndpoint("primary", "https://primary.example/v1")])
    adapter = ProviderAdapter(registry, StaticCredentials({"primary": "sk-explorer"}), authz, transport,
                              clock=clock, wait=clock.wait)
    plan = plan_generation_turn(CAMPAIGN, "t1", present=("ash", "brin", "wren"), speakers=("ash", "brin"),
                                controls=controls, policy=InferencePolicy(max_attempts_per_call=point.attempts))
    meter = TurnMeter(TurnUsageLedger(plan))
    briefs = [npc_brief(views[actor], voices[actor], secrets=secrets) for actor in ("ash", "brin")]
    runtime = (runtime_factory or GenerationRuntime)(adapter, (RouteChoice("primary", "model-a"),), handle,
                                approvals=approvals if point.approve else None, max_output_tokens=16)

    def run():
        return runtime.run_turn(meter, briefs, controls=controls, token=token)
    return run, transport, meter, token, plan


def _run_point(point: _Point, max_sends: int, runtime_factory=None):
    from grimoire.testing.explore import Violation

    run, transport, meter, token, plan = _world(point, runtime_factory)
    outcome, raised = None, None
    try:
        outcome = run()
    except BaseException as error:  # a broken runtime: recorded, never swallowed silently
        raised = f"{type(error).__name__}: {error}"
    booked = meter.ledger.attempts
    trace = (tuple(transport.connects), tuple(s.body_digest for s in transport.sends),
             tuple((a.call_id.actor_id, a.attempt, a.outcome) for a in booked), raised,
             None if outcome is None else (outcome.status, tuple((c.status, c.reason) for c in outcome.calls),
                                           tuple((line.speaker, line.text) for line in outcome.lines)))
    violations = []

    def fail(invariant: str, detail: str) -> None:
        violations.append(Violation(invariant, point.name, detail, tuple(str(part) for part in trace[2:])))

    if raised is not None or outcome is None:
        fail("halt after failure", f"run_turn raised {raised}")
        return trace, violations
    statuses = {call.call_id: meter.status(call.call_id) for call in plan.calls}
    # A line is a whole scripted reply, never a prefix of one that was cut off.
    scripted = [step if isinstance(step, Reply) else getattr(step, "reply", None) for step in _script(point)]
    accepted_texts = {"".join(step.deltas).strip() for step in scripted if isinstance(step, Reply)}
    for line in outcome.lines:
        if statuses.get(line.call_id) != "accepted" or line.text not in accepted_texts:
            fail("no unaccepted line", f"{line.speaker} line from a call that is {statuses.get(line.call_id)}")
        if SECRET_MARKER in line.text.lower() and line.speaker == "Ash":
            fail("no leaked line", "Ash stated a secret it never learned")
    if len({line.call_id for line in outcome.lines}) != len(outcome.lines):
        fail("one line per call", "a call produced two lines")
    if token.stale and (outcome.lines or outcome.status != "stale"):
        fail("stale delivers nothing", f"stale run returned {len(outcome.lines)} lines as {outcome.status}")
    labels = [call.status for call in outcome.calls]
    if len(labels) != len(plan.calls):
        fail("halt after failure", "an outcome is missing for a planned call")
    for index, label in enumerate(labels):
        if label != "accepted" and any(later != "not_issued" for later in labels[index + 1:]):
            fail("halt after failure", f"a call ran after call {index} ended {label}")
    for call, label in zip(plan.calls, labels):
        if label == "not_issued" and any(a.call_id == call.call_id for a in booked):
            fail("halt after failure", f"{call.call_id} was booked but reported not issued")
    if outcome.status == "complete" and labels != ["accepted"] * len(labels):
        fail("halt after failure", "a complete turn with a call that did not produce a line")
    sends = len(transport.sends)
    if sends != sum(1 for a in booked if a.outcome != "not_sent") or len(transport.connects) != len(booked):
        fail("every send booked", f"{len(transport.connects)} connects, {sends} sends, {len(booked)} booked")
    if len(transport.connects) > point.attempts * len(plan.calls) or sends > max_sends:
        fail("bounded calls", f"{len(transport.connects)} connects for {len(plan.calls)} calls")
    return trace, violations


def explore_generation_faults(*, seed: int = 0, max_runs: int = 1000, max_sends: int = 4,
                              runtime_factory=None) -> GenerationFaultReport:
    """Run every point (each twice, for determinism) and check every invariant.

    Deterministic: the seed only shuffles the run order, and is recorded. ``runtime_factory``
    replaces ``GenerationRuntime`` (same constructor), so a test can check each invariant can fail.
    """
    import random

    from grimoire.testing.explore import Violation

    points = _points()
    random.Random(seed).shuffle(points)
    traces: dict[str, tuple] = {}
    violations: list = []
    runs = 0
    for point in points:
        if runs >= max_runs:
            violations.append(Violation("bounded exploration", point.name, f"more than max_runs={max_runs} points"))
            break
        runs += 1
        trace, found = _run_point(point, max_sends, runtime_factory)
        repeat, _ = _run_point(point, max_sends, runtime_factory)
        if repeat != trace:
            found.append(Violation("deterministic", point.name, "a rerun produced another trace"))
        traces[point.name] = trace
        violations.extend(found)
    return GenerationFaultReport(seed, runs, tuple(p.name for p in points[:runs]), traces, tuple(violations))
