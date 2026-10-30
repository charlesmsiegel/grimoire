"""Player sovereignty: who may author which part of a PC's story.

A deterministic policy over *labelled* narration claims, not a prose detector.
Claim labels come from a labeller that is not the narrator; contributions,
delegations, revocations and corrections come only from the player-input
channel; conditions and resolutions come from rule/adjudication writers.
Model output must never be turned into any of those authority inputs. Every
review re-checks authority against current control, so a value built with the
wrong submitter or grantor grants nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .model import ControlAssignment, _nonempty

DIRECTOR = "director"  # reserved controller id; never a player authority

PROTECTED_DOMAINS = frozenset({
    "intent", "voluntary_action", "voluntary_speech", "interiority", "belief", "commitment",
})
OPEN_DOMAINS = frozenset({"sensation", "external_effect", "world_fact"})
CONDITIONAL_DOMAINS = frozenset({"involuntary_reaction"})
ADJUDICATED_DOMAINS = frozenset({"outcome"})
ALL_DOMAINS = PROTECTED_DOMAINS | OPEN_DOMAINS | CONDITIONAL_DOMAINS | ADJUDICATED_DOMAINS

CONTRIBUTION_DOMAINS = {
    "speech": frozenset({"voluntary_speech", "commitment"}),
    "action": frozenset({"intent", "voluntary_action"}),
    "interiority": frozenset({"interiority", "belief"}),
    "commitment": frozenset({"commitment"}),
    "directorial_request": frozenset(),  # out of character: authorizes no PC domain
}
# Frozen into explicit domains at grant time. Intent, belief and commitment are
# never preset; they need an explicit custom grant.
PRESET_DOMAINS = {
    "incidental_dialogue": frozenset({"voluntary_action", "voluntary_speech"}),
    "characterization": frozenset({"voluntary_action", "voluntary_speech", "interiority"}),
}
_AUTO_COMPLETE = frozenset({"voluntary_action"})
_SCOPE_IDS = {
    "contribution": ("contribution_id",),
    "turn": ("scene_id", "turn_id"),
    "scene": ("scene_id",),
    "campaign": (),
}


class AuthorityError(PermissionError):
    """Control is ambiguous, unknown, or held by someone else: fail closed."""


def _choice(value: str, options, label: str) -> None:
    if value not in options:
        raise ValueError(f"unknown {label}: {value!r}")


def _optional(value: str | None, label: str) -> None:
    if value is not None:
        _nonempty(value, label)


# --- authority inputs --------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PlayerContribution:
    """What a controller authored for their actor this turn, exactly as given."""

    campaign_id: str
    contribution_id: str
    scene_id: str
    turn_id: str
    actor_id: str
    submitted_by: str
    kind: str
    text: str

    def __post_init__(self) -> None:
        for label in ("campaign_id", "contribution_id", "scene_id", "turn_id",
                      "actor_id", "submitted_by", "text"):
            _nonempty(getattr(self, label), label)
        _choice(self.kind, CONTRIBUTION_DOMAINS, "contribution kind")
        if self.submitted_by == DIRECTOR:
            raise ValueError("the director cannot author a player contribution")


@dataclass(frozen=True, slots=True)
class Delegation:
    """An explicit, scoped grant of protected domains from controller to director."""

    campaign_id: str
    delegation_id: str
    actor_id: str
    granted_by: str
    domains: frozenset[str]
    scope: str
    mode: str
    scene_id: str | None = None
    turn_id: str | None = None
    contribution_id: str | None = None

    def __post_init__(self) -> None:
        for label in ("campaign_id", "delegation_id", "actor_id", "granted_by"):
            _nonempty(getattr(self, label), label)
        if self.granted_by == DIRECTOR:
            raise ValueError("the director cannot delegate a player's authority to itself")
        object.__setattr__(self, "domains", frozenset(self.domains))
        if not self.domains or not self.domains <= PROTECTED_DOMAINS:
            raise ValueError("a delegation needs a nonempty set of protected domains")
        _choice(self.scope, _SCOPE_IDS, "delegation scope")
        for label in ("scene_id", "turn_id", "contribution_id"):
            value = getattr(self, label)
            if label in _SCOPE_IDS[self.scope]:
                _nonempty(value, label)
            elif value is not None:
                raise ValueError(f"{self.scope!r} scope does not take {label}")
        _choice(self.mode, {*PRESET_DOMAINS, "auto_complete", "custom"}, "delegation mode")
        if self.mode in PRESET_DOMAINS and (
                self.domains != PRESET_DOMAINS[self.mode] or self.scope == "contribution"):
            raise ValueError(f"{self.mode!r} carries exactly its preset domains, never "
                             "for a single contribution")
        if self.mode == "auto_complete" and (
                self.scope != "contribution" or self.domains != _AUTO_COMPLETE):
            raise ValueError("auto_complete is contribution-scoped voluntary_action only")


@dataclass(frozen=True, slots=True)
class Revocation:
    campaign_id: str
    delegation_id: str
    revoked_by: str

    def __post_init__(self) -> None:
        for label in ("campaign_id", "delegation_id", "revoked_by"):
            _nonempty(getattr(self, label), label)


@dataclass(frozen=True, slots=True)
class ImposedCondition:
    """A rule- or effect-imposed involuntary state such as fear or compulsion.

    Both flags fail closed. Until Session Zero computes agreed limits, the
    rule/effect writer that creates the condition sets them explicitly.
    """

    campaign_id: str
    condition_id: str
    actor_id: str
    source: str
    origin_id: str
    description: str
    disclosed: bool = False
    within_agreed_limits: bool = False

    def __post_init__(self) -> None:
        for label in ("campaign_id", "condition_id", "actor_id", "origin_id", "description"):
            _nonempty(getattr(self, label), label)
        _choice(self.source, {"rule", "effect"}, "condition source")
        for label in ("disclosed", "within_agreed_limits"):
            if not isinstance(getattr(self, label), bool):
                raise ValueError(f"{label} must be a bool")  # "false" must not read as true


@dataclass(frozen=True, slots=True)
class Resolution:
    """A recorded outcome; `result` is an opaque token owned by adjudication.

    For a PC attempt, `attempt_id` is the action's contribution_id.
    """

    campaign_id: str
    attempt_id: str
    result: str

    def __post_init__(self) -> None:
        for label in ("campaign_id", "attempt_id", "result"):
            _nonempty(getattr(self, label), label)


@dataclass(frozen=True, slots=True)
class Correction:
    """A controller's objection to an accepted claim."""

    campaign_id: str
    claim_id: str
    corrected_by: str
    note: str = ""

    def __post_init__(self) -> None:
        for label in ("campaign_id", "claim_id", "corrected_by"):
            _nonempty(getattr(self, label), label)


# --- narration -----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NarrationContext:
    campaign_id: str
    scene_id: str
    turn_id: str

    def __post_init__(self) -> None:
        for label in ("campaign_id", "scene_id", "turn_id"):
            _nonempty(getattr(self, label), label)


@dataclass(frozen=True, slots=True)
class NarrationClaim:
    """One atomic, labelled director proposal about the fiction."""

    campaign_id: str
    claim_id: str
    domain: str
    text: str
    subject_id: str | None = None
    expresses: str | None = None
    condition_id: str | None = None
    attempt_id: str | None = None
    result: str | None = None

    def __post_init__(self) -> None:
        for label in ("campaign_id", "claim_id", "text"):
            _nonempty(getattr(self, label), label)
        _choice(self.domain, ALL_DOMAINS, "authorship domain")
        for label in ("subject_id", "expresses", "condition_id", "attempt_id", "result"):
            _optional(getattr(self, label), label)
        if self.subject_id is None and self.domain != "world_fact":
            raise ValueError("only a world_fact may omit its subject")
        is_outcome = self.domain == "outcome"
        if is_outcome != (self.attempt_id is not None) or is_outcome != (self.result is not None):
            raise ValueError("outcome claims, and only they, need attempt_id and result")
        if self.condition_id is not None and self.domain != "involuntary_reaction":
            raise ValueError("condition_id is only for involuntary reactions")
        if self.expresses is not None and self.domain not in PROTECTED_DOMAINS:
            raise ValueError("only protected-domain claims express a contribution")


@dataclass(frozen=True, slots=True)
class AcceptedClaim:
    """`basis` names the authority: director, player:, delegation:, condition:, resolution:."""

    claim: NarrationClaim
    basis: str


@dataclass(frozen=True, slots=True)
class RejectedClaim:
    claim: NarrationClaim
    reason: str


@dataclass(frozen=True, slots=True)
class NarrationReview:
    accepted: tuple[AcceptedClaim, ...]
    rejected: tuple[RejectedClaim, ...]
    disputed: tuple[Correction, ...] = ()  # flagged for a referee, still accepted


@dataclass(frozen=True, slots=True)
class CorrectionRefusal:
    correction: Correction
    reason: str


@dataclass(frozen=True, slots=True)
class NarrationPolicy:
    """What the director may narrate about one actor at one moment."""

    campaign_id: str
    actor_id: str
    controller_id: str
    director_may: frozenset[str]
    reserved: frozenset[str]
    delegation_ids: tuple[str, ...]
    condition_ids: tuple[str, ...]


# --- control and player input --------------------------------------------------

def controller_of(campaign_id: str, actor_id: str,
                  controls: Iterable[ControlAssignment]) -> str | None:
    found = {c.controller_id for c in controls
             if c.campaign_id == campaign_id and c.actor_id == actor_id}
    if len(found) > 1:
        raise AuthorityError(f"ambiguous control of {actor_id!r} in {campaign_id!r}")
    return next(iter(found), None)


def submit_contribution(contribution: PlayerContribution,
                        controls: Iterable[ControlAssignment]) -> PlayerContribution:
    """Accept player input only from the actor's current human controller."""
    if controller_of(contribution.campaign_id, contribution.actor_id, controls) \
            != contribution.submitted_by:
        raise AuthorityError(
            f"{contribution.submitted_by!r} does not control {contribution.actor_id!r}")
    return contribution


def delegate(mode: str, *, campaign_id: str, delegation_id: str, actor_id: str,
             granted_by: str, scope: str, scene_id: str | None = None,
             turn_id: str | None = None) -> Delegation:
    """Grant a named preset, frozen into explicit domains now."""
    _choice(mode, PRESET_DOMAINS, "delegation preset")
    if scope == "contribution":
        raise ValueError("use auto_complete for consent to one contribution")
    return Delegation(campaign_id, delegation_id, actor_id, granted_by,
                      PRESET_DOMAINS[mode], scope, mode, scene_id, turn_id)


def auto_complete(contribution: PlayerContribution, delegation_id: str) -> Delegation:
    """Explicit consent for the director to phrase one submitted action.

    Keeping that phrasing to the one action (claim atomicity) is the
    labeller's responsibility.
    """
    if contribution.kind != "action":
        raise ValueError("only a submitted action can be auto-completed")
    return Delegation(contribution.campaign_id, delegation_id, contribution.actor_id,
                      contribution.submitted_by, _AUTO_COMPLETE, "contribution",
                      "auto_complete", contribution_id=contribution.contribution_id)


# --- shared authority path -----------------------------------------------------

def _unique(items: tuple, key: str, label: str) -> None:
    """Ids are unique per campaign; another campaign's record is merely irrelevant."""
    ids = [(item.campaign_id, getattr(item, key)) for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {label} ids")


def _delegation_valid(d: Delegation, *, context: NarrationContext, actor_id: str,
                      controller: str, revocations: tuple[Revocation, ...],
                      expresses: str | None = None) -> bool:
    """Valid for this actor and moment; contribution grants need their contribution."""
    if (d.campaign_id != context.campaign_id or d.actor_id != actor_id
            or controller == DIRECTOR or d.granted_by != controller):
        return False
    if any(r.campaign_id == d.campaign_id and r.delegation_id == d.delegation_id
           and r.revoked_by == controller for r in revocations):
        return False
    return {
        "campaign": True,
        "scene": d.scene_id == context.scene_id,
        "turn": (d.scene_id, d.turn_id) == (context.scene_id, context.turn_id),
        "contribution": expresses is not None and d.contribution_id == expresses,
    }[d.scope]


def _granting(delegations, *, domain: str, **kwargs) -> Delegation | None:
    return next((d for d in delegations
                 if domain in d.domains and _delegation_valid(d, **kwargs)), None)


def _usable_conditions(conditions, context: NarrationContext, actor_id: str):
    return tuple(c for c in conditions
                 if c.campaign_id == context.campaign_id and c.actor_id == actor_id
                 and c.disclosed and c.within_agreed_limits)


def narration_policy(context: NarrationContext, actor_id: str,
                     controls: Iterable[ControlAssignment],
                     delegations: Iterable[Delegation] = (),
                     revocations: Iterable[Revocation] = (),
                     conditions: Iterable[ImposedCondition] = ()) -> NarrationPolicy:
    """Deterministic prompt-ready statement of the director's authority."""
    delegations, revocations = tuple(delegations), tuple(revocations)
    conditions = tuple(conditions)
    _unique(delegations, "delegation_id", "delegation")
    _unique(conditions, "condition_id", "condition")
    controller = controller_of(context.campaign_id, actor_id, controls)
    if controller is None:
        raise AuthorityError(f"no control assignment for {actor_id!r}")
    if controller == DIRECTOR:
        return NarrationPolicy(context.campaign_id, actor_id, controller,
                               ALL_DOMAINS, frozenset(), (), ())
    granted = tuple(d for d in delegations if d.scope != "contribution" and _delegation_valid(
        d, context=context, actor_id=actor_id, controller=controller, revocations=revocations))
    usable = _usable_conditions(conditions, context, actor_id)
    director_may = OPEN_DOMAINS | ADJUDICATED_DOMAINS | frozenset(
        domain for d in granted for domain in d.domains)
    if usable:
        director_may |= CONDITIONAL_DOMAINS
    return NarrationPolicy(context.campaign_id, actor_id, controller, director_may,
                           PROTECTED_DOMAINS - director_may,
                           tuple(d.delegation_id for d in granted),
                           tuple(c.condition_id for c in usable))


def review_narration(context: NarrationContext, claims: Iterable[NarrationClaim],
                     controls: Iterable[ControlAssignment],
                     contributions: Iterable[PlayerContribution] = (),
                     delegations: Iterable[Delegation] = (),
                     revocations: Iterable[Revocation] = (),
                     conditions: Iterable[ImposedCondition] = (),
                     resolutions: Iterable[Resolution] = ()) -> NarrationReview:
    """Accept each claim with its authority basis, or reject it with a reason."""
    claims, controls, delegations = tuple(claims), tuple(controls), tuple(delegations)
    revocations, conditions = tuple(revocations), tuple(conditions)
    contributions = tuple(contributions)
    _unique(claims, "claim_id", "claim")
    _unique(delegations, "delegation_id", "delegation")
    _unique(conditions, "condition_id", "condition")
    _unique(contributions, "contribution_id", "contribution")
    found = {c.contribution_id: c for c in contributions if c.campaign_id == context.campaign_id}
    results: dict[str, set[str]] = {}
    for r in resolutions:
        if r.campaign_id == context.campaign_id:
            results.setdefault(r.attempt_id, set()).add(r.result)

    def decide(claim: NarrationClaim) -> tuple[bool, str]:
        if claim.campaign_id != context.campaign_id:
            return False, "cross_campaign"
        controller = None
        if claim.subject_id is not None:
            controller = controller_of(context.campaign_id, claim.subject_id, controls)
            if controller is None:
                return False, "unknown_control"
        human = controller not in (None, DIRECTOR)
        if claim.domain == "outcome":
            recorded = results.get(claim.attempt_id, set())
            if len(recorded) > 1:
                return False, "conflicting_resolutions"
            if recorded and recorded != {claim.result}:
                return False, "contradicts_resolution"
            attempt = found.get(claim.attempt_id)
            if human and not (attempt and attempt.kind == "action"
                              and attempt.actor_id == claim.subject_id
                              and attempt.submitted_by == controller):
                return False, "unknown_attempt"
            return True, f"resolution:{claim.attempt_id}" if recorded else "director"
        if claim.domain in OPEN_DOMAINS or not human:
            return True, "director"
        if claim.domain == "involuntary_reaction":
            condition = next((c for c in conditions if c.condition_id == claim.condition_id
                              and c.campaign_id == context.campaign_id
                              and c.actor_id == claim.subject_id), None)
            if condition is None:
                return False, "missing_condition"
            if not condition.disclosed:
                return False, "undisclosed_condition"
            if not condition.within_agreed_limits:
                return False, "outside_agreed_limits"
            return True, f"condition:{condition.condition_id}"
        grant = dict(context=context, actor_id=claim.subject_id, controller=controller,
                     revocations=revocations, domain=claim.domain)
        if claim.expresses is not None:
            source = found.get(claim.expresses)
            if not (source and source.scene_id == context.scene_id
                    and source.turn_id == context.turn_id
                    and source.actor_id == claim.subject_id
                    and source.submitted_by == controller
                    and claim.domain in CONTRIBUTION_DOMAINS[source.kind]):
                return False, "invalid_expression"
            if claim.text.strip() == source.text.strip():
                return True, f"player:{source.contribution_id}"
            d = _granting(delegations, expresses=claim.expresses, **grant)
            return (True, f"delegation:{d.delegation_id}") if d else (False, "not_verbatim")
        d = _granting(delegations, **grant)
        return (True, f"delegation:{d.delegation_id}") if d else (False, "pc_authorship")

    accepted, rejected = [], []
    for claim in claims:
        ok, why = decide(claim)
        if ok:
            accepted.append(AcceptedClaim(claim, why))
        else:
            rejected.append(RejectedClaim(claim, why))
    return NarrationReview(tuple(accepted), tuple(rejected))


def apply_corrections(review: NarrationReview, corrections: Iterable[Correction],
                      controls: Iterable[ControlAssignment],
                      ) -> tuple[NarrationReview, tuple[CorrectionRefusal, ...]]:
    """Strike protected overreach, flag other disputes, refuse vetoes of rulings."""
    controls = tuple(controls)
    accepted = list(review.accepted)
    rejected, disputed = list(review.rejected), list(review.disputed)
    refused: list[CorrectionRefusal] = []
    for correction in corrections:
        index = next((i for i, a in enumerate(accepted)
                      if a.claim.claim_id == correction.claim_id
                      and a.claim.campaign_id == correction.campaign_id), None)
        if index is None:
            refused.append(CorrectionRefusal(correction, "unknown_claim"))
            continue
        item = accepted[index]
        claim = item.claim
        if claim.subject_id is None:
            players = {c.controller_id for c in controls
                       if c.campaign_id == claim.campaign_id and c.controller_id != DIRECTOR}
            entitled = correction.corrected_by in players
        else:
            controller = controller_of(claim.campaign_id, claim.subject_id, controls)
            entitled = controller != DIRECTOR and correction.corrected_by == controller
        if not entitled:
            refused.append(CorrectionRefusal(correction, "not_controller"))
        elif claim.domain in PROTECTED_DOMAINS:
            del accepted[index]
            rejected.append(RejectedClaim(claim, "player_correction"))
        elif item.basis.startswith(("resolution:", "condition:")):
            refused.append(CorrectionRefusal(correction, "not_player_authority"))
        else:
            disputed.append(correction)
    return NarrationReview(tuple(accepted), tuple(rejected), tuple(disputed)), tuple(refused)
