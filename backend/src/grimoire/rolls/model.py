"""Recorded mechanical rolls (Sprint 075, first slice): dice drawn once, and the result they give.

062's ``grimoire.rulesets`` checks a supplied action/result pair and never rolls. This module adds the
missing step between them: which dice an action needs (``dice_for``), and the one result a set of faces
gives under the action's reference profile (``resolve``). A ``RecordedRoll`` holds the faces, never a
result of its own: the result is derived from them, so a record cannot say a failed roll succeeded.

The result binds narration through 003: ``RecordedRoll.resolution()`` is a ``Resolution`` whose result
token (``success``, ``partial`` or ``failure``) is the vocabulary 029's outcomes use, so
``review_narration`` rejects a narration retry that tells the roll otherwise (``contradicts_resolution``).
Drawing and recording are the store adapter's (``grimoire.rollstore``); nothing here draws or writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.authority import PlayerContribution, Resolution
from grimoire.rulesets import (
    ActionRequest, ArsStudy, D20Check, D20Result, PbtaMove, PbtaResult, WodResult, WodTest,
)
from grimoire.rulesets.families import FamilyAction, FamilyResult, check_result

#: A roll no player contribution backs binds narration under this prefix (029's convention), so it can
#: never share an attempt id with a player's action contribution: 003 binds a result by attempt id alone.
DIRECTOR_ROLL = "director:"

#: A PbtA partial result's cost or choice is the director's to offer, and the player's to take. The roll
#: only says one is owed; it never picks one (062: a partial keeps its choice unresolved).
PARTIAL_CHOICE = "Unresolved: the director offers a hard bargain, a worse outcome or an ugly choice."

#: How each profile's outcome reads as a 003 result token, shared with 029's decisions.
_TOKENS = {
    "success": "success", "critical": "success", "messy-critical": "success",
    "failure": "failure", "bestial-failure": "failure",
    "strong": "success", "partial": "partial", "miss": "failure",
}


class RollError(ValueError):
    """A refused roll, or a record that cannot be one; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def dice_for(action: FamilyAction) -> tuple[int, ...]:
    """The sides of each die ``action`` needs, in the order its faces are recorded.

    A WoD pool lists its normal dice first and its Hunger dice last. An Ars season of study rolls no die
    (``no_roll``): its result is arithmetic over the student's record, and 074 owns the elapsed season.
    """
    if type(action) is D20Check:
        return (20,)
    if type(action) is WodTest:
        return (10,) * action.pool
    if type(action) is PbtaMove:
        return (6, 6)
    if type(action) is ArsStudy:
        raise RollError("an Ars season of study rolls no die", "no_roll")
    raise RollError("not a rules-family action", "invalid_field")


def resolve(action: FamilyAction, faces: tuple[int, ...]) -> FamilyResult:
    """The one result ``faces`` give for ``action`` under its 062 profile; impossible faces are refused."""
    sides = dice_for(action)
    if type(faces) is not tuple or len(faces) != len(sides) or any(
            type(face) is not int or not 1 <= face <= side for face, side in zip(faces, sides)):
        raise RollError(f"the action needs {len(sides)} dice of {sorted(set(sides))} sides", "invalid_faces")
    if type(action) is D20Check:
        total = faces[0] + action.modifier
        result: FamilyResult = D20Result(faces[0], total, total >= action.dc)
    elif type(action) is WodTest:
        split = action.pool - action.hunger
        normal, hunger = faces[:split], faces[split:]
        successes = sum(face >= 6 for face in faces) + 2 * (faces.count(10) // 2)
        if successes >= action.difficulty:
            outcome = ("messy-critical" if 10 in hunger else "critical") if faces.count(10) >= 2 else "success"
        else:
            outcome = "bestial-failure" if 1 in hunger else "failure"
        result = WodResult(normal, hunger, successes, outcome)  # type: ignore[arg-type]
    else:
        total = sum(faces) + action.modifier  # type: ignore[union-attr]
        band = "strong" if total >= 10 else "partial" if total >= 7 else "miss"
        result = PbtaResult((faces[0], faces[1]), total, band,  # type: ignore[arg-type]
                            PARTIAL_CHOICE if band == "partial" else None)
    check_result(action, result)  # 062's own profile check: the two must never disagree
    return result


def result_token(result: FamilyResult) -> str:
    """The 003 result a family result binds narration to: ``success``, ``partial`` or ``failure``."""
    if type(result) is D20Result:
        return "success" if result.success else "failure"
    if type(result) in (WodResult, PbtaResult):
        return _TOKENS[result.outcome]  # type: ignore[union-attr]
    raise RollError("only a rolled result binds narration", "no_roll")


def check_backing(request: ActionRequest, contribution: PlayerContribution) -> None:
    """A player's own action contribution backs exactly this request: same campaign, actor and id."""
    if type(contribution) is not PlayerContribution:
        raise RollError("a roll is backed by a PlayerContribution", "invalid_field")
    actor = request.actor
    if (contribution.campaign_id != actor.campaign_id or contribution.contribution_id != request.request_id
            or contribution.actor_id != actor.instance_id or contribution.kind != "action"):
        raise RollError("the contribution is not this actor's action under this request id", "invalid_expression")


@dataclass(frozen=True)
class RecordedRoll:
    """A roll on record: the request, the contribution behind it, the faces drawn and who recorded it.

    ``result`` is derived from ``faces`` at construction; it is never read from anywhere else.
    """

    request: ActionRequest
    contribution: PlayerContribution | None
    faces: tuple[int, ...]
    recorded_by: str

    def __post_init__(self) -> None:
        if type(self.request) is not ActionRequest:
            raise RollError("a roll needs a complete ActionRequest", "invalid_field")
        if self.contribution is not None:
            check_backing(self.request, self.contribution)
        object.__setattr__(self, "_result", resolve(self.request.action, self.faces))

    @property
    def result(self) -> FamilyResult:
        return self._result  # type: ignore[attr-defined]

    @property
    def campaign_id(self) -> str:
        return self.request.actor.campaign_id

    @property
    def attempt_id(self) -> str:
        """The attempt narration names: the player's contribution id, or ``director:<request id>``."""
        rid = self.request.request_id
        return rid if self.contribution is not None else f"{DIRECTOR_ROLL}{rid}"

    @property
    def outcome(self) -> str:
        return result_token(self.result)

    def resolution(self) -> Resolution:
        return Resolution(self.campaign_id, self.attempt_id, self.outcome)
