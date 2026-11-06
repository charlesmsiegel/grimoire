"""Open obstacles (Sprint 040, first slice): a problem with stakes and several credible answers.

An ``Obstacle`` states a problem, never its solution: the goal on the far side, the actors in the way,
the established facts that bear on it (constraints, clues, resources, motives), what is at stake and
how failure escalates, and a few **suggested** approaches with the risks they run and the price a
success costs. It records no result and no required approach. Attempts against it are 029 action
frames, and outcomes are 029 decisions: this package only says which facts bear on an attempt.

Player choice is the guarantee, in three rules:

1. **An approach is never refused for being unlisted.** A novel approach (one no author foresaw) is
   framed like any other; the suggestions are offers, not a menu.
2. **The obstacle cannot shut out a novel approach.** Its facts may favour or hinder an unlisted
   approach, never prevent it, and no fact may prevent every approach (``blanket_prevention``). An
   obstacle with fewer than two viable suggestions is a railroad (``railroaded``).
3. **The author's facts bind the labeller.** A frame against a suggested approach carries exactly the
   facts the author said bear on it, with the author's bearing, and exactly its declared risks, so
   difficulty is the author's, set before the player chose, not the labeller's in the moment.

Failure matters: a stake that comes true may **escalate**, becoming a new fact that bears on later
attempts (the guard who refused a bribe is now wary). A success or a success at a cost overcomes the
obstacle and brings the chosen approach's costs (the purse that bought the guard, the favour now owed).
``derive`` replays an obstacle's attempts against their 029 outcomes; it never decides one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from grimoire.adjudication import ActionFrame, AdjudicationError, Factor, Outcome, Stake
from grimoire.adjudication.model import PROSE_MAX
from grimoire.core.identity import TEXT_MAX, InstanceRef, validate_segment

Bearing = Literal["favours", "hinders", "prevents"]

BEARINGS = frozenset({"favours", "hinders", "prevents"})
#: A bearing on every approach the fact does not name, including a novel one. It can never prevent.
ANY = "*"
FACT_KINDS = frozenset({"constraint", "clue", "resource", "motive"})
#: The kind of a fact a stake becomes when it comes true and escalates.
ESCALATION = "escalation"
#: An obstacle needs this many suggested approaches that nothing it holds prevents.
MIN_VIABLE = 2
# Bounded so that every fact and escalation that may bear on one attempt (16 + 8) still leaves a 029
# frame (32 factors) room for the labeller's own, such as the actor's training.
MAX_FACTS = 16
MAX_STAKES = 8
MAX_APPROACHES = 8
MAX_COSTS = 4
MAX_ACTORS = 16


class ObstacleError(ValueError):
    """An invalid obstacle or attempt, or a refused step; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str) -> ObstacleError:
    return ObstacleError(message, "invalid_field")


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


def _bearings(value: object, label: str) -> tuple[tuple[str, str], ...]:
    """``{approach id or "*": bearing}`` as sorted pairs, so the value is hashable and canonical."""
    if isinstance(value, Mapping):
        pairs = tuple(value.items())
    else:
        pairs = _tuple(value, label)
        if not all(type(pair) is tuple and len(pair) == 2 for pair in pairs):
            raise _invalid(f"{label} must map approach ids to bearings")
    for key, bearing in pairs:
        if key != ANY:
            _segment(key, f"{label} approach id")
        if type(bearing) is not str or bearing not in BEARINGS:
            raise _invalid(f"{label} bearing must be one of {sorted(BEARINGS)}: {bearing!r}")
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _invalid(f"{label} names an approach twice")
    return tuple(sorted(pairs))


def _bearing_on(bearings: tuple[tuple[str, str], ...], approach_id: str) -> str | None:
    named = dict(bearings)
    return named.get(approach_id, named.get(ANY))


# --- what an obstacle holds ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ObstacleFact:
    """An established fact about the obstacle and how it bears on each approach.

    ``bearings`` maps a suggested approach id (or ``"*"``, every other approach, a novel one included)
    to ``favours``, ``hinders`` or ``prevents``; an approach it does not reach is untouched. ``kind``
    (constraint, clue, resource or motive; ``escalation`` for a stake that came true) and ``text`` are
    for people. ``established`` and ``source`` are as for a 029 ``Factor``: the fact must predate an
    attempt to bear on it, and its source is attribution, never read.
    """

    fact_id: str
    kind: str
    text: str
    established: int
    source: str
    bearings: tuple[tuple[str, Bearing], ...]

    def __post_init__(self) -> None:
        _segment(self.fact_id, "fact_id")
        if type(self.kind) is not str or self.kind not in FACT_KINDS | {ESCALATION}:
            raise _invalid(f"kind must be one of {sorted(FACT_KINDS)}: {self.kind!r}")
        _text(self.text, "fact text", PROSE_MAX)
        if type(self.established) is not int or self.established < 0:
            raise _invalid("established must be a non-negative int sequence")
        _text(self.source, "fact source", TEXT_MAX)
        object.__setattr__(self, "bearings", _bearings(self.bearings, "fact"))

    def bearing_on(self, approach_id: str) -> str | None:
        """How the fact bears on ``approach_id``: its own entry, else the ``"*"`` one, else ``None``."""
        return _bearing_on(self.bearings, approach_id)

    def factor(self, bearing: str) -> Factor:
        """The fact as a 029 factor with ``bearing``: same id, text, establishment and source."""
        return Factor(self.fact_id, bearing, self.text, self.established, self.source)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ObstacleStake:
    """A consequence an attempt may bring, and what it does to later attempts if it comes true.

    ``escalates`` is in the form of a fact's ``bearings``: when a decision makes the stake come true on
    a failed attempt, the stake becomes an ``escalation`` fact with those bearings, established at that
    attempt's moment. Empty ``escalates`` changes nothing for later attempts.
    """

    stake_id: str
    text: str
    escalates: tuple[tuple[str, Bearing], ...] = ()

    def __post_init__(self) -> None:
        _segment(self.stake_id, "stake_id")
        _text(self.text, "stake text", PROSE_MAX)
        object.__setattr__(self, "escalates", _bearings(self.escalates, "escalation"))

    @property
    def stake(self) -> Stake:
        """The 029 stake a frame declares."""
        return Stake(self.stake_id, self.text)


@dataclass(frozen=True, slots=True)
class Cost:
    """What succeeding by an approach spends or changes whatever else happens: coin, time, a debt owed."""

    cost_id: str
    text: str

    def __post_init__(self) -> None:
        _segment(self.cost_id, "cost_id")
        _text(self.text, "cost text", PROSE_MAX)


@dataclass(frozen=True, slots=True)
class Approach:
    """One way at the obstacle: a suggestion the author offers, or a novel one a player chose.

    ``risks`` are the stake ids an attempt this way puts at stake: a frame declares exactly these. A
    suggestion's risks are the obstacle's stakes; a novel approach may also risk something the author
    never foresaw (its frame then supplies that stake's text). ``costs`` come true when the approach
    overcomes the obstacle. ``text`` is for people and is never read.
    """

    approach_id: str
    text: str
    risks: frozenset[str] = frozenset()
    costs: tuple[Cost, ...] = ()

    def __post_init__(self) -> None:
        _segment(self.approach_id, "approach_id")
        _text(self.text, "approach text", PROSE_MAX)
        risks = _tuple(self.risks, "risks")
        for risk in risks:
            _segment(risk, "risk")
        costs = tuple(sorted(_tuple(self.costs, "costs"), key=lambda c: getattr(c, "cost_id", "")))
        if not all(type(cost) is Cost for cost in costs):
            raise _invalid("costs must be Cost values")
        if len(costs) > MAX_COSTS or len(risks) > MAX_STAKES:
            raise _invalid(f"at most {MAX_COSTS} costs and {MAX_STAKES} risks")
        if len({cost.cost_id for cost in costs}) != len(costs):
            raise ObstacleError("two costs share an id", "duplicate_id")
        object.__setattr__(self, "risks", frozenset(risks))
        object.__setattr__(self, "costs", costs)


@dataclass(frozen=True, slots=True)
class Obstacle:
    """A problem in a campaign: its goal, actors, facts, stakes and suggested approaches. Never a result.

    ``actors`` are the campaign's instances who stand in the way or hold a key (the guard); facts about
    their motives bear on the approaches. ``provenance`` is attribution (who authored it), never authority.
    """

    obstacle_id: str
    campaign_id: str
    goal: str
    actors: frozenset[InstanceRef]
    facts: tuple[ObstacleFact, ...]
    stakes: tuple[ObstacleStake, ...]
    approaches: tuple[Approach, ...]
    provenance: str

    def __post_init__(self) -> None:
        _segment(self.obstacle_id, "obstacle_id")
        _segment(self.campaign_id, "campaign_id")
        _text(self.goal, "goal", PROSE_MAX)
        _text(self.provenance, "provenance", TEXT_MAX)
        actors = _tuple(self.actors, "actors")
        if not all(type(actor) is InstanceRef for actor in actors):
            raise _invalid("actors must be InstanceRef values")
        if any(actor.campaign_id != self.campaign_id for actor in actors):
            raise ObstacleError("an actor belongs to another campaign", "campaign_mismatch")
        facts = tuple(sorted(_tuple(self.facts, "facts"), key=lambda f: getattr(f, "fact_id", "")))
        stakes = tuple(sorted(_tuple(self.stakes, "stakes"), key=lambda s: getattr(s, "stake_id", "")))
        approaches = tuple(sorted(_tuple(self.approaches, "approaches"),
                                  key=lambda a: getattr(a, "approach_id", "")))
        if not all(type(f) is ObstacleFact for f in facts) or not all(type(s) is ObstacleStake for s in stakes) \
                or not all(type(a) is Approach for a in approaches):
            raise _invalid("facts, stakes and approaches must be ObstacleFact, ObstacleStake and Approach values")
        if (len(facts) > MAX_FACTS or len(stakes) > MAX_STAKES or len(approaches) > MAX_APPROACHES
                or len(set(actors)) > MAX_ACTORS):
            raise _invalid(f"at most {MAX_FACTS} facts, {MAX_STAKES} stakes, {MAX_APPROACHES} approaches and "
                           f"{MAX_ACTORS} actors")
        if any(f.kind == ESCALATION for f in facts):
            raise _invalid("an escalation is a stake that came true, never an authored fact")
        ids = [f.fact_id for f in facts] + [s.stake_id for s in stakes]
        if len(set(ids)) != len(ids):
            # A stake that escalates becomes a fact under its own id, so the two share one namespace.
            raise ObstacleError("two facts or stakes share an id", "duplicate_id")
        approach_ids = [a.approach_id for a in approaches]
        if len(set(approach_ids)) != len(approach_ids):
            raise ObstacleError("two approaches share an id", "duplicate_id")
        listed = set(approach_ids)
        for owner, bearings in [(f.fact_id, f.bearings) for f in facts] + [(s.stake_id, s.escalates) for s in stakes]:
            unknown = sorted(key for key, _ in bearings if key != ANY and key not in listed)
            if unknown:
                raise ObstacleError(f"{owner} bears on approaches that are not suggested: {', '.join(unknown)}",
                                    "unknown_approach")
            if dict(bearings).get(ANY) == "prevents":
                # A fact that prevents every approach is a wall, not an obstacle: it would refuse a novel
                # approach before anyone heard it.
                raise ObstacleError(f"{owner} prevents every approach", "blanket_prevention")
        declared = {s.stake_id for s in stakes}
        for approach in approaches:
            unknown = sorted(approach.risks - declared)
            if unknown:
                raise ObstacleError(f"approach {approach.approach_id} risks undeclared stakes: {', '.join(unknown)}",
                                    "unknown_stake")
        viable = _viable(approach_ids, facts)
        lasting = _viable(viable, [s.escalates for s in stakes])
        if len(lasting) < MIN_VIABLE:
            # One credible answer is a puzzle with a key, and the encounter is engineered toward it. That
            # holds after every failure too: stakes that escalate may not close all but one suggestion.
            raise ObstacleError(f"an obstacle offers at least {MIN_VIABLE} approaches nothing it holds or can "
                                f"come to hold prevents (viable: {', '.join(lasting) or 'none'})", "railroaded")
        object.__setattr__(self, "actors", frozenset(actors))
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "stakes", stakes)
        object.__setattr__(self, "approaches", approaches)

    def approach(self, approach_id: str) -> Approach | None:
        return next((a for a in self.approaches if a.approach_id == approach_id), None)

    def stake(self, stake_id: str) -> ObstacleStake | None:
        return next((s for s in self.stakes if s.stake_id == stake_id), None)

    def viable(self) -> tuple[str, ...]:
        """The suggested approaches no authored fact prevents (an escalation may close some later)."""
        return _viable([a.approach_id for a in self.approaches], self.facts)

    @property
    def opens(self) -> int:
        """The latest sequence its authored facts are established at: an attempt comes after it."""
        return max((f.established for f in self.facts), default=0)


def _viable(approach_ids: Iterable[str], held: Iterable[object]) -> tuple[str, ...]:
    """The approaches none of ``held`` prevents: facts, or the bearings of stakes that may escalate."""
    bearings = [dict(h.bearings) if type(h) is ObstacleFact else dict(h) for h in held]  # type: ignore[arg-type]
    return tuple(a for a in approach_ids if all(b.get(a) != "prevents" for b in bearings))


def escalation(obstacle: Obstacle, stake: ObstacleStake, sequence: int) -> ObstacleFact:
    """``stake``, come true at ``sequence``, as the fact it becomes for later attempts."""
    return ObstacleFact(stake.stake_id, ESCALATION, stake.text, sequence,
                        f"obstacle:{obstacle.obstacle_id}/{stake.stake_id}", stake.escalates)


# --- attempts --------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Attempt:
    """One action against an obstacle: the 029 attempt it is, and the approach it takes.

    ``listed`` says whether the approach is one of the obstacle's suggestions; a novel approach carries
    its own risks and costs, chosen with the player's action.
    """

    attempt_id: str
    approach: Approach
    listed: bool

    def __post_init__(self) -> None:
        _text(self.attempt_id, "attempt_id", TEXT_MAX)
        if type(self.approach) is not Approach or type(self.listed) is not bool:
            raise _invalid("an attempt takes an Approach and a listed flag")


def resolve_approach(obstacle: Obstacle, approach: object) -> tuple[Approach, bool]:
    """A suggestion named by id, or a novel ``Approach``: ``(approach, listed)``.

    A novel approach may not reuse a suggestion's id (``not_novel``), so a suggestion's facts and risks
    cannot be escaped by calling it new under the same name.
    """
    if type(approach) is str:
        found = obstacle.approach(approach)
        if found is None:
            raise ObstacleError(f"no suggested approach {approach!r}; a novel one is given as an Approach",
                                "unknown_approach")
        return found, True
    if type(approach) is not Approach:
        raise _invalid("an approach is a suggestion's id or a novel Approach")
    if obstacle.approach(approach.approach_id) is not None:
        raise ObstacleError(f"{approach.approach_id!r} is a suggested approach: name it by id", "not_novel")
    return approach, False


def required_factors(facts: Iterable[ObstacleFact], approach_id: str, sequence: int) -> tuple[Factor, ...]:
    """The factors a frame at ``sequence`` must carry for ``approach_id``: every fact established before
    it that bears on the approach, with its bearing. For a novel approach that is the ``"*"`` facts."""
    found = [f.factor(bearing) for f in facts
             if f.established < sequence and (bearing := f.bearing_on(approach_id)) is not None]
    return tuple(sorted(found, key=lambda factor: factor.factor_id))


def check_attempt(obstacle: Obstacle, facts: Iterable[ObstacleFact], attempt: Attempt, frame: ActionFrame,
                  after: int | None = None) -> None:
    """Refuse ``frame`` as ``attempt`` against ``obstacle`` whose facts are now ``facts`` (its own and its
    escalations); return ``None`` when it stands. ``after`` is the latest earlier attempt's sequence.

    - ``campaign_mismatch``; ``premature``: not later than every authored fact (``Obstacle.opens``), so
      the moment would choose which facts bear; ``out_of_order``: not later than an earlier attempt, so
      it would dodge what that attempt changed.
    - ``fact_changed``: an obstacle fact with other text, establishment or source, or a suggestion's
      fact with another bearing than the author's. ``fact_misapplied``: a fact the author said does not
      bear on this suggestion. ``missing_fact``: one that bears on it is left out (a ``"*"`` fact bears
      on a novel approach too). ``novel_prevented``: an obstacle fact preventing a novel approach.
    - ``stake_changed``: an obstacle stake with other text. ``stakes_mismatch``: the frame's stakes are
      not exactly the approach's risks.
    """
    if type(frame) is not ActionFrame or type(attempt) is not Attempt or type(obstacle) is not Obstacle:
        raise TypeError("check_attempt takes an Obstacle, an Attempt and an ActionFrame")
    if frame.attempt_id != attempt.attempt_id:
        raise ObstacleError("the frame is another attempt's", "attempt_mismatch")
    if frame.campaign_id != obstacle.campaign_id:
        raise ObstacleError("the frame belongs to another campaign", "campaign_mismatch")
    sequence = frame.moment.sequence
    if sequence <= obstacle.opens:
        # Before the obstacle stands as authored, a labeller choosing the moment could leave its facts out.
        raise ObstacleError(f"an attempt comes after the obstacle's facts (at {obstacle.opens})", "premature")
    if after is not None and sequence <= after:
        raise ObstacleError(f"an attempt comes after the one before it (at {after})", "out_of_order")
    approach = attempt.approach
    known = {f.fact_id: f for f in facts}
    for factor in frame.factors:
        fact = known.get(factor.factor_id)
        if fact is None:
            continue  # the labeller's own factor, such as the actor's training
        if (factor.text, factor.established, factor.source) != (fact.text, fact.established, fact.source):
            raise ObstacleError(f"{fact.fact_id} is not the obstacle's fact as recorded", "fact_changed")
        bearing = fact.bearing_on(approach.approach_id)
        if attempt.listed:
            if bearing is None:
                raise ObstacleError(f"{fact.fact_id} does not bear on {approach.approach_id}", "fact_misapplied")
            if factor.bearing != bearing:
                raise ObstacleError(f"{fact.fact_id} {bearing} {approach.approach_id}, not {factor.bearing}",
                                    "fact_changed")
        elif factor.bearing == "prevents":
            raise ObstacleError(f"{fact.fact_id} cannot prevent an approach no one suggested", "novel_prevented")
        elif bearing is not None and factor.bearing != bearing:
            raise ObstacleError(f"{fact.fact_id} {bearing} every approach, not {factor.bearing}", "fact_changed")
    present = {factor.factor_id for factor in frame.factors}
    missing = sorted(factor.factor_id for factor in required_factors(known.values(), approach.approach_id, sequence)
                     if factor.factor_id not in present)
    if missing:
        raise ObstacleError(f"facts that bear on {approach.approach_id} are left out: {', '.join(missing)}",
                            "missing_fact")
    for stake in frame.stakes:
        declared = obstacle.stake(stake.stake_id)
        if declared is not None and declared.stake != stake:
            raise ObstacleError(f"{stake.stake_id} is not the obstacle's stake as declared", "stake_changed")
        if declared is None and attempt.listed:
            raise ObstacleError(f"{stake.stake_id} is not a stake of this obstacle", "stakes_mismatch")
    if {stake.stake_id for stake in frame.stakes} != approach.risks:
        raise ObstacleError(f"an attempt by {approach.approach_id} risks exactly "
                            f"{', '.join(sorted(approach.risks)) or 'nothing'}", "stakes_mismatch")


# --- progress --------------------------------------------------------------------------------

Status = Literal["open", "overcome", "diverged"]
AttemptStatus = Literal["pending", "failed", "overcame", "diverged", "void"]


@dataclass(frozen=True, slots=True)
class Consequence:
    """Something an attempt made true: a declared stake (``stake``) or the approach's price (``cost``)."""

    kind: Literal["stake", "cost"]
    consequence_id: str
    text: str


@dataclass(frozen=True, slots=True)
class AttemptState:
    attempt: Attempt
    frame: ActionFrame | None
    status: AttemptStatus
    result: str | None = None
    consequences: tuple[Consequence, ...] = ()
    #: why the attempt does not count, for ``diverged``
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class ObstacleState:
    """An obstacle replayed against the 029 outcomes of its attempts.

    ``facts`` are its own facts plus the escalations failed attempts brought: what bears on the next
    attempt. ``diverged`` means an attempt no longer agrees with the obstacle (its frame was revised
    around it, or the records disagree), and the obstacle takes no new attempt.
    """

    obstacle: Obstacle
    revision: int
    status: Status
    attempts: tuple[AttemptState, ...]
    facts: tuple[ObstacleFact, ...]
    overcome_by: str | None = None
    problem: str | None = None

    @property
    def pending(self) -> AttemptState | None:
        return next((a for a in self.attempts if a.status == "pending"), None)

    @property
    def after(self) -> int | None:
        """The sequence of the latest attempt: the next one comes after it."""
        framed = [a.frame.moment.sequence for a in self.attempts if a.frame is not None]
        return max(framed) if framed else None

    def due(self) -> tuple[Consequence, ...]:
        """Every consequence the decided attempts brought, oldest first: for 021 to apply."""
        return tuple(c for a in self.attempts for c in a.consequences)


def derive(obstacle: Obstacle, revision: int, attempts: Iterable[Attempt],
           frames: Mapping[str, ActionFrame], outcomes: Mapping[str, Outcome]) -> ObstacleState:
    """Replay ``attempts`` (oldest first) against their latest 029 ``frames`` and recorded ``outcomes``.

    Each attempt is checked against the obstacle's facts as they stood when it was made (its own, and
    the escalations of earlier failures), using the frame its outcome decided. A failure that made an
    escalating stake come true adds that fact; a success or a partial success overcomes the obstacle
    with the approach's costs. An attempt that does not check, or any record out of the order the
    service keeps (an attempt after a pending or a successful one), diverges.

    The service links an attempt before 029 frames it, so a link with no frame is an attempt whose frame
    was never written (a crash, or a write the store refused): the latest is ``pending`` until framed,
    and an earlier one is ``void``, superseded by the attempt after it.
    """
    attempts = list(attempts)
    facts = list(obstacle.facts)
    states: list[AttemptState] = []
    status: Status = "open"
    overcome_by = problem = None
    after: int | None = None
    blocked = False
    for index, attempt in enumerate(attempts):
        outcome = outcomes.get(attempt.attempt_id)
        frame = outcome.frame if outcome is not None else frames.get(attempt.attempt_id)
        reason = None
        if status != "open" or blocked:
            reason = "attempt_out_of_turn"  # the service links nothing after a pending or decisive attempt
        elif frame is None:
            last = index == len(attempts) - 1
            states.append(AttemptState(attempt, None, "pending" if last else "void"))
            blocked = last
            continue
        else:
            try:
                check_attempt(obstacle, facts, attempt, frame, after)
            except (ObstacleError, AdjudicationError) as error:
                reason = error.reason
        if reason is not None:
            states.append(AttemptState(attempt, frame, "diverged", problem=reason))
            status, problem = "diverged", reason
            break
        assert frame is not None
        after = frame.moment.sequence
        if outcome is None:
            states.append(AttemptState(attempt, frame, "pending"))
            blocked = True
            continue
        decision = outcome.decision
        stakes = {stake.stake_id: stake for stake in frame.stakes}
        brought = [Consequence("stake", stake_id, stakes[stake_id].text) for stake_id in sorted(decision.consequences)]
        if decision.result in ("success", "partial"):
            brought += [Consequence("cost", cost.cost_id, cost.text) for cost in attempt.approach.costs]
            states.append(AttemptState(attempt, frame, "overcame", decision.result, tuple(brought)))
            status, overcome_by = "overcome", attempt.attempt_id
            continue
        for stake_id in sorted(decision.consequences):
            declared = obstacle.stake(stake_id)
            if declared is not None and declared.escalates and all(f.fact_id != stake_id for f in facts):
                facts.append(escalation(obstacle, declared, after))  # once: a stake comes true only once
        states.append(AttemptState(attempt, frame, "failed", decision.result, tuple(brought)))
    return ObstacleState(obstacle, revision, status, tuple(states), tuple(facts), overcome_by, problem)
