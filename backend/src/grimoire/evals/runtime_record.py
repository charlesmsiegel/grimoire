"""Record 027 generation turns as a ``grimoire.story-quality/3`` run of source ``v2-runtime``.

Pure: it reads each turn's usage ledger (``core.inference``) and the lines the runtime accepted,
never a provider, file or clock. ``grimoire.runtime.record.recorded_turn`` unwraps a ``TurnMeter`` and
``TurnOutcome`` into a ``RecordedTurn``.

Call attribution is exact because it is copied, not inferred: the run's calls are the turn
plan's planned calls, in plan order, each with every attempt its ledger booked (failed and
unsent ones included). A speaking NPC's call is ``npc_generation`` with its actor; a director
reply is the one ``director_generation`` call, with no actor. A planned call that never ran
stays in the run with no attempts, so the gate reports it as unmetered, never free. A call with
a completed attempt but no line (a screened-out reply, a stale run) stays too, and the gate
reports it as an orphan. Latency is recorded as unreported: the meter times attempts, not the
whole call with its backoff, and the format never takes a partial figure for the total.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.core.inference import TOKEN_FIELDS, CallId, PlannedCall, TurnUsageLedger

from .format import Scenario
from .format3 import FORMAT_V3, parse_run_v3

#: The harness named by a run this module records.
RUNTIME_HARNESS = "grimoire.runtime/1"


class RuntimeRecordError(ValueError):
    """The turns cannot be one ``/3`` run; ``code`` says why (ids only, never text)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class RecordedLine:
    """One accepted line: its planned call, its actor (``None`` for the director) and its text."""

    call_id: CallId
    actor_id: str | None
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RecordedTurn:
    """One generation turn: its run turn number, its ledger and lines, and the player's input.

    ``player_lines`` are ``(actor_id, text)`` pairs, recorded before the model lines because the
    player spoke first. The runtime does not see them; the caller supplies what was played.
    """

    turn: int
    ledger: TurnUsageLedger
    lines: tuple[RecordedLine, ...] = ()
    player_lines: tuple[tuple[str, str], ...] = ()


def _call_key(turn: int, call: PlannedCall) -> str:
    cid = call.call_id
    return f"t{turn}:{cid.phase}:{cid.stage.value}:{cid.actor_id or 'scene'}:{cid.ordinal}"


def _usage(usage) -> dict:
    return {name: getattr(usage, name) for name in TOKEN_FIELDS}


def _turn(recorded: RecordedTurn) -> tuple[dict, list[dict]]:
    plan = recorded.ledger.plan
    planned = {call.call_id: call for call in plan.calls}
    keys = {call.call_id: _call_key(recorded.turn, call) for call in plan.calls}
    calls = []
    for call in plan.calls:
        calls.append({
            "call_id": keys[call.call_id], "turn": recorded.turn, "phase": call.call_id.phase,
            "stage": call.stage.value, "actor_id": call.actor_id, "purpose": call.purpose,
            "latency_ms": None,
            "attempts": [{"attempt": each.attempt, "outcome": each.outcome,
                          "measurement": each.measurement.value, "route": each.route,
                          "usage": _usage(each.usage)}
                         for each in recorded.ledger.attempts if each.call_id == call.call_id],
        })
    contributions: list[dict] = [{"speaker_id": actor_id, "author": "player", "text": text}
                                 for actor_id, text in recorded.player_lines]
    spoken: set[CallId] = set()
    for line in recorded.lines:
        call = planned.get(line.call_id)
        if call is None:
            raise RuntimeRecordError("unplanned_line",
                                     f"turn {recorded.turn}: a line names no planned call")
        if line.actor_id != call.actor_id:
            raise RuntimeRecordError("speaker_mismatch",
                                     f"turn {recorded.turn}: {keys[call.call_id]} speaks for "
                                     f"{call.actor_id or 'the scene'}, not {line.actor_id}")
        if call.call_id in spoken:
            raise RuntimeRecordError("duplicate_line",
                                     f"turn {recorded.turn}: {keys[call.call_id]} has two lines")
        spoken.add(call.call_id)
        contributions.append({"speaker_id": line.actor_id, "author": "model", "text": line.text,
                              "call_id": keys[call.call_id]})
    return {"turn": recorded.turn, "contributions": contributions}, calls


def record_runtime_run(scenario: Scenario, turns: Iterable[RecordedTurn], *, run_id: str,
                       label: str, conditions: Mapping[str, str],
                       harness: str = RUNTIME_HARNESS, route: str | None = None,
                       choice_outcomes: Mapping[str, str] | None = None) -> dict:
    """The ``/3`` run dict for ``turns``, already validated by ``parse_run_v3``.

    Every turn must share one campaign and one inference policy: a run has one policy, so a
    director reply enabled on one turn is enabled, and visible to the gate, on all of them. Each
    turn's ledger is a distinct plan turn (``duplicate_turn`` otherwise), so no bill counts twice.
    ``choice_outcomes`` defaults to ``unlabelled`` for every scripted choice, so the gate judges
    them on marker evidence only. A malformed result raises ``BenchmarkFormatError``.
    """
    turns = tuple(turns)
    if not turns or not all(isinstance(turn, RecordedTurn) for turn in turns):
        raise RuntimeRecordError("bad_turns", "turns must be a nonempty sequence of RecordedTurn")
    first = turns[0].ledger.plan
    seen_turn_ids: set[str] = set()
    for recorded in turns:
        # One turn's bill recorded twice (re-sent, or renumbered) would double the run's cost. A
        # ledger carries its plan, so the same ledger twice repeats its plan's turn id too.
        if recorded.ledger.plan.turn_id in seen_turn_ids:
            raise RuntimeRecordError("duplicate_turn",
                                     f"turn {recorded.turn} repeats an earlier turn's ledger")
        seen_turn_ids.add(recorded.ledger.plan.turn_id)
    for recorded in turns[1:]:
        plan = recorded.ledger.plan
        if plan.campaign_id != first.campaign_id:
            raise RuntimeRecordError("campaign_mismatch",
                                     f"turn {recorded.turn} belongs to another campaign")
        if plan.policy != first.policy:
            raise RuntimeRecordError("policy_mismatch",
                                     f"turn {recorded.turn} was planned under another policy")
    run_turns, run_calls = [], []
    for recorded in turns:
        turn, calls = _turn(recorded)
        run_turns.append(turn)
        run_calls.extend(calls)
    policy = first.policy
    caps = {stage.value: cap for stage, cap in policy.per_turn_call_caps}
    document = {
        "format": FORMAT_V3,
        "run_id": run_id,
        "scenario_id": scenario.scenario_id,
        "label": label,
        "source": {"kind": "v2-runtime", "harness": harness, "route": route},
        "conditions": dict(conditions),
        "policy": {"per_turn_call_caps": caps, "max_attempts_per_call": policy.max_attempts_per_call},
        "turns": run_turns,
        "calls": run_calls,
        "choice_outcomes": (dict(choice_outcomes) if choice_outcomes is not None
                            else {choice.choice_id: "unlabelled" for choice in scenario.choices}),
    }
    parse_run_v3(document, scenario)
    return document
