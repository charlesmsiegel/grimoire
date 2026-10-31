"""Eval-only run interchange format ``grimoire.story-quality/2``, reconciled with the inference
contract.

A ``/2`` run is rebuilt, turn by turn, as a ``TurnInferencePlan`` and a ``TurnUsageLedger``: a
call the planner would refuse or decline is invalid input, every attempt is booked (failed
retries included), and an unreported count stays ``None``. Nothing in the file says the run is
"measured"; that is derived from its ledger and, for a capture, from verified evidence
(``gate2.usage_status``). Scenarios are unchanged, and ``/1`` runs keep their meaning in
``format.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from grimoire.core.inference import (
    CallAttempt, InferencePlanError, InferencePolicy, InferenceStage, Measurement,
    OptionalRequest, SessionUsage, TokenUsage, TurnInferencePlan, TurnUsageLedger,
    TurnUsageSummary, UnplannedCallError, plan_turn,
)

from .format import (
    AUTHORS, NPC_GENERATION, OUTCOMES, REQUIRED_CONDITIONS, BenchmarkFormatError, Call,
    Contribution, Run, Scenario, Turn, _choice, _fail, _frozen, _int, _list, _number, _obj,
    _opt_str, _str, _unique,
)

FORMAT_V2 = "grimoire.story-quality/2"
#: Frozen for /2. A new inference stage (narration, say) is a /3, not a reinterpretation.
STAGES_V2 = ("npc_generation", "planning", "decision", "epistemic_inference", "recall",
             "graph_lookup", "critique")
OUTCOMES_V2 = (*OUTCOMES, "unlabelled")
SOURCE_KINDS = ("synthetic", "v0.2.1-capture", "v2-runtime")
CAPTURE_MODES = ("dry_run", "live")
COST_BASES = ("billed", "equivalent")
ATTEMPT_OUTCOMES = ("completed", "failed", "cancelled", "not_sent")
MEASUREMENTS = tuple(m.value for m in Measurement)
DEFAULT_PHASE = "main"
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class Source:
    """Where a run's numbers came from. Declared by the file, so never proof by itself."""

    kind: Literal["synthetic", "v0.2.1-capture", "v2-runtime"]
    mode: Literal["dry_run", "live"] | None = None
    v1_commit: str | None = None
    harness: str | None = None
    evidence_sha256: str | None = None
    profile: str | None = None
    llm_retries: int | None = None
    route: str | None = None

    def allowed_measurements(self) -> frozenset[str]:
        if self.kind == "synthetic" or self.mode == "dry_run":
            return frozenset({Measurement.SYNTHETIC.value})
        if self.kind == "v0.2.1-capture":
            return frozenset({Measurement.PROVIDER_REPORTED.value, Measurement.ESTIMATED.value})
        return frozenset(MEASUREMENTS)


@dataclass(frozen=True, slots=True)
class Cost:
    usd: float
    basis: Literal["billed", "equivalent"]


@dataclass(frozen=True, slots=True)
class CallV2:
    call_id: str
    turn: int
    phase: str
    stage: InferenceStage
    actor_id: str | None
    purpose: str
    attempts: tuple[CallAttempt, ...]
    #: Wall time of the whole call, every attempt and backoff included; None if unreported.
    latency_ms: int | None
    #: One per attempt; None where the attempt's cost was not reported.
    costs: tuple[Cost | None, ...]

    @property
    def completed(self) -> bool:
        return any(each.outcome == "completed" for each in self.attempts)


@dataclass(frozen=True, slots=True)
class RunV2:
    run_id: str
    scenario_id: str
    label: str
    source: Source
    conditions: Mapping[str, str]
    policy: InferencePolicy
    turns: tuple[Turn, ...]
    calls: tuple[CallV2, ...]
    choice_outcomes: Mapping[str, str]
    judge_scores: Mapping[str, float]
    plans: tuple[TurnInferencePlan, ...]
    summaries: tuple[TurnUsageSummary, ...]
    usage: SessionUsage

    @property
    def enabled_stages(self) -> tuple[str, ...]:
        return tuple(stage.value for stage, _ in self.policy.per_turn_call_caps)

    def scoring_view(self) -> Run:
        """This run in the ``/1`` shape the existing hard checks and voice metric read.

        Token fields are reported sums (unreported counted as 0), so the view is for
        story-quality checks only; usage questions go to ``usage`` and ``summaries``.
        """
        calls = []
        for call in self.calls:
            def total(name: str) -> int:
                return sum(getattr(each.usage, name) or 0 for each in call.attempts)
            optional = not call.stage.is_baseline
            calls.append(Call(
                call.call_id, call.turn, call.stage.value,
                None if optional else call.actor_id, call.stage.value if optional else None,
                total("input_tokens"), total("cache_read_tokens"), total("output_tokens"),
                call.latency_ms or 0))
        return Run(self.run_id, self.scenario_id, self.label, "synthetic", self.conditions,
                   self.enabled_stages, self.turns, tuple(calls), self.choice_outcomes,
                   self.judge_scores)


# --- readers ------------------------------------------------------------------------------------

def _opt_int(value: object, location: str) -> int | None:
    return None if value is None else _int(value, location)


def _hex(value: object, location: str, pattern: re.Pattern, what: str) -> str:
    text = _str(value, location)
    if not pattern.fullmatch(text):
        raise _fail(location, f"expected {what}")
    return text


def _source(data: object, loc: str) -> Source:
    if not isinstance(data, dict):
        raise _fail(loc, "expected an object")
    kind = _choice(data.get("kind"), f"{loc}.kind", SOURCE_KINDS)
    if kind == "synthetic":
        _obj(data, loc, ("kind",))
        return Source("synthetic")
    if kind == "v2-runtime":
        data = _obj(data, loc, ("kind", "harness"), {"route": None, "evidence_sha256": None})
        digest = data["evidence_sha256"]
        return Source("v2-runtime", harness=_str(data["harness"], f"{loc}.harness"),
                      route=_opt_str(data["route"], f"{loc}.route"),
                      evidence_sha256=None if digest is None else _hex(
                          digest, f"{loc}.evidence_sha256", _HEX64, "64 lowercase hex digits"))
    data = _obj(data, loc, ("kind", "mode", "v1_commit", "harness", "evidence_sha256", "profile",
                            "llm_retries", "route"))
    return Source(
        "v0.2.1-capture",
        _choice(data["mode"], f"{loc}.mode", CAPTURE_MODES),  # type: ignore[arg-type]
        _hex(data["v1_commit"], f"{loc}.v1_commit", _HEX40, "a 40-hex commit id"),
        _str(data["harness"], f"{loc}.harness"),
        _hex(data["evidence_sha256"], f"{loc}.evidence_sha256", _HEX64,
             "64 lowercase hex digits"),
        _str(data["profile"], f"{loc}.profile"),
        _int(data["llm_retries"], f"{loc}.llm_retries"),
        _str(data["route"], f"{loc}.route"),
    )


def _policy(data: object, loc: str) -> InferencePolicy:
    data = _obj(data, loc, ("per_turn_call_caps", "max_attempts_per_call"))
    caps = data["per_turn_call_caps"]
    if not isinstance(caps, dict):
        raise _fail(f"{loc}.per_turn_call_caps", "expected an object")
    checked = {}
    for stage, cap in caps.items():
        at = f"{loc}.per_turn_call_caps.{stage}"
        _choice(stage, at, STAGES_V2)
        if stage == NPC_GENERATION:
            raise _fail(at, "npc_generation is baseline, not a capped optional stage")
        checked[stage] = _int(cap, at, 1)
    return InferencePolicy(checked, _int(data["max_attempts_per_call"],
                                         f"{loc}.max_attempts_per_call", 1))


def _cost(data: object, loc: str) -> Cost | None:
    if data is None:
        return None
    data = _obj(data, loc, ("usd", "basis"))
    usd = _number(data["usd"], f"{loc}.usd")
    if usd < 0:
        raise _fail(f"{loc}.usd", "must be >= 0")
    return Cost(usd, _choice(data["basis"], f"{loc}.basis", COST_BASES))  # type: ignore[arg-type]


def _usage(data: object, loc: str) -> TokenUsage:
    data = _obj(data, loc, ("input_tokens", "output_tokens"),
                {"cache_read_tokens": None, "cache_write_tokens": None, "reasoning_tokens": None})
    values = {name: _opt_int(data[name], f"{loc}.{name}") for name in data}
    try:
        return TokenUsage(**values)
    except ValueError as error:
        raise _fail(loc, str(error)) from None


# --- run ----------------------------------------------------------------------------------------

@dataclass(slots=True)
class _RawCall:
    index: int
    call: CallV2
    raw_attempts: list[tuple[str, dict]]


def parse_run_v2(data: object, scenario: Scenario) -> RunV2:
    loc = "run"
    data = _obj(data, loc, ("format", "run_id", "scenario_id", "label", "source", "conditions",
                            "policy", "turns", "calls", "choice_outcomes"),
                {"judge_scores": {}})
    if data["format"] != FORMAT_V2:
        raise _fail(f"{loc}.format", f"expected {FORMAT_V2!r}")
    run_id = _str(data["run_id"], f"{loc}.run_id")
    scenario_id = _str(data["scenario_id"], f"{loc}.scenario_id")
    if scenario_id != scenario.scenario_id:
        raise _fail(f"{loc}.scenario_id", f"does not match scenario {scenario.scenario_id!r}")
    label = _str(data["label"], f"{loc}.label")
    source = _source(data["source"], f"{loc}.source")

    raw_conditions = data["conditions"]
    if not isinstance(raw_conditions, dict):
        raise _fail(f"{loc}.conditions", "expected an object")
    for key in REQUIRED_CONDITIONS:
        if key not in raw_conditions:
            raise _fail(f"{loc}.conditions.{key}", "required condition is missing")
    conditions = {_str(key, f"{loc}.conditions"): _str(value, f"{loc}.conditions.{key}")
                  for key, value in raw_conditions.items()}
    policy = _policy(data["policy"], f"{loc}.policy")
    directors = set(scenario.director_ids())
    players = {actor.actor_id for actor in scenario.actors if actor.control == "player"}
    known_actors = {actor.actor_id for actor in scenario.actors}
    allowed = source.allowed_measurements()

    calls: list[_RawCall] = []
    call_ids: set[str] = set()
    for index, raw in enumerate(_list(data["calls"], f"{loc}.calls")):
        at = f"{loc}.calls[{index}]"
        raw = _obj(raw, at, ("call_id", "turn", "stage", "actor_id", "purpose", "latency_ms",
                             "attempts"), {"phase": DEFAULT_PHASE})
        call_id = _unique(call_ids, _str(raw["call_id"], f"{at}.call_id"), f"{at}.call_id")
        stage = InferenceStage(_choice(raw["stage"], f"{at}.stage", STAGES_V2))
        actor_id = _opt_str(raw["actor_id"], f"{at}.actor_id")
        if actor_id is not None and actor_id not in known_actors:
            raise _fail(f"{at}.actor_id", f"unknown actor {actor_id!r}")
        if stage.is_baseline and actor_id not in directors:
            raise _fail(f"{at}.actor_id", "npc_generation needs a director actor")
        raw_attempts, costs = [], []
        for position, attempt in enumerate(_list(raw["attempts"], f"{at}.attempts")):
            aat = f"{at}.attempts[{position}]"
            attempt = _obj(attempt, aat, ("attempt", "outcome", "measurement", "usage"),
                           {"route": None, "cost": None})
            measurement = _choice(attempt["measurement"], f"{aat}.measurement", MEASUREMENTS)
            if measurement not in allowed:
                raise _fail(f"{aat}.measurement",
                            f"a {source.kind} source{' (' + source.mode + ')' if source.mode else ''}"
                            f" cannot hold {measurement} usage")
            outcome = _choice(attempt["outcome"], f"{aat}.outcome", ATTEMPT_OUTCOMES)
            cost = _cost(attempt["cost"], f"{aat}.cost")
            if outcome == "not_sent" and cost is not None:
                raise _fail(f"{aat}.cost", "an attempt that was never sent cost nothing")
            raw_attempts.append((aat, attempt))
            costs.append(cost)
        call = CallV2(call_id, _int(raw["turn"], f"{at}.turn", 1),
                      _str(raw["phase"], f"{at}.phase"), stage, actor_id,
                      _str(raw["purpose"], f"{at}.purpose"), (),
                      _opt_int(raw["latency_ms"], f"{at}.latency_ms"), tuple(costs))
        calls.append(_RawCall(index, call, raw_attempts))

    turns, last_turn = [], 0
    for index, raw in enumerate(_list(data["turns"], f"{loc}.turns")):
        at = f"{loc}.turns[{index}]"
        raw = _obj(raw, at, ("turn", "contributions"))
        turn = _int(raw["turn"], f"{at}.turn", 1)
        if turn <= last_turn:
            raise _fail(f"{at}.turn", "turns must be strictly increasing")
        last_turn = turn
        turns.append((at, turn, raw["contributions"]))
    turn_numbers = {turn for _, turn, _ in turns}
    for entry in calls:
        if entry.call.turn not in turn_numbers:
            raise _fail(f"{loc}.calls[{entry.index}].turn",
                        f"turn {entry.call.turn} is not in the run")

    plans, summaries, booked = _book(scenario, policy, calls, turns, players)
    by_id = {call.call_id: call for call in booked}

    parsed_turns = []
    for at, turn, raw_contributions in turns:
        contributions = []
        for position, item in enumerate(_list(raw_contributions, f"{at}.contributions")):
            cat = f"{at}.contributions[{position}]"
            item = _obj(item, cat, ("speaker_id", "author", "text"), {"call_id": None})
            speaker = _opt_str(item["speaker_id"], f"{cat}.speaker_id")
            if speaker is not None and speaker not in known_actors:
                raise _fail(f"{cat}.speaker_id", f"unknown actor {speaker!r}")
            author = _choice(item["author"], f"{cat}.author", AUTHORS)
            text = _str(item["text"], f"{cat}.text", allow_empty=True)
            call_id = _opt_str(item["call_id"], f"{cat}.call_id")
            if author == "player":
                if call_id is not None:
                    raise _fail(f"{cat}.call_id", "player contributions are not model calls")
                if not scenario.is_player(speaker):
                    raise _fail(f"{cat}.speaker_id", "a player contribution needs a player actor")
            else:
                if call_id is None:
                    raise _fail(f"{cat}.call_id", "a model contribution needs its call")
                call = by_id.get(call_id)
                if call is None:
                    raise _fail(f"{cat}.call_id", f"unknown call {call_id!r}")
                if call.turn != turn:
                    raise _fail(f"{cat}.call_id", f"call {call_id!r} belongs to another turn")
                if not call.stage.is_baseline:
                    raise _fail(f"{cat}.call_id",
                                "only an npc_generation call produces a contribution")
                if not call.completed:
                    raise _fail(f"{cat}.call_id", f"call {call_id!r} has no completed attempt")
                if speaker != call.actor_id:
                    raise _fail(f"{cat}.speaker_id",
                                f"must be {call.actor_id!r}, the actor of call {call_id!r}"
                                " (no scene-level narration in /2)")
            contributions.append(Contribution(speaker, author, text, call_id))  # type: ignore[arg-type]
        parsed_turns.append(Turn(turn, tuple(contributions)))

    raw_outcomes = data["choice_outcomes"]
    if not isinstance(raw_outcomes, dict):
        raise _fail(f"{loc}.choice_outcomes", "expected an object")
    choice_ids = {choice.choice_id for choice in scenario.choices}
    for choice_id in sorted(set(raw_outcomes) - choice_ids):
        raise _fail(f"{loc}.choice_outcomes.{choice_id}", "unknown choice")
    outcomes = {}
    for choice in scenario.choices:
        at = f"{loc}.choice_outcomes.{choice.choice_id}"
        if choice.choice_id not in raw_outcomes:
            raise _fail(at, "every scripted choice needs an outcome (or \"unlabelled\")")
        outcomes[choice.choice_id] = _choice(raw_outcomes[choice.choice_id], at, OUTCOMES_V2)

    raw_scores = data["judge_scores"]
    if not isinstance(raw_scores, dict):
        raise _fail(f"{loc}.judge_scores", "expected an object")
    judge_scores = {_str(name, f"{loc}.judge_scores"): _number(value, f"{loc}.judge_scores.{name}")
                    for name, value in raw_scores.items()}

    return RunV2(run_id, scenario_id, label, source, _frozen(conditions), policy,
                 tuple(parsed_turns), tuple(booked), _frozen(outcomes), _frozen(judge_scores),
                 plans, summaries, SessionUsage(scenario_id, summaries))


def _book(scenario: Scenario, policy: InferencePolicy, calls: list[_RawCall],
          turns: list[tuple[str, int, object]], players: set[str],
          ) -> tuple[tuple[TurnInferencePlan, ...], tuple[TurnUsageSummary, ...], list[CallV2]]:
    """Plan each turn from its declared calls and book every attempt to its ledger."""
    present = tuple(actor.actor_id for actor in scenario.actors)
    plans, summaries = [], []
    booked: dict[int, CallV2] = {}
    for _, turn, _ in turns:
        mine = [entry for entry in calls if entry.call.turn == turn]
        phases = list(dict.fromkeys(entry.call.phase for entry in mine)) or [DEFAULT_PHASE]
        _precheck(mine, policy)
        plan: TurnInferencePlan | None = None
        for phase in phases:
            in_phase = [entry.call for entry in mine if entry.call.phase == phase]
            speakers = [c.actor_id for c in in_phase if c.stage.is_baseline]
            requests = [OptionalRequest(c.stage, c.actor_id, c.purpose)
                        for c in in_phase if not c.stage.is_baseline]
            first = next((e for e in mine if e.call.phase == phase), None)
            at = f"run.calls[{first.index}]" if first else "run.turns"
            try:
                plan = (plan_turn(scenario.scenario_id, f"t{turn}", present_actor_ids=present,
                                  speaking_npc_ids=speakers, player_controlled_ids=players,
                                  optional_requests=requests, phase=phase, policy=policy)
                        if plan is None else
                        plan.add_phase(phase, speaking_npc_ids=speakers,
                                       optional_requests=requests))
            except (InferencePlanError, ValueError) as error:
                raise _fail(at, str(error)) from None
        assert plan is not None
        if plan.declined:
            raise _fail("run.calls", f"turn {turn} holds a call its policy declines")
        ledger = TurnUsageLedger(plan)
        used: dict[tuple, int] = {}
        for entry in mine:
            call = entry.call
            key = (call.phase, call.stage, call.actor_id)
            used[key] = used.get(key, 0) + 1
            planned = next(p for p in plan.calls if (p.call_id.phase, p.stage, p.actor_id) == key
                           and p.call_id.ordinal == used[key])
            attempts = []
            for aat, raw in entry.raw_attempts:
                try:
                    attempt = CallAttempt(planned.call_id, _int(raw["attempt"], f"{aat}.attempt", 1),
                                          _usage(raw["usage"], f"{aat}.usage"),
                                          Measurement(raw["measurement"]), raw["outcome"],
                                          _opt_str(raw["route"], f"{aat}.route"))
                    ledger = ledger.record(attempt)
                except BenchmarkFormatError:
                    raise
                except UnplannedCallError as error:
                    raise _fail(aat, str(error)) from None
                except ValueError as error:
                    message = str(error)
                    if "exceeds the limit" in message:
                        message += " (policy limit)"
                    raise _fail(aat, message) from None
                attempts.append(attempt)
            booked[entry.index] = CallV2(call.call_id, call.turn, call.phase, call.stage,
                                         call.actor_id, call.purpose, tuple(attempts),
                                         call.latency_ms, call.costs)
        plans.append(plan)
        summaries.append(ledger.summary())
    return tuple(plans), tuple(summaries), [booked[entry.index] for entry in calls]


def _precheck(mine: list[_RawCall], policy: InferencePolicy) -> None:
    """Locate the planner's refusals at the offending call, before planning the turn."""
    speakers: set[str] = set()
    optional: dict[InferenceStage, int] = {}
    for entry in mine:
        call, at = entry.call, f"run.calls[{entry.index}]"
        if call.stage.is_baseline:
            if call.actor_id in speakers:
                raise _fail(f"{at}.actor_id",
                            f"one generation per speaking NPC per turn: {call.actor_id!r}")
            speakers.add(call.actor_id)  # type: ignore[arg-type]
            continue
        cap = policy.cap_for(call.stage)
        if cap is None:
            raise _fail(f"{at}.stage", f"{call.stage} is disabled by the run's policy")
        optional[call.stage] = optional.get(call.stage, 0) + 1
        if optional[call.stage] > cap:
            raise _fail(f"{at}.stage", f"{call.stage} exceeds the policy's turn cap of {cap}")
