"""Hand a finished runtime turn to the story-quality recorder (``grimoire.evals.runtime_record``).

The recorder is pure and knows only the inference ledger; this unwraps the meter and outcome and
checks that they describe the same run of the same turn, so a recorded run cannot pair one turn's (or
one swipe's) lines with another's bill. Nothing is issued, persisted or committed.
"""

from __future__ import annotations

from collections.abc import Iterable

from grimoire.core.inference import InferenceStage
from grimoire.evals.runtime_record import RecordedLine, RecordedTurn
from grimoire.generation import GenerationError
from grimoire.providers.meter import TurnMeter

from .runtime import TurnOutcome


#: The meter statuses each runtime call outcome can come from. ``rejected`` is a reply the provider
#: delivered and the screen refused; ``failed`` may have stopped before the meter saw the call.
_METER_STATUS: dict[str, frozenset[str | None]] = {
    "accepted": frozenset({"accepted"}), "rejected": frozenset({"accepted"}),
    "incomplete": frozenset({"incomplete"}), "failed": frozenset({"failed", None}),
    "cancelled": frozenset({"cancelled"}), "refused": frozenset({"refused"}),
    "not_issued": frozenset({None}),
}


def recorded_turn(turn: int, meter: TurnMeter, outcome: TurnOutcome, *,
                  player_lines: Iterable[tuple[str, str]] = ()) -> RecordedTurn:
    """The recorder's view of one ``run_turn``: ``meter``'s ledger and ``outcome``'s lines.

    Refused (``GenerationError``) with ``outcome_mismatch``: an outcome ``run_turn`` did not
    return for this very ``meter`` (another swipe of the same plan has the same call ids but its own
    bill), whose calls are not ``meter``'s planned calls in plan order (decision calls aside, which
    ``decide`` runs before the turn and which speak no line), whose call outcomes disagree with
    ``meter``'s call statuses (a call run again by hand after the turn, say), or whose lines are not
    exactly its accepted calls'. Refused with ``poisoned_meter``: a meter with
    a call whose booking failed, since an attempt that was sent may be missing from its ledger and
    the run would understate the bill. A stale outcome keeps its calls but has no lines; recording
    it shows paid calls that delivered nothing.
    """
    if not isinstance(meter, TurnMeter):
        raise GenerationError("bad_meter", "recorded_turn needs the turn's TurnMeter")
    if not isinstance(outcome, TurnOutcome):
        raise GenerationError("bad_outcome", "recorded_turn needs the turn's TurnOutcome")
    if outcome.meter_token is None or outcome.meter_token != meter.token:
        raise GenerationError("outcome_mismatch", "the outcome was not produced on this meter")
    plan = meter.ledger.plan
    # 045: a decision call is decided before run_turn and speaks no line, so the outcome lists only
    # the speaking calls; its attempts stay in the ledger the record bills.
    speaking = tuple(call.call_id for call in plan.calls if call.stage is not InferenceStage.DECISION)
    if tuple(call.call_id for call in outcome.calls) != speaking:
        raise GenerationError("outcome_mismatch", "the outcome's calls are not this meter's planned calls")
    if meter.poisoned_call_ids:
        raise GenerationError("poisoned_meter", "a call's booking failed, so its ledger may miss an attempt")
    for call in outcome.calls:
        if meter.status(call.call_id) not in _METER_STATUS[call.status]:
            raise GenerationError("outcome_mismatch",
                                  f"{call.call_id} is {call.status} in the outcome but not on the meter")
    accepted = [call.call_id for call in outcome.calls if call.status == "accepted"]
    expected = [] if outcome.status == "stale" else accepted
    if [line.call_id for line in outcome.lines] != expected:
        raise GenerationError("outcome_mismatch", "the outcome's lines are not its accepted calls'")
    lines = tuple(RecordedLine(line.call_id, None if line.actor is None else line.actor.instance_id, line.text)
                  for line in outcome.lines)
    return RecordedTurn(turn, meter.ledger, lines, tuple(player_lines))
