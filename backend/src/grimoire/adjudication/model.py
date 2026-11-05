"""Fiction-first adjudication of a mechanics-free action (Sprint 029, first slice).

A labelled ``ActionFrame`` says what an actor tries, which established facts bear on it and what is at
stake. ``assess_action`` classifies it deterministically, with no dice and no model call: a
straightforward action succeeds, an impossible one fails, an uncertain one waits for a decision, and an
action that cannot be judged as labelled needs clarification. ``check_decision`` then refuses a decided
outcome that the frame does not support: narrator fiat is a result with no cause on record.

What makes the outcome credible is **order**: every cause (a ``Factor``) and every possible consequence
(a ``Stake``) is on record in the frame before the outcome is decided, and the decision may cite only
those. A factor must be a fact established strictly before the action's moment, so a difficulty cannot
be invented in the same breath as the ruling it would justify. The frame's labels come from a labeller
that is not the narrator, like 003's ``NarrationClaim`` and 025's ``AbilityAttempt``; the store-backed
service (``grimoire.adjudicationstore``) records them only through the adjudication channel.

An ability the action relies on is judged by 025's ``AbilityAssessment``, which the caller of this pure
module supplies and the service computes itself. Only ``permitted`` makes a power work.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from grimoire.core.ability_use import (
    APPROVABLE, CLASSIFICATIONS, DECLARED, AbilityAssessment, AbilityAttempt, Reason,
)
from grimoire.core.authority import Resolution
from grimoire.core.identity import TEXT_MAX, ChatRef, InstanceRef, validate_segment
from grimoire.core.knowledge import Moment

Bearing = Literal["favours", "hinders", "prevents"]
Result = Literal["success", "partial", "failure"]
Classification = Literal["straightforward", "uncertain", "impossible", "clarify"]
Basis = Literal["rule", "adjudicated", "consent"]

BEARINGS = frozenset({"favours", "hinders", "prevents"})
#: Worst first: a consent decision is a setback, below the best result the frame allows.
RESULTS = ("failure", "partial", "success")
BASES = frozenset({"rule", "adjudicated", "consent"})

#: Derived factor ids. ``#`` is not a segment character, so no labelled factor can take them.
ABILITY_FACTOR = "#ability"
ABILITY_UNCERTAIN = "#ability-uncertain"

PROSE_MAX = 4000
# A director's attempt (no player contribution) is named with this prefix, so it never shares an id
# with a player's action contribution: 003 binds narration to a result by attempt id alone.
DIRECTOR_ATTEMPT = "director:"
MAX_FACTORS = 32
MAX_STAKES = 16
#: 025 classifications that cannot be judged as labelled: the frame needs a ruling or new labels.
_CLARIFY = {"relabel": "ability_relabel", "needs_upgrade": "ability_needs_upgrade"}


class AdjudicationError(ValueError):
    """An invalid frame or decision, or a refused step; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str) -> AdjudicationError:
    return AdjudicationError(message, "invalid_field")


def _segment(value: object, label: str) -> None:
    if type(value) is not str:  # a str subclass could redefine equality
        raise _invalid(f"{label} must be plain text: {value!r}")
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise _invalid(str(error)) from None


def _text(value: object, label: str, limit: int) -> None:
    if type(value) is not str or not value.strip():
        raise _invalid(f"{label} must be nonempty plain text")
    if len(value) > limit:
        raise _invalid(f"{label} is longer than {limit} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate could never be stored
        raise _invalid(f"{label} is not storable text") from None


def _tuple(value: object, label: str) -> tuple:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise _invalid(f"{label} must be a collection: {value!r}")
    return tuple(value)


def _ids(value: object, label: str) -> frozenset[str]:
    entries = _tuple(value, label)
    for entry in entries:
        if type(entry) is not str:
            raise _invalid(f"{label} entries must be plain text: {entry!r}")
    return frozenset(entries)


# --- the frame -------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Factor:
    """An established fact that bears on the action: what makes it easier, harder or impossible.

    ``established`` is the campaign sequence at which the fact became part of the fiction; it must be
    earlier than the action. ``source`` names the canonical record it comes from (a 017 relation, a 020
    event or ruling, a 021 accepted event) as attribution: it is recorded, never read or verified here.
    ``text`` is for people and is never read.
    """

    factor_id: str
    bearing: Bearing
    text: str
    established: int
    source: str

    def __post_init__(self) -> None:
        _segment(self.factor_id, "factor_id")
        if type(self.bearing) is not str or self.bearing not in BEARINGS:
            raise _invalid(f"bearing must be one of {sorted(BEARINGS)}: {self.bearing!r}")
        _text(self.text, "factor text", PROSE_MAX)
        if type(self.established) is not int or self.established < 0:
            raise _invalid("established must be a non-negative int sequence")
        _text(self.source, "factor source", TEXT_MAX)


@dataclass(frozen=True, slots=True)
class Stake:
    """A consequence a failure (or a success at a cost) may bring, declared before the outcome."""

    stake_id: str
    text: str

    def __post_init__(self) -> None:
        _segment(self.stake_id, "stake_id")
        _text(self.text, "stake text", PROSE_MAX)


@dataclass(frozen=True, slots=True)
class ActionFrame:
    """One labelled action: who tries what, at which moment, against which facts, risking what.

    ``attempt_id`` identifies the action in its campaign; for a player-controlled actor it is the id of
    the player's own action contribution, as 003's ``Resolution.attempt_id`` and 025's attempt are.
    ``ability`` labels the power the action relies on, if any: the same attempt id and actor.
    ``intent`` is prose for people; a player's own words stay in their contribution. ``provenance`` is
    attribution (which labeller), never authority.
    """

    attempt_id: str
    actor: InstanceRef
    chat: ChatRef
    moment: Moment
    intent: str
    factors: tuple[Factor, ...]
    stakes: tuple[Stake, ...]
    provenance: str
    ability: AbilityAttempt | None = None

    def __post_init__(self) -> None:
        _text(self.attempt_id, "attempt_id", TEXT_MAX)
        if type(self.actor) is not InstanceRef or type(self.chat) is not ChatRef:
            raise _invalid("an action names an exact InstanceRef actor and ChatRef chat")
        if self.chat.campaign_id != self.actor.campaign_id:
            raise AdjudicationError("the chat belongs to another campaign", "campaign_mismatch")
        if type(self.moment) is not Moment or self.moment.scene_id is None or self.moment.turn_id is None:
            raise _invalid("an action's moment needs a scene_id and a turn_id")
        for label in ("scene_id", "turn_id"):
            _segment(getattr(self.moment, label), label)
        _text(self.intent, "intent", PROSE_MAX)
        _text(self.provenance, "provenance", TEXT_MAX)
        factors = tuple(sorted(_tuple(self.factors, "factors"), key=lambda f: getattr(f, "factor_id", "")))
        stakes = tuple(sorted(_tuple(self.stakes, "stakes"), key=lambda s: getattr(s, "stake_id", "")))
        if not all(type(f) is Factor for f in factors) or not all(type(s) is Stake for s in stakes):
            raise _invalid("factors and stakes must be Factor and Stake values")
        if len(factors) > MAX_FACTORS or len(stakes) > MAX_STAKES:
            raise _invalid(f"at most {MAX_FACTORS} factors and {MAX_STAKES} stakes")
        if len({f.factor_id for f in factors}) != len(factors):
            raise AdjudicationError("two factors share an id", "duplicate_factor")
        if len({s.stake_id for s in stakes}) != len(stakes):
            raise AdjudicationError("two stakes share an id", "duplicate_stake")
        late = sorted(f.factor_id for f in factors if f.established >= self.moment.sequence)
        if late:
            raise AdjudicationError(f"factors not established before the action: {', '.join(late)}",
                                    "unestablished_factor")
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "stakes", stakes)
        if self.ability is not None:
            if type(self.ability) is not AbilityAttempt:
                raise _invalid("ability must be an AbilityAttempt")
            if self.ability.attempt_id != self.attempt_id or self.ability.actor != self.actor:
                raise AdjudicationError("the ability attempt is another action's", "ability_mismatch")

    @property
    def campaign_id(self) -> str:
        return self.actor.campaign_id


# --- assessment ------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AbilityFinding:
    """What an action's assessment takes from 025's ``AbilityAssessment`` of the power it relies on.

    It is also what a decision records, so a replay re-checks the decision exactly as it was made and a
    later use can tell whether the assessment still holds. ``charges`` are ``(ability id, cost id, cost
    text)``: the text is what the price means, so a cost reworded under the same id no longer matches.
    """

    ability_id: str
    classification: str
    approval: str
    ruling_id: str | None
    uncertain: bool
    charges: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        for label in ("ability_id", "classification", "approval"):
            if type(getattr(self, label)) is not str:
                raise _invalid(f"{label} must be plain text")
        if self.ruling_id is not None and type(self.ruling_id) is not str:
            raise _invalid("ruling_id must be plain text or None")
        if type(self.uncertain) is not bool:
            raise _invalid("uncertain must be a bool")
        charges = _tuple(self.charges, "charges")
        if not all(type(charge) is tuple and len(charge) == 3 and all(type(part) is str for part in charge)
                   for charge in charges):
            raise _invalid("charges must be (ability id, cost id, cost text) triples")
        object.__setattr__(self, "charges", tuple(sorted(charges)))
        self._check_consistent()

    def _check_consistent(self) -> None:
        """The combinations 025's ``assess_attempt`` can produce, so a forged finding cannot claim a power."""
        classification, approval = self.classification, self.approval
        if classification not in CLASSIFICATIONS:
            raise _invalid(f"classification must be one of 025's: {classification!r}")
        if classification in DECLARED:
            expected = {"not_needed"}
        elif classification in APPROVABLE:
            expected = {"pending", "approved", "refused"}
        else:
            expected = {"not_approvable"}
        if approval not in expected:
            raise _invalid(f"a {classification} has approval {' or '.join(sorted(expected))}, not {approval!r}")
        if (self.ruling_id is not None) != (approval in ("approved", "refused")):
            raise _invalid("a finding names a ruling exactly when a ruling approved or refused it")
        approved = approval == "approved"
        if self.uncertain != (classification == "attempted_feat" or (classification in APPROVABLE and approved)):
            raise _invalid(f"a {classification} with approval {approval!r} has uncertain={not self.uncertain}")
        charged = classification in DECLARED or (classification in ("extrapolation", "combination") and approved)
        if self.charges and not charged:
            raise _invalid(f"a {classification} with approval {approval!r} charges nothing")

    @classmethod
    def of(cls, assessment: AbilityAssessment) -> AbilityFinding:
        if type(assessment) is not AbilityAssessment:
            raise TypeError(f"expected an AbilityAssessment, not {type(assessment).__name__}")
        return cls(assessment.attempt.ability_id, assessment.classification, assessment.approval,
                   assessment.ruling_id, assessment.uncertain,
                   tuple((ability, cost.id, cost.text) for ability, cost in assessment.charges))

    @property
    def permitted(self) -> bool:
        """025's ``permitted``: a declared use, or one a recorded ruling approved."""
        return self.classification in DECLARED or self.approval == "approved"

    @property
    def needs_approval(self) -> bool:
        return self.approval == "pending"


@dataclass(frozen=True, slots=True)
class ActionAssessment:
    """What kind of action this is and which results it can credibly have. Never an outcome."""

    frame: ActionFrame
    classification: Classification
    #: results a ``rule`` or ``adjudicated`` decision may record, worst first; empty for ``clarify``
    allowed: tuple[Result, ...]
    #: the frame's factors plus the derived ability factors, by id
    factors: Mapping[str, Factor | DerivedFactor]
    ability: AbilityFinding | None
    reasons: tuple[Reason, ...]

    def bearing(self, factor_id: str) -> str | None:
        factor = self.factors.get(factor_id)
        return None if factor is None else factor.bearing


@dataclass(frozen=True, slots=True)
class DerivedFactor:
    """A factor the assessment derives from the ability assessment: ``#ability`` or ``#ability-uncertain``."""

    factor_id: str
    bearing: Bearing
    text: str


def _ability_factors(ability: AbilityFinding) -> tuple[list[DerivedFactor], list[Reason]]:
    """The power as factors: works (favours), may fail this time (hinders) or does not do this (prevents)."""
    name = ability.ability_id
    if ability.permitted:
        factors = [DerivedFactor(ABILITY_FACTOR, "favours", f"{name} can do this")]
        reasons = [Reason("ability_permitted", f"{name} permits this use ({ability.classification})")]
        if ability.uncertain:
            factors.append(DerivedFactor(ABILITY_UNCERTAIN, "hinders", f"{name} may not work this time"))
            reasons.append(Reason("ability_uncertain", f"{name} is not sure to work this time"))
        return factors, reasons
    return ([DerivedFactor(ABILITY_FACTOR, "prevents", f"{name} cannot do this")],
            [Reason("ability_prevents", f"{name} does not permit this use ({ability.classification}, "
                                        f"{ability.approval})")])


def assess_action(frame: ActionFrame,
                  ability: AbilityAssessment | AbilityFinding | None = None) -> ActionAssessment:
    """Classify ``frame``, given the 025 assessment of the ability it relies on (``None`` if none).

    A recorded ``AbilityFinding`` classifies exactly as the assessment it was taken from.

    First match: an ability use that cannot be judged as labelled (a pending ruling, a relabel, a newer
    build) is ``clarify``; anything that prevents the action is ``impossible`` (failure only); nothing
    hindering it is ``straightforward`` (success only), and so is a hindered action with nothing at
    stake, since failing it would change nothing; otherwise it is ``uncertain``: it may fail, and it may
    succeed (fully or at a cost) only if something favours it.
    """
    if type(frame) is not ActionFrame:
        raise TypeError(f"expected an ActionFrame, not {type(frame).__name__}")
    if (ability is None) != (frame.ability is None):
        raise AdjudicationError("an ability assessment is given exactly when the frame names an ability",
                                "ability_mismatch")
    if type(ability) is AbilityAssessment:
        if ability.attempt != frame.ability:
            raise AdjudicationError("the ability assessment is of another attempt", "ability_mismatch")
        ability = AbilityFinding.of(ability)
    elif ability is not None:
        named = frame.ability.ability_id  # type: ignore[union-attr]
        if type(ability) is not AbilityFinding or ability.ability_id != named:
            raise AdjudicationError("the ability finding is of another ability", "ability_mismatch")
    factors: dict[str, Factor | DerivedFactor] = {f.factor_id: f for f in frame.factors}
    reasons: list[Reason] = []
    if ability is not None:
        clarify = _CLARIFY.get(ability.classification)
        if ability.needs_approval or clarify is not None:
            code = clarify or "ability_ruling_needed"
            text = (f"{ability.ability_id}: {ability.classification} needs a director's ruling first"
                    if clarify is None else f"{ability.ability_id}: {ability.classification}")
            return ActionAssessment(frame, "clarify", (), factors, ability, (Reason(code, text),))
        derived, why = _ability_factors(ability)
        factors.update((factor.factor_id, factor) for factor in derived)
        reasons.extend(why)
    prevents = sorted(i for i, f in factors.items() if f.bearing == "prevents")
    hinders = sorted(i for i, f in factors.items() if f.bearing == "hinders")
    favours = sorted(i for i, f in factors.items() if f.bearing == "favours")
    if prevents:
        reasons.append(Reason("prevented", f"prevented by {', '.join(prevents)}"))
        return ActionAssessment(frame, "impossible", ("failure",), factors, ability, tuple(reasons))
    if not hinders:
        reasons.append(Reason("unopposed", "nothing established stands in the way"))
        return ActionAssessment(frame, "straightforward", ("success",), factors, ability, tuple(reasons))
    if not frame.stakes:
        reasons.append(Reason("nothing_at_stake", f"hindered by {', '.join(hinders)}, but failing would "
                                                  "change nothing"))
        return ActionAssessment(frame, "straightforward", ("success",), factors, ability, tuple(reasons))
    reasons.append(Reason("hindered", f"hindered by {', '.join(hinders)}"))
    if favours:
        reasons.append(Reason("favoured", f"favoured by {', '.join(favours)}"))
        allowed: tuple[Result, ...] = RESULTS
    else:
        reasons.append(Reason("nothing_favours", "nothing established favours it, so it can only fail"))
        allowed = ("failure",)
    return ActionAssessment(frame, "uncertain", allowed, factors, ability, tuple(reasons))


# --- decisions -------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Decision:
    """The authoritative result of one assessed action: what happened, on what basis and why.

    ``cites`` names the factors (labelled or derived) that cause the result; ``consequences`` names the
    declared stakes that now come true. Dramatic framing is narration, never part of a decision.
    """

    result: Result
    basis: Basis
    cites: frozenset[str] = frozenset()
    consequences: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if type(self.result) is not str or self.result not in RESULTS:
            raise _invalid(f"result must be one of {list(RESULTS)}: {self.result!r}")
        if type(self.basis) is not str or self.basis not in BASES:
            raise _invalid(f"basis must be one of {sorted(BASES)}: {self.basis!r}")
        cites = _ids(self.cites, "cites")
        consequences = _ids(self.consequences, "consequences")
        if len(cites) > MAX_FACTORS + 2 or len(consequences) > MAX_STAKES:
            raise _invalid("too many cites or consequences")
        object.__setattr__(self, "cites", cites)
        object.__setattr__(self, "consequences", consequences)


def _refuse(message: str, reason: str) -> AdjudicationError:
    return AdjudicationError(message, reason)


def check_decision(assessment: ActionAssessment, decision: Decision) -> None:
    """Refuse a decision the assessment does not support; return ``None`` when it stands.

    - ``needs_clarification``: the action cannot be judged as labelled.
    - ``unknown_factor`` / ``undeclared_consequence``: a cause or consequence that is not on record.
    - ``rule_mismatch``: a ``rule`` decision on an uncertain action, or one other than the forced result.
    - ``not_uncertain``: an ``adjudicated`` decision where the rules already fix the result.
    - ``result_not_allowed``: a result the frame cannot support. ``consent`` chooses a setback: failure,
      or a result worse than the best the frame allows (never the success of a risky action).
    - ``uncited_result``: a result without a cited factor of its direction (success and a partial
      success need a favouring one; failure and a partial success need a hindering or preventing one).
    - ``success_with_consequence`` / ``partial_without_cost``.
    """
    if type(assessment) is not ActionAssessment or type(decision) is not Decision:
        raise TypeError("check_decision takes an ActionAssessment and a Decision")
    if assessment.classification == "clarify":
        raise _refuse("the action needs clarification before it can be decided", "needs_clarification")
    unknown = sorted(decision.cites - set(assessment.factors))
    if unknown:
        raise _refuse(f"cites factors not on record: {', '.join(unknown)}", "unknown_factor")
    declared = {stake.stake_id for stake in assessment.frame.stakes}
    undeclared = sorted(decision.consequences - declared)
    if undeclared:
        raise _refuse(f"consequences not declared at stake: {', '.join(undeclared)}", "undeclared_consequence")
    if decision.result == "success" and decision.consequences:
        raise _refuse("a full success brings none of the stakes", "success_with_consequence")
    if decision.result == "partial" and not decision.consequences:
        raise _refuse("a success at a cost names the cost among the stakes", "partial_without_cost")
    best = assessment.allowed[-1]
    if decision.basis == "consent":
        # The controller's own authorial choice for their actor: a setback the rules would not force.
        # Never the best result the frame allows, so consent cannot turn a risk into a success.
        if decision.result != "failure" and RESULTS.index(decision.result) >= RESULTS.index(best):
            raise _refuse(f"consent chooses a worse result than {best}", "result_not_allowed")
        return
    if decision.basis == "rule":
        if assessment.classification == "uncertain" or decision.result != best:
            raise _refuse(f"a {assessment.classification} action's result is fixed: {best}", "rule_mismatch")
    elif assessment.classification != "uncertain":
        raise _refuse(f"a {assessment.classification} action is decided by rule, not adjudicated", "not_uncertain")
    elif decision.result not in assessment.allowed:
        raise _refuse(f"{decision.result} is not supported: allowed {', '.join(assessment.allowed)}",
                      "result_not_allowed")
    bearings = {assessment.bearing(factor_id) for factor_id in decision.cites}
    needs_favour = decision.result in ("success", "partial") and assessment.classification == "uncertain"
    needs_hindrance = decision.result in ("failure", "partial")
    if (needs_favour and "favours" not in bearings) or (needs_hindrance and not bearings & {"hinders", "prevents"}):
        raise _refuse(f"a {decision.result} must cite a factor that causes it", "uncited_result")


@dataclass(frozen=True, slots=True)
class Outcome:
    """A recorded decision on one action: final in this slice, and the same for every narration retry."""

    frame: ActionFrame
    classification: Classification
    decision: Decision
    #: the director key's principal that recorded it, and the consenting controller for ``consent``
    decided_by: str
    consented_by: str | None = None

    def resolution(self) -> Resolution:
        """The 003 resolution: narration of this attempt's outcome must agree with it."""
        return Resolution(self.frame.campaign_id, self.frame.attempt_id, self.decision.result)
