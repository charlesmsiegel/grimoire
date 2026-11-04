"""Assessing a labelled attempt to use a fictional ability (sprint 025, first slice).

``assess_attempt`` is deterministic and calls no model. It reads only the
``EffectiveEntity`` it is given and the attempt's labels, and classifies the
attempt as a known capability, an attempted feat, an extrapolation or a new
ability (both needing approval), beyond the ability's limits, unavailable for
lack of a prerequisite, needing a relabel, or needing a newer build. It also
reports the costs that would apply and why.

An assessment is **not an outcome**: it is not a ``Resolution``, commits no
cost, writes nothing and narrates nothing. Fiction-first adjudication (029)
consumes it. The labels (``needs``, ``effect_id``, ``conditions_met``) come
from a labeller that is not the narrator; the assessment is only as good as
they are. ``AbilityRuling`` is a provisional, attempt-scoped approval surface
for the adjudication channel only, never narration; 029 may replace it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from .abilities import MAX_PARTS, MAX_TAGS, Abilities, Ability, Cost
from .entity import EffectiveEntity
from .facets import Agent
from .identity import TEXT_MAX, InstanceRef, validate_segment

Classification = Literal["known_capability", "attempted_feat", "extrapolation", "new_ability",
                         "beyond_limits", "unavailable", "relabel", "needs_upgrade"]
Approval = Literal["not_needed", "not_approvable", "pending", "approved", "refused"]
CLASSIFICATIONS = frozenset({"known_capability", "attempted_feat", "extrapolation", "new_ability",
                             "beyond_limits", "unavailable", "relabel", "needs_upgrade"})
#: The only classifications an approval ruling can decide.
APPROVABLE = frozenset({"extrapolation", "new_ability"})
#: Classifications in which the power works as declared; no ruling is involved.
DECLARED = frozenset({"known_capability", "attempted_feat"})
DECISIONS = frozenset({"approved", "refused"})
#: Rulings on one attempt id; others in the input are not counted.
MAX_RULINGS = 256
#: ``unrecognized`` path for extras on the facet itself; not a segment, so no ability id collides.
FACET_PATH = "#facet"


class AbilityError(ValueError):
    """An invalid attempt, ruling or assessment input; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str) -> AbilityError:
    return AbilityError(message, "invalid_field")


def _segment(value: object, label: str) -> None:
    if type(value) is not str:  # a str subclass could redefine equality
        raise _invalid(f"{label} must be plain text: {value!r}")
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise _invalid(str(error)) from None


def _text(value: object, label: str, *, blank: bool = False) -> None:
    if type(value) is not str or (not blank and not value.strip()):
        raise _invalid(f"{label} must be {'' if blank else 'nonempty '}plain text")
    if len(value) > TEXT_MAX:
        raise _invalid(f"{label} is longer than {TEXT_MAX} characters")


def _collection(value: object, label: str) -> list:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise _invalid(f"{label} must be a collection: {value!r}")
    return list(value)


def _names(value: object, label: str, limit: int) -> frozenset[str]:
    entries = _collection(value, label)
    for entry in entries:
        _segment(entry, f"{label} entry")
    names = frozenset(entries)
    if len(names) > limit:
        raise _invalid(f"{label} has more than {limit} entries")
    return names


@dataclass(frozen=True, slots=True)
class AbilityAttempt:
    """A labelled attempted use. For a player's actor, ``attempt_id`` is the action's contribution_id."""

    attempt_id: str
    actor: InstanceRef
    ability_id: str
    needs: frozenset[str]
    provenance: str
    effect_id: str | None = None
    conditions_met: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        # Any nonempty text, like ``Resolution.attempt_id`` and ``PlayerContribution.contribution_id``.
        if type(self.attempt_id) is not str or not self.attempt_id.strip():
            raise _invalid("attempt_id must be nonempty plain text")
        if type(self.actor) is not InstanceRef:  # a subclass could redefine equality
            raise _invalid("an attempt's actor must be an InstanceRef")
        _segment(self.ability_id, "ability_id")
        needs = _names(self.needs, "needs", MAX_TAGS)
        if not needs:
            raise _invalid("an attempt needs at least one tag")
        object.__setattr__(self, "needs", needs)
        _text(self.provenance, "provenance")
        if self.effect_id is not None:
            _segment(self.effect_id, "effect_id")
        object.__setattr__(self, "conditions_met", _names(self.conditions_met, "conditions_met", MAX_PARTS))


@dataclass(frozen=True, slots=True)
class AbilityRuling:
    """An adjudication-channel decision on one exact attempt and one classification (provisional)."""

    ruling_id: str
    attempt: AbilityAttempt
    decides: Literal["extrapolation", "new_ability"]
    decision: Literal["approved", "refused"]
    provenance: str
    note: str = ""

    def __post_init__(self) -> None:
        _segment(self.ruling_id, "ruling_id")
        if type(self.attempt) is not AbilityAttempt:
            raise _invalid("a ruling decides an AbilityAttempt")
        if type(self.decides) is not str or self.decides not in APPROVABLE:
            raise _invalid(f"a ruling decides one of {sorted(APPROVABLE)}: {self.decides!r}")
        if type(self.decision) is not str or self.decision not in DECISIONS:
            raise _invalid(f"decision must be one of {sorted(DECISIONS)}: {self.decision!r}")
        _text(self.provenance, "provenance")
        _text(self.note, "note", blank=True)


@dataclass(frozen=True, slots=True, order=True)
class Reason:
    """One finding: a stable ``code`` for programs and deterministic prose for people."""

    code: str
    text: str


@dataclass(frozen=True, slots=True)
class AbilityAssessment:
    """What an attempt is, what it would cost and why. Never an outcome."""

    attempt: AbilityAttempt
    classification: Classification
    approval: Approval
    ruling_id: str | None
    uncertain: bool
    costs: tuple[Cost, ...]
    unmet_prerequisites: tuple[str, ...]
    conflicts: tuple[tuple[str, tuple[str, ...]], ...]
    candidates: tuple[str, ...]
    unknown_tags: tuple[str, ...]
    other_conflicts: tuple[tuple[str, str, tuple[str, ...]], ...]
    unrecognized: tuple[str, ...]
    other_unrecognized: tuple[str, ...]
    other_candidates: tuple[str, ...]
    other_drawn: tuple[str, ...]
    stale_references: tuple[str, ...]
    inapplicable_rulings: tuple[str, ...]
    mismatched_rulings: tuple[str, ...]
    reasons: tuple[Reason, ...]

    @property
    def needs_approval(self) -> bool:
        return self.approval == "pending"

    @property
    def permitted(self) -> bool:
        """Whether the power may be narrated as working: declared, or an approved ruling.

        ``approval == "not_needed"`` alone is not permission: refusals say ``not_approvable``.
        """
        return self.classification in DECLARED or self.approval == "approved"


# --- rulings --------------------------------------------------------------------

def _label(attempt: AbilityAttempt) -> tuple:
    """What a ruling decides: the attempt's labels. ``provenance`` is attribution, not a label."""
    return (attempt.attempt_id, attempt.actor, attempt.ability_id, attempt.needs, attempt.effect_id,
            attempt.conditions_met)


def _matching_rulings(attempt: AbilityAttempt, rulings: object):
    """``(mine, same, matching, mismatched ids)``: same-campaign rulings, those on this attempt id,
    those on these exact labels, and the ids of those on other labels."""
    given = _collection(rulings, "rulings")
    for ruling in given:
        if type(ruling) is not AbilityRuling:
            raise _invalid(f"not an AbilityRuling: {ruling!r}")
    campaign = attempt.actor.campaign_id
    mine = [ruling for ruling in given if ruling.attempt.actor.campaign_id == campaign]
    # Another actor's ruling under the same attempt id is not about this attempt. The same record
    # seen twice (two merged logs) is one ruling.
    same = list(dict.fromkeys(ruling for ruling in mine if ruling.attempt.attempt_id == attempt.attempt_id
                              and ruling.attempt.actor == attempt.actor))
    # Only this attempt's rulings are checked here: one bad record elsewhere in the campaign
    # must not block every other use. Campaign-wide id uniqueness is ``check_ruling``'s job.
    ids = [ruling.ruling_id for ruling in same]
    if len(set(ids)) != len(ids):
        raise AbilityError("a ruling id appears twice on this attempt", "duplicate_ruling")
    if len(same) > MAX_RULINGS:
        raise _invalid(f"more than {MAX_RULINGS} rulings on attempt {attempt.attempt_id!r}")
    label = _label(attempt)
    matching = [ruling for ruling in same if _label(ruling.attempt) == label]
    mismatched = tuple(sorted(ruling.ruling_id for ruling in same if _label(ruling.attempt) != label))
    return mine, same, matching, mismatched


def _live(matching: list, classification: str) -> tuple[list, tuple[str, ...]]:
    """The rulings that decide this classification, and the ids of the rest."""
    live = [ruling for ruling in matching if ruling.decides == classification] \
        if classification in APPROVABLE else []
    if len({ruling.decision for ruling in live}) > 1:
        raise AbilityError(f"rulings both approve and refuse this {classification}", "conflicting_rulings")
    rest = tuple(sorted(ruling.ruling_id for ruling in matching if ruling not in live))
    return live, rest


# --- assessment -----------------------------------------------------------------

def _tags(tags: Iterable[str]) -> str:
    return ", ".join(sorted(tags))


def _with_extras(ability: Ability) -> list[tuple[str, tuple[str, ...]]]:
    """``(path, unknown keys)`` for the ability and each of its parts that carries extras."""
    found = [(ability.id, tuple(ability.extras))] if ability.extras else []
    parts = [*ability.effects, *ability.limits, *ability.costs, *ability.prerequisites]
    return found + [(f"{ability.id}/{part.id}", tuple(part.extras)) for part in parts if part.extras]


def _unrecognized(facet: Abilities | None, ability: Ability | None) -> list[tuple[str, tuple[str, ...]]]:
    """Values carrying extras on the facet or anywhere in the named ability (fail closed)."""
    found = [(FACET_PATH, tuple(facet.extras))] if facet is not None and facet.extras else []
    if ability is not None:
        found += _with_extras(ability)
    return sorted(found)


def assess_attempt(attempt: AbilityAttempt, entity: EffectiveEntity,
                   rulings: Iterable[AbilityRuling] = ()) -> AbilityAssessment:
    """Classify a labelled attempt against the entity's effective abilities."""
    if type(attempt) is not AbilityAttempt:
        raise TypeError(f"expected an AbilityAttempt, not {type(attempt).__name__}")
    if type(entity) is not EffectiveEntity:
        raise TypeError(f"expected an EffectiveEntity, not {type(entity).__name__}")
    if entity.ref != attempt.actor:
        raise AbilityError(f"the attempt is by {attempt.actor}, not {entity.ref}", "actor_mismatch")
    if not entity.facets.has(Agent):
        raise AbilityError(f"{entity.ref} is not an agent and cannot attempt an ability", "not_agent")
    _, _, matching, mismatched = _matching_rulings(attempt, rulings)

    needs = attempt.needs
    facet = entity.facets.get(Abilities)
    ability = facet.by_id(attempt.ability_id) if facet is not None else None
    reasons: list[Reason] = []

    stale: list[str] = []
    effect = None
    if attempt.effect_id is not None:
        effect = ability.effect(attempt.effect_id) if ability is not None else None
        if effect is None:
            stale.append(f"effect:{attempt.effect_id}")
            reasons.append(Reason("stale_effect", f"{attempt.ability_id!r} has no effect "
                                                  f"{attempt.effect_id!r}; it is treated as not named"))
    for condition in sorted(attempt.conditions_met):
        if ability is None or ability.prerequisite(condition) is None:
            stale.append(f"prerequisite:{condition}")
            reasons.append(Reason("stale_prerequisite",
                                  f"{attempt.ability_id!r} has no prerequisite {condition!r}"))

    covers = effect is not None and needs <= effect.tags
    # Effects whose scoped prerequisites and costs apply. A covering named effect is exact. A
    # stretch (no covering named effect) counts every effect it touches: each one sharing a needed
    # tag. Adding a needed tag can only add effects, so no label or extra tag ever drops the
    # constraints of an effect the attempt draws on. This is deliberately conservative.
    scope: set[str] = set()
    if effect is not None:
        scope.add(effect.id)
    touched: set[str] = set()
    if ability is not None:
        touched = {each.id for each in ability.effects if each.tags & needs}
        if not covers:
            scope |= touched

    conflicts: list[tuple[str, tuple[str, ...]]] = []
    unmet: list[str] = []
    candidates: list[str] = []
    if ability is not None:
        for limit in ability.limits:
            hit = tuple(sorted(limit.excludes & needs))
            if hit:
                conflicts.append((limit.id, hit))
                unread = " (it also has fields this version does not understand)" if limit.extras else ""
                reasons.append(Reason("limit_excludes", f"limit {limit.id!r} of {ability.id!r} "
                                                        f"excludes {_tags(hit)}{unread}"))
        for prerequisite in ability.prerequisites:
            applies = not prerequisite.applies_to or bool(prerequisite.applies_to & scope)
            if applies and prerequisite.id not in attempt.conditions_met:
                unmet.append(prerequisite.id)
                reasons.append(Reason("prerequisite_unmet", f"prerequisite {prerequisite.id!r} of "
                                                            f"{ability.id!r} is not met"))
        candidates = [candidate.id for candidate in ability.effects if needs <= candidate.tags]
    else:
        reasons.append(Reason("no_ability", f"{attempt.actor} has no ability {attempt.ability_id!r}"))

    unknown: list[str] = []
    others: list[tuple[str, str, tuple[str, ...]]] = []
    other_unrecognized: list[tuple[str, tuple[str, ...]]] = []
    other_candidates: list[str] = []
    # Tags the named ability cannot provide. An effect of another ability that provides one of
    # them means the attempt draws on that ability: a cross-ability combination, which this slice
    # does not adjudicate. It must not escape that ability's limits, prerequisites and costs.
    own = {tag for part in ability.effects for tag in part.tags} if ability is not None else set()
    beyond_own = needs - own
    drawn: list[str] = []
    if facet is None:
        unknown = sorted(needs)  # nothing is authored, so every needed tag is unknown
        reasons += [Reason("unknown_tag", f"no ability of {attempt.actor} uses the tag {tag!r}")
                    for tag in unknown]
    else:
        used = {tag for each in facet.abilities for part in each.effects for tag in part.tags}
        used |= {tag for each in facet.abilities for part in each.limits for tag in part.excludes}
        unknown = sorted(needs - used)
        reasons += [Reason("unknown_tag", f"no ability of {attempt.actor} uses the tag {tag!r}")
                    for tag in unknown]
        for each in facet.abilities:
            if each.id == attempt.ability_id:
                continue
            other_unrecognized += _with_extras(each)
            for part in each.effects:
                provided = tuple(sorted(part.tags & beyond_own))
                if provided:
                    drawn.append(f"{each.id}/{part.id}")
                    reasons.append(Reason("draws_on_other_ability", f"effect {part.id!r} of {each.id!r} "
                                                                    f"provides {_tags(provided)}"))
                if needs <= part.tags:
                    other_candidates.append(f"{each.id}/{part.id}")
                    reasons.append(Reason("covered_by_other_ability", f"effect {part.id!r} of {each.id!r} "
                                                                      f"provides {_tags(needs)}"))
            for limit in each.limits:
                hit = tuple(sorted(limit.excludes & needs))
                if hit:
                    others.append((each.id, limit.id, hit))
                    reasons.append(Reason("other_limit_excludes", f"limit {limit.id!r} of another ability "
                                                                  f"{each.id!r} excludes {_tags(hit)}"))
    found = _unrecognized(facet, ability)
    unrecognized = [path for path, _ in found]
    reasons += [Reason("unrecognized_constraint", f"{path} has fields this version does not understand: "
                                                  f"{', '.join(keys)}") for path, keys in found]
    reasons += [Reason("other_unrecognized_constraint", f"another ability's {path} has fields this version "
                                                        f"does not understand: {', '.join(keys)}")
                for path, keys in sorted(other_unrecognized)]
    if ability is not None:
        if covers:
            reasons.append(Reason("effect_covers", f"effect {effect.id!r} provides {_tags(needs)}"))
            if effect.certainty == "uncertain":
                reasons.append(Reason("effect_uncertain", f"effect {effect.id!r} is marked uncertain"))
        else:
            if effect is not None:
                reasons.append(Reason("not_covered", f"effect {effect.id!r} does not provide "
                                                     f"{_tags(needs - effect.tags)}"))
            if candidates:
                reasons.append(Reason("covered_by_other_effect", f"declared effects {', '.join(candidates)} "
                                                                 f"provide {_tags(needs)}"))
            else:
                reasons.append(Reason("not_covered", f"no single declared effect of {ability.id!r} "
                                                     f"provides {_tags(needs)}"))

    if ability is None:
        # A declared effect of another ability already covers it: filing it under a new id must
        # not turn a known (perhaps unavailable or costly) power into an approvable new one.
        # Naming an effect or a prerequisite, or drawing on another ability's effects, says the
        # attempt is a use of an existing power, not a new one.
        if FACET_PATH in unrecognized:
            classification = "needs_upgrade"
        elif stale or other_candidates or drawn:
            classification = "relabel"
        else:
            classification = "new_ability"
    elif conflicts:
        classification = "beyond_limits"
    elif stale:
        classification = "relabel"
    elif drawn or (other_candidates and not candidates):
        # Drawing on another ability is a combination this slice does not adjudicate. A sibling
        # that also covers the attempt only matters when the named ability cannot: its own
        # covering effect is the character's choice, so overlapping powers never deadlock.
        classification = "relabel"
    elif not touched:
        # No effect of the named ability shares a needed tag: this is not a use of it. File it
        # under its own ability id (a new ability, or the ability that covers it).
        classification = "relabel"
        reasons.append(Reason("outside_ability", f"no effect of {ability.id!r} shares a needed tag; "
                                                 "file the attempt under its own ability id"))
    elif unmet:
        classification = "unavailable"
    elif unrecognized:
        classification = "needs_upgrade"
    elif covers:
        classification = "known_capability" if effect.certainty == "reliable" else "attempted_feat"
    elif candidates:
        classification = "relabel"
    else:
        classification = "extrapolation"

    live, inapplicable = _live(matching, classification)
    ruling_id = None
    if classification in DECLARED:
        approval = "not_needed"
    elif classification not in APPROVABLE:
        approval = "not_approvable"
    elif live:
        approval = live[0].decision
        ruling_id = min(ruling.ruling_id for ruling in live)
        reasons.append(Reason(approval, f"ruling {ruling_id!r} {approval} this {classification}"))
    else:
        approval = "pending"
        reasons.append(Reason("pending_approval", f"this {classification} needs an approval ruling"))
    reasons += [Reason("inapplicable_ruling", f"ruling {each!r} does not decide a {classification}")
                for each in inapplicable]
    reasons += [Reason("mismatched_ruling", f"ruling {each!r} decided a differently labelled attempt")
                for each in mismatched]

    charged = classification in ("known_capability", "attempted_feat") or (
        classification == "extrapolation" and approval == "approved")
    costs = tuple(cost for cost in ability.costs if not cost.applies_to or cost.applies_to & scope) \
        if charged else ()
    uncertain = classification == "attempted_feat" or (
        classification in APPROVABLE and approval == "approved")
    return AbilityAssessment(
        attempt=attempt,
        classification=classification,
        approval=approval,
        ruling_id=ruling_id,
        uncertain=uncertain,
        costs=costs,
        unmet_prerequisites=tuple(sorted(unmet)),
        conflicts=tuple(sorted(conflicts)),
        candidates=tuple(sorted(candidates)),
        unknown_tags=tuple(unknown),
        other_conflicts=tuple(sorted(others)),
        unrecognized=tuple(unrecognized),
        other_unrecognized=tuple(path for path, _ in sorted(other_unrecognized)),
        other_candidates=tuple(sorted(other_candidates)),
        other_drawn=tuple(sorted(drawn)),
        stale_references=tuple(sorted(stale)),
        inapplicable_rulings=inapplicable,
        mismatched_rulings=mismatched,
        reasons=tuple(sorted(reasons)),
    )


def check_ruling(ruling: AbilityRuling, entity: EffectiveEntity,
                 existing: Iterable[AbilityRuling] = ()) -> None:
    """Refuse a ruling that could not decide this attempt now (issue-time check)."""
    if type(ruling) is not AbilityRuling:
        raise TypeError(f"expected an AbilityRuling, not {type(ruling).__name__}")
    existing = _collection(existing, "existing rulings")
    classification = assess_attempt(ruling.attempt, entity, existing).classification
    if classification == "beyond_limits":
        raise AbilityError("an authored limit excludes this attempt; only an authored change to the "
                           "ability can alter it", "limit_not_approvable")
    if classification in DECLARED:
        raise AbilityError(f"a {classification} needs no ruling", "ruling_not_needed")
    if classification not in APPROVABLE:
        raise AbilityError(f"a {classification} cannot be ruled on as labelled", "not_approvable")
    if ruling.decides != classification:
        raise AbilityError(f"the attempt is a {classification}, not a {ruling.decides}",
                           "wrong_classification")
    mine, same, matching, _ = _matching_rulings(ruling.attempt, existing)
    if len(same) >= MAX_RULINGS:
        raise AbilityError(f"attempt {ruling.attempt.attempt_id!r} already has {MAX_RULINGS} rulings",
                           "too_many_rulings")
    if ruling.ruling_id in {each.ruling_id for each in mine}:
        raise AbilityError(f"ruling id {ruling.ruling_id!r} is already used", "duplicate_ruling")
    if any(each.decides == classification and each.decision != ruling.decision for each in matching):
        raise AbilityError(f"an existing ruling already decided this {classification} the other way",
                           "conflicting_rulings")


__all__ = [
    "APPROVABLE", "CLASSIFICATIONS", "DECISIONS", "DECLARED", "FACET_PATH", "MAX_RULINGS",
    "AbilityAssessment", "AbilityAttempt", "AbilityError", "AbilityRuling", "Reason",
    "assess_attempt", "check_ruling",
]
