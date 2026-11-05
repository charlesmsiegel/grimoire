"""Hand a finished runtime turn to the story-quality recorder (``grimoire.evals.runtime_record``).

The recorder is pure and knows only the inference ledger; this unwraps the meter and outcome and
checks that they describe the same turn, so a recorded run cannot pair one turn's lines with
another turn's bill. Nothing is issued, persisted or committed.
"""

from __future__ import annotations

from collections.abc import Iterable

from grimoire.evals.runtime_record import RecordedLine, RecordedTurn
from grimoire.generation import GenerationError
from grimoire.providers.meter import TurnMeter

from .runtime import TurnOutcome


def recorded_turn(turn: int, meter: TurnMeter, outcome: TurnOutcome, *,
                  player_lines: Iterable[tuple[str, str]] = ()) -> RecordedTurn:
    """The recorder's view of one ``run_turn``: ``meter``'s ledger and ``outcome``'s lines.

    Refused (``GenerationError``): an outcome whose calls are not ``meter``'s planned calls in
    plan order, or whose lines are not exactly its accepted calls' (``outcome_mismatch``), and a
    meter with a call whose booking failed (``poisoned_meter``), since an attempt that was sent
    may be missing from its ledger and the run would understate the bill. A stale outcome keeps
    its calls but has no lines; recording it shows paid calls that delivered nothing.
    """
    if not isinstance(meter, TurnMeter):
        raise GenerationError("bad_meter", "recorded_turn needs the turn's TurnMeter")
    if not isinstance(outcome, TurnOutcome):
        raise GenerationError("bad_outcome", "recorded_turn needs the turn's TurnOutcome")
    plan = meter.ledger.plan
    if tuple(call.call_id for call in outcome.calls) != tuple(call.call_id for call in plan.calls):
        raise GenerationError("outcome_mismatch", "the outcome's calls are not this meter's planned calls")
    if meter.poisoned_call_ids:
        raise GenerationError("poisoned_meter", "a call's booking failed, so its ledger may miss an attempt")
    accepted = [call.call_id for call in outcome.calls if call.status == "accepted"]
    expected = [] if outcome.status == "stale" else accepted
    if [line.call_id for line in outcome.lines] != expected:
        raise GenerationError("outcome_mismatch", "the outcome's lines are not its accepted calls'")
    lines = tuple(RecordedLine(line.call_id, None if line.actor is None else line.actor.instance_id, line.text)
                  for line in outcome.lines)
    return RecordedTurn(turn, meter.ledger, lines, tuple(player_lines))
